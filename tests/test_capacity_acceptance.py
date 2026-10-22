"""Authored complete-window traces validate accounting, never deployment capacity."""

import copy
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from bidops.operations_acceptance.capacity import (
    CapacityManifest,
    build_run_plan,
    canonical_sha256,
    evaluate_capacity,
    load_manifest,
    scheduled_requests,
)
from pydantic import ValidationError

MANIFEST = Path(__file__).resolve().parents[1] / "evals/capacity-v1/manifest.json"


@pytest.fixture
def manifest():
    return load_manifest(MANIFEST, hashlib.sha256(MANIFEST.read_bytes()).hexdigest())


def plan_for(manifest):
    return build_run_plan(
        manifest,
        run_id="UNIT-capacity",
        started_at=datetime(2026, 10, 23, tzinfo=UTC),
        code_sha="a" * 40,
        lock_sha256="b" * 64,
        durable_lock_sha256="c" * 64,
    )


def complete_unit_trace(manifest):
    """Only a scorer fixture. Values are fabricated, not measured HTTP results."""
    plan = plan_for(manifest)
    requests, jobs = [], []
    slots = [0] * manifest.limits.configured_compute_concurrency
    for scheduled in scheduled_requests(manifest, plan.run_id):
        start = scheduled.scheduled_offset_ms
        run_id = f"UNIT-job-{scheduled.sequence}" if scheduled.operation == "submit_task" else None
        requests.append(
            {
                **scheduled.model_dump(mode="json"),
                "started_offset_ms": start,
                "finished_offset_ms": start + 100,
                "outcome": "response",
                "status_code": 202 if run_id else 200,
                "content_verified": True,
                "expected_items": 1,
                "returned_items": 1,
                "expected_total": 1,
                "reported_total": 1,
                "truncated": False,
                "run_id": run_id,
            }
        )
        if run_id:
            index = min(range(len(slots)), key=lambda n: slots[n])
            compute_start = max(start + 50, slots[index])
            slots[index] = compute_start + 30_100
            jobs.append(
                {
                    "run_id": run_id,
                    "submission_event_id": scheduled.event_id,
                    "accepted_offset_ms": start + 50,
                    "compute_started_offset_ms": compute_start,
                    "compute_finished_offset_ms": slots[index],
                    "status": "waiting_review",
                    "requested_model_delay_ms": 30_000,
                    "observed_model_delay_ms": 30_010,
                    "real_model_calls": 0,
                    "real_source_calls": 0,
                    "result_ids": [f"UNIT-result-{scheduled.sequence}"],
                }
            )
    workload, data = manifest.workload, manifest.data
    stop = (workload.warmup_seconds + workload.steady_seconds) * 1000
    end = max(stop, math.ceil(max(slots) / 1000) * 1000)
    inventory = {
        "enterprises": data.enterprises,
        "accounts": data.accounts,
        "notice_versions": data.notice_versions,
        "procurement_packages": data.procurement_packages,
        "notice_text_bytes_total": data.notice_versions * data.notice_text_bytes_each,
        "notice_text_bytes_min": data.notice_text_bytes_each,
        "notice_text_bytes_max": data.notice_text_bytes_each,
        "material_sizes_bytes": [data.material_bytes_each] * data.materials,
        "attachment_sizes_bytes": [data.attachment_bytes_each] * data.attachments,
        "inventory_sha256": "d" * 64,
    }
    trace = {
        "run_id": plan.run_id,
        "plan_sha256": canonical_sha256(plan),
        "measurement_kind": "target_environment",  # UNIT assertion, not a real target run.
        "ended_offset_ms": end,
        "clients": [
            {
                "client_id": client,
                "account_id": account,
                "session_sha256": hashlib.sha256(client.encode()).hexdigest(),
            }
            for client, account in manifest.client_accounts.items()
        ],
        "hardware": manifest.hardware_target.model_dump(),
        "dataset_before": inventory,
        "dataset_after": copy.deepcopy(inventory),
        "requests": requests,
        "jobs": jobs,
        "resources": [
            {
                "offset_ms": at,
                "rss_bytes": 50_000_000,
                "cpu_percent": 10.0,
                "disk_free_bytes": manifest.hardware_target.disk_free_bytes,
                "compute_active": sum(
                    j["compute_started_offset_ms"] <= at < j["compute_finished_offset_ms"]
                    for j in jobs
                ),
                "active_jobs": sum(j["accepted_offset_ms"] <= at for j in jobs),
                "queued_jobs": sum(
                    j["accepted_offset_ms"] <= at < j["compute_started_offset_ms"] for j in jobs
                ),
                "worker_alive": True,
                "oom_events": 0,
                "deadlock_events": 0,
            }
            for at in range(0, end + 1, workload.resource_interval_ms)
        ],
    }
    return plan, trace


@pytest.fixture
def passing(manifest):
    # A hypothetical two-slot configuration proves the scorer's positive path;
    # the committed runtime remains one slot and must fail its declared target.
    value = manifest.model_dump()
    value["profile_id"] = "UNIT-hypothetical-two-slots"
    value["limits"]["configured_compute_concurrency"] = 2
    manifest = CapacityManifest.model_validate(value)
    return manifest, *complete_unit_trace(manifest)


def score(passing):
    return evaluate_capacity(*passing)


def codes(report):
    return {i.code for i in report.issues}


def steady_read(trace, n=0):
    return [
        r
        for r in trace["requests"]
        if r["scheduled_offset_ms"] >= 300_000 and r["operation"] == "read_search"
    ][n]


def test_frozen_profile_is_complete_but_existing_one_slot_fails(manifest):
    plan, trace = complete_unit_trace(manifest)
    result = evaluate_capacity(manifest, plan, trace)
    assert result.manifest_status == "PASS"
    assert result.capacity_status == "FAIL"
    assert result.gates["api_performance"] == "PASS"
    assert result.gates["declared_compute_target"] == "FAIL"
    assert result.metrics["observed_compute_peak"] == 1
    assert result.metrics["model_wait_p95_ms"] == 30_010
    assert manifest.limits.configured_compute_concurrency == 1


def test_complete_hypothetical_trace_exercises_positive_path(passing):
    report = score(passing)
    assert report.capacity_status == "PASS"
    assert report.recovery_status == "BLOCKED"
    assert report.model_evidence == "offline_simulation_only"
    assert report.metrics["operation_counts"] == {
        "read_search": 2808,
        "task_status": 648,
        "submit_task": 72,
        "review": 72,
    }
    assert set(report.metrics["client_counts"].values()) == {360}
    assert report.metrics["observed_compute_peak"] == 2
    assert report.metrics["admission_p95_ms"] == 100
    assert report.metrics["model_wait_p95_ms"] == 30_010
    assert report.metrics["steady_error_denominator"] == 3600


@pytest.mark.parametrize("trace", [{}, None, [], "UNIT-invalid"])
def test_empty_or_invalid_input_never_passes(manifest, trace):
    result = evaluate_capacity(manifest, plan_for(manifest), trace)
    assert result.manifest_status == "PASS"
    assert result.gates["measurement"] == "BLOCKED"
    assert result.capacity_status != "PASS"


def test_missing_event_arrays_are_a_reported_failure_not_a_crash(passing):
    manifest, plan, _ = passing
    result = evaluate_capacity(manifest, plan, {"run_id": plan.run_id})
    assert result.capacity_status == "FAIL"
    assert "requests_missing_or_invalid" in codes(result)


@pytest.mark.parametrize(
    "field", ["schema_version", "data", "hardware_target", "limits", "simulation", "thresholds"]
)
def test_manifest_missing_dimension_is_rejected(manifest, field):
    raw = manifest.model_dump()
    del raw[field]
    with pytest.raises(ValidationError):
        CapacityManifest.model_validate(raw)


def test_manifest_pin_and_duplicate_json_keys(tmp_path, manifest):
    path = tmp_path / "manifest.json"
    path.write_bytes(MANIFEST.read_bytes())
    with pytest.raises(ValueError, match="different from its pin"):
        load_manifest(path, "0" * 64)
    raw = path.read_text().replace(
        '"schema_version":', '"schema_version":"capacity-v1", "schema_version":', 1
    )
    path.write_text(raw)
    with pytest.raises(ValueError, match="invalid"):
        load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize(
    "field,value",
    [
        ("warmup_seconds", True),
        ("steady_seconds", 1.8),
        ("requests_per_second", "2"),
        ("operation_weights", {"read_search": 100}),
    ],
)
def test_manifest_units_types_and_operation_mix_are_strict(manifest, field, value):
    raw = manifest.model_dump()
    raw["workload"][field] = value
    with pytest.raises(ValidationError):
        CapacityManifest.model_validate(raw)


def test_mutable_nested_manifest_is_revalidated(manifest):
    manifest.workload.operation_weights["review"] = 1
    with pytest.raises(ValidationError):
        plan_for(manifest)


def test_plan_requires_aware_time(manifest):
    with pytest.raises(ValidationError):
        build_run_plan(
            manifest,
            run_id="UNIT",
            started_at=datetime(2026, 10, 23),
            code_sha="a" * 40,
            lock_sha256="b" * 64,
            durable_lock_sha256="c" * 64,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "different-run"),
        ("plan_sha256", "0" * 64),
        ("ended_offset_ms", 1_800_000),
        ("measurement_kind", {}),
    ],
)
def test_trace_cannot_replace_frozen_plan_or_window(passing, field, value):
    passing[2][field] = value
    assert score(passing).capacity_status == "FAIL"


def test_local_engineering_is_not_target_environment_acceptance(passing):
    passing[2]["measurement_kind"] = "local_engineering"
    assert score(passing).capacity_status == "BLOCKED"


def test_single_client_or_shared_session_cannot_impersonate_ten(passing):
    trace = passing[2]
    for row in trace["clients"]:
        row["session_sha256"] = "e" * 64
    assert "active_client_or_session_inventory_mismatch" in codes(score(passing))
    trace["clients"] = trace["clients"][:1]
    assert score(passing).capacity_status == "FAIL"


def test_per_client_actual_requests_are_checked(passing):
    for row in passing[2]["requests"]:
        row["client_id"] = "client-01"
    result = score(passing)
    assert result.capacity_status == "FAIL"
    assert "per_client_load_not_met" in codes(result)


def test_proportion_change_is_not_hidden_in_a_successful_total(passing):
    steady_read(passing[2])["operation"] = "review"
    assert "operation_mix_not_met" in codes(score(passing))


def test_duplicate_request_is_reported_not_used_to_fill_a_missing_client(passing):
    passing[2]["requests"].append(copy.deepcopy(steady_read(passing[2])))
    report = score(passing)
    assert report.capacity_status == "FAIL"
    assert "duplicate_request_event" in codes(report)
    assert report.metrics["requests_raw_count"] == 4201


def test_missing_tail_cannot_be_cherry_picked_as_a_shorter_window(passing):
    passing[2]["requests"] = passing[2]["requests"][:-200]
    passing[2]["steady_stop_ms"] = (
        2_000_000  # Ignored: only the pre-run plan determines the window.
    )
    result = score(passing)
    assert result.capacity_status == "FAIL"
    assert result.metrics["expected_steady_requests"] == 3600
    assert result.metrics["steady_stop_ms"] == 2_100_000
    assert result.metrics["steady_errors"] == 200


def test_invalid_event_retains_one_planned_error_in_denominator(passing):
    steady_read(passing[2])["started_offset_ms"] = "not-an-integer"
    report = score(passing)
    assert report.capacity_status == "FAIL"
    assert report.metrics["requests_invalid_count"] == 1
    assert report.metrics["steady_error_denominator"] == 3600
    assert report.metrics["steady_errors"] == 1
    assert report.metrics["non_model_api_p95_ms"] is None


@pytest.mark.parametrize("outcome", ["exception", "timeout"])
def test_failed_requests_remain_in_error_and_latency_denominators(passing, outcome):
    row = steady_read(passing[2])
    row.update(outcome=outcome, status_code=None, content_verified=False)
    result = score(passing)
    assert result.metrics["steady_error_denominator"] == 3600
    assert result.metrics["steady_errors"] == 1
    assert result.metrics["non_model_api_p95_ms"] == 100


def test_one_percent_error_rate_is_failure_not_rounded_down(passing):
    for index in range(36):
        steady_read(passing[2], index).update(status_code=503, content_verified=False)
    result = score(passing)
    assert result.metrics["steady_errors"] == 36
    assert result.metrics["error_rate"] == 0.01
    assert result.gates["api_performance"] == "FAIL"


def test_p95_uses_nearest_rank_and_includes_error_latencies(passing):
    for index in range(181):
        row = steady_read(passing[2], index)
        row["finished_offset_ms"] = row["started_offset_ms"] + 1001
        row.update(status_code=500, content_verified=False)
    result = score(passing)
    assert result.metrics["non_model_api_p95_ms"] == 1001
    assert result.gates["api_performance"] == "FAIL"


def test_submit_admission_latency_is_measured_separately(passing):
    for row in passing[2]["requests"]:
        if row["operation"] == "submit_task":
            row["finished_offset_ms"] = row["started_offset_ms"] + 1001
    report = score(passing)
    assert report.metrics["non_model_api_p95_ms"] == 100
    assert report.metrics["admission_p95_ms"] == 1001
    assert report.gates["api_performance"] == "FAIL"


def test_warmup_is_excluded_without_moving_the_steady_window(passing):
    for row in passing[2]["requests"]:
        if row["scheduled_offset_ms"] < 300_000:
            row["finished_offset_ms"] = row["started_offset_ms"] + 1500
    report = score(passing)
    assert report.metrics["non_model_api_p95_ms"] == 100
    assert report.capacity_status == "PASS"


@pytest.mark.parametrize(
    "field,value", [("truncated", True), ("returned_items", 0), ("reported_total", 500)]
)
def test_successful_http_cannot_hide_omission_or_500_row_truncation(passing, field, value):
    steady_read(passing[2])[field] = value
    assert score(passing).gates["data_integrity"] == "FAIL"


def test_just_matching_row_counts_without_file_sizes_is_not_full_scale(passing):
    passing[2]["dataset_before"]["material_sizes_bytes"][0] = 1
    assert "dataset_before_target_mismatch" in codes(score(passing))


@pytest.mark.parametrize("field", ["requested_model_delay_ms", "observed_model_delay_ms"])
def test_model_delay_cannot_be_an_unobserved_zero_or_wrong_unit(passing, field):
    passing[2]["jobs"][0][field] = 30
    assert score(passing).gates["model_simulation"] == "FAIL"


def test_live_call_is_not_disguised_as_offline_simulation(passing):
    passing[2]["jobs"][0]["real_model_calls"] = 1
    assert score(passing).gates["model_simulation"] == "FAIL"


def test_accepted_job_and_result_receipts_are_complete(passing):
    passing[2]["jobs"].pop()
    assert "accepted_job_receipts_missing" in codes(score(passing))


def test_two_jobs_cannot_share_one_result(passing):
    passing[2]["jobs"][1]["result_ids"] = passing[2]["jobs"][0]["result_ids"]
    assert "missing_or_duplicate_job_result" in codes(score(passing))


def test_interval_claims_need_resource_concurrency_observation(passing):
    for row in passing[2]["resources"]:
        row["compute_active"] = min(1, row["compute_active"])
    assert "sampled_compute_below_target" in codes(score(passing))


@pytest.mark.parametrize(
    "field,value",
    [
        ("oom_events", 1),
        ("deadlock_events", 1),
        ("worker_alive", False),
        ("disk_free_bytes", 0),
        ("active_jobs", 101),
    ],
)
def test_resource_failures_are_not_dropped_from_steady_statistics(passing, field, value):
    passing[2]["resources"][1][field] = value  # Warmup is not exempt from fatal integrity checks.
    assert score(passing).gates["data_integrity"] == "FAIL"


def test_missing_resource_tail_and_interior_gap_are_failures(passing):
    passing[2]["resources"] = passing[2]["resources"][:-10]
    assert "resource_window_missing_or_gapped" in codes(score(passing))


def recovery():
    return {
        "backup_consistent_through_at": "2026-10-23T00:00:00Z",
        "backup_completed_at": "2026-10-23T00:10:00Z",
        "loss_at": "2026-10-23T01:00:00Z",
        "health_ready_at": "2026-10-23T01:05:00Z",
        "authorization_verified_at": "2026-10-23T01:10:00Z",
        "business_verified_at": "2026-10-23T01:15:00Z",
        **dict.fromkeys(
            [
                "post_backup_new_data",
                "post_backup_account_disabled",
                "post_backup_grant_revoked",
                "post_backup_material_deleted",
                "old_session_denied",
                "old_material_link_denied",
                "old_review_event_denied",
                "snapshot_binding_verified",
                "confirmed_ledger_preserved",
                "unknown_reservations_preserved",
                "current_authorization_confirmed",
                "private_access_closed_if_unconfirmed",
            ],
            True,
        ),
    }


def test_rpo_and_rto_include_authorization_and_business_readiness(passing):
    passing[2]["recovery"] = recovery()
    result = score(passing)
    assert result.recovery_status == "PASS"
    assert result.metrics["rpo_seconds"] == 3600
    assert result.metrics["rto_seconds"] == 900


def test_fast_health_is_not_recovery_if_authorization_takes_over_30_minutes(passing):
    value = recovery()
    value["authorization_verified_at"] = "2026-10-23T01:31:00Z"
    passing[2]["recovery"] = value
    assert score(passing).recovery_status == "FAIL"


def test_unconfirmed_authorization_is_blocked_and_must_remain_closed(passing):
    value = recovery()
    value["current_authorization_confirmed"] = False
    passing[2]["recovery"] = value
    assert score(passing).recovery_status == "BLOCKED"
    value["private_access_closed_if_unconfirmed"] = False
    assert score(passing).recovery_status == "FAIL"


@pytest.mark.parametrize(
    "field",
    [
        "post_backup_grant_revoked",
        "post_backup_material_deleted",
        "old_session_denied",
        "old_review_event_denied",
        "confirmed_ledger_preserved",
        "unknown_reservations_preserved",
    ],
)
def test_recovery_never_hides_revocation_or_ledger_loss(passing, field):
    value = recovery()
    value[field] = False
    passing[2]["recovery"] = value
    assert score(passing).recovery_status == "FAIL"


def test_invalid_record_body_is_never_echoed_in_report(passing):
    row = steady_read(passing[2])
    row["UNIT_PRIVATE_BODY"] = "UNIT_SECRET_DO_NOT_LOG"
    report = score(passing)
    assert report.capacity_status == "FAIL"
    assert "UNIT_SECRET_DO_NOT_LOG" not in report.model_dump_json()


def test_example_roundtrip_for_external_harness(passing):
    manifest, plan, trace = passing
    # The harness writes JSON; strict numeric units survive serialization.
    report = evaluate_capacity(manifest, plan, json.loads(json.dumps(trace)))
    assert report.capacity_status == "PASS"


def test_warmup_concurrency_cannot_replace_steady_samples(passing):
    for row in passing[2]["resources"]:
        row["compute_active"] = 2 if row["offset_ms"] < 300_000 else min(1, row["compute_active"])
    report = score(passing)
    assert report.capacity_status == "FAIL"
    assert report.gates["observed_compute_target"] == "FAIL"
    assert report.metrics["resource_compute_peak"] == 1
    assert "resource_job_compute_mismatch" in codes(report)


def test_resource_concurrency_must_match_registered_job_intervals(passing):
    # Preserve the steady peak of two but invent activity on an idle sample.
    next(row for row in passing[2]["resources"] if row["offset_ms"] == 0)["compute_active"] = 1
    report = score(passing)
    assert report.capacity_status == "FAIL"
    assert report.gates["data_integrity"] == "FAIL"
    assert "resource_job_compute_mismatch" in codes(report)


def test_resource_sampler_uses_actual_clock_without_requiring_exact_zero(passing):
    passing[2]["resources"][0]["offset_ms"] = 1
    assert score(passing).capacity_status == "PASS"
    passing[2]["resources"] = passing[2]["resources"][2:]
    assert "resource_window_missing_or_gapped" in codes(score(passing))
