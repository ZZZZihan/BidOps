"""Synthetic completeness/negative checks, never real enterprise acceptance."""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bidops.uat import UatBundle, verify_bundle
from bidops.uat.environment import LOCK_FILES, sha256_file
from test_uat_environment import SHA
from test_uat_environment import uat_env as uat_env
from test_uat_environment import uat_project as uat_project

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
# Explicit contract counts are independent of the production Markdown parser.
# Original 69 + B04-08 + B09-08 = 71, of which 65 precede B10's six items.
UPSTREAM_IDS = {
    f"B{section:02}-{number:02}"
    for section, count in enumerate((7, 6, 8, 8, 7, 7, 7, 7, 8), start=1)
    for number in range(1, count + 1)
}


def stamp(minutes):
    return (NOW - timedelta(hours=2) + timedelta(minutes=minutes)).isoformat()


def ref(name):
    return {
        "object_id": name + "-object",
        "version_id": name,
        "sha256": hashlib.sha256(name.encode()).hexdigest(),
    }


@pytest.fixture
def complete(uat_project, uat_env, tmp_path):
    evidence = tmp_path / "controlled"
    evidence.mkdir(mode=0o700)
    body = b'{"fixture":"synthetic; not a real user or business signoff"}'
    path = evidence / "record.json"
    path.write_bytes(body)
    path.chmod(0o600)
    artifact = {
        "artifact_id": "proof",
        "path": "record.json",
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
        "media_type": "application/json",
        "sensitivity": "private",
    }
    accounts = [
        {
            "account_id": name,
            "operator_id": name + "-person",
            "subject_binding_sha256": hashlib.sha256(name.encode()).hexdigest(),
            "roles": ["analyst"] if name == "alice" else ["analyst", "reviewer"],
            "actual_human": True,
            "independent": True,
            "developer_or_tuner": False,
            "attestation_artifact_id": "proof",
        }
        for name in ("alice", "bob")
    ]
    inventory = {
        "deployment_id": "trial-host",
        "origin": uat_env["BIDOPS_ENTERPRISE_PUBLIC_URL"],
        "issuer": uat_env["BIDOPS_ENTERPRISE_OIDC_ISSUER"],
        "client_id": "bidops",
        "callback_uri": uat_env["BIDOPS_ENTERPRISE_PUBLIC_URL"] + "/api/auth/callback",
        "backchannel_uri": uat_env["BIDOPS_ENTERPRISE_PUBLIC_URL"] + "/api/auth/backchannel-logout",
        "keycloak_version": "configured-version",
        "mapped_roles": ["analyst", "reviewer"],
        "target_host_id": "host-a",
        "operations_owner_id": "ops-person",
        "single_host_single_worker": True,
        "clock_synchronized": True,
        "accounts": accounts,
        "profile_revisions": [ref("profile-v1")],
        "material_revisions": [ref("material-v1")],
        "notice_revisions": [ref("notice-v1")],
        "materials_authorized": True,
        "source_permission_artifact_ids": ["proof"],
        "alert_receiver_id": "ops-inbox",
        "rpo_target_seconds": 60,
        "rto_target_seconds": 300,
    }
    records = [
        {
            "ac_id": f"B10-{i:02}",
            "environment_kind": "real_enterprise",
            "deployment_id": "trial-host",
            "code_sha": SHA,
            "operator_id": "operator",
            "reviewer_id": "independent-reviewer",
            "started_at": stamp(0),
            "finished_at": stamp(60),
            "artifact_ids": ["proof"],
            "claimed_outcome": "PASS",
        }
        for i in range(1, 7)
    ]
    records[0]["configuration_artifact_id"] = "proof"
    records[1]["sessions"] = [
        {
            "account_id": name,
            "login_at": stamp(5),
            "reauthentication_at": stamp(10),
            "logout_at": stamp(20),
            "login_succeeded": True,
            "reauthentication_succeeded": True,
            "logout_replay_rejected": True,
            "cookie_flags_verified": True,
            "csrf_rejected": True,
        }
        for name in ("alice", "bob")
    ]
    records[1]["access_checks"] = [
        {
            "actor_account_id": actor,
            "target_account_id": owner,
            "resource": kind,
            "observed_at": stamp(15),
            "expected_status": 404,
            "observed_status": 404,
        }
        for actor, owner in (("alice", "bob"), ("bob", "alice"))
        for kind in ("material", "session", "job")
    ] + [
        {
            "resource": "anonymous",
            "observed_at": stamp(15),
            "expected_status": 401,
            "observed_status": 401,
        },
        {
            "actor_account_id": "alice",
            "resource": "role",
            "observed_at": stamp(15),
            "expected_status": 403,
            "observed_status": 403,
        },
    ]
    records[2].update(
        changes=[
            {
                "account_id": "alice",
                "kind": kind,
                "idp_event_at": stamp(25),
                "protected_denied_at": stamp(29),
                "new_task_denied_at": stamp(29),
                "tool_denied_at": stamp(29),
                "result_denied_at": stamp(29),
                "queued_job_id": "queue-job",
                "waiting_job_id": "waiting-job",
                "old_session_retained": True,
            }
            for kind in ("disable", "role_change")
        ],
        idp_outage_expiry_denied=True,
        clock_skew_bounded=True,
    )
    records[3]["flow"] = {
        "owner_account_id": "alice",
        "reviewer_account_id": "bob",
        "profile_revision": ref("profile-v1"),
        "notice_revision": ref("notice-v1"),
        "material_revisions": [ref("material-v1")],
        "batch_id": "batch-1",
        "run_id": "job-1",
        "case_id": "case-1",
        "successor_run_id": "job-2",
        "export_artifact_id": "proof",
        "independent_review_artifact_id": "proof",
        "all_evidence_opened": True,
        "individually_reviewed": True,
        "successor_recomputed": True,
        "agent_result_unchanged": True,
        "reviewer_assignment_verified": True,
    }
    records[4]["recovery"] = {
        "job_id": "job-1",
        "interrupted_at": stamp(40),
        "restarted_at": stamp(41),
        "recovered_at": stamp(42),
        "model_calls_before": 9,
        "model_calls_after": 9,
        "same_key_same_id": True,
        "different_input_409": True,
        "native_steps_verified": True,
        "backup_artifact_id": "proof",
        "restore_artifact_id": "proof",
        "ledger_reconciliation_artifact_id": "proof",
        "restore_epoch_changed": True,
        "old_session_rejected": True,
        "source_quarantine_verified": True,
        "job_quarantine_verified": True,
        "review_quarantine_verified": True,
        "explicit_reauthorization_verified": True,
        "old_decisions_ineffective": True,
        "alert_sent_at": stamp(42),
        "alert_received_at": stamp(43),
        "rpo_observed_seconds": 30,
        "rto_observed_seconds": 120,
    }
    records[5].update(
        prerequisites=[
            {"ac_id": ac, "outcome": "PASS", "artifact_ids": ["proof"]}
            for ac in sorted(UPSTREAM_IDS)
        ],
        human_claimed_decision="GO",
        release_review_artifact_id="proof",
        trial_scale_artifact_id="proof",
    )
    payload = {
        "schema_version": "bidops-uat-v1",
        "code_sha": SHA,
        "lock_hashes": {
            name: sha256_file(uat_project / relative) for name, (_, relative) in LOCK_FILES.items()
        },
        "plan_sha256": sha256_file(uat_project / "docs/ENTERPRISE-PR-PLAN.md"),
        "inventory": inventory,
        "artifacts": [artifact],
        "records": records,
    }
    return payload, evidence


def verify(complete, uat_project, uat_env):
    payload, evidence = complete
    return verify_bundle(payload, evidence, environ=uat_env, project_root=uat_project, now=NOW)


def test_complete_claims_never_become_tool_business_pass(complete, uat_project, uat_env):
    before = copy.deepcopy(complete[0])
    report = verify(complete, uat_project, uat_env)
    assert report["schema_valid"]
    assert report["issues"] == []
    assert [row["status"] for row in report["acs"]] == ["EVIDENCE_COMPLETE"] * 6
    assert report["required_upstream_acceptance_items"] == len(UPSTREAM_IDS) == 65
    assert report["evidence_readiness"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert report["release_decision"] == "NOT_AUTHORIZED_BY_TOOL"
    assert report["business_acceptance"] == "NOT_VERIFIED_BY_TOOL"
    assert report["model_calls"] == report["network_probes"] == 0
    assert complete[0] == before
    assert "synthetic; not a real user" not in json.dumps(report)
    assert "record.json" not in json.dumps(report)


@pytest.mark.parametrize("ac_id", ["B04-08", "B09-08"])
@pytest.mark.parametrize("outcome", [None, "NOT_RUN", "BLOCKED", "FAIL"])
def test_added_upstream_gate_cannot_be_omitted_or_unpassed(
    complete, uat_project, uat_env, ac_id, outcome
):
    prereqs = complete[0]["records"][5]["prerequisites"]
    item = next(row for row in prereqs if row["ac_id"] == ac_id)
    if outcome is None:
        prereqs.remove(item)
    else:
        item["outcome"] = outcome
    report = verify(complete, uat_project, uat_env)
    assert report["required_upstream_acceptance_items"] == 65
    assert report["acs"][5]["status"] == "BLOCKED"
    expected = (
        "all_b01_to_b09_acceptance_ids_required"
        if outcome is None
        else "upstream_acceptance_not_passed"
    )
    assert expected in report["acs"][5]["issues"]
    assert report["evidence_readiness"] == "INCOMPLETE"
    assert report["release_decision"] == "NO_GO"


@pytest.mark.parametrize(
    "label",
    ["C（实施新增）", "C （实施新增）", "C(implementation-added)", "C (implementation-added)"],
)
def test_annotated_plan_type_retains_the_acceptance_gate(complete, uat_project, uat_env, label):
    plan_path = uat_project / "docs/ENTERPRISE-PR-PLAN.md"
    plan = plan_path.read_text()
    assert "| B04-08 C（实施新增） |" in plan
    plan_path.write_text(plan.replace("| B04-08 C（实施新增） |", f"| B04-08 {label} |"))
    complete[0]["plan_sha256"] = sha256_file(plan_path)
    report = verify(complete, uat_project, uat_env)
    assert report["required_upstream_acceptance_items"] == 65
    assert report["issues"] == []
    assert report["acs"][5]["status"] == "EVIDENCE_COMPLETE"
    assert report["evidence_readiness"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert report["release_decision"] == "NOT_AUTHORIZED_BY_TOOL"


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_upstream",
        "duplicate_b10",
        "unknown_section",
        "invalid_id_separator",
        "invalid_id_digits",
        "invalid_type",
        "unclosed_annotation",
        "annotation_suffix",
        "missing_opening_pipe",
        "missing_closing_pipe",
        "missing_column",
    ],
)
def test_malformed_or_duplicate_plan_gate_is_not_silently_skipped(
    complete, uat_project, uat_env, mutation
):
    plan_path = uat_project / "docs/ENTERPRISE-PR-PLAN.md"
    plan = plan_path.read_text()
    row = next(line for line in plan.splitlines() if line.startswith("| B04-08 "))
    if mutation == "duplicate_upstream":
        changed = row + "\n" + row
    elif mutation == "duplicate_b10":
        b10 = next(line for line in plan.splitlines() if line.startswith("| B10-01 "))
        changed = row + "\n" + b10
    elif mutation == "unknown_section":
        changed = row.replace("B04-08", "B11-01")
    elif mutation == "invalid_id_separator":
        changed = row.replace("B04-08", "B04_08")
    elif mutation == "invalid_id_digits":
        changed = row.replace("B04-08", "B4-08")
    elif mutation == "invalid_type":
        changed = row.replace("C（实施新增）", "X")
    elif mutation == "unclosed_annotation":
        changed = row.replace("C（实施新增）", "C（实施新增")
    elif mutation == "annotation_suffix":
        changed = row.replace("C（实施新增）", "C（实施新增）ignored")
    elif mutation == "missing_opening_pipe":
        changed = row[1:].lstrip()
    elif mutation == "missing_closing_pipe":
        changed = row.rstrip(" |")
    else:
        changed = "|".join(row.split("|")[:-2]) + "|"
    plan_path.write_text(plan.replace(row, changed))
    complete[0]["plan_sha256"] = sha256_file(plan_path)
    report = verify(complete, uat_project, uat_env)
    assert "acceptance_plan_unavailable" in report["issues"]
    assert report["required_upstream_acceptance_items"] == 0
    assert all(item["status"] == "BLOCKED" for item in report["acs"])
    assert report["evidence_readiness"] == "INCOMPLETE"
    assert report["release_decision"] == "NO_GO"


@pytest.mark.parametrize("ac_id", ["B04-08", "B09-08", "B10-06"])
@pytest.mark.parametrize("mutation", ["deleted_row", "no_pipes", "blank_and_malformed_id"])
def test_baseline_gate_cannot_disappear_with_its_prerequisite(
    complete, uat_project, uat_env, ac_id, mutation
):
    plan_path = uat_project / "docs/ENTERPRISE-PR-PLAN.md"
    plan = plan_path.read_text()
    row = next(line for line in plan.splitlines() if line.startswith(f"| {ac_id} "))
    if mutation == "deleted_row":
        changed = ""
    elif mutation == "no_pipes":
        changed = row.replace("|", "")
    else:
        bad_id = ac_id.replace("B04", "B4").replace("B09", "B9").replace("B10", "B1")
        changed = "\n" + row.replace(ac_id, bad_id)
    plan_path.write_text(plan.replace(row, changed))
    complete[0]["plan_sha256"] = sha256_file(plan_path)
    complete[0]["records"][5]["prerequisites"] = [
        item for item in complete[0]["records"][5]["prerequisites"] if item["ac_id"] != ac_id
    ]
    report = verify(complete, uat_project, uat_env)
    assert "acceptance_plan_unavailable" in report["issues"]
    assert report["required_upstream_acceptance_items"] == 0
    assert all(item["status"] == "BLOCKED" for item in report["acs"])
    assert report["evidence_readiness"] == "INCOMPLETE"
    assert report["release_decision"] == "NO_GO"


def test_valid_added_gate_is_required_without_replacing_the_baseline(
    complete, uat_project, uat_env
):
    plan_path = uat_project / "docs/ENTERPRISE-PR-PLAN.md"
    plan = plan_path.read_text()
    row = next(line for line in plan.splitlines() if line.startswith("| B09-08 "))
    added = "| B09-09 C（实施新增） | independent input | explicit invariant | evidence |"
    plan_path.write_text(plan.replace(row, row + "\n" + added))
    complete[0]["plan_sha256"] = sha256_file(plan_path)
    report = verify(complete, uat_project, uat_env)
    assert report["required_upstream_acceptance_items"] == 66
    assert report["acs"][5]["status"] == "BLOCKED"
    assert report["release_decision"] == "NO_GO"
    complete[0]["records"][5]["prerequisites"].append(
        {"ac_id": "B09-09", "outcome": "PASS", "artifact_ids": ["proof"]}
    )
    report = verify(complete, uat_project, uat_env)
    assert report["required_upstream_acceptance_items"] == 66
    assert report["issues"] == []
    assert report["evidence_readiness"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert report["business_acceptance"] == "NOT_VERIFIED_BY_TOOL"
    assert report["release_decision"] == "NOT_AUTHORIZED_BY_TOOL"


def test_new_b10_gate_requires_explicit_uat_contract_upgrade(complete, uat_project, uat_env):
    plan_path = uat_project / "docs/ENTERPRISE-PR-PLAN.md"
    plan = plan_path.read_text()
    row = next(line for line in plan.splitlines() if line.startswith("| B10-06 "))
    added = "| B10-07 B（实施新增） | actual input | new UAT invariant | evidence |"
    plan_path.write_text(plan.replace(row, row + "\n" + added))
    complete[0]["plan_sha256"] = sha256_file(plan_path)
    report = verify(complete, uat_project, uat_env)
    assert "acceptance_plan_unavailable" in report["issues"]
    assert report["required_upstream_acceptance_items"] == 0
    assert all(item["status"] == "BLOCKED" for item in report["acs"])
    assert report["release_decision"] == "NO_GO"


@pytest.mark.parametrize("index", range(6))
def test_each_ac_missing_is_blocked_never_omitted(complete, uat_project, uat_env, index):
    complete[0]["records"].pop(index)
    report = verify(complete, uat_project, uat_env)
    assert len(report["acs"]) == 6
    assert report["acs"][index]["status"] == "BLOCKED"
    assert report["release_decision"] == "NO_GO"


@pytest.mark.parametrize("kind", ["engineering_fixture", "historical"])
def test_simulation_and_history_never_real_acceptance(complete, uat_project, uat_env, kind):
    for row in complete[0]["records"]:
        row["environment_kind"] = kind
    report = verify(complete, uat_project, uat_env)
    assert all(row["status"] == "BLOCKED" for row in report["acs"])
    assert report["release_decision"] == "NO_GO"


@pytest.mark.parametrize(
    "index,field,value,expected",
    [
        (0, "configuration_artifact_id", None, "configuration_evidence_missing"),
        (1, "sessions", [], "two_account_sessions_missing"),
        (1, "access_checks", [], "anonymous_check_missing"),
        (2, "changes", [], "disable_and_role_change_checks_missing"),
        (2, "idp_outage_expiry_denied", False, "idp_outage_or_clock_check_missing"),
        (3, "flow", None, "business_flow_missing"),
        (4, "recovery", None, "recovery_drill_missing"),
        (5, "prerequisites", [], "all_b01_to_b09_acceptance_ids_required"),
        (5, "human_claimed_decision", "NO_GO", "human_release_decision_not_go"),
        (5, "production_e3_claimed", True, "production_e3_requires_separate_acceptance"),
    ],
)
def test_required_ac_inputs_fail_closed(
    complete, uat_project, uat_env, index, field, value, expected
):
    complete[0]["records"][index][field] = value
    report = verify(complete, uat_project, uat_env)
    assert expected in report["acs"][index]["issues"]
    assert report["release_decision"] == "NO_GO"


def test_300_second_boundary_and_clock_rollback(complete, uat_project, uat_env):
    change = complete[0]["records"][2]["changes"][0]
    change["protected_denied_at"] = stamp(30)
    assert verify(complete, uat_project, uat_env)["acs"][2]["status"] == "EVIDENCE_COMPLETE"
    change["protected_denied_at"] = (
        datetime.fromisoformat(stamp(30)) + timedelta(microseconds=1)
    ).isoformat()
    assert (
        "revocation_exceeds_300_seconds_or_clock_invalid"
        in verify(complete, uat_project, uat_env)["acs"][2]["issues"]
    )
    change["protected_denied_at"] = stamp(24)
    assert (
        "revocation_exceeds_300_seconds_or_clock_invalid"
        in verify(complete, uat_project, uat_env)["acs"][2]["issues"]
    )


def test_duplicate_humans_developer_reviewer_and_unassigned_review_block(
    complete, uat_project, uat_env
):
    inventory = complete[0]["inventory"]
    inventory["accounts"][1]["operator_id"] = inventory["accounts"][0]["operator_id"]
    inventory["accounts"][1]["developer_or_tuner"] = True
    complete[0]["records"][3]["flow"]["reviewer_assignment_verified"] = False
    report = verify(complete, uat_project, uat_env)
    assert "two_distinct_human_accounts_missing" in report["acs"][0]["issues"]
    assert "independent_business_reviewer_missing" in report["acs"][3]["issues"]
    assert "business_chain_or_full_reassessment_missing" in report["acs"][3]["issues"]


def test_restore_isolation_rto_and_ledger_evidence_required(complete, uat_project, uat_env):
    recovery = complete[0]["records"][4]["recovery"]
    recovery.update(
        old_decisions_ineffective=False,
        model_calls_after=0,
        rto_observed_seconds=1,
        ledger_reconciliation_artifact_id="missing",
    )
    report = verify(complete, uat_project, uat_env)
    issues = report["acs"][4]["issues"]
    assert "recovery_isolation_or_idempotency_check_missing" in issues
    assert "model_usage_history_regressed" in issues
    assert "rto_target_unmet_or_missing" in issues
    assert "referenced_artifact_unverified" in issues


@pytest.mark.parametrize(
    "field,target",
    [("rpo_target_seconds", 86_401), ("rto_target_seconds", 1_801)],
)
def test_preregistered_recovery_targets_cannot_relax_the_plan(
    complete, uat_project, uat_env, field, target
):
    # Even a short successful drill cannot validate a weaker declared target.
    complete[0]["inventory"][field] = target
    report = verify(complete, uat_project, uat_env)
    assert "recovery_targets_exceed_plan" in report["acs"][0]["issues"]
    assert report["acs"][4]["status"] == "BLOCKED"
    assert report["release_decision"] == "NO_GO"


def test_reported_rpo_and_rto_cannot_pass_by_raising_targets(complete, uat_project, uat_env):
    complete[0]["inventory"].update(rpo_target_seconds=172_800, rto_target_seconds=3_600)
    complete[0]["records"][4]["recovery"].update(
        interrupted_at=stamp(10),
        restarted_at=stamp(11),
        recovered_at=stamp(50),
        alert_sent_at=stamp(12),
        alert_received_at=stamp(13),
        rpo_observed_seconds=90_000,
        rto_observed_seconds=2_400,
    )
    report = verify(complete, uat_project, uat_env)
    assert {"rpo_target_unmet_or_missing", "rto_target_unmet_or_missing"} <= set(
        report["acs"][4]["issues"]
    )
    assert report["release_decision"] == "NO_GO"


@pytest.mark.parametrize(
    "sent,received",
    [(stamp(1), stamp(2)), (stamp(39), stamp(41)), (stamp(42), stamp(41))],
)
def test_alert_must_follow_this_interruption_and_precede_receipt(
    complete, uat_project, uat_env, sent, received
):
    complete[0]["records"][4]["recovery"].update(alert_sent_at=sent, alert_received_at=received)
    report = verify(complete, uat_project, uat_env)
    assert "alert_receipt_order_invalid" in report["acs"][4]["issues"]
    assert report["release_decision"] == "NO_GO"


def test_alert_generation_is_bounded_by_five_minutes_at_microsecond_precision(
    complete, uat_project, uat_env
):
    recovery = complete[0]["records"][4]["recovery"]
    recovery.update(alert_sent_at=stamp(45), alert_received_at=stamp(46))
    assert verify(complete, uat_project, uat_env)["acs"][4]["status"] == "EVIDENCE_COMPLETE"
    recovery["alert_sent_at"] = (
        datetime.fromisoformat(stamp(45)) + timedelta(microseconds=1)
    ).isoformat()
    report = verify(complete, uat_project, uat_env)
    assert "alert_generation_exceeds_300_seconds" in report["acs"][4]["issues"]
    assert report["release_decision"] == "NO_GO"


def test_plan_recovery_limits_and_more_stringent_targets_are_inclusive(
    complete, uat_project, uat_env
):
    inventory = complete[0]["inventory"]
    recovery = complete[0]["records"][4]["recovery"]
    inventory.update(rpo_target_seconds=86_400, rto_target_seconds=1_800)
    recovery.update(
        interrupted_at=stamp(10),
        restarted_at=stamp(11),
        recovered_at=stamp(40),
        alert_sent_at=stamp(12),
        alert_received_at=stamp(13),
        rpo_observed_seconds=86_400,
        rto_observed_seconds=1_800,
    )
    assert verify(complete, uat_project, uat_env)["acs"][4]["status"] == "EVIDENCE_COMPLETE"
    inventory["rpo_target_seconds"] = 86_399
    inventory["rto_target_seconds"] = 1_799
    report = verify(complete, uat_project, uat_env)
    assert {"rpo_target_unmet_or_missing", "rto_target_unmet_or_missing"} <= set(
        report["acs"][4]["issues"]
    )
    assert report["release_decision"] == "NO_GO"


@pytest.mark.parametrize("mutation", ["tamper", "missing", "symlink", "permissions", "size"])
def test_artifact_hash_path_size_and_permissions(
    complete, uat_project, uat_env, mutation, tmp_path
):
    path = complete[1] / "record.json"
    if mutation == "tamper":
        path.write_bytes(b"changed")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "symlink":
        target = tmp_path / "outside.json"
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)
        path.unlink()
        path.symlink_to(target)
    elif mutation == "permissions":
        path.chmod(0o644)
    else:
        complete[0]["artifacts"][0]["size_bytes"] += 1
    report = verify(complete, uat_project, uat_env)
    assert "artifact_integrity_or_permissions_failed" in report["issues"]
    assert report["verified_artifacts"] == 0
    assert report["release_decision"] == "NO_GO"


@pytest.mark.parametrize(
    "path", ["../secret", "/private/secret", "a/../b", "a\\b", "https://secret"]
)
def test_traversal_and_remote_artifact_rejected_without_echo(complete, uat_project, uat_env, path):
    complete[0]["artifacts"][0]["path"] = path
    report = verify(complete, uat_project, uat_env)
    assert not report["schema_valid"]
    assert path not in json.dumps(report)


def test_stale_code_lock_plan_and_upstream_blockers_cannot_be_removed(
    complete, uat_project, uat_env
):
    complete[0]["code_sha"] = "b" * 40
    complete[0]["lock_hashes"]["durable"] = "b" * 64
    complete[0]["plan_sha256"] = "c" * 64
    complete[0]["records"][5]["prerequisites"][0]["outcome"] = "BLOCKED"
    report = verify(complete, uat_project, uat_env)
    assert {
        "code_version_mismatch",
        "lock_version_mismatch",
        "acceptance_plan_hash_mismatch",
    } <= set(report["issues"])
    assert "upstream_acceptance_not_passed" in report["acs"][5]["issues"]
    assert report["release_decision"] == "NO_GO"


def test_extra_actor_secret_unknown_ac_and_naive_time_rejected(complete, uat_project, uat_env):
    original = copy.deepcopy(complete[0])
    for mutate in (
        lambda p: p.update(client_secret="DO_NOT_ECHO"),
        lambda p: p["records"][0].update(actor="DO_NOT_ECHO"),
        lambda p: p["records"][0].update(ac_id="DO_NOT_ECHO"),
        lambda p: p["records"][0].update(started_at="2026-10-23T10:00:00"),
    ):
        payload = copy.deepcopy(original)
        mutate(payload)
        report = verify_bundle(
            payload, complete[1], environ=uat_env, project_root=uat_project, now=NOW
        )
        assert not report["schema_valid"]
        assert "DO_NOT_ECHO" not in json.dumps(report)
        assert report["release_decision"] == "NO_GO"


def test_example_has_all_six_unexecuted_records():
    root = Path(__file__).resolve().parents[1]
    example = UatBundle.model_validate(json.loads((root / "config/uat.example.json").read_text()))
    assert len(example.records) == 6
    assert all(row.claimed_outcome == "NOT_RUN" for row in example.records)
    assert not example.artifacts


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("duplicate_artifact", "duplicate_artifact_id"),
        ("duplicate_record", "duplicate_ac_record"),
        ("root_permissions", "evidence_root_must_be_private_directory"),
        ("callback_mismatch", "environment_not_ready"),
    ],
)
def test_global_integrity_blocks_all_release_claims(
    complete, uat_project, uat_env, mutation, expected
):
    payload, root = complete
    if mutation == "duplicate_artifact":
        payload["artifacts"].append(copy.deepcopy(payload["artifacts"][0]))
    elif mutation == "duplicate_record":
        payload["records"][-1] = copy.deepcopy(payload["records"][0])
    elif mutation == "root_permissions":
        root.chmod(0o755)
    else:
        payload["inventory"]["callback_uri"] += "/wrong"
    report = verify(complete, uat_project, uat_env)
    assert expected in report["issues"]
    assert report["release_decision"] == "NO_GO"


def test_only_one_direction_of_cross_account_access_is_insufficient(complete, uat_project, uat_env):
    checks = complete[0]["records"][1]["access_checks"]
    checks[:] = [
        row
        for row in checks
        if not (row.get("actor_account_id") == "bob" and row["resource"] == "job")
    ]
    report = verify(complete, uat_project, uat_env)
    assert "cross_account_job_check_missing" in report["acs"][1]["issues"]


def test_future_or_out_of_window_observations_are_not_valid_uat(complete, uat_project, uat_env):
    complete[0]["records"][0]["finished_at"] = (NOW + timedelta(minutes=1)).isoformat()
    complete[0]["records"][1]["sessions"][0]["login_at"] = stamp(-1)
    report = verify(complete, uat_project, uat_env)
    assert "observation_in_future" in report["acs"][0]["issues"]
    assert "observation_outside_record_window" in report["acs"][1]["issues"]


def test_declared_failure_is_retained_even_when_all_fields_are_complete(
    complete, uat_project, uat_env
):
    complete[0]["records"][2]["claimed_outcome"] = "FAIL"
    report = verify(complete, uat_project, uat_env)
    assert report["acs"][2]["status"] == "FAIL"
    assert report["acs"][2]["claimed_outcome"] == "FAIL"
    assert report["release_decision"] == "NO_GO"
