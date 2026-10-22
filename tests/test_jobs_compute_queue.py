"""Real DBOS/SQLite admission, including cancelled but still active model calls."""

import asyncio
import json
import time
from collections import Counter
from threading import Event, Lock

import pytest
from bidops.jobs.config import JobsSettings
from bidops.jobs.model import ACTIVE_MODEL
from bidops.jobs.runtime import CALCULATION_QUEUE, QUEUE, JobsService
from dbos import DBOS
from pydantic import ValidationError
from pydantic_ai.models.function import FunctionModel
from test_company_access import ALICE
from test_jobs_runtime import wait_for
from test_qualification_service import scripted_response
from test_qualification_service import service as service
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources


def until(predicate, timeout=12, diagnostic=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    detail = json.dumps(diagnostic(), sort_keys=True) if diagnostic else "no diagnostic"
    pytest.fail(f"Native queue did not reach the expected observable state: {detail}")


@pytest.fixture
def two_compute(service, settings):
    screening, notice, profile, _, _ = service
    calls, released, lock = Counter(), {}, Lock()
    active, observed, first_started_at = set(), [], {}

    async def model(messages, info):
        run_id = ACTIVE_MODEL.get().run_id
        assert DBOS.workflow_id == run_id + "-calculation"
        with lock:
            calls[run_id] += 1
            first = calls[run_id] == 1
            if first:
                first_started_at[run_id] = time.monotonic()
            gate = released.setdefault(run_id, Event())
            active.add(run_id)
            observed.append(len(active))
        try:
            if first:
                while not gate.is_set():
                    await asyncio.sleep(0.01)
            assert ACTIVE_MODEL.get().run_id == run_id
            assert DBOS.workflow_id == run_id + "-calculation"
            return scripted_response(messages, info)
        finally:
            with lock:
                active.remove(run_id)

    screening.model_override = FunctionModel(model, model_name="offline-two-compute")
    runtime = JobsService(
        screening,
        settings.data_dir,
        JobsSettings(_env_file=None, enabled=True, compute_concurrency=2),
    )
    try:
        runtime.start()
        yield (
            runtime,
            {"profile_revision": profile, "project_id": notice.project_id},
            {
                "calls": calls,
                "released": released,
                "observed": observed,
                "active": active,
                "first_started_at": first_started_at,
            },
        )
    finally:
        for gate in released.values():
            gate.set()
        runtime.close()
        DBOS.destroy(destroy_registry=True)


def calculation_snapshot(case, run_ids):
    runtime, _, observation = case
    rows = []
    for index, run_id in enumerate(run_ids):
        record = runtime.repository.get_system(run_id)
        native = DBOS.get_workflow_status(run_id + "-calculation")
        rows.append(
            {
                "task": index + 1,
                "status": record["status"],
                "progress": record["progress"],
                "native_status": native.status if native else None,
                "model_calls": observation["calls"][run_id],
            }
        )
    return rows


def wait_for_calculations(case, run_ids, *, first=False):
    """Wait for nine-call business work before starting a queue-admission wait."""
    runtime, _, _ = case

    def completed():
        rows = calculation_snapshot(case, run_ids)
        if any(
            row["status"] in {"failed", "interrupted", "timed_out", "cancelled"}
            or row["native_status"] in {"ERROR", "CANCELLED"}
            for row in rows
        ):
            pytest.fail(json.dumps(rows, sort_keys=True))
        successes = [row["native_status"] == "SUCCESS" for row in rows]
        return any(successes) if first else all(successes)

    # The fixture still executes one extraction and eight assessments through
    # real durable steps. Its configured business deadline is not the short
    # deadline used to observe admission after a calculation has freed a slot.
    until(
        completed,
        timeout=runtime.screening.settings.max_seconds,
        diagnostic=lambda: calculation_snapshot(case, run_ids),
    )
    return time.monotonic()


def wait_for_next_calculation(case, ids, queued, record_property):
    runtime, _, observation = case
    release_observed = wait_for_calculations(
        case, [run_id for run_id in ids if run_id != queued], first=True
    )
    until(
        lambda: queued in observation["calls"],
        diagnostic=lambda: calculation_snapshot(case, ids),
    )
    # The new call can precede our poll observing its predecessor's SUCCESS.
    # Keep the signed observation delta rather than inventing a dequeue time.
    record_property(
        "next_call_minus_observed_predecessor_success_seconds",
        observation["first_started_at"][queued] - release_observed,
    )


def submit_three(case):
    runtime, payload, observation = case
    ids = [runtime.submit(ALICE, payload, f"bounded-{index}")["run_id"] for index in range(3)]
    until(lambda: len(observation["calls"]) == 2)
    queued = next(run_id for run_id in ids if run_id not in observation["calls"])
    until(lambda: DBOS.get_workflow_status(queued + "-calculation"))
    child = DBOS.get_workflow_status(queued + "-calculation")
    assert child.status == "ENQUEUED" and child.queue_name == CALCULATION_QUEUE
    assert runtime.repository.get_system(queued)["status"] == "queued"
    assert max(observation["observed"]) == 2
    return ids, queued


def test_native_calculation_queue_bounds_two_and_review_wait_frees_computation(
    two_compute, record_property
):
    runtime, _, observation = two_compute
    ids, queued = submit_three(two_compute)
    for run_id in ids:
        if run_id != queued:
            observation["released"][run_id].set()
    wait_for_next_calculation(two_compute, ids, queued, record_property)
    observation["released"][queued].set()
    wait_for_calculations(two_compute, ids)
    saved = [wait_for(runtime, run_id, "waiting_review") for run_id in ids]
    assert len({result["result_id"] for result in saved}) == 3
    assert all(result["usage"]["model_calls"] == 9 for result in saved)
    assert all(DBOS.get_workflow_status(run_id).queue_name == QUEUE for run_id in ids)
    assert all(DBOS.get_workflow_status(run_id).status == "PENDING" for run_id in ids)
    assert all(
        DBOS.get_workflow_status(run_id + "-calculation").status == "SUCCESS" for run_id in ids
    )
    assert max(observation["observed"]) == 2 and not observation["active"]


def test_cancelled_inflight_call_holds_physical_slot_until_it_returns(two_compute):
    runtime, _, observation = two_compute
    ids, queued = submit_three(two_compute)
    cancelled = next(run_id for run_id in ids if run_id != queued)
    record = runtime.read(ALICE, cancelled)
    runtime.cancel(ALICE, cancelled, record["state_version"])
    # Cancellation is persisted while the provider coroutine is still active.
    # The SDK may retain its local slot despite clearing its persisted queue;
    # either way, the application must not admit a third actual calculation.
    until(lambda: DBOS.get_workflow_status(cancelled + "-calculation").status == "CANCELLED")
    time.sleep(0.15)
    assert DBOS.get_workflow_status(queued + "-calculation").status in {"ENQUEUED", "PENDING"}
    assert queued not in observation["calls"]
    assert cancelled in observation["active"]
    observation["released"][cancelled].set()
    until(lambda: queued in observation["calls"])
    for gate in observation["released"].values():
        gate.set()
    remaining = [run_id for run_id in ids if run_id != cancelled]
    wait_for_calculations(two_compute, remaining)
    for run_id in ids:
        if run_id != cancelled:
            waiting = wait_for(runtime, run_id, "waiting_review")
            assert waiting["usage"]["model_calls"] == 9
    final = runtime.read(ALICE, cancelled)
    assert final["status"] == "cancelled" and final["result_id"] is None
    assert final["usage"]["model_calls"] == 1
    assert max(observation["observed"]) == 2


def test_authorized_resume_returns_child_to_bounded_queue(two_compute, record_property):
    runtime, _, observation = two_compute
    ids, queued = submit_three(two_compute)
    # Simulate a recorded backend interruption, distinct from a terminal user
    # cancellation. Public resume must preserve the native calculation queue.
    DBOS.cancel_workflow(queued, cancel_children=True)
    runtime._interrupt(queued, "job_backend_unavailable")
    record = runtime.read(ALICE, queued)
    runtime.resume(ALICE, queued, record["state_version"])
    child = until(lambda: DBOS.get_workflow_status(queued + "-calculation"))
    assert child.queue_name == CALCULATION_QUEUE and child.status == "ENQUEUED"
    assert DBOS.get_workflow_status(queued).queue_name == QUEUE
    time.sleep(0.15)
    assert queued not in observation["calls"]
    for gate in observation["released"].values():
        gate.set()
    wait_for_next_calculation(two_compute, ids, queued, record_property)
    observation["released"][queued].set()
    wait_for_calculations(two_compute, ids)
    for run_id in ids:
        waiting = wait_for(runtime, run_id, "waiting_review")
        assert waiting["usage"]["model_calls"] == 9
    assert max(observation["observed"]) == 2


@pytest.mark.parametrize("value", [0, 3, 100])
def test_compute_setting_does_not_enable_unvalidated_parallelism(value):
    assert JobsSettings(_env_file=None).compute_concurrency == 1
    with pytest.raises(ValidationError):
        JobsSettings(_env_file=None, compute_concurrency=value)
