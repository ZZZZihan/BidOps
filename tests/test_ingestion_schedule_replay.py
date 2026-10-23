"""Native schedule/recovery admission races with actual DBOS and SQLite.

The only barrier pauses a schedule after its committed admission returns a
queued snapshot.  Recovery uses the real queue and performs the real page;
neither the business result nor a native workflow result is manufactured.
"""

import json
import time
from threading import Event, Lock

import httpx
import pytest
from bidops.company.authorization import CompanyError
from bidops.ingestion.runtime import SCHEDULE_WORKFLOW
from dbos import DBOS
from test_ingestion_api import BODY
from test_ingestion_api import ingestion_http as ingestion_http
from test_ingestion_runtime import owner, schedule_payload, transport, wait


def native_terminal(handle):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = DBOS.get_workflow_status(handle.get_workflow_id())
        if status.status in {"SUCCESS", "ERROR", "CANCELLED"}:
            return status
        time.sleep(0.02)
    pytest.fail(f"native workflow did not terminate: {status.status}")


def pause_queued_schedule(monkeypatch, service):
    entered, release, claimed = Event(), Event(), Lock()
    captured = {}
    original = service.repository.get_system

    def get_system(run_id, conn=None):
        record = original(run_id, conn)
        native_id = DBOS.workflow_id
        if conn is None and record["status"] == "queued" and native_id:
            native = DBOS.get_workflow_status(native_id)
            # A persisted tick distinguishes the post-admission read from any
            # get_system used while admission itself is still committing.
            tick = service.repository.db.rows(
                "SELECT run_id FROM ingestion_schedule_ticks WHERE native_id=:id",
                {"id": native_id},
            )
            if native.name == SCHEDULE_WORKFLOW and tick:
                with claimed:
                    first = not entered.is_set()
                    if first:
                        captured.update(record=record, native_id=native_id)
                        entered.set()
                if first:
                    assert release.wait(15), "test did not release the queued snapshot"
        return record

    monkeypatch.setattr(service.repository, "get_system", get_system)
    return entered, release, captured


def business_snapshot(service):
    return {
        table: service.repository.db.rows(f"SELECT * FROM {table} ORDER BY rowid")
        for table in (
            "ingestion_runs",
            "ingestion_run_state",
            "ingestion_schedule_ticks",
            "ingestion_pages",
            "ingestion_page_attempts",
            "ingestion_source_state",
            "snapshots",
            "nd_versions",
            "nd_observations",
        )
    }


def test_recovery_completes_between_schedule_snapshot_and_execution_guard(
    ingestion_http, monkeypatch, record_property
):
    case, calls = ingestion_http, []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(
            200, headers={"Content-Type": "text/plain; charset=utf-8"}, content=BODY.encode()
        )

    service = transport(case, respond)
    context = owner(case)
    service.schedule(context, schedule_payload("schedule-recovery-toctou"))
    generation = service.repository.db.rows("SELECT * FROM ingestion_schedules")[0]
    entered, release, captured = pause_queued_schedule(monkeypatch, service)
    handle = DBOS.trigger_schedule(generation["native_name"])
    try:
        assert entered.wait(15), "schedule never reached its post-admission queued read"
        queued = captured["record"]
        assert queued["status"] == "queued"
        assert service.repository.db.rows("SELECT * FROM ingestion_pages") == []
        # This is the same native recovery sweep called after engine launch.
        service.runtime.after_launch()
        record = wait(service, queued["run_id"])
        assert record["status"] == "completed"
        source_handle = DBOS.retrieve_workflow(record["native_id"])
        assert native_terminal(source_handle).status == "SUCCESS"
    finally:
        release.set()

    native = native_terminal(handle)
    # Old code deterministically reaches ERROR(ingestion_run_not_active) here.
    assert native.status == "SUCCESS", native.error
    result = handle.get_result()
    assert result == {"run_id": record["run_id"], "error_code": None}
    assert len(calls) == 1
    assert record["counts"] == {
        "pages": 1,
        "items": 1,
        "imported": 1,
        "unchanged": 0,
        "failed": 0,
        "attachments": 0,
        "attachment_failed": 0,
    }
    db = service.repository.db
    assert len(db.rows("SELECT * FROM ingestion_runs")) == 1
    assert len(db.rows("SELECT * FROM ingestion_schedule_ticks")) == 1
    assert len(db.rows("SELECT * FROM ingestion_pages")) == 1
    source = db.rows("SELECT * FROM ingestion_source_state WHERE source_id='unit-allowed'")[0]
    assert source["cursor_version"] == 1 and json.loads(source["cursor_json"]) is None
    assert source["last_success"] is not None and source["active_run_id"] is None
    before = business_snapshot(service)
    assert (
        service.runtime.schedules.admit(generation["native_name"], handle.get_workflow_id())
        == result
    )
    service.runtime.after_launch()
    assert service.read(context, record["run_id"])["status"] == "completed"
    assert DBOS.retrieve_workflow(handle.get_workflow_id()).get_result() == result
    assert business_snapshot(service) == before
    assert len(calls) == 1
    for name, value in {
        "guard": "completed_by_recovery",
        "schedule_status": native.status,
        "business_status": record["status"],
        "http_requests": len(calls),
        "runs": len(db.rows("SELECT * FROM ingestion_runs")),
        "pages": len(db.rows("SELECT * FROM ingestion_pages")),
        "cursor_version": source["cursor_version"],
        "replay_business_unchanged": True,
        "model_calls": len(db.rows("SELECT * FROM agent_runs")),
    }.items():
        record_property(name, value)


def test_running_recovery_uses_fresh_record_without_a_second_enqueue(
    ingestion_http, monkeypatch, record_property
):
    case, calls, enqueues = ingestion_http, [], []
    request_entered, finish_request = Event(), Event()

    def respond(request):
        calls.append(str(request.url))
        request_entered.set()
        assert finish_request.wait(15), "test did not release the offline response"
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, content=BODY.encode())

    service = transport(case, respond)
    context = owner(case)
    service.schedule(context, schedule_payload("schedule-running-recovery"))
    generation = service.repository.db.rows("SELECT * FROM ingestion_schedules")[0]
    entered, release, captured = pause_queued_schedule(monkeypatch, service)
    enqueue = service.runtime.enqueue

    def tracked_enqueue(record):
        enqueues.append((record["status"], DBOS.workflow_id))
        return enqueue(record)

    monkeypatch.setattr(service.runtime, "enqueue", tracked_enqueue)
    handle = DBOS.trigger_schedule(generation["native_name"])
    try:
        assert entered.wait(15)
        queued = captured["record"]
        service.runtime.after_launch()
        assert request_entered.wait(15)
        assert service.repository.get_system(queued["run_id"])["status"] == "running"
        release.set()
        native = native_terminal(handle)
        assert native.status == "SUCCESS", native.error
        assert handle.get_result() == {"run_id": queued["run_id"], "error_code": None}
        # Recovery owns the sole enqueue. The schedule's old queued snapshot
        # must not cause another client enqueue once its live guard says running.
        assert enqueues == [("queued", None)]
    finally:
        release.set()
        finish_request.set()
    record = wait(service, queued["run_id"])
    assert record["status"] == "completed" and len(calls) == 1
    db = service.repository.db
    assert len(db.rows("SELECT * FROM ingestion_runs")) == 1
    assert len(db.rows("SELECT * FROM ingestion_pages")) == 1
    source = db.rows("SELECT * FROM ingestion_source_state WHERE source_id='unit-allowed'")[0]
    assert source["cursor_version"] == 1
    for name, value in {
        "guard": "running_by_recovery",
        "schedule_status": native.status,
        "business_status": record["status"],
        "http_requests": len(calls),
        "enqueue_calls": len(enqueues),
        "runs": 1,
        "pages": 1,
        "cursor_version": source["cursor_version"],
        "model_calls": len(db.rows("SELECT * FROM agent_runs")),
    }.items():
        record_property(name, value)


@pytest.mark.parametrize(
    ("change", "error_code"),
    [
        ("downgrade", "ingestion_admin_required"),
        ("revoke_policy", "source_policy_unconfirmed"),
        ("pause_generation", "ingestion_schedule_not_current"),
        ("cancel_requested", "ingestion_cancelled"),
        ("cancelled", "ingestion_cancelled"),
        ("application_version", "ingestion_application_version_changed"),
    ],
)
def test_queued_schedule_rechecks_current_guards_instead_of_swallowing_denials(
    ingestion_http, monkeypatch, record_property, change, error_code
):
    case, calls = ingestion_http, []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, content=BODY.encode())

    service = transport(case, respond)
    context = owner(case)
    command = service.schedule(context, schedule_payload("schedule-current-guards"))
    generation = service.repository.db.rows("SELECT * FROM ingestion_schedules")[0]
    entered, release, captured = pause_queued_schedule(monkeypatch, service)
    handle = DBOS.trigger_schedule(generation["native_name"])
    try:
        assert entered.wait(15), "schedule never reached its post-admission queued read"
        queued = captured["record"]
        db = service.repository.db
        if change == "downgrade":
            db.execute(
                "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id=:id",
                {"id": queued["owner_id"]},
            )
        elif change == "revoke_policy":
            values = json.loads(case["settings"].source_registry.read_text())
            values[0]["policy"]["status"] = "denied"
            case["settings"].source_registry.write_text(json.dumps(values))
        elif change == "pause_generation":
            service.schedule(
                context,
                schedule_payload(
                    "pause-before-schedule-enqueue",
                    enabled=False,
                    expected_state_version=command["state_version"],
                ),
            )
        elif change in {"cancel_requested", "cancelled"}:
            service.repository.request_cancel(context, queued["run_id"], queued["state_version"])
            if change == "cancelled":
                service.runtime.cancel(queued["run_id"])
        else:
            service.repository.versions["application_version"] = "UNIT-incompatible-version"
    finally:
        release.set()

    # These are live authorization/permission/cancel failures.  They must remain
    # ERROR, rather than being reclassified as the harmless completed-run race.
    native = native_terminal(handle)
    assert native.status == "ERROR"
    with pytest.raises(CompanyError, match=error_code):
        handle.get_result()
    assert isinstance(native.error, CompanyError) and native.error.code == error_code
    # Even a subsequent actual recovery sweep cannot issue a request or create
    # a fact from the old queued snapshot under the changed guards.
    service.runtime.after_launch()
    assert calls == []
    record = service.repository.get_system(queued["run_id"])
    assert record["request_count"] == 0
    # No page ran, so the internal record still has its untouched empty
    # counter map; the actual page/fact tables below prove the zero count.
    assert record["counts"] == {}
    assert record["status"] != "completed"
    source = db.rows("SELECT * FROM ingestion_source_state WHERE source_id='unit-allowed'")[0]
    assert source["cursor_version"] == 0 and source["last_success"] is None
    assert db.rows("SELECT * FROM ingestion_pages") == []
    assert db.rows("SELECT * FROM snapshots") == []
    assert db.rows("SELECT * FROM nd_versions") == []
    assert db.rows("SELECT * FROM nd_observations") == []
    assert db.rows("SELECT * FROM agent_runs") == []
    for name, value in {
        "guard": change,
        "error_code": native.error.code,
        "schedule_status": native.status,
        "business_status": record["status"],
        "http_requests": len(calls),
        "runs": len(db.rows("SELECT * FROM ingestion_runs")),
        "pages": len(db.rows("SELECT * FROM ingestion_pages")),
        "cursor_version": source["cursor_version"],
        "model_calls": len(db.rows("SELECT * FROM agent_runs")),
    }.items():
        record_property(name, value)
