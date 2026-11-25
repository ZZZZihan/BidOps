"""Actual enterprise ingestion HTTP with signed offline OIDC and durable SQLite.

UNIT inputs and permission records are engineering fixtures. Network and model
transports remain forbidden by conftest; this is not real-source acceptance.
"""

import base64
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import httpx2
import pytest
from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.ingestion.config import IngestionSettings
from bidops.ingestion.schemas import (
    MAX_REQUEST_BYTES,
    CancelCommand,
    HttpRun,
    ManualImport,
    decode_body,
)
from bidops.jobs.config import JobsSettings
from bidops.qualification.config import ScreeningSettings
from bidops.review.config import ReviewSettings
from dbos import DBOS
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request
from test_enterprise import ORIGIN, ProtocolProvider, enterprise

SOURCE = "unit-unknown"
URL = "https://unknown.unit.example/notice.html"
SECRET = "UNIT_PRIVATE_INGESTION_RESPONSIBILITY"
BODY = (
    "项目名称：UNIT_INGESTION_HTTP\n项目编号：UNIT-INGESTION-HTTP\n"
    "采购人名称：UNIT合成采购中心\n公告类型：招标公告\n"
    "投标截止时间：2099年11月30日10:00\n第1包\n预算金额：100元\n"
    "资质要求：UNIT 合成工程证明。\n"
)


def registry(path):
    """Only generated UNIT origins; policy evidence is explicitly non-business."""
    now = datetime.now(UTC)
    allowed = "https://allowed.unit.example/notice.html"
    fixture = {
        "id": "unit-allowed",
        "name": "UNIT 合成许可来源",
        "host": "allowed.unit.example",
        "home_url": "https://allowed.unit.example/",
        "robots_status": "allowed",
        "terms_status": "allowed",
        "permission_evidence": "UNIT offline permission fixture, not actual permission",
        "reviewed_at": (now - timedelta(hours=1)).isoformat(),
        "decision_reason": "UNIT engineering fixture only; no network acceptance",
        "allowed_urls": [allowed, "https://allowed.unit.example/proof.txt"],
        "policy": {
            "revision": "unit-policy-v1",
            "status": "allowed",
            "evidence_sha256": hashlib.sha256(b"UNIT offline permission only").hexdigest(),
            "evidence_reference": "UNIT offline permission fixture, not actual permission",
            "reviewed_by": "UNIT engineering fixture",
            "reviewed_at": (now - timedelta(hours=1)).isoformat(),
            "valid_from": (now - timedelta(hours=1)).isoformat(),
            "valid_until": (now + timedelta(days=1)).isoformat(),
            "acquisition_modes": ["manual_import", "manual_fetch", "scheduled_fetch"],
            "url_scope": [allowed, "https://allowed.unit.example/proof.txt"],
            "rate_limit_seconds": 5.0,
            "max_attempts": 1,
            "max_requests": 10,
            "max_bytes": 2_000_000,
            "total_timeout_seconds": 15.0,
        },
    }
    unknown = {
        "id": SOURCE,
        "name": "UNIT 许可待核实来源",
        "host": "unknown.unit.example",
        "home_url": "https://unknown.unit.example/",
        "robots_status": "unknown",
        "terms_status": "unknown",
        "permission_evidence": None,
        "reviewed_at": now.isoformat(),
        "decision_reason": "UNIT unknown permission; explicit manual responsibility required",
        "allowed_urls": [URL, "https://unknown.unit.example/proof.txt"],
    }
    path.write_text(json.dumps([fixture, unknown], ensure_ascii=False))
    return path


def manual(**changes):
    payload = {
        "source_id": SOURCE,
        "idempotency_key": "unit-manual-import",
        "rights_attestation": True,
        "responsibility_reason": SECRET,
        "pages": [
            {
                "items": [
                    {
                        "source_url": URL,
                        "content_type": "text/plain",
                        "encoding": "utf-8",
                        "body_base64": base64.b64encode(BODY.encode()).decode(),
                        "attachments": [],
                    }
                ],
                "cursor_after": "unit-committed-page-1",
            }
        ],
    }
    return {**payload, **changes}


@pytest.fixture
def ingestion_http(settings, tmp_path):
    settings.source_registry = registry(tmp_path / "sources.json")
    app = create_app(
        settings,
        DemoSettings(enabled=False, _env_file=None),
        enterprise(_env_file=None),
        screening_settings=ScreeningSettings(
            enabled=False, live_calls_enabled=False, _env_file=None
        ),
        jobs_settings=JobsSettings(enabled=False, _env_file=None),
        review_settings=ReviewSettings(enabled=False, _env_file=None),
        ingestion_settings=IngestionSettings(enabled=True, _env_file=None),
    )
    provider, cookies = ProtocolProvider(), {}
    try:
        with TestClient(app, base_url=ORIGIN) as client:
            app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
                provider.handle
            )
            for actor, roles in (
                ("owner", ["admin"]),
                ("other", ["analyst"]),
                ("viewer", ["viewer"]),
                ("second-admin", ["admin"]),
            ):
                client.cookies.clear()
                provider.subject, provider.roles = actor, roles
                assert provider.login(client).status_code == 303
                cookies[actor] = client.cookies.get("bidops_session")

            def login(actor):
                client.cookies.clear()
                if actor is not None:
                    client.cookies.set(
                        "bidops_session", cookies[actor], domain="bidops.test", path="/"
                    )
                value = client.get("/api/auth/me").json()
                return {"Origin": ORIGIN, "X-CSRF-Token": value["csrf_token"] or ""}

            yield {
                "app": app,
                "client": client,
                "login": login,
                "settings": settings,
                "provider": provider,
                "cookies": cookies,
            }
    finally:
        DBOS.destroy(destroy_registry=True)


def accepted(case, payload=None, headers=None):
    response = case["client"].post(
        "/api/ingestion/imports",
        json=payload or manual(),
        headers=headers if headers is not None else case["login"]("owner"),
    )
    assert response.status_code == 202, response.text
    return response.json()


def finished(case, run_id):
    deadline, last = time.monotonic() + 15, None
    while time.monotonic() < deadline:
        response = case["client"].get(f"/api/ingestion/runs/{run_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] not in {"queued", "running", "cancel_requested"}:
            return last
        time.sleep(0.025)
    pytest.fail(f"actual ingestion worker did not finish: {last}")


def test_schema_manual_defaults_incomplete_and_http_cannot_claim_success():
    payload = ManualImport.model_validate(manual())
    assert payload.pages[0].items[0].source_complete is False
    assert decode_body(payload.pages[0].items[0].body_base64) == BODY.encode()
    with pytest.raises(ValidationError):
        HttpRun.model_validate(
            {
                "source_id": SOURCE,
                "idempotency_key": "unit",
                "items": [{"source_url": URL, "source_complete": True}],
            }
        )


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_schema_rights_must_be_explicit_strict_true(value):
    with pytest.raises(ValidationError):
        ManualImport.model_validate(manual(rights_attestation=value))


@pytest.mark.parametrize("value", ["%%secret%%", "VUU=\n", "汉字", ""])
def test_schema_base64_is_strict(value):
    payload = manual()
    payload["pages"][0]["items"][0]["body_base64"] = value
    with pytest.raises(ValidationError):
        ManualImport.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", "admin"),
        ("observed_at", "2099-01-01"),
        ("success", True),
        ("permission_status", "allowed"),
    ],
)
def test_schema_rejects_client_actor_time_success_or_permission(field, value):
    with pytest.raises(ValidationError):
        ManualImport.model_validate(manual(**{field: value}))


def test_schema_attachments_cannot_be_body_and_failed_or_claim_parsed():
    payload = manual()
    attachment = {
        "attachment_key": "unit",
        "filename": "unit.txt",
        "content_type": "text/plain",
        "body_base64": "VU5JVA==",
        "failed": True,
        "error_code": "manual_unavailable",
    }
    payload["pages"][0]["items"][0]["attachments"] = [attachment]
    with pytest.raises(ValidationError):
        ManualImport.model_validate(payload)
    attachment.pop("body_base64")
    assert ManualImport.model_validate(payload).pages[0].items[0].attachments[0].failed
    attachment["parse_status"] = "parsed"
    with pytest.raises(ValidationError):
        ManualImport.model_validate(payload)


def test_schema_finite_pages_blob_size_unique_keys_and_strict_cas():
    payload = manual()
    payload["pages"] *= 51
    with pytest.raises(ValidationError):
        ManualImport.model_validate(payload)
    with pytest.raises(ValueError, match="blob_too_large"):
        decode_body(base64.b64encode(b"UNIT").decode(), max_bytes=3)
    for value in (True, -1, 1.0, "1"):
        with pytest.raises(ValidationError):
            CancelCommand(expected_state_version=value)
    with pytest.raises(ValidationError):
        HttpRun(
            source_id=SOURCE,
            idempotency_key="unit",
            items=[{"source_url": URL}, {"source_url": URL}],
        )


def test_actual_manual_import_repeat_paging_and_get_do_not_reexecute(ingestion_http):
    case = ingestion_http
    first = accepted(case)
    saved = finished(case, first["run_id"])
    assert saved["status"] == "completed", saved
    again = accepted(case)
    assert again["run_id"] == first["run_id"] and again["created"] is False
    for _ in range(3):
        response = case["client"].get(f"/api/ingestion/runs/{first['run_id']}")
        assert (
            response.status_code == 200
            and response.json()["state_version"] == saved["state_version"]
        )
    page = case["client"].get("/api/ingestion/runs?offset=0&limit=1").json()
    assert page["total"] == 1 and page["items"][0]["run_id"] == first["run_id"]
    assert SECRET not in json.dumps(page) and "body_base64" not in json.dumps(page)
    state = case["app"].state.store.db.rows(
        "SELECT * FROM ingestion_source_state WHERE source_id=:source", {"source": SOURCE}
    )[0]
    assert state["last_attempt"] is not None and state["last_success"] is not None
    assert json.loads(state["cursor_json"]) == "unit-committed-page-1"
    assert len(case["app"].state.store.db.rows("SELECT * FROM ingestion_runs")) == 1


def test_actual_concurrent_identical_key_is_one_run_and_different_input_is_409(ingestion_http):
    case = ingestion_http
    headers, barrier = case["login"]("owner"), Barrier(2)

    def submit_one(_):
        barrier.wait(timeout=5)
        return case["client"].post("/api/ingestion/imports", json=manual(), headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(submit_one, range(2)))
    assert all(response.status_code == 202 for response in replies), [r.text for r in replies]
    assert len({response.json()["run_id"] for response in replies}) == 1
    changed = manual(responsibility_reason=SECRET + " changed")
    response = case["client"].post("/api/ingestion/imports", json=changed, headers=headers)
    assert response.status_code == 409 and SECRET not in response.text


@pytest.mark.parametrize("actor,status", [(None, 401), ("viewer", 403), ("other", 403)])
def test_actual_import_authenticates_before_stream_or_json_decode(
    ingestion_http, monkeypatch, actor, status
):
    case, reads = ingestion_http, []
    headers, original = case["login"](actor), Request.stream

    async def recorded_stream(request):
        reads.append(request.url.path)
        async for chunk in original(request):
            yield chunk

    monkeypatch.setattr(Request, "stream", recorded_stream)
    response = case["client"].post(
        "/api/ingestion/imports", headers=headers, content=b"invalid JSON " + SECRET.encode()
    )
    assert response.status_code == status and SECRET not in response.text
    assert "/api/ingestion/imports" not in reads


@pytest.mark.parametrize("failure", ["csrf_missing", "csrf_wrong", "origin_wrong"])
def test_actual_import_csrf_is_checked_before_stream(ingestion_http, monkeypatch, failure):
    case = ingestion_http
    headers, reads, original = case["login"]("owner"), [], Request.stream
    if failure == "csrf_missing":
        headers.pop("X-CSRF-Token")
    elif failure == "csrf_wrong":
        headers["X-CSRF-Token"] = "UNIT wrong CSRF"
    else:
        headers["Origin"] = "https://foreign.unit.example"

    async def recorded_stream(request):
        reads.append(request.url.path)
        async for chunk in original(request):
            yield chunk

    monkeypatch.setattr(Request, "stream", recorded_stream)
    response = case["client"].post("/api/ingestion/imports", headers=headers, content=SECRET)
    assert response.status_code == 403 and SECRET not in response.text
    assert "/api/ingestion/imports" not in reads


def test_actual_stream_limits_and_validation_never_echo_private_bundle(ingestion_http):
    case = ingestion_http
    headers = case["login"]("owner")
    malformed = manual(actor_id=SECRET)
    response = case["client"].post("/api/ingestion/imports", json=malformed, headers=headers)
    assert response.status_code == 422 and SECRET not in response.text
    response = case["client"].post(
        "/api/ingestion/imports", content=b" " * (MAX_REQUEST_BYTES + 1), headers=headers
    )
    assert response.status_code == 413
    too_large = manual()
    too_large["pages"][0]["items"][0]["body_base64"] = base64.b64encode(
        b"x" * (case["settings"].max_bytes + 1)
    ).decode()
    response = case["client"].post("/api/ingestion/imports", json=too_large, headers=headers)
    assert response.status_code in {413, 422} and SECRET not in response.text


@pytest.mark.parametrize(
    "actor,status", [(None, 401), ("viewer", 404), ("other", 404), ("second-admin", 200)]
)
def test_actual_run_owner_current_admin_and_forged_header_boundary(ingestion_http, actor, status):
    case = ingestion_http
    run = accepted(case)
    finished(case, run["run_id"])
    headers = {**case["login"](actor), "X-Actor-ID": "owner", "X-Roles": "admin"}
    response = case["client"].get(f"/api/ingestion/runs/{run['run_id']}", headers=headers)
    assert response.status_code == status, response.text
    if status != 200:
        assert SECRET not in response.text and "body_base64" not in response.text
    if actor is not None:
        source_response = case["client"].get("/api/ingestion/sources")
        assert source_response.status_code == 200
        assert SECRET not in source_response.text and "body_base64" not in source_response.text


def test_actual_schedule_is_admin_csrf_cas_and_explicitly_paused(ingestion_http):
    case = ingestion_http
    payload = {
        "schedule_id": "unit-plan",
        "source_id": "unit-allowed",
        "items": [{"source_url": "https://allowed.unit.example/notice.html", "attachments": []}],
        "idempotency_key": "unit-plan-v1",
        "expected_state_version": 0,
        "cron": "0 * * * *",
        "enabled": False,
        "reason": "UNIT pause only; no external request",
    }
    response = case["client"].post(
        "/api/ingestion/schedules", json=payload, headers=case["login"]("owner")
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["enabled"] is False
    stale = {**payload, "idempotency_key": "unit-plan-stale"}
    response = case["client"].post(
        "/api/ingestion/schedules", json=stale, headers=case["login"]("owner")
    )
    assert response.status_code == 409
    case["login"]("viewer")
    assert case["client"].get("/api/ingestion/schedules").status_code == 403
    response = case["client"].post(
        "/api/ingestion/schedules", json=payload, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 403


def test_actual_cancel_checks_csrf_owner_current_roles_and_cas_without_late_commit(
    ingestion_http, monkeypatch
):
    case = ingestion_http
    entered, released, returned = Event(), Event(), Event()
    service = case["app"].state.ingestion
    original = service.process_page

    def blocked(run_id, page_seq):
        entered.set()
        try:
            assert released.wait(5), "controlled page must be released by test cleanup"
            return original(run_id, page_seq)
        finally:
            returned.set()

    monkeypatch.setattr(service, "process_page", blocked)
    try:
        run = accepted(case, manual(idempotency_key="unit-blocked-cancel"))
        assert entered.wait(5), "actual DBOS workflow did not enter its page step"
        path = f"/api/ingestion/runs/{run['run_id']}"
        saved = case["client"].get(path).json()
        command = {"expected_state_version": saved["state_version"]}
        for actor, status in ((None, 401), ("viewer", 404), ("other", 404)):
            headers = {**case["login"](actor), "X-Actor-ID": "owner", "X-Roles": "admin"}
            response = case["client"].post(path + "/cancel", json=command, headers=headers)
            assert response.status_code == status, response.text
        case["login"]("owner")
        response = case["client"].post(path + "/cancel", json=command, headers={"Origin": ORIGIN})
        assert response.status_code == 403
        response = case["client"].post(
            path + "/cancel",
            json={"expected_state_version": saved["state_version"] + 100},
            headers=case["login"]("second-admin"),
        )
        assert response.status_code == 409
        # A later signed SSO snapshot demotes the owner. It removes new-import
        # rights but must not prevent that same owner stopping existing work.
        owner = case["app"].state.store.db.rows(
            "SELECT owner_id FROM ingestion_runs WHERE run_id=:id", {"id": run["run_id"]}
        )[0]["owner_id"]
        snapshot = case["app"].state.store.db.rows(
            "SELECT roles_checked_at FROM enterprise_subjects WHERE owner_id=:owner",
            {"owner": owner},
        )[0]
        provider = case["provider"]
        provider.subject, provider.roles = "owner", ["viewer"]
        provider.claims = {"iat": int(snapshot["roles_checked_at"]) + 1}
        case["client"].cookies.clear()
        assert provider.login(case["client"]).status_code == 303
        case["cookies"]["owner"] = case["client"].cookies.get("bidops_session")
        headers = case["login"]("owner")
        assert (
            case["client"]
            .post(
                "/api/ingestion/imports",
                json=manual(idempotency_key="unit-forbidden-after-demotion"),
                headers=headers,
            )
            .status_code
            == 403
        )
        response = case["client"].post(path + "/cancel", json=command, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "cancelled"
        assert response.json()["allowed_actions"] == []
    finally:
        released.set()
        if entered.is_set():
            assert returned.wait(5), "actual page thread was not joined before test teardown"
    current = case["client"].get(path)
    assert current.status_code == 200
    assert current.json()["status"] == "cancelled" and current.json()["pages"] == []
    assert case["app"].state.store.db.rows("SELECT COUNT(*) AS n FROM nd_versions")[0]["n"] == 0
