"""Audit caller-pinned saved evaluation records, never execute a paid model."""

import argparse
import json
import os
import subprocess
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError

from bidops.acceptance.collection import TaskRegistration, _prediction
from bidops.acceptance.evaluation import BUSINESS_ACS, audit_evaluation, canonical_sha256
from bidops.acceptance.run_manifest import (
    RunManifest,
    check_environment,
    money,
    pinned_json,
    reconcile_ledger,
)


def _section(issues, **details):
    return {
        "status": "FAIL" if issues else "PASS",
        "issues": [{"code": code, "status": "FAIL"} for code in sorted(set(issues))],
        **details,
    }


def check_collection(collection, bundle, manifest):
    if collection is None:
        return {
            "status": "PASS" if manifest.purpose == "engineering_fixture" else "BLOCKED",
            "issues": []
            if manifest.purpose == "engineering_fixture"
            else [{"code": "saved_collection_missing", "status": "BLOCKED"}],
            "executions": [],
            "actual_models": [],
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_cny": "0",
            "reserved_cost_cny": "0",
        }
    issues = []
    metadata, records = collection["metadata"], collection["records"]
    observed = collection["observed_tasks"]
    registrations = [TaskRegistration.model_validate(row["registration"]) for row in records]
    if {r.task_id for r in registrations} != {row["task_id"] for row in bundle["gold_tasks"]}:
        issues.append("registered_task_set_mismatch")
    if len(registrations) != len({r.task_id for r in registrations}):
        issues.append("duplicate_registered_task")
    if metadata["registered"] != len(records):
        issues.append("registered_count_mismatch")
    registration_sha = canonical_sha256([r.model_dump(mode="json") for r in registrations])
    if (
        registration_sha != manifest.registration_sha256
        or registration_sha != metadata["registration_sha256"]
    ):
        issues.append("registration_pin_mismatch")
    if canonical_sha256(records) != metadata["records_sha256"]:
        issues.append("collection_records_hash_mismatch")
    if (
        canonical_sha256(observed) != metadata["observed_tasks_sha256"]
        or observed != bundle["observed_tasks"]
    ):
        issues.append("scored_output_not_saved_collection")
    projected = []
    for record, registration in zip(records, registrations, strict=True):
        unit = record["candidate"]
        if (
            unit["unit_id"] != registration.unit_id
            or unit["package_id"] != registration.package_id
            or registration.notice_revision.model_dump(mode="json") not in unit["notice_revisions"]
        ):
            issues.append("record_target_mismatch")
        if registration.execution_kind == "not_submitted" and (
            record["agent_result"] is not None
            or record["execution"] is not None
            or record["provenance"]
            or record["usage"]
        ):
            issues.append("unsubmitted_record_has_execution")
        original, job = record["agent_result"], record["execution"]
        if original is not None:
            if record["provenance"] != original["provenance"]:
                issues.append("original_provenance_mismatch")
            if registration.expected_result_revision is not None and (
                original["result_revision"] != registration.expected_result_revision
            ):
                issues.append("original_result_revision_mismatch")
        elif record["provenance"]:
            issues.append("provenance_without_original_result")
        if registration.execution_kind == "screening":
            if (
                original is None
                or original["run_id"] != registration.execution_id
                or job is not None
                or record["usage"] != record["provenance"]
            ):
                issues.append("original_screening_receipt_mismatch")
        elif registration.execution_kind == "job":
            if (
                job is None
                or job["run_id"] != registration.execution_id
                or record["usage"] != job["usage"]
                or (original is None) != (job["result_id"] is None)
                or (original is not None and original["run_id"] != job["result_id"])
            ):
                issues.append("original_job_receipt_mismatch")
        expected, selection_error = _prediction(
            registration,
            unit,
            record["fields"],
            record["execution"],
            record["agent_result"],
            record["source_state"],
        )
        projected.append(expected)
        if selection_error != record["assessment_selection_error"]:
            issues.append("assessment_selection_error_mismatch")
    if projected != observed:
        issues.append("observed_not_original_record_projection")
    if metadata["states"] != dict(Counter(row["state"] for row in projected)):
        issues.append("collection_states_mismatch")
    executions = metadata["executions"]
    unique = {(row["kind"], row["execution_id"]) for row in executions}
    if len(unique) != len(executions) or unique != {
        (r.execution_kind, r.execution_id) for r in registrations if r.execution_id is not None
    }:
        issues.append("execution_receipt_set_mismatch")
    for row in records:
        registration = row["registration"]
        if registration["execution_id"] is not None:
            matches = [
                e
                for e in executions
                if e["kind"] == registration["execution_kind"]
                and e["execution_id"] == registration["execution_id"]
            ]
            if len(matches) != 1 or any(
                row[key] != matches[0][key]
                for key in ("usage", "provenance", "raw_model_outputs_sha256")
            ):
                issues.append("task_execution_receipt_mismatch")
    actual_models, calls, inputs, outputs = set(), 0, 0, 0
    estimated, reserved = Decimal(0), Decimal(0)
    for execution in executions:
        usage, provenance = execution["usage"], execution["provenance"]
        for field in ("model_calls", "input_tokens", "output_tokens"):
            if type(usage.get(field)) is not int or usage[field] < 0:
                raise ValueError("invalid_usage_count")
        for field in ("estimated_cost_cny", "reserved_cost_cny"):
            value = money(usage.get(field, "0"))
            if not value.is_finite() or value < 0:
                raise ValueError("invalid_usage_cost")
        calls += usage["model_calls"]
        inputs += usage["input_tokens"]
        outputs += usage["output_tokens"]
        estimated += money(usage.get("estimated_cost_cny", "0"))
        reserved += money(usage.get("reserved_cost_cny", "0"))
        models = usage.get("actual_models", [])
        if not isinstance(models, list) or any(not isinstance(m, str) or not m for m in models):
            raise ValueError("invalid_actual_models")
        actual_models.update(models)
        if provenance:
            for field, expected in (
                ("code_sha", manifest.candidate_code_sha),
                ("implementation_sha256", manifest.candidate_implementation_sha256),
                ("lock_sha256", manifest.lock_sha256),
                ("prompt_version", manifest.prompt_version),
                ("prompt_sha256", manifest.prompt_sha256),
                ("rule_version", manifest.rule_version),
            ):
                if provenance.get(field) != expected:
                    issues.append(f"execution_{field}_mismatch")
            if canonical_sha256(provenance.get("parameters")) != manifest.parameters_sha256:
                issues.append("execution_parameters_mismatch")
        elif usage["model_calls"]:
            issues.append("execution_provenance_missing")
        if manifest.purpose == "independent_acceptance" and (
            usage.get("offline") is not False
            or usage.get("model_identity_complete") is not True
            or usage.get("usage_complete") is not True
        ):
            issues.append("actual_provider_usage_incomplete_or_offline")
    return _section(
        issues,
        executions=executions,
        actual_models=sorted(actual_models),
        model_calls=calls,
        input_tokens=inputs,
        output_tokens=outputs,
        estimated_cost_cny=str(estimated),
        reserved_cost_cny=str(reserved),
        limits=[
            "Usage is deduplicated by saved execution, never summed per package task.",
            "stale_disclosed describes the service disclosure at collection time.",
            "Raw outputs remain in the authorized source repository; collection retains hashes.",
        ],
    )


def audit_run(manifest_path, manifest_pin, input_path, *, collection_path=None, ledger_path=None):
    """Pins are supplied out of band; output retains every scored failure."""
    manifest = RunManifest.model_validate(pinned_json(manifest_path, manifest_pin))
    started = datetime.now(UTC)
    environment = check_environment(manifest)
    if environment["status"] != "PASS":
        return {
            "status": "FAIL",
            "environment": environment,
            "business_acceptance": "BLOCKED",
            "agent_score": None,
        }
    bundle = pinned_json(input_path, manifest.input_sha256)
    if bundle.get("purpose") != manifest.purpose:
        raise ValueError("purpose_mismatch")
    collection = (
        pinned_json(collection_path, manifest.collection_sha256) if collection_path else None
    )
    ledger = pinned_json(ledger_path, manifest.ledger_sha256) if ledger_path else None
    if (collection_path is None) != (manifest.collection_sha256 is None) or (
        (ledger_path is None) != (manifest.ledger_sha256 is None)
    ):
        raise ValueError("artifact_path_pin_mismatch")
    scored = audit_evaluation(bundle)
    collected = check_collection(collection, bundle, manifest)
    budget = reconcile_ledger(ledger, collected["executions"], manifest.execution_finished_at)
    environment_end = check_environment(manifest)
    # These are separate conclusions: a valid report can record failed quality.
    integrity = _section(
        [
            name
            for name, result in (
                ("environment", environment_end),
                ("collection", collected),
                ("budget", budget),
            )
            if result["status"] == "FAIL"
        ]
    )
    statuses = [integrity["status"], scored["status"]]
    if manifest.purpose == "independent_acceptance":
        statuses += [collected["status"], budget["status"], "BLOCKED"]
    status = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
    ac = dict(scored["ac_status"])
    # Supplied declarations cannot authenticate independent people/source permission.
    # Preserve declared checks separately; never sign B gates on their behalf.
    for name in BUSINESS_ACS:
        if ac[name] != "FAIL":
            ac[name] = "BLOCKED"
    if integrity["status"] == "FAIL":
        ac["B08-03"] = "FAIL"
    return {
        "status": status,
        "purpose": manifest.purpose,
        "run_id": manifest.run_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "manifest_sha256": manifest_pin,
        "versions": manifest.model_dump(mode="json"),
        "environment": environment_end,
        "integrity": integrity,
        "evaluation": scored,
        "collection": collected,
        "cumulative_budget": budget,
        "ac_status": ac,
        "report_generation": "PASS",
        "agent_score": None,
        "business_acceptance": "BLOCKED",
        "real_calls_made_by_this_audit": 0,
        "external_checks_required": [
            "Actual source permission, complete source bytes, independent people and signatures.",
            "Provider and full ledger reconciliation; undisclosed runs and answer-access audit.",
            "B10 independent user acceptance in the real enterprise environment.",
        ],
    }


def write_private_report(path, result):
    """Create exclusively with 0600; never overwrite a first/failed report."""
    encoded = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--collection", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = audit_run(
            args.manifest,
            args.expected_manifest_sha256,
            args.input,
            collection_path=args.collection,
            ledger_path=args.ledger,
        )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
        InvalidOperation,
        ValidationError,
        subprocess.SubprocessError,
    ):
        result = {
            "status": "FAIL",
            "issues": [{"code": "invalid_pinned_input_or_environment"}],
            "business_acceptance": "BLOCKED",
            "agent_score": None,
        }
    try:
        write_private_report(args.output, result)
    except OSError:
        print(json.dumps({"status": "FAIL", "code": "new_private_output_required"}))
        return 2
    print(
        json.dumps(
            {"status": result["status"], "business_acceptance": "BLOCKED", "agent_score": None}
        )
    )
    return 0 if result["status"] == "PASS" else 2 if result["status"] == "FAIL" else 3
