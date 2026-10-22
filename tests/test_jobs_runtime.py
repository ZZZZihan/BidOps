"""Real DBOS workers and SQLite with offline Pydantic AI model responses."""

import asyncio
import time
from threading import Event

import pytest
from bidops.company.authorization import CompanyError
from bidops.enterprise.backup import backup, restore
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import JobsService
from bidops.qualification.service import ScreeningService
from dbos import DBOS
from pydantic_ai.models.function import FunctionModel
from test_company_access import ADMIN, ALICE, BOB
from test_company_access import payload as profile_payload
from test_jobs_backup import reopened
from test_jobs_repository import review
from test_qualification_service import scripted_response
from test_qualification_service import service as service
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources


def wait_for(runtime, run_id, status, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = runtime.repository.get_system(run_id)
        if row["status"] == status:
            return runtime.read(ALICE, run_id)
        if row["status"] in {"failed", "interrupted", "timed_out"} and status not in {
            "failed",
            "interrupted",
            "timed_out",
        }:
            pytest.fail(str(row))
        time.sleep(0.05)
    pytest.fail(str(runtime.repository.get_system(run_id)))


@pytest.fixture
def durable(service, settings):
    screening, notice, profile, material, fact = service
    runtime = JobsService(screening, settings.data_dir, JobsSettings(enabled=True))
    try:
        runtime.start()
        yield runtime, {"profile_revision": profile, "project_id": notice.project_id}
    finally:
        runtime.close()
        DBOS.destroy(destroy_registry=True)


def test_actual_queue_model_steps_result_and_native_review_wait(durable):
    runtime, payload = durable
    first = runtime.submit(ALICE, payload, "once")
    replay = runtime.submit(ALICE, payload, "once")
    assert first["run_id"] == replay["run_id"] and not replay["created"]
    waiting = wait_for(runtime, first["run_id"], "waiting_review")
    assert waiting["result_id"] != waiting["run_id"]
    assert len(waiting["result"]["inventory"]["conditions"]) == 4
    # The project has two packages: one extraction plus four checks per package.
    assert waiting["usage"]["model_calls"] == 9
    assert waiting["result"]["human_review_state"] == "unreviewed"
    assert DBOS.get_workflow_status(first["run_id"] + "-calculation").status == "SUCCESS"
    event = review(waiting)
    runtime.review_fixture(first["run_id"], event, waiting["state_version"])
    finished = wait_for(runtime, first["run_id"], "completed")
    assert finished["usage"]["model_calls"] == 9
    # A fixture only drives execution state; it does not forge human approval.
    assert finished["result"]["human_review_state"] == "unreviewed"


def test_waiting_review_releases_calculation_slot_and_cancel_is_terminal(durable):
    runtime, payload = durable
    first = runtime.submit(ALICE, payload, "one")
    waiting = wait_for(runtime, first["run_id"], "waiting_review")
    second = runtime.submit(ALICE, payload, "two")
    wait_for(runtime, second["run_id"], "waiting_review")
    cancelled = runtime.cancel(ALICE, first["run_id"], waiting["state_version"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["allowed_actions"] == []
    with pytest.raises(CompanyError, match="job_resume_forbidden"):
        runtime.resume(ALICE, first["run_id"], cancelled["state_version"])


@pytest.mark.parametrize(
    "decision,expected", [("confirm", "completed"), ("request_materials", "waiting_review")]
)
def test_revoked_review_wait_replays_native_checkpoints_after_authorized_resume(
    durable, decision, expected
):
    runtime, payload = durable
    first = runtime.submit(ALICE, payload, "pause-review")
    waiting = wait_for(runtime, first["run_id"], "waiting_review")
    event = review(waiting)
    event["command"]["decision"] = decision
    saved = runtime.repository.record_review_fixture(
        first["run_id"], event, waiting["state_version"]
    )
    material = waiting["manifest"]["material_revisions"][0]
    auth = runtime.repository.auth
    auth.grant(ALICE, material["object_id"], BOB.owner_id, ["read", "grant"])
    auth.revoke(ALICE, material["object_id"], ALICE.owner_id)
    DBOS.send(first["run_id"], saved["event_id"], topic="review")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        interrupted = runtime.repository.get_system(first["run_id"])
        if interrupted["status"] == "interrupted":
            break
        time.sleep(0.025)
    assert interrupted["status"] == "interrupted"
    assert DBOS.get_workflow_status(first["run_id"]).status == "CANCELLED"
    with pytest.raises(CompanyError):
        runtime.resume(ALICE, first["run_id"], interrupted["state_version"])
    auth.grant(BOB, material["object_id"], ALICE.owner_id, ["read", "write", "grant", "delete"])
    assert "resume" in runtime.read(ALICE, first["run_id"])["allowed_actions"]
    runtime.resume(ALICE, first["run_id"], interrupted["state_version"])
    result = wait_for(runtime, first["run_id"], expected)
    assert result["usage"]["model_calls"] == 9
    assert result["result_id"] == waiting["result_id"]
    assert result["result"]["human_review_state"] == "unreviewed"
    with pytest.raises(CompanyError, match="job_resume_forbidden"):
        runtime.resume(ALICE, first["run_id"], interrupted["state_version"])


def test_cancelled_queue_cannot_be_revived_by_late_enqueue(durable, monkeypatch):
    runtime, payload = durable
    enqueue = runtime.enqueue
    monkeypatch.setattr(runtime, "enqueue", lambda *_: None)
    queued = runtime.submit(ALICE, payload, "queued-cancel")
    assert queued["status"] == "queued"
    cancelled = runtime.cancel(ALICE, queued["run_id"], queued["state_version"])
    enqueue(queued["run_id"])
    time.sleep(0.25)
    current = runtime.read(ALICE, queued["run_id"])
    assert current["status"] == cancelled["status"] == "cancelled"
    assert current["usage"]["model_calls"] == 0
    assert runtime.repository.db.rows("SELECT * FROM qualification_results") == []


def test_cancel_during_provider_call_stops_following_steps_and_retains_receipt(service, settings):
    screening, notice, profile, _, _ = service
    entered, release, returned = Event(), Event(), Event()

    async def model(messages, info):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        returned.set()
        return scripted_response(messages, info)

    screening.model_override = FunctionModel(model, model_name="offline-unit")
    runtime = JobsService(screening, settings.data_dir, JobsSettings(enabled=True))
    try:
        runtime.start()
        first = runtime.submit(
            ALICE, {"profile_revision": profile, "project_id": notice.project_id}, "in-flight"
        )
        assert entered.wait(timeout=10)
        current = runtime.read(ALICE, first["run_id"])
        cancelled = runtime.cancel(ALICE, first["run_id"], current["state_version"])
        release.set()
        assert returned.wait(timeout=5)
        time.sleep(0.15)
        final = runtime.read(ALICE, first["run_id"])
        assert final["status"] == cancelled["status"] == "cancelled"
        assert final["usage"]["model_calls"] == 1
        assert final["usage"]["confirmed_calls"] + final["usage"]["unknown_calls"] == 1
        assert runtime.repository.db.rows("SELECT * FROM qualification_results") == []
        with pytest.raises(CompanyError, match="job_resume_forbidden"):
            runtime.resume(ALICE, first["run_id"], final["state_version"])
    finally:
        release.set()
        runtime.close()


def test_read_history_after_restart_with_provider_and_live_calls_disabled(durable, settings):
    runtime, payload = durable
    submitted = runtime.submit(ALICE, payload, "reader-only")
    waiting = wait_for(runtime, submitted["run_id"], "waiting_review")
    runtime.close()
    runtime.screening.model_override = None
    runtime.screening.settings.api_key = None
    runtime.screening.settings.live_calls_enabled = False
    reader = JobsService(runtime.screening, settings.data_dir, JobsSettings(enabled=True))
    reader.start()
    try:
        assert not reader.started and not reader.status()["configured"]
        again = reader.read(ALICE, submitted["run_id"])
        assert again["usage"] == waiting["usage"] and again["allowed_actions"] == []
        assert reader.list(ALICE)["total"] == 1
        with pytest.raises(CompanyError, match="job_backend_unavailable"):
            reader.submit(ALICE, payload, "no-provider")
    finally:
        reader.close()


def test_updated_profile_requires_new_task_revision_and_preserves_stale_result(durable):
    runtime, payload = durable
    submitted = runtime.submit(ALICE, payload, "before-profile-update")
    waiting = wait_for(runtime, submitted["run_id"], "waiting_review")
    old = payload["profile_revision"]
    updated = runtime.screening.sources.company.repo.put(
        ALICE,
        profile_payload(display_name="Changed synthetic company"),
        object_id=old["object_id"],
        expected_version=old["version_id"],
    )
    with pytest.raises(CompanyError, match="job_inputs_changed"):
        runtime.review_fixture(submitted["run_id"], review(waiting), waiting["state_version"])
    stale = runtime.read(ALICE, submitted["run_id"])
    assert stale["source_state"]["current"] is False
    assert stale["result"]["result_revision"] == waiting["result"]["result_revision"]
    later = runtime.submit(
        ALICE,
        {
            **payload,
            "profile_revision": {k: updated[k] for k in ("object_id", "version_id", "sha256")},
        },
        "new-profile",
    )
    assert later["run_id"] != submitted["run_id"]
    assert later["versions"]["result_revision"] != waiting["versions"]["result_revision"]
    runtime.cancel(ALICE, later["run_id"], runtime.read(ALICE, later["run_id"])["state_version"])


@pytest.mark.parametrize(
    "mode,status,error",
    [
        ("request_limit", "failed", "screening_request_limit"),
        ("timeout", "timed_out", "screening_timeout"),
    ],
)
def test_bounded_failure_retains_partial_result(service, settings, mode, status, error):
    screening, notice, profile, _, _ = service
    if mode == "request_limit":
        screening.settings.max_requests = 1
    else:
        screening.settings.max_seconds = 0.1

        async def slow(messages, info):
            await asyncio.sleep(1)
            return scripted_response(messages, info)

        screening.model_override = FunctionModel(slow, model_name="offline-unit")
    runtime = JobsService(screening, settings.data_dir, JobsSettings(enabled=True))
    try:
        runtime.start()
        first = runtime.submit(
            ALICE, {"profile_revision": profile, "project_id": notice.project_id}, mode
        )
        final = wait_for(runtime, first["run_id"], status)
        assert final["error_code"] == error and final["result"]["state"] == status
        assert final["result"]["recommendation"] == "not_evaluated"
        assert final["result"]["human_review_state"] == "unreviewed"
        assert final["allowed_actions"] == []
    finally:
        runtime.close()


def test_cancel_between_native_model_steps(durable, monkeypatch):
    runtime, payload = durable
    entered, release = Event(), Event()
    original = runtime.progress_step

    async def progress(run_id, phase, completed, total):
        result = await original(run_id, phase, completed, total)
        if phase == "assessment" and completed == 0:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        return result

    monkeypatch.setattr(runtime, "progress_step", progress)
    try:
        first = runtime.submit(ALICE, payload, "between-steps")
        assert entered.wait(timeout=10)
        current = runtime.read(ALICE, first["run_id"])
        assert current["usage"]["model_calls"] == 1
        runtime.cancel(ALICE, first["run_id"], current["state_version"])
        release.set()
        time.sleep(0.2)
        current = runtime.read(ALICE, first["run_id"])
        assert current["status"] == "cancelled" and current["usage"]["model_calls"] == 1
        assert runtime.repository.db.rows("SELECT * FROM qualification_results") == []
    finally:
        release.set()


def test_unavailable_cancel_keeps_request_and_exposes_explicit_retry(durable, monkeypatch):
    runtime, payload = durable
    first = runtime.submit(ALICE, payload, "cancel-unavailable")
    waiting = wait_for(runtime, first["run_id"], "waiting_review")

    def unavailable(*args, **kwargs):
        raise OSError("SYNTHETIC PRIVATE backend failure")

    with monkeypatch.context() as patch:
        patch.setattr(DBOS, "cancel_workflow", unavailable)
        with pytest.raises(CompanyError, match="job_backend_unavailable"):
            runtime.cancel(ALICE, first["run_id"], waiting["state_version"])
    current = runtime.read(ALICE, first["run_id"])
    assert current["status"] == "cancel_requested" and current["allowed_actions"] == ["cancel"]
    with pytest.raises(CompanyError, match="job_not_executable"):
        runtime.require_execution(first["run_id"])
    cancelled = runtime.cancel(ALICE, first["run_id"], current["state_version"])
    assert cancelled["status"] == "cancelled" and cancelled["allowed_actions"] == []
    assert cancelled["usage"]["model_calls"] == 9


def test_native_resume_unavailable_keeps_interrupted_and_allows_explicit_retry(
    durable, monkeypatch
):
    runtime, payload = durable
    job = runtime.submit(ALICE, payload, "resume-unavailable")
    wait_for(runtime, job["run_id"], "waiting_review")
    DBOS.cancel_workflow(job["run_id"], cancel_children=True)
    runtime._interrupt(job["run_id"], "synthetic_pause")
    paused = runtime.read(ALICE, job["run_id"])

    def unavailable(*args, **kwargs):
        raise OSError("SYNTHETIC PRIVATE SDK ERROR must not reach the response")

    with monkeypatch.context() as patch:
        patch.setattr(DBOS, "resume_workflow", unavailable)
        with pytest.raises(CompanyError, match="job_backend_unavailable"):
            runtime.resume(ALICE, job["run_id"], paused["state_version"])
    current = runtime.read(ALICE, job["run_id"])
    assert current["status"] == "interrupted" and current["usage"]["model_calls"] == 9
    runtime.resume(ALICE, job["run_id"], current["state_version"])
    assert wait_for(runtime, job["run_id"], "waiting_review")["usage"]["model_calls"] == 9


def test_restored_empty_enqueue_boundary_requires_reauthorization_then_one_native_task(
    durable, monkeypatch
):
    runtime, payload = durable
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "enqueue", lambda *_: None)
        queued = runtime.submit(ALICE, payload, "empty-boundary")
    runtime.repository.quarantine_all()
    quarantined = runtime.repository.get_system(queued["run_id"])
    with pytest.raises(CompanyError):
        runtime.resume(ALICE, queued["run_id"], quarantined["state_version"])
    authorized = runtime.repository.reauthorize(
        ALICE, queued["run_id"], quarantined["state_version"]
    )
    assert authorized["status"] == "interrupted"
    runtime.resume(ALICE, queued["run_id"], authorized["state_version"])
    waiting = wait_for(runtime, queued["run_id"], "waiting_review")
    assert waiting["usage"]["model_calls"] == 9
    assert len(runtime.repository.db.rows("SELECT * FROM qualification_results")) == 1


def test_real_waiting_workflow_backup_restore_reauthorize_then_native_continue(
    durable, settings, tmp_path
):
    runtime, payload = durable
    first = runtime.submit(ALICE, payload, "real-restore")
    waiting = wait_for(runtime, first["run_id"], "waiting_review")
    runtime.close()
    bundle, destination = tmp_path / "bundle", tmp_path / "recovered"
    backup(settings, bundle)
    report = restore(bundle, destination)
    assert report["quarantined_jobs"] == 1 and report["budget_review_required"]
    db, repository = reopened(destination)
    screening = ScreeningService(
        repository.sources.company,
        repository.sources.notices,
        runtime.screening.settings.model_copy(),
        FunctionModel(scripted_response, model_name="offline-unit"),
    )
    recovered = JobsService(screening, destination, JobsSettings(enabled=True))
    try:
        recovered.start()
        with pytest.raises(CompanyError):
            recovered.read(ALICE, first["run_id"])
        db.execute("UPDATE enterprise_subjects SET fresh_until=:now", {"now": time.time() + 300})
        with pytest.raises(CompanyError):
            recovered.read(ALICE, first["run_id"])
        for ref in [
            waiting["manifest"]["profile_revision"],
            *waiting["manifest"]["material_revisions"],
        ]:
            recovered.repository.auth.reauthorize(
                ADMIN, ref["object_id"], ALICE.owner_id, ["read", "write", "grant", "delete"]
            )
        blocked = recovered.repository.get_system(first["run_id"])
        authorized = recovered.repository.reauthorize(
            ALICE, first["run_id"], blocked["state_version"]
        )
        assert authorized["status"] == "interrupted"
        recovered.resume(ALICE, first["run_id"], authorized["state_version"])
        resumed = wait_for(recovered, first["run_id"], "waiting_review")
        assert resumed["result_id"] == waiting["result_id"] and resumed["usage"]["model_calls"] == 9
        recovered.review_fixture(first["run_id"], review(resumed), resumed["state_version"])
        finished = wait_for(recovered, first["run_id"], "completed")
        assert finished["result"]["human_review_state"] == "unreviewed"
        assert finished["usage"]["model_calls"] == 9
    finally:
        recovered.close()
        db.engine.dispose()
