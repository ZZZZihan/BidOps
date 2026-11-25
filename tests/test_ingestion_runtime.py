"""Real DBOS orchestration with bounded offline HTTP and generated notices."""

import json
import time
from threading import Event

import httpx
import pytest
from bidops.company.authorization import CompanyError, ToolContext
from dbos import DBOS
from test_attachment_parser import pdf
from test_ingestion_api import BODY, manual
from test_ingestion_api import ingestion_http as ingestion_http


def owner(case):
    case["login"]("owner")
    return ToolContext(case["client"].get("/api/auth/me").json()["user"]["subject_id"])


def wait(service, run_id, statuses=("completed", "partial", "failed", "cancelled")):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        record = service.repository.get_system(run_id)
        if record["status"] in statuses:
            return record
        time.sleep(0.02)
    pytest.fail(f"did not finish: {record['status']}")


def http_payload(key="runtime-http", attachments=None):
    return {
        "source_id": "unit-allowed",
        "idempotency_key": key,
        "items": [
            {
                "source_url": "https://allowed.unit.example/notice.html",
                "attachments": attachments or [],
            }
        ],
    }


def transport(case, handler):
    service = case["app"].state.ingestion
    case["settings"].network_enabled = True
    service.client = httpx.Client(transport=httpx.MockTransport(handler))
    return service


def test_actual_source_only_native_queue_has_no_model_dependency(ingestion_http):
    case = ingestion_http
    service, context = case["app"].state.ingestion, owner(case)
    assert not case["app"].state.jobs.status()["configured"]
    first = service.import_manual(context, manual())
    record = wait(service, first["run_id"])
    assert record["status"] == "completed" and record["counts"]["items"] == 1
    native = DBOS.get_workflow_status(record["native_id"])
    deadline = time.monotonic() + 3
    while native.status != "SUCCESS" and time.monotonic() < deadline:
        time.sleep(0.02)
        native = DBOS.get_workflow_status(record["native_id"])
    assert native.name == "bidops_ingestion_run_v1" and native.status == "SUCCESS"
    assert native.parent_workflow_id is None
    assert case["app"].state.store.db.rows("SELECT * FROM agent_runs") == []
    assert case["app"].state.store.db.rows("SELECT * FROM durable_model_attempts") == []
    # The ingestion extension starts the shared engine, but cannot enable the
    # qualification worker or a previously persisted qualification workflow.
    with pytest.raises(CompanyError, match="jobs_disabled"):
        case["app"].state.jobs.require_execution("persisted-qualification-run")


def test_actual_two_manual_pages_commit_then_replay_never_refreshes(ingestion_http):
    case = ingestion_http
    service, context = case["app"].state.ingestion, owner(case)
    payload = manual()
    payload["pages"].append(
        {"items": [dict(payload["pages"][0]["items"][0])], "cursor_after": "second"}
    )
    first = service.import_manual(context, payload)
    record = wait(service, first["run_id"])
    assert record["status"] == "completed"
    assert record["counts"] == {
        "pages": 2,
        "items": 2,
        "imported": 1,
        "unchanged": 1,
        "failed": 0,
        "attachments": 0,
        "attachment_failed": 0,
    }
    state = service.repository.sources(context)[1]
    again = service.import_manual(context, payload)
    assert again["run_id"] == first["run_id"] and not again["created"]
    assert service.repository.sources(context)[1]["last_success"] == state["last_success"]


def test_actual_partial_attachment_commits_visible_failure_without_cursor(ingestion_http):
    case = ingestion_http
    service, context = case["app"].state.ingestion, owner(case)
    payload = manual()
    payload["pages"][0]["items"][0]["source_complete"] = True
    payload["pages"][0]["items"][0]["attachments"] = [
        {
            "attachment_key": "missing",
            "filename": "missing.pdf",
            "content_type": "application/pdf",
            "failed": True,
            "error_code": "http_403",
        }
    ]
    first = service.import_manual(context, payload)
    saved = wait(service, first["run_id"])
    assert saved["status"] == "partial" and saved["counts"]["attachment_failed"] == 1
    source = service.sources(context)["items"][1]
    assert source["cursor"] is None and source["last_success"] is None
    assert len(service.read(context, first["run_id"])["pages"]) == 1


@pytest.mark.parametrize("status", [403, 429, 302, 404])
def test_actual_restricted_response_never_retries_or_refreshes(ingestion_http, status):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(
            status, headers={"Location": "https://outside.invalid/secret"}, content=b"restricted"
        )

    case = ingestion_http
    service = transport(case, handle)
    first = service.submit(owner(case), http_payload())
    saved = wait(service, first["run_id"])
    assert saved["status"] == "failed" and len(calls) == 1
    source = service.sources(owner(case))["items"][0]
    assert source["last_attempt"] is not None and source["last_success"] is None
    assert source["last_error_code"] == f"http_{status}"
    assert service.repository.db.rows("SELECT * FROM snapshots") == []


def test_actual_failed_refresh_does_not_renew_success_and_clock_shows_stale(ingestion_http):
    case, fail = ingestion_http, [False]

    def handle(request):
        return httpx.Response(
            503 if fail[0] else 200,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            content=BODY.encode(),
        )

    service = transport(case, handle)
    context = owner(case)
    first = service.submit(context, http_payload())
    assert wait(service, first["run_id"])["status"] == "completed"
    success = service.sources(context)["items"][0]["last_success"]
    fail[0] = True
    # Move only the persisted rate slot into the past; tests never send real HTTP.
    service.repository.db.execute("UPDATE ingestion_source_state SET next_request_at=0")
    second = service.submit(context, http_payload("refresh-failure"))
    assert wait(service, second["run_id"])["status"] == "failed"
    assert service.sources(context)["items"][0]["last_success"] == success
    original = service.repository.clock
    service.repository.clock = lambda: original() + service.settings.freshness_seconds + 10
    state = service.sources(context)["items"][0]
    assert state["freshness"] == "stale" and state["last_success"] == success


def test_actual_current_policy_revocation_during_response_blocks_commit(ingestion_http):
    case = ingestion_http

    def handle(request):
        values = json.loads(case["settings"].source_registry.read_text())
        values[0]["policy"]["status"] = "denied"
        case["settings"].source_registry.write_text(json.dumps(values))
        return httpx.Response(
            200, headers={"Content-Type": "text/plain; charset=utf-8"}, content=BODY.encode()
        )

    service = transport(case, handle)
    first = service.submit(owner(case), http_payload())
    record = wait(service, first["run_id"])
    assert record["status"] == "failed" and record["request_count"] == 1
    assert service.repository.db.rows("SELECT * FROM snapshots") == []
    assert service.repository.db.rows("SELECT * FROM ingestion_pages") == []


def test_actual_admin_revocation_before_page_commit_rejects_all_writes(ingestion_http):
    case = ingestion_http
    service, context = case["app"].state.ingestion, owner(case)

    def fault(window):
        if window == "before_page_commit":
            service.repository.db.execute(
                "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id=:id",
                {"id": context.owner_id},
            )

    service.fault = fault
    first = service.import_manual(context, manual())
    record = wait(service, first["run_id"])
    assert record["status"] == "failed"
    assert service.repository.db.rows("SELECT * FROM snapshots") == []
    assert service.repository.db.rows("SELECT * FROM ingestion_pages") == []


def test_actual_same_source_admission_blocks_concurrency_but_same_key_replays(ingestion_http):
    case = ingestion_http
    service, context = case["app"].state.ingestion, owner(case)
    entered, release = Event(), Event()

    def fault(window):
        if window == "before_page_commit":
            entered.set()
            assert release.wait(5)

    service.fault = fault
    first = service.import_manual(context, manual())
    try:
        assert entered.wait(3)
        replay = service.import_manual(context, manual())
        assert replay["run_id"] == first["run_id"] and not replay["created"]
        with pytest.raises(CompanyError, match="ingestion_source_busy"):
            service.import_manual(context, manual(idempotency_key="other-key"))
    finally:
        release.set()
    assert wait(service, first["run_id"])["status"] == "completed"


def schedule_payload(key="runtime-schedule", **changes):
    return {
        **http_payload(key),
        "schedule_id": "runtime-plan",
        "expected_state_version": 0,
        "cron": "0 0 1 1 *",
        "enabled": True,
        "reason": "UNIT explicit offline schedule",
        **changes,
    }


def test_actual_native_schedule_trigger_creates_one_bound_independent_run(ingestion_http):
    case, calls = ingestion_http, []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(
            200, headers={"Content-Type": "text/plain; charset=utf-8"}, content=BODY.encode()
        )

    service = transport(case, handle)
    context = owner(case)
    command = service.schedule(context, schedule_payload())
    row = service.repository.db.rows("SELECT * FROM ingestion_schedules")[0]
    handle = DBOS.trigger_schedule(row["native_name"])
    result = handle.get_result()
    assert result["run_id"] and result["error_code"] is None
    record = wait(service, result["run_id"])
    assert record["status"] == "completed" and len(calls) == 1
    assert record["schedule_native_name"] == row["native_name"]
    assert DBOS.get_workflow_status(record["native_id"]).parent_workflow_id is None
    tick = service.runtime.schedules.admit(row["native_name"], handle.get_workflow_id())
    assert tick == result
    assert len(service.repository.db.rows("SELECT * FROM ingestion_runs")) == 1
    paused = service.schedule(
        context,
        schedule_payload("pause", enabled=False, expected_state_version=command["state_version"]),
    )
    assert not paused["enabled"] and len(calls) == 1
    old = DBOS.get_schedule(row["native_name"])
    assert old["status"] == "PAUSED"


@pytest.mark.parametrize("declared", ["application/octet-stream", "application/pdf"])
def test_actual_http_pdf_keeps_native_pages_and_parser_receipt(ingestion_http, declared):
    case, calls = ingestion_http, []
    data = pdf("UNIT first page", "UNIT second page")

    def handle(request):
        calls.append(str(request.url))
        attachment = request.url.path.endswith("proof.txt")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf" if attachment else "text/plain"},
            content=data if attachment else BODY.encode(),
        )

    service = transport(case, handle)
    first = service.submit(
        owner(case),
        http_payload(
            attachments=[
                {
                    "attachment_key": "pdf-proof",
                    "filename": "proof.pdf",
                    "source_url": "https://allowed.unit.example/proof.txt",
                    "content_type": declared,
                    "expected_page_count": 2,
                }
            ]
        ),
    )
    record = wait(service, first["run_id"])
    assert record["status"] == "completed", record.get("error_code")
    assert record["counts"]["attachments"] == 1 and len(calls) == 2
    receipts = service.repository.db.rows("SELECT * FROM nd_attachment_receipts")
    assert len(receipts) == 1
    receipt = json.loads(receipts[0]["receipt_json"])
    assert receipt["page_count"] == 2


def test_actual_invalid_cron_and_expired_policy_pause(ingestion_http):
    case = ingestion_http
    service = transport(case, lambda r: httpx.Response(500))
    context = owner(case)
    with pytest.raises(CompanyError, match="ingestion_invalid_cron"):
        service.schedule(context, schedule_payload(cron="* * * * * *"))
    saved = service.schedule(context, schedule_payload())
    values = json.loads(case["settings"].source_registry.read_text())
    values[0]["policy"]["status"] = "denied"
    case["settings"].source_registry.write_text(json.dumps(values))
    paused = service.schedule(
        context,
        schedule_payload(
            "paused-after-denial", enabled=False, expected_state_version=saved["state_version"]
        ),
    )
    assert not paused["enabled"]


@pytest.mark.parametrize("change", ["removed", "scope_removed"])
def test_existing_schedule_can_pause_after_registry_withdrawal(ingestion_http, change):
    case = ingestion_http
    calls = []
    service = transport(case, lambda request: calls.append(request) or httpx.Response(500))
    context = owner(case)
    saved = service.schedule(context, schedule_payload())
    generation = service.repository.db.rows("SELECT native_name FROM ingestion_schedules")[0]
    values = json.loads(case["settings"].source_registry.read_text())
    if change == "removed":
        values = values[1:]
    else:
        values[0]["allowed_urls"] = ["https://allowed.unit.example/proof.txt"]
        values[0]["policy"]["url_scope"] = ["https://allowed.unit.example/proof.txt"]
    case["settings"].source_registry.write_text(json.dumps(values))
    command = schedule_payload("pause-withdrawn", enabled=False, expected_state_version=1)
    # A changed destination cannot borrow the historical permission to mutate
    # a definition, even when the submitted definition starts disabled.
    changed = json.loads(json.dumps(command))
    changed["items"][0]["source_url"] = "https://outside.invalid/notice"
    with pytest.raises(CompanyError):
        service.schedule(context, changed)
    changed_source = {**command, "source_id": "unit-unknown"}
    with pytest.raises(CompanyError, match="ingestion_source_scope_denied") as denied:
        service.schedule(context, changed_source)
    assert denied.value.status_code == 403
    paused = service.schedule(context, command)
    assert not paused["enabled"] and paused["state_version"] == saved["state_version"] + 1
    assert DBOS.get_schedule(generation["native_name"])["status"] == "PAUSED"
    with pytest.raises(CompanyError):
        service.schedule(context, schedule_payload("reenable", expected_state_version=2))
    assert calls == []
