"""Offline, complete-window capacity accounting; this module sends no requests.

The caller freezes the manifest and RunPlan before execution. Measurements are
receipts from a separate HTTP/resource harness, not proof supplied by this
scorer. UNIT traces test the scorer and cannot establish deployment capacity.
All times ending in ``_ms`` are integer milliseconds relative to plan.started_at.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Positive = Annotated[int, Field(strict=True, gt=0)]
Nonnegative = Annotated[int, Field(strict=True, ge=0)]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
Operation = Literal["read_search", "task_status", "submit_task", "review"]
Status = Literal["PASS", "FAIL", "BLOCKED"]
OPERATIONS = ("read_search", "task_status", "submit_task", "review")


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
        allow_inf_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class DataTarget(Contract):
    enterprises: Positive
    accounts: Positive
    notice_versions: Positive
    procurement_packages: Positive
    notice_text_bytes_each: Positive
    materials: Positive
    material_bytes_each: Positive
    attachments: Positive
    attachment_bytes_each: Positive


class Hardware(Contract):
    logical_cpus: Positive
    memory_bytes: Positive
    disk_free_bytes: Positive
    storage: Literal["local_disk"]


class Workload(Contract):
    warmup_seconds: Positive
    steady_seconds: Positive
    drain_seconds: Nonnegative
    requests_per_second: Positive
    arrival_tolerance_percent: Annotated[int, Field(strict=True, ge=0, le=5)]
    arrival_lateness_ms: Nonnegative
    rate_bucket_seconds: Positive
    clients: Positive
    min_requests_per_client: Positive
    think_time_ms: Positive
    operation_weights: dict[Operation, Positive]
    http_timeout_ms: Positive
    http_retries: Literal[0]
    resource_interval_ms: Positive
    resource_max_gap_ms: Positive

    @model_validator(mode="after")
    def consistent(self):
        if (
            set(self.operation_weights) != set(OPERATIONS)
            or sum(self.operation_weights.values()) != 100
        ):
            raise ValueError("Four operation weights must total 100")
        if (
            1000 % self.requests_per_second
            or self.think_time_ms * self.requests_per_second != self.clients * 1000
        ):
            raise ValueError(
                "Round-robin clients require exact millisecond arrival/think intervals"
            )
        if (
            self.steady_seconds % self.rate_bucket_seconds
            or self.warmup_seconds % self.rate_bucket_seconds
        ):
            raise ValueError("Windows must contain complete rate buckets")
        count = self.steady_seconds * self.requests_per_second
        if (
            count % 100
            or count % self.clients
            or count // self.clients < self.min_requests_per_client
        ):
            raise ValueError("The complete schedule must realize the mix and per-client minimum")
        if self.resource_max_gap_ms < self.resource_interval_ms:
            raise ValueError("Resource sampling gap is smaller than its interval")
        return self


class Limits(Contract):
    asgi_workers: Literal[1]
    target_compute_concurrency: Positive
    configured_compute_concurrency: Positive
    max_active_jobs: Positive
    json_request_bytes: Positive
    material_upload_bytes: Positive
    material_text_chars: Positive
    page_size: Positive
    qualification_requests: Positive
    qualification_seconds: Positive
    qualification_input_bytes: Positive
    qualification_output_tokens: Positive


class ModelSimulation(Contract):
    kind: Literal["offline_function_model"]
    delay_distribution: Literal["fixed_total_per_job"]
    requested_delay_ms: Positive
    observed_delay_tolerance_ms: Nonnegative
    real_model_calls: Literal[0]
    real_source_calls: Literal[0]


class Thresholds(Contract):
    non_model_api_p95_ms: Positive
    admission_p95_ms: Positive
    error_rate_strictly_below: Annotated[float, Field(gt=0, le=1)]
    concurrent_compute_overlap_ms: Positive
    rpo_seconds: Positive
    rto_seconds: Positive


class CapacityManifest(Contract):
    schema_version: Literal["capacity-v1"]
    profile_id: Name
    fixture_kind: Literal["synthetic"]
    data: DataTarget
    hardware_target: Hardware
    workload: Workload
    limits: Limits
    simulation: ModelSimulation
    thresholds: Thresholds
    account_ids: list[Name]
    client_accounts: dict[Name, Name]

    @model_validator(mode="after")
    def complete(self):
        if (
            len(set(self.account_ids)) != len(self.account_ids)
            or len(self.account_ids) != self.data.accounts
        ):
            raise ValueError("Every declared account needs a unique logical ID")
        if (
            len(self.client_accounts) != self.workload.clients
            or len(set(self.client_accounts.values())) != self.workload.clients
        ):
            raise ValueError("Active clients need different account IDs")
        if not set(self.client_accounts.values()) <= set(self.account_ids):
            raise ValueError("Client accounts must belong to the frozen enterprise inventory")
        if self.data.material_bytes_each > self.limits.material_upload_bytes:
            raise ValueError("Material target exceeds the frozen upload bound")
        return self


def load_manifest(path: str | Path, expected_sha256: str) -> CapacityManifest:
    """Verify the byte pin before parsing; never disclose rejected input text."""
    try:
        raw = Path(path).read_bytes()
        if len(expected_sha256) != 64 or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError

        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate manifest key")
                result[key] = value
            return result

        return CapacityManifest.model_validate(json.loads(raw, object_pairs_hook=unique_keys))
    except (OSError, ValueError, TypeError):
        raise ValueError(
            "Capacity manifest unavailable, invalid, or different from its pin"
        ) from None


class RunPlan(Contract):
    run_id: Name
    manifest_sha256: Sha
    started_at: datetime
    code_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    lock_sha256: Sha
    durable_lock_sha256: Sha
    schedule_sha256: Sha

    @model_validator(mode="after")
    def aware(self):
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("The frozen start time must be timezone-aware")
        return self


class ScheduledRequest(Contract):
    event_id: str
    sequence: Nonnegative
    client_id: Name
    account_id: Name
    operation: Operation
    scheduled_offset_ms: Nonnegative


def _operation_cycle(weights):
    scores = dict.fromkeys(OPERATIONS, 0)
    result = []
    for _ in range(100):
        for op in OPERATIONS:
            scores[op] += weights[op]
        chosen = max(OPERATIONS, key=lambda op: scores[op])
        scores[chosen] -= 100
        result.append(chosen)
    return result


def scheduled_requests(manifest: CapacityManifest, run_id: str) -> list[ScheduledRequest]:
    """The fixed schedule includes warmup; the trace cannot redefine the window."""
    manifest = CapacityManifest.model_validate(manifest)
    cycle, clients = (
        _operation_cycle(manifest.workload.operation_weights),
        sorted(manifest.client_accounts),
    )
    workload = manifest.workload
    return [
        ScheduledRequest(
            event_id=f"{run_id}-request-{i:06d}",
            sequence=i,
            client_id=clients[i % len(clients)],
            account_id=manifest.client_accounts[clients[i % len(clients)]],
            operation=cycle[i % 100],
            scheduled_offset_ms=i * (1000 // workload.requests_per_second),
        )
        for i in range(
            (workload.warmup_seconds + workload.steady_seconds) * workload.requests_per_second
        )
    ]


def build_run_plan(
    manifest: CapacityManifest,
    *,
    run_id: str,
    started_at: datetime,
    code_sha: str,
    lock_sha256: str,
    durable_lock_sha256: str,
) -> RunPlan:
    return RunPlan(
        run_id=run_id,
        manifest_sha256=canonical_sha256(manifest),
        started_at=started_at.astimezone(UTC) if started_at.tzinfo else started_at,
        code_sha=code_sha,
        lock_sha256=lock_sha256,
        durable_lock_sha256=durable_lock_sha256,
        schedule_sha256=canonical_sha256(
            [r.model_dump(mode="json") for r in scheduled_requests(manifest, run_id)]
        ),
    )


class ClientReceipt(Contract):
    client_id: Name
    account_id: Name
    session_sha256: Sha


class DataReceipt(Contract):
    enterprises: Nonnegative
    accounts: Nonnegative
    notice_versions: Nonnegative
    procurement_packages: Nonnegative
    notice_text_bytes_total: Nonnegative
    notice_text_bytes_min: Nonnegative
    notice_text_bytes_max: Nonnegative
    material_sizes_bytes: list[Positive]
    attachment_sizes_bytes: list[Positive]
    inventory_sha256: Sha


class RequestReceipt(ScheduledRequest):
    started_offset_ms: Nonnegative
    finished_offset_ms: Nonnegative | None
    outcome: Literal["response", "timeout", "exception", "missing_finish"]
    status_code: Annotated[int, Field(strict=True, ge=100, le=599)] | None
    content_verified: bool
    expected_items: Nonnegative | None
    returned_items: Nonnegative | None
    expected_total: Nonnegative | None
    reported_total: Nonnegative | None
    truncated: bool
    run_id: Name | None

    @model_validator(mode="after")
    def times(self):
        if self.finished_offset_ms is not None and self.finished_offset_ms < self.started_offset_ms:
            raise ValueError("Response finishes before arrival")
        if (self.outcome == "response") != (self.status_code is not None):
            raise ValueError("Only a response may carry an HTTP status")
        if (self.outcome == "missing_finish") != (self.finished_offset_ms is None):
            raise ValueError("Only missing_finish may omit the stop time")
        return self


class JobReceipt(Contract):
    run_id: Name
    submission_event_id: str
    accepted_offset_ms: Nonnegative
    compute_started_offset_ms: Nonnegative
    compute_finished_offset_ms: Nonnegative
    status: Literal[
        "waiting_review", "completed", "failed", "cancelled", "interrupted", "queued", "running"
    ]
    requested_model_delay_ms: Nonnegative
    observed_model_delay_ms: Nonnegative
    real_model_calls: Nonnegative
    real_source_calls: Nonnegative
    result_ids: list[Name]


class ResourceReceipt(Contract):
    offset_ms: Nonnegative
    rss_bytes: Nonnegative
    cpu_percent: Annotated[float, Field(ge=0)]
    disk_free_bytes: Nonnegative
    compute_active: Nonnegative
    active_jobs: Nonnegative
    queued_jobs: Nonnegative
    worker_alive: bool
    oom_events: Nonnegative
    deadlock_events: Nonnegative


class RecoveryReceipt(Contract):
    backup_consistent_through_at: datetime
    backup_completed_at: datetime
    loss_at: datetime
    health_ready_at: datetime
    authorization_verified_at: datetime
    business_verified_at: datetime
    post_backup_new_data: bool
    post_backup_account_disabled: bool
    post_backup_grant_revoked: bool
    post_backup_material_deleted: bool
    old_session_denied: bool
    old_material_link_denied: bool
    old_review_event_denied: bool
    snapshot_binding_verified: bool
    confirmed_ledger_preserved: bool
    unknown_reservations_preserved: bool
    current_authorization_confirmed: bool
    private_access_closed_if_unconfirmed: bool


class Issue(Contract):
    code: str
    record_ids: list[str] = Field(default_factory=list)


class CapacityReport(Contract):
    manifest_status: Status
    capacity_status: Status
    recovery_status: Status
    gates: dict[str, Status]
    metrics: dict[str, Any]
    issues: list[Issue]
    manifest_sha256: Sha
    plan_sha256: Sha
    model_evidence: Literal["offline_simulation_only"] = "offline_simulation_only"


def _combined(statuses):
    return "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"


def _p95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1] if values else None


def evaluate_capacity(manifest: CapacityManifest, plan: RunPlan, raw_trace: dict) -> CapacityReport:
    """Score every planned arrival, including missing, failed and malformed ones.

    ``plan`` is the caller's pre-execution pin, not a plan selected from the trace.
    This pure function validates measurements; it does not authenticate their
    origin, prove an actual hardware deployment, or issue a release decision.
    """
    manifest, plan = CapacityManifest.model_validate(manifest), RunPlan.model_validate(plan)
    gates: dict[str, Status] = {"manifest_complete": "PASS"}
    issues, metrics, invalid_indices = [], {}, {}

    def fail(gate, code, ids=()):
        gates[gate] = "FAIL"
        issues.append(Issue(code=code, record_ids=list(ids)))

    def parse_many(key, model):
        values = raw_trace.get(key)
        metrics[f"{key}_raw_count"] = len(values) if isinstance(values, list) else 0
        metrics[f"{key}_invalid_count"] = 0
        if not isinstance(values, list):
            fail("input_integrity", f"{key}_missing_or_invalid")
            return []
        parsed = []
        invalid_indices[key] = []
        for index, item in enumerate(values):
            try:
                parsed.append(model.model_validate(item))
            except (ValidationError, TypeError, ValueError):
                fail("input_integrity", f"{key}_record_invalid", [str(index)])
                invalid_indices[key].append(index)
        metrics[f"{key}_raw_count"] = len(values)
        metrics[f"{key}_invalid_count"] = len(values) - len(parsed)
        return parsed

    gates["declared_compute_target"] = "PASS"
    if manifest.limits.configured_compute_concurrency < manifest.limits.target_compute_concurrency:
        fail("declared_compute_target", "configured_compute_below_target")
    if plan.manifest_sha256 != canonical_sha256(manifest):
        fail("input_integrity", "plan_manifest_mismatch")
    schedule = scheduled_requests(manifest, plan.run_id)
    if plan.schedule_sha256 != canonical_sha256([r.model_dump(mode="json") for r in schedule]):
        fail("input_integrity", "plan_schedule_mismatch")
    if not isinstance(raw_trace, dict) or not raw_trace:
        gates["measurement"] = "BLOCKED"
        return CapacityReport(
            manifest_status="PASS",
            capacity_status=_combined(gates.values()),
            recovery_status="BLOCKED",
            gates=gates,
            metrics=metrics,
            issues=issues,
            manifest_sha256=canonical_sha256(manifest),
            plan_sha256=canonical_sha256(plan),
        )
    gates["input_integrity"] = gates.get("input_integrity", "PASS")
    gates["measurement"] = (
        "PASS" if raw_trace.get("measurement_kind") == "target_environment" else "BLOCKED"
    )
    if raw_trace.get("measurement_kind") not in ("target_environment", "local_engineering"):
        fail("input_integrity", "measurement_kind_missing_or_invalid")
    if raw_trace.get("run_id") != plan.run_id or raw_trace.get("plan_sha256") != canonical_sha256(
        plan
    ):
        fail("input_integrity", "trace_plan_mismatch")
    start = manifest.workload.warmup_seconds * 1000
    stop = start + manifest.workload.steady_seconds * 1000
    end = raw_trace.get("ended_offset_ms")
    if type(end) is not int or not stop <= end <= stop + manifest.workload.drain_seconds * 1000:
        fail("input_integrity", "incomplete_or_shifted_run_window")
        end = stop
    metrics.update(steady_start_ms=start, steady_stop_ms=stop, ended_offset_ms=end)

    clients = parse_many("clients", ClientReceipt)
    expected_clients = manifest.client_accounts
    if (
        len(clients) != len(expected_clients)
        or {c.client_id: c.account_id for c in clients} != expected_clients
        or len({c.session_sha256 for c in clients}) != len(expected_clients)
    ):
        fail("input_integrity", "active_client_or_session_inventory_mismatch")
    for name in ("dataset_before", "dataset_after"):
        try:
            data = DataReceipt.model_validate(raw_trace.get(name))
            target = manifest.data
            if (
                any(
                    getattr(data, k) != getattr(target, k)
                    for k in ("enterprises", "accounts", "notice_versions", "procurement_packages")
                )
                or data.notice_text_bytes_total
                != target.notice_versions * target.notice_text_bytes_each
                or data.notice_text_bytes_min != target.notice_text_bytes_each
                or data.notice_text_bytes_max != target.notice_text_bytes_each
                or data.material_sizes_bytes != [target.material_bytes_each] * target.materials
                or data.attachment_sizes_bytes
                != [target.attachment_bytes_each] * target.attachments
            ):
                fail("input_integrity", f"{name}_target_mismatch")
        except (ValidationError, TypeError, ValueError):
            fail("input_integrity", f"{name}_missing_or_invalid")
    if isinstance(raw_trace.get("dataset_before"), dict) and isinstance(
        raw_trace.get("dataset_after"), dict
    ):
        if raw_trace["dataset_before"].get("inventory_sha256") != raw_trace["dataset_after"].get(
            "inventory_sha256"
        ):
            fail("input_integrity", "fixed_dataset_changed_or_missing")
    try:
        hardware = Hardware.model_validate(raw_trace.get("hardware"))
        if any(
            getattr(hardware, k) < getattr(manifest.hardware_target, k)
            for k in ("logical_cpus", "memory_bytes", "disk_free_bytes")
        ):
            fail("input_integrity", "hardware_below_frozen_target")
    except (ValidationError, TypeError, ValueError):
        fail("input_integrity", "hardware_missing_or_invalid")

    requests = parse_many("requests", RequestReceipt)
    expected = {r.event_id: r for r in schedule}
    seen, steady, steady_missing = set(), [], []
    for request in requests:
        fixed = expected.get(request.event_id)
        if request.event_id in seen:
            fail("input_integrity", "duplicate_request_event", [request.event_id])
        seen.add(request.event_id)
        if fixed is None or any(
            getattr(request, k) != getattr(fixed, k) for k in ScheduledRequest.model_fields
        ):
            fail("input_integrity", "request_outside_frozen_schedule", [request.event_id])
        if (
            not 0
            <= request.started_offset_ms - request.scheduled_offset_ms
            <= manifest.workload.arrival_lateness_ms
        ):
            fail("input_integrity", "arrival_schedule_not_met", [request.event_id])
        if request.finished_offset_ms is None or request.finished_offset_ms > end:
            fail("input_integrity", "request_finish_missing_or_outside_run", [request.event_id])
        elif (
            request.finished_offset_ms - request.started_offset_ms
            > manifest.workload.http_timeout_ms
        ):
            fail("input_integrity", "request_exceeds_frozen_timeout", [request.event_id])
        if request.truncated or (
            request.outcome == "response"
            and (
                request.expected_items != request.returned_items
                or request.expected_total != request.reported_total
            )
        ):
            fail("data_integrity", "missing_or_truncated_response", [request.event_id])
        if (
            request.operation == "read_search"
            and request.outcome == "response"
            and any(
                getattr(request, k) is None
                for k in ("expected_items", "returned_items", "expected_total", "reported_total")
            )
        ):
            fail("data_integrity", "read_completeness_not_measured", [request.event_id])
        if start <= request.scheduled_offset_ms < stop:
            steady.append(request)
    missing = [r for r in schedule if r.event_id not in seen]
    if missing:
        fail("input_integrity", "scheduled_requests_missing", [r.event_id for r in missing])
        steady_missing = [r for r in missing if start <= r.scheduled_offset_ms < stop]
    client_counts = Counter(r.client_id for r in steady)
    ideal = (
        manifest.workload.steady_seconds
        * manifest.workload.requests_per_second
        / manifest.workload.clients
    )
    tolerance = manifest.workload.arrival_tolerance_percent / 100
    if any(
        client_counts[c] < manifest.workload.min_requests_per_client
        or not ideal * (1 - tolerance) <= client_counts[c] <= ideal * (1 + tolerance)
        for c in expected_clients
    ):
        fail("input_integrity", "per_client_load_not_met")
    arrivals = [r for r in requests if start <= r.started_offset_ms < stop]
    actual_rate = len(arrivals) / manifest.workload.steady_seconds
    if (
        not manifest.workload.requests_per_second * (1 - tolerance)
        <= actual_rate
        <= manifest.workload.requests_per_second * (1 + tolerance)
    ):
        fail("input_integrity", "arrival_rate_not_met")
    bucket_ms = manifest.workload.rate_bucket_seconds * 1000
    bins = [
        sum(left <= r.started_offset_ms < left + bucket_ms for r in requests)
        for left in range(start, stop, bucket_ms)
    ]
    bucket_ideal = manifest.workload.rate_bucket_seconds * manifest.workload.requests_per_second
    if any(not bucket_ideal * (1 - tolerance) <= n <= bucket_ideal * (1 + tolerance) for n in bins):
        fail("input_integrity", "arrival_rate_bucket_not_met")
    counts = Counter(r.operation for r in steady)
    expected_counts = Counter(
        r.operation for r in schedule if start <= r.scheduled_offset_ms < stop
    )
    if counts != expected_counts:
        fail("input_integrity", "operation_mix_not_met")
    expected_status = {"read_search": 200, "task_status": 200, "submit_task": 202, "review": 200}
    errors = sum(
        r.outcome != "response"
        or r.status_code != expected_status[r.operation]
        or not r.content_verified
        for r in steady
    )
    # A malformed planned record already counts as its missing scheduled slot.
    # Extra malformed attempts are additional errors, never a filtered subset.
    invalid_extra, covered_invalid = 0, set()
    for index in invalid_indices.get("requests", []):
        raw = raw_trace["requests"][index]
        event_id = raw.get("event_id") if isinstance(raw, dict) else None
        fixed = expected.get(event_id) if isinstance(event_id, str) else None
        if fixed is None or event_id in seen or event_id in covered_invalid:
            invalid_extra += 1
        elif start <= fixed.scheduled_offset_ms < stop:
            covered_invalid.add(event_id)
    errors += len(steady_missing) + invalid_extra
    denominator = len(steady) + len(steady_missing) + invalid_extra
    latencies = [
        r.finished_offset_ms - r.started_offset_ms
        for r in steady
        if r.finished_offset_ms is not None
    ]
    admissions = [
        r.finished_offset_ms - r.started_offset_ms
        for r in steady
        if r.operation == "submit_task" and r.finished_offset_ms is not None
    ]
    p95 = _p95(latencies) if len(latencies) == denominator else None
    admission_p95 = _p95(admissions)
    error_rate = errors / denominator if denominator else None
    metrics.update(
        expected_steady_requests=sum(expected_counts.values()),
        observed_steady_requests=len(steady),
        steady_error_denominator=denominator,
        steady_errors=errors,
        error_rate=error_rate,
        operation_counts=dict(counts),
        client_counts=dict(client_counts),
        actual_requests_per_second=actual_rate,
        rate_bucket_counts=bins,
        non_model_api_p95_ms=p95,
        admission_p95_ms=admission_p95,
    )
    gates["api_performance"] = "PASS"
    if (
        p95 is None
        or p95 > manifest.thresholds.non_model_api_p95_ms
        or admission_p95 is None
        or admission_p95 > manifest.thresholds.admission_p95_ms
    ):
        fail("api_performance", "api_latency_target_not_met")
    if error_rate is None or error_rate >= manifest.thresholds.error_rate_strictly_below:
        fail("api_performance", "api_error_rate_target_not_met")

    jobs = parse_many("jobs", JobReceipt)
    accepted = {
        r.event_id: r
        for r in requests
        if r.operation == "submit_task" and r.outcome == "response" and r.status_code == 202
    }
    job_ids, submissions, result_ids, boundaries = set(), set(), set(), []
    gates["model_simulation"] = "PASS" if accepted else "BLOCKED"
    queue_waits, model_waits, compute_times = [], [], []
    for job in jobs:
        request = accepted.get(job.submission_event_id)
        if job.run_id in job_ids or job.submission_event_id in submissions:
            fail("data_integrity", "duplicate_job_or_submission", [job.run_id])
        job_ids.add(job.run_id)
        submissions.add(job.submission_event_id)
        if (
            request is None
            or request.run_id != job.run_id
            or not request.started_offset_ms
            <= job.accepted_offset_ms
            <= (request.finished_offset_ms or -1)
        ):
            fail("data_integrity", "job_submission_binding_invalid", [job.run_id])
        if (
            not job.accepted_offset_ms
            <= job.compute_started_offset_ms
            <= job.compute_finished_offset_ms
            <= end
            or job.status not in {"waiting_review", "completed"}
        ):
            fail("data_integrity", "job_incomplete_or_invalid_times", [job.run_id])
        if len(job.result_ids) != 1 or any(r in result_ids for r in job.result_ids):
            fail("data_integrity", "missing_or_duplicate_job_result", [job.run_id])
        result_ids.update(job.result_ids)
        simulation = manifest.simulation
        if (
            job.requested_model_delay_ms != simulation.requested_delay_ms
            or not simulation.requested_delay_ms
            <= job.observed_model_delay_ms
            <= simulation.requested_delay_ms + simulation.observed_delay_tolerance_ms
            or job.observed_model_delay_ms
            > job.compute_finished_offset_ms - job.compute_started_offset_ms
            or job.real_model_calls
            or job.real_source_calls
        ):
            fail("model_simulation", "model_delay_or_offline_boundary_not_met", [job.run_id])
        queue_waits.append(job.compute_started_offset_ms - job.accepted_offset_ms)
        model_waits.append(job.observed_model_delay_ms)
        compute_times.append(job.compute_finished_offset_ms - job.compute_started_offset_ms)
        left, right = (
            max(start, job.compute_started_offset_ms),
            min(stop, job.compute_finished_offset_ms),
        )
        if left < right:
            boundaries.extend([(left, 1), (right, -1)])
    if set(accepted) != submissions:
        fail("data_integrity", "accepted_job_receipts_missing")
    active, peak, overlap, previous = 0, 0, 0, start
    for at, delta in sorted(boundaries):
        if active >= manifest.limits.target_compute_concurrency:
            overlap += at - previous
        active += delta
        peak = max(peak, active)
        previous = at
    gates["observed_compute_target"] = "PASS"
    if (
        peak < manifest.limits.target_compute_concurrency
        or overlap < manifest.thresholds.concurrent_compute_overlap_ms
    ):
        fail("observed_compute_target", "observed_compute_below_target")
    if peak > manifest.limits.configured_compute_concurrency:
        fail("data_integrity", "observed_compute_exceeds_declared_bound")
    metrics.update(
        accepted_jobs=len(accepted),
        observed_jobs=len(jobs),
        observed_compute_peak=peak,
        target_concurrency_overlap_ms=overlap,
        model_wait_p95_ms=_p95(model_waits),
        queue_wait_p95_ms=_p95(queue_waits),
        compute_p95_ms=_p95(compute_times),
    )

    resources = parse_many("resources", ResourceReceipt)
    positions = [r.offset_ms for r in resources]
    if (
        not positions
        or positions != sorted(set(positions))
        or positions[0] > manifest.workload.resource_interval_ms
        or positions[-1] < end
        or positions[-1] > end + manifest.workload.resource_interval_ms
        or any(
            b - a > manifest.workload.resource_max_gap_ms
            for a, b in zip(positions, positions[1:], strict=False)
        )
    ):
        fail("data_integrity", "resource_window_missing_or_gapped")
    if any(
        not r.worker_alive or r.oom_events or r.deadlock_events or r.disk_free_bytes == 0
        for r in resources
    ):
        fail("data_integrity", "worker_oom_deadlock_or_disk_failure")
    if any(
        r.compute_active > manifest.limits.configured_compute_concurrency
        or r.active_jobs > manifest.limits.max_active_jobs
        or r.queued_jobs > r.active_jobs
        for r in resources
    ):
        fail("data_integrity", "resource_capacity_bound_exceeded")
    steady_resources = [r for r in resources if start <= r.offset_ms < stop]
    # Both measurements describe the same compute intervals. One millisecond
    # accommodates integer clock rounding at a start/finish boundary, without
    # allowing warmup activity to substitute for steady-window concurrency.
    inconsistent_samples = []
    for sample in resources:
        possible = {
            sum(j.compute_started_offset_ms <= at < j.compute_finished_offset_ms for j in jobs)
            for at in (sample.offset_ms - 1, sample.offset_ms, sample.offset_ms + 1)
        }
        if sample.compute_active not in possible:
            inconsistent_samples.append(str(sample.offset_ms))
    if inconsistent_samples:
        fail("data_integrity", "resource_job_compute_mismatch", inconsistent_samples)
    metrics.update(
        rss_peak_bytes=max((r.rss_bytes for r in resources), default=None),
        cpu_peak_percent=max((r.cpu_percent for r in resources), default=None),
        disk_free_min_bytes=min((r.disk_free_bytes for r in resources), default=None),
        queue_peak=max((r.queued_jobs for r in resources), default=None),
        resource_compute_peak=max((r.compute_active for r in steady_resources), default=None),
    )
    if (
        max((r.compute_active for r in steady_resources), default=0)
        < manifest.limits.target_compute_concurrency
    ):
        fail("observed_compute_target", "sampled_compute_below_target")
    gates.setdefault("data_integrity", "PASS")
    recovery_status, recovery_metrics, recovery_issues = _recovery(
        manifest, raw_trace.get("recovery")
    )
    metrics.update(recovery_metrics)
    issues.extend(recovery_issues)
    return CapacityReport(
        manifest_status="PASS",
        capacity_status=_combined(gates.values()),
        recovery_status=recovery_status,
        gates=gates,
        metrics=metrics,
        issues=issues,
        manifest_sha256=canonical_sha256(manifest),
        plan_sha256=canonical_sha256(plan),
    )


def _recovery(manifest, raw):
    if raw is None:
        return "BLOCKED", {}, []
    try:
        receipt = RecoveryReceipt.model_validate_json(json.dumps(raw, allow_nan=False))
        dates = [
            getattr(receipt, key) for key in RecoveryReceipt.model_fields if key.endswith("_at")
        ]
        if any(d.tzinfo is None or d.utcoffset() is None for d in dates):
            raise ValueError
        rpo = (receipt.loss_at - receipt.backup_consistent_through_at).total_seconds()
        ready = max(
            receipt.health_ready_at, receipt.authorization_verified_at, receipt.business_verified_at
        )
        rto = (ready - receipt.loss_at).total_seconds()
        valid = (
            receipt.backup_consistent_through_at <= receipt.backup_completed_at <= receipt.loss_at
            and min(
                receipt.health_ready_at,
                receipt.authorization_verified_at,
                receipt.business_verified_at,
            )
            >= receipt.loss_at
            and 0 <= rpo <= manifest.thresholds.rpo_seconds
            and 0 <= rto <= manifest.thresholds.rto_seconds
        )
        checks = [
            getattr(receipt, key)
            for key in RecoveryReceipt.model_fields
            if not key.endswith("_at")
            and key
            not in {"current_authorization_confirmed", "private_access_closed_if_unconfirmed"}
        ]
        valid = valid and all(checks)
        if (
            not receipt.current_authorization_confirmed
            and not receipt.private_access_closed_if_unconfirmed
        ):
            valid = False
        status = (
            "FAIL"
            if not valid
            else "PASS"
            if receipt.current_authorization_confirmed
            else "BLOCKED"
        )
        return (
            status,
            {"rpo_seconds": rpo, "rto_seconds": rto},
            (
                []
                if status == "PASS"
                else [
                    Issue(
                        code="recovery_target_not_met"
                        if status == "FAIL"
                        else "recovery_authorization_unconfirmed"
                    )
                ]
            ),
        )
    except (ValidationError, TypeError, ValueError):
        return "FAIL", {}, [Issue(code="recovery_receipt_invalid")]
