"""Check supplied UAT evidence integrity and completeness, never its truth."""

import hashlib
import json
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from bidops.uat.environment import check_environment, non_placeholder_https, sha256_file
from bidops.uat.schemas import UatBundle

AC_IDS = tuple(f"B10-{number:02}" for number in range(1, 7))
# B09-04/06 minima remain binding in B10. Inventory may tighten these
# preregistered targets, but cannot weaken the frozen plan's limits.
MAX_RPO_SECONDS = 86_400
MAX_RTO_SECONDS = 1_800
MAX_ALERT_GENERATION_SECONDS = 300


def _refs(model):
    refs = set()
    value = model.model_dump() if hasattr(model, "model_dump") else model
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("artifact_id") and item:
                refs.add(item)
            elif key.endswith("artifact_ids"):
                refs.update(item)
            elif isinstance(item, (dict, list)):
                refs.update(_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_refs(item))
    return refs


def _artifact_ok(root, artifact):
    try:
        relative = Path(artifact.path)
        path = root / relative
        # Reject links in every component, including links back into the root.
        for parent in (path, *path.parents):
            if parent == root.parent:
                break
            if parent.is_symlink():
                return False
        if not path.resolve().is_relative_to(root.resolve()):
            return False
        mode = path.stat()
        return (
            stat.S_ISREG(mode.st_mode)
            and mode.st_mode & 0o077 == 0
            and mode.st_size == artifact.size_bytes
            and mode.st_size > 0
            and sha256_file(path, max_bytes=artifact.size_bytes) == artifact.sha256
        )
    except (OSError, ValueError):
        return False


def _times(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, datetime):
        return [value]
    if isinstance(value, dict):
        return [stamp for item in value.values() for stamp in _times(item)]
    if isinstance(value, list):
        return [stamp for item in value for stamp in _times(item)]
    return []


def verify_bundle(payload, evidence_root, *, environ=None, project_root=None, now=None):
    """Read bounded private files. Never performs requests or workflow actions.

    PASS is only the operator's supplied claim. EVIDENCE_COMPLETE means the
    declared checks and hashes are complete; a separate independent release
    review must establish that people, signatures and events are real.
    """
    now = now or datetime.now(UTC)
    root = Path(project_root or Path(__file__).resolve().parents[3])
    issues = set()
    try:
        bundle = UatBundle.model_validate(payload)
    except (ValidationError, TypeError, ValueError):
        return {
            "schema_valid": False,
            "issues": ["invalid_uat_contract"],
            "acs": [
                {"ac_id": key, "status": "BLOCKED", "issues": ["invalid_uat_contract"]}
                for key in AC_IDS
            ],
            "evidence_readiness": "INCOMPLETE",
            "business_acceptance": "NOT_VERIFIED_BY_TOOL",
            "release_decision": "NO_GO",
            "network_probes": 0,
            "model_calls": 0,
        }
    environment = check_environment(environ, project_root=root, inventory=bundle.inventory)
    if not environment["configuration_ready"]:
        issues.add("environment_not_ready")
    if bundle.code_sha != environment["code_sha"]:
        issues.add("code_version_mismatch")
    if bundle.lock_hashes != environment["lock_hashes"]:
        issues.add("lock_version_mismatch")
    try:
        plan_path = root / "docs/ENTERPRISE-PR-PLAN.md"
        plan = plan_path.read_text()
        if hashlib.sha256(plan.encode()).hexdigest() != bundle.plan_sha256:
            issues.add("acceptance_plan_hash_mismatch")
        required = set(re.findall(r"^\| (B0[1-9]-[0-9]{2}) [CB] \|", plan, re.MULTILINE))
        if not required or {key[:3] for key in required} != {f"B0{i}" for i in range(1, 10)}:
            issues.add("acceptance_plan_unavailable")
    except (OSError, UnicodeError):
        issues.add("acceptance_plan_unavailable")
        required = set()
    private_root = Path(evidence_root)
    try:
        root_private = (
            private_root.is_dir()
            and not private_root.is_symlink()
            and private_root.stat().st_mode & 0o077 == 0
        )
    except OSError:
        root_private = False
    if not root_private:
        issues.add("evidence_root_must_be_private_directory")
    artifacts = {item.artifact_id: item for item in bundle.artifacts}
    if len(artifacts) != len(bundle.artifacts):
        issues.add("duplicate_artifact_id")
    if sum(item.size_bytes for item in artifacts.values()) > 200_000_000:
        issues.add("evidence_total_size_exceeded")
        verified = set()
    else:
        verified = {
            key
            for key, item in artifacts.items()
            if root_private and _artifact_ok(private_root, item)
        }
    if len(verified) != len(artifacts):
        issues.add("artifact_integrity_or_permissions_failed")
    records = {record.ac_id: record for record in bundle.records}
    if len(records) != len(bundle.records):
        issues.add("duplicate_ac_record")
    inventory = bundle.inventory
    accounts = {account.account_id: account for account in inventory.accounts}
    accounts_unique = (
        len(accounts) == len(inventory.accounts)
        and len({a.subject_binding_sha256 for a in accounts.values()}) == len(accounts)
        and len({a.operator_id for a in accounts.values()}) == len(accounts)
    )
    results = []
    for ac_id in AC_IDS:
        local = set()

        def require(ok, code, target=local):
            if not ok:
                target.add(code)

        record = records.get(ac_id)
        if record is None:
            results.append({"ac_id": ac_id, "status": "BLOCKED", "issues": ["record_missing"]})
            continue
        require(record.environment_kind == "real_enterprise", "real_environment_evidence_missing")
        require(record.deployment_id == inventory.deployment_id, "deployment_mismatch")
        require(record.code_sha == bundle.code_sha, "record_code_version_mismatch")
        require(record.claimed_outcome == "PASS", "operator_has_not_claimed_pass")
        require(
            record.operator_id and record.reviewer_id and record.operator_id != record.reviewer_id,
            "independent_operator_and_reviewer_missing",
        )
        require(record.started_at and record.finished_at, "record_times_missing")
        require(
            bool(record.artifact_ids) and len(set(record.artifact_ids)) == len(record.artifact_ids),
            "record_artifacts_missing_or_duplicated",
        )
        refs = _refs(record)
        require(refs <= verified, "referenced_artifact_unverified")
        require(refs <= set(record.artifact_ids), "nested_artifact_not_in_record")
        if record.started_at and record.finished_at:
            require(
                all(record.started_at <= t <= record.finished_at for t in _times(record)),
                "observation_outside_record_window",
            )
            require(record.finished_at <= now + timedelta(seconds=30), "observation_in_future")
        if ac_id == "B10-01":
            require(
                non_placeholder_https(inventory.origin, origin=True)
                and non_placeholder_https(inventory.issuer),
                "real_https_configuration_missing",
            )
            require(
                inventory.keycloak_version
                and inventory.target_host_id
                and inventory.operations_owner_id,
                "idp_machine_or_operations_owner_missing",
            )
            require(
                inventory.single_host_single_worker and inventory.clock_synchronized,
                "single_worker_or_clock_evidence_missing",
            )
            require(
                len(accounts) >= 2
                and accounts_unique
                and all(a.actual_human for a in accounts.values()),
                "two_distinct_human_accounts_missing",
            )
            require(
                all(set(a.roles) <= set(inventory.mapped_roles) for a in accounts.values()),
                "registered_role_mapping_missing",
            )
            require(
                bool(
                    inventory.material_revisions
                    and inventory.notice_revisions
                    and inventory.profile_revisions
                )
                and inventory.materials_authorized,
                "authorized_material_or_notice_missing",
            )
            require(
                bool(inventory.source_permission_artifact_ids) and _refs(inventory) <= verified,
                "inventory_attestations_missing",
            )
            require(
                _refs(inventory) <= set(record.artifact_ids),
                "inventory_artifact_not_in_environment_record",
            )
            require(
                all(a.attestation_artifact_id for a in accounts.values()),
                "account_attestation_missing",
            )
            require(
                inventory.alert_receiver_id
                and inventory.rpo_target_seconds is not None
                and inventory.rto_target_seconds is not None,
                "alert_or_recovery_targets_missing",
            )
            require(
                (
                    inventory.rpo_target_seconds is None
                    or inventory.rpo_target_seconds <= MAX_RPO_SECONDS
                )
                and (
                    inventory.rto_target_seconds is None
                    or inventory.rto_target_seconds <= MAX_RTO_SECONDS
                ),
                "recovery_targets_exceed_plan",
            )
            require(record.configuration_artifact_id, "configuration_evidence_missing")
        elif ac_id == "B10-02":
            session_ids = {item.account_id for item in record.sessions}
            require(
                len(session_ids) >= 2
                and len(session_ids) == len(record.sessions)
                and session_ids <= set(accounts)
                and accounts_unique,
                "two_account_sessions_missing",
            )
            for session in record.sessions:
                require(
                    session.login_at <= session.reauthentication_at <= session.logout_at,
                    "session_time_order_invalid",
                )
                require(
                    all(
                        (
                            session.login_succeeded,
                            session.reauthentication_succeeded,
                            session.logout_replay_rejected,
                            session.cookie_flags_verified,
                            session.csrf_rejected,
                        )
                    ),
                    "session_or_cookie_checks_failed",
                )
            for item in record.access_checks:
                require(
                    (
                        item.actor_account_id is None
                        if item.resource == "anonymous"
                        else item.actor_account_id in accounts
                    ),
                    "access_account_not_registered",
                )
                require(item.observed_status == item.expected_status, "access_status_mismatch")
            for kind in ("material", "session", "job"):
                for actor in session_ids:
                    for owner in session_ids - {actor}:
                        require(
                            any(
                                item.resource == kind
                                and item.actor_account_id == actor
                                and item.target_account_id == owner
                                and item.expected_status == 404
                                for item in record.access_checks
                            ),
                            "cross_account_" + kind + "_check_missing",
                        )
            require(
                any(
                    item.resource == "anonymous" and item.expected_status == 401
                    for item in record.access_checks
                ),
                "anonymous_check_missing",
            )
            require(
                any(
                    item.resource == "role" and item.expected_status == 403
                    for item in record.access_checks
                ),
                "role_denial_check_missing",
            )
        elif ac_id == "B10-03":
            require(
                {change.kind for change in record.changes} >= {"disable", "role_change"},
                "disable_and_role_change_checks_missing",
            )
            for change in record.changes:
                require(
                    change.account_id in accounts
                    and change.old_session_retained
                    and change.queued_job_id != change.waiting_job_id,
                    "original_session_or_account_missing",
                )
                for stamp in (
                    change.protected_denied_at,
                    change.new_task_denied_at,
                    change.tool_denied_at,
                    change.result_denied_at,
                ):
                    require(
                        0 <= (stamp - change.idp_event_at).total_seconds() <= 300,
                        "revocation_exceeds_300_seconds_or_clock_invalid",
                    )
            require(
                record.idp_outage_expiry_denied and record.clock_skew_bounded,
                "idp_outage_or_clock_check_missing",
            )
        elif ac_id == "B10-04":
            flow = record.flow
            require(flow is not None, "business_flow_missing")
            if flow:
                reviewer = accounts.get(flow.reviewer_account_id)
                require(
                    flow.owner_account_id in accounts
                    and reviewer
                    and flow.owner_account_id != flow.reviewer_account_id
                    and reviewer.independent
                    and reviewer.actual_human
                    and not reviewer.developer_or_tuner
                    and "reviewer" in reviewer.roles,
                    "independent_business_reviewer_missing",
                )
                require(
                    flow.profile_revision in inventory.profile_revisions
                    and flow.notice_revision in inventory.notice_revisions
                    and all(ref in inventory.material_revisions for ref in flow.material_revisions),
                    "business_input_not_in_inventory",
                )
                require(
                    flow.run_id != flow.successor_run_id
                    and all(
                        (
                            flow.all_evidence_opened,
                            flow.individually_reviewed,
                            flow.successor_recomputed,
                            flow.agent_result_unchanged,
                            flow.reviewer_assignment_verified,
                        )
                    ),
                    "business_chain_or_full_reassessment_missing",
                )
        elif ac_id == "B10-05":
            recovery = record.recovery
            require(recovery is not None, "recovery_drill_missing")
            if recovery:
                require(
                    recovery.interrupted_at <= recovery.restarted_at <= recovery.recovered_at,
                    "recovery_time_order_invalid",
                )
                require(
                    recovery.interrupted_at <= recovery.alert_sent_at <= recovery.alert_received_at,
                    "alert_receipt_order_invalid",
                )
                require(
                    (recovery.alert_sent_at - recovery.interrupted_at).total_seconds()
                    <= MAX_ALERT_GENERATION_SECONDS,
                    "alert_generation_exceeds_300_seconds",
                )
                require(
                    all(
                        (
                            recovery.same_key_same_id,
                            recovery.different_input_409,
                            recovery.native_steps_verified,
                            recovery.restore_epoch_changed,
                            recovery.old_session_rejected,
                            recovery.source_quarantine_verified,
                            recovery.job_quarantine_verified,
                            recovery.review_quarantine_verified,
                            recovery.explicit_reauthorization_verified,
                            recovery.old_decisions_ineffective,
                        )
                    ),
                    "recovery_isolation_or_idempotency_check_missing",
                )
                require(
                    recovery.model_calls_after >= recovery.model_calls_before,
                    "model_usage_history_regressed",
                )
                require(
                    inventory.rpo_target_seconds is not None
                    and recovery.rpo_observed_seconds
                    <= inventory.rpo_target_seconds
                    <= MAX_RPO_SECONDS,
                    "rpo_target_unmet_or_missing",
                )
                elapsed = (recovery.recovered_at - recovery.interrupted_at).total_seconds()
                require(
                    inventory.rto_target_seconds is not None
                    and elapsed
                    <= recovery.rto_observed_seconds
                    <= inventory.rto_target_seconds
                    <= MAX_RTO_SECONDS,
                    "rto_target_unmet_or_missing",
                )
        else:
            prereqs = {item.ac_id: item for item in record.prerequisites}
            require(
                len(prereqs) == len(record.prerequisites) and set(prereqs) == required and required,
                "all_b01_to_b09_acceptance_ids_required",
            )
            require(
                all(item.outcome == "PASS" and item.artifact_ids for item in prereqs.values()),
                "upstream_acceptance_not_passed",
            )
            require(
                record.release_review_artifact_id and record.trial_scale_artifact_id,
                "named_release_review_or_trial_scale_missing",
            )
            require(record.human_claimed_decision == "GO", "human_release_decision_not_go")
            require(not record.production_e3_claimed, "production_e3_requires_separate_acceptance")
        status = (
            "FAIL"
            if record.claimed_outcome == "FAIL"
            else "BLOCKED"
            if local or issues
            else "EVIDENCE_COMPLETE"
        )
        results.append(
            {
                "ac_id": ac_id,
                "status": status,
                "claimed_outcome": record.claimed_outcome,
                "issues": sorted(local),
            }
        )
    ready = not issues and all(item["status"] == "EVIDENCE_COMPLETE" for item in results)
    return {
        "schema_valid": True,
        "issues": sorted(issues),
        "acs": results,
        "environment": environment,
        "verified_artifacts": len(verified),
        "required_upstream_acceptance_items": len(required),
        "contract_sha256": hashlib.sha256(
            json.dumps(
                bundle.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "evidence_readiness": "READY_FOR_INDEPENDENT_REVIEW" if ready else "INCOMPLETE",
        "release_decision": "NOT_AUTHORIZED_BY_TOOL" if ready else "NO_GO",
        "business_acceptance": "NOT_VERIFIED_BY_TOOL",
        "network_probes": 0,
        "model_calls": 0,
        "limits": [
            "Artifact hashes verify bytes, not truthful contents, identities or human signatures.",
            "Operator PASS and GO do not authorize acceptance or release through this tool.",
            "Engineering fixtures, history and developer demonstrations cannot satisfy real UAT.",
            "Independent review of private artifacts is required; contents are omitted here.",
        ],
    }
