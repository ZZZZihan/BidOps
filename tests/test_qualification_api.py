"""Actual create_app HTTP/SQLite/ACL boundaries, using signed OIDC and local answers."""

from datetime import UTC, datetime

import pytest
from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.company.authorization import ToolContext
from bidops.enterprise.config import EnterpriseSettings
from bidops.qualification.config import ScreeningSettings
from bidops.screening.schemas import VersionRef
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from test_company_access import company_http as _company_http_fixture
from test_enterprise import ORIGIN
from test_notice_versions import BODY, persist
from test_qualification_service import scripted_response

company_http = _company_http_fixture
SECRET = "API_PRIVATE_PROOF"
OTHER_SECRET = "OTHER_PRIVATE_PROOF"
FAKE_REF = {"object_id": "unknown-profile", "version_id": "unknown-version", "sha256": "a" * 64}


def requests_for(case=None):
    profile = case["profile"] if case else FAKE_REF
    batch = case["batch"]["batch_id"] if case else "unknown-batch"
    run = case["run"]["run_id"] if case else "unknown-run"
    project = case["notice"].project_id if case else "unknown-project"
    return [
        ("GET", "/api/screening/status", None),
        ("POST", "/api/screening/candidates", {"profile_revision": profile}),
        ("GET", f"/api/screening/candidates/{batch}", None),
        ("POST", "/api/screening/checklists", {"profile_revision": profile, "project_id": project}),
        ("GET", f"/api/screening/runs/{run}", None),
        ("GET", "/api/screening/runs", None),
        ("POST", f"/api/screening/runs/{run}/reauthorize", None),
    ]


def perform(client, method, path, data, headers=None):
    return client.request(method, path, json=data, headers=headers or {})


def assert_no_private(response):
    assert SECRET not in response.text and OTHER_SECRET not in response.text


@pytest.fixture
def screening_http(company_http):
    app, client, login = company_http
    app.state.screening.settings = ScreeningSettings(enabled=True, _env_file=None)
    calls = []

    def answer(messages, info):
        calls.append(1)
        return scripted_response(messages, info)

    app.state.screening.model_override = FunctionModel(answer, model_name="offline-http-fixture")
    headers = login("alice")
    upload = client.post(
        "/api/company/materials",
        content=(SECRET + " synthetic certificate").encode(),
        headers={**headers, "Content-Type": "text/plain", "X-Filename": "synthetic.txt"},
    )
    assert upload.status_code == 201, upload.text
    doc = VersionRef.model_validate({key: upload.json()[key] for key in FAKE_REF}).model_dump()
    citation = client.post(
        f"/api/company/materials/{doc['object_id']}/versions/{doc['version_id']}/citations",
        json={"start": 0, "end": len(SECRET)},
        headers=headers,
    )
    assert citation.status_code == 200, citation.text
    profile_response = client.post(
        "/api/company/profiles",
        headers=headers,
        json={
            "payload": {
                "display_name": "PRIVATE HTTP engineering profile",
                "facts": [
                    {
                        "fact_id": "proof",
                        "kind": "qualification",
                        "label": "proof",
                        "status": "document_supported",
                        "value": "synthetic",
                        "evidence": [citation.json()],
                    }
                ],
            }
        },
    )
    assert profile_response.status_code == 201, profile_response.text
    profile = profile_response.json()
    notice = persist(app.state.screening.sources.notices, BODY + "\nUNIT-1", at=datetime.now(UTC))
    candidate = client.post(
        "/api/screening/candidates",
        json={"profile_revision": profile},
        headers=headers,
    )
    assert candidate.status_code == 201, candidate.text
    checklist = client.post(
        "/api/screening/checklists",
        json={
            "profile_revision": profile,
            "project_id": notice.project_id,
            "as_of": datetime.now(UTC).isoformat(),
        },
        headers=headers,
    )
    assert checklist.status_code == 201, checklist.text
    assert checklist.json()["state"] == "completed", checklist.text
    return {
        "app": app,
        "client": client,
        "login": login,
        "headers": headers,
        "doc": doc,
        "profile": profile,
        "notice": notice,
        "batch": candidate.json(),
        "run": checklist.json(),
        "calls": calls,
    }


def test_local_mode_has_no_enterprise_screening_entry(settings):
    app = create_app(
        settings,
        DemoSettings(enabled=False, _env_file=None),
        EnterpriseSettings(enabled=False),
        screening_settings=ScreeningSettings(enabled=True, _env_file=None),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        for method, path, data in requests_for():
            response = perform(client, method, path, data)
            assert response.status_code == 404, (path, response.text)
            assert_no_private(response)
        assert not app.state.store.db.rows("SELECT * FROM qualification_results")
        assert not app.state.store.db.rows("SELECT * FROM agent_runs")


def test_every_http_entry_requires_login_even_with_spoofed_actor_headers(screening_http):
    case = screening_http
    case["login"](None)
    before = len(case["calls"])
    for method, path, data in requests_for(case):
        response = perform(
            case["client"],
            method,
            path,
            data,
            {
                "X-Owner-ID": "alice",
                "X-Subject-ID": "alice",
                "X-Roles": "admin",
                "Origin": ORIGIN,
            },
        )
        assert response.status_code == 401, (path, response.text)
        assert_no_private(response)
    assert len(case["calls"]) == before


def test_viewer_cannot_create_candidates_or_run_models(screening_http):
    case = screening_http
    headers = case["login"]("viewer")
    before = len(case["calls"])
    for method, path, data in requests_for(case):
        if method == "POST" and not path.endswith("reauthorize"):
            response = perform(case["client"], method, path, data, headers)
            assert response.status_code == 403, response.text
            assert_no_private(response)
    assert case["client"].get("/api/screening/status").status_code == 200
    assert case["client"].get("/api/screening/runs").json()["total"] == 0
    assert len(case["calls"]) == before


@pytest.mark.parametrize("actor", ["bob", "admin", "viewer"])
def test_other_identity_cannot_read_owner_results_even_when_inputs_are_shared(
    screening_http, actor
):
    case = screening_http
    client, login = case["client"], case["login"]
    target = case["app"].state.store.db.rows(
        "SELECT owner_id FROM enterprise_subjects WHERE subject=:actor",
        {"actor": actor},
    )[0]["owner_id"]
    for ref in (case["profile"], case["doc"]):
        response = client.post(
            f"/api/company/objects/{ref['object_id']}/grants",
            headers=login("alice"),
            json={"subject_id": target, "actions": ["read"]},
        )
        assert response.status_code == 200
    headers = login(actor)
    for path in (
        f"/api/screening/runs/{case['run']['run_id']}",
        f"/api/screening/candidates/{case['batch']['batch_id']}",
    ):
        response = client.get(path, headers={"X-Owner-ID": "alice", "X-Roles": "admin"})
        assert response.status_code == 404, response.text
        assert_no_private(response)
    denied = client.post(
        f"/api/screening/runs/{case['run']['run_id']}/reauthorize",
        headers=headers,
        json={"owner_id": "alice", "roles": ["admin"]},
    )
    assert denied.status_code == 404 and SECRET not in denied.text
    history = client.get("/api/screening/runs")
    assert history.status_code == 200 and history.json() == {
        "items": [],
        "total": 0,
        "offset": 0,
        "limit": 20,
    }


def test_unknown_alias_and_wrong_result_kind_never_resolve_to_latest(screening_http):
    case = screening_http
    case["login"]("alice")
    aliases = ["latest", "head", "unknown", case["profile"]["object_id"], case["notice"].project_id]
    for alias in aliases:
        for resource in ("runs", "candidates"):
            response = case["client"].get(f"/api/screening/{resource}/{alias}")
            assert response.status_code == 404, (alias, response.text)
            assert_no_private(response)
        response = case["client"].post(
            f"/api/screening/runs/{alias}/reauthorize",
            headers=case["headers"],
        )
        assert response.status_code == 404
    assert case["client"].get(f"/api/screening/runs/{case['batch']['batch_id']}").status_code == 404
    assert (
        case["client"].get(f"/api/screening/candidates/{case['run']['run_id']}").status_code == 404
    )


@pytest.mark.parametrize("failure", ["missing", "wrong_token", "wrong_origin"])
def test_all_mutations_enforce_csrf_before_work(screening_http, failure):
    case = screening_http
    headers = dict(case["login"]("alice"))
    if failure == "missing":
        headers = {}
    elif failure == "wrong_token":
        headers["X-CSRF-Token"] = "forged"
    else:
        headers["Origin"] = "https://attacker.invalid"
    before = len(case["calls"])
    count = len(case["app"].state.store.db.rows("SELECT * FROM qualification_results"))
    for method, path, data in requests_for(case):
        if method == "POST":
            response = perform(case["client"], method, path, data, headers)
            assert response.status_code == 403 and response.json()["detail"] == "csrf_failed"
            assert_no_private(response)
    assert len(case["calls"]) == before
    assert len(case["app"].state.store.db.rows("SELECT * FROM qualification_results")) == count


@pytest.mark.parametrize("resource", ["candidates", "checklists"])
def test_model_and_client_cannot_supply_actor_or_permissions(screening_http, resource):
    case = screening_http
    data = {
        "profile_revision": case["profile"],
        "owner_id": "bob",
        "roles": ["admin"],
        "context": {"owner_id": "bob", "allowed": ["all"]},
    }
    if resource == "checklists":
        data["project_id"] = case["notice"].project_id
    before = len(case["calls"])
    response = case["client"].post(
        f"/api/screening/{resource}",
        json=data,
        headers=case["login"]("alice"),
    )
    assert response.status_code == 422
    assert {item["loc"][-1] for item in response.json()["detail"]} >= {
        "owner_id",
        "roles",
        "context",
    }
    assert_no_private(response)
    assert len(case["calls"]) == before


def test_default_screening_is_disabled_and_does_not_create_history(company_http):
    app, client, login = company_http
    headers = login("alice")
    assert app.state.screening.settings.enabled is False
    status = client.get("/api/screening/status")
    assert status.status_code == 200 and status.json()["enabled"] is False
    for method, path, data in requests_for()[1:]:
        response = perform(client, method, path, data, headers)
        assert response.status_code == 503 and response.json()["detail"] == "screening_disabled"
        assert_no_private(response)
    assert not app.state.store.db.rows("SELECT * FROM qualification_results")
    assert not app.state.store.db.rows("SELECT * FROM agent_runs")


def test_live_disabled_does_not_start_model_but_candidates_remain_available(screening_http):
    case = screening_http
    service = case["app"].state.screening
    service.model_override = None
    service.settings = ScreeningSettings(
        enabled=True, DEEPSEEK_API_KEY="UNIT_NOT_A_REAL_KEY", ledger_verified=True, _env_file=None
    )
    assert service.settings.api_key
    status = case["client"].get("/api/screening/status").json()
    assert status["live_calls_enabled"] is False and status["configured"] is False
    requests = [
        r for r in requests_for(case) if r[0] == "POST" and not r[1].endswith("reauthorize")
    ]
    batch = perform(case["client"], *requests[0], case["headers"])
    assert batch.status_code == 201
    before = len(case["calls"])
    denied = perform(case["client"], *requests[1], case["headers"])
    assert denied.status_code == 503 and denied.json()["detail"] == "screening_model_unavailable"
    assert_no_private(denied)
    assert len(case["calls"]) == before
    assert not case["app"].state.store.db.rows("SELECT * FROM agent_runs")


def test_revoked_source_blocks_results_history_reauthorization_and_next_tool_run(screening_http):
    case = screening_http
    client = case["client"]
    headers = case["login"]("alice")
    me = client.get("/api/auth/me").json()
    revoked = client.delete(
        f"/api/company/objects/{case['doc']['object_id']}/grants/{me['user']['subject_id']}",
        headers=headers,
    )
    assert revoked.status_code == 200
    before = len(case["calls"])
    for method, path, data in requests_for(case):
        if path.endswith("/status") or path == "/api/screening/runs":
            continue
        response = perform(client, method, path, data, headers)
        assert response.status_code == 404, (path, response.text)
        assert_no_private(response)
    assert client.get("/api/screening/runs").json()["items"] == []
    assert len(case["calls"]) == before


def test_real_agent_tool_cannot_read_material_outside_authorized_input_closure(screening_http):
    case = screening_http
    client, login = case["client"], case["login"]
    upload = client.post(
        "/api/company/materials",
        content=OTHER_SECRET.encode(),
        headers={**login("bob"), "Content-Type": "text/plain", "X-Filename": "bob-only.txt"},
    )
    assert upload.status_code == 201
    outside = upload.json()
    forbidden = []

    def attempted_tool(messages, info):
        prompt = next(
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        if '"stage":"assessment"' in prompt:
            forbidden.append(1)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "read_company_material",
                        {
                            "material_version_id": outside["version_id"],
                            "start": 0,
                            "end": len(OTHER_SECRET),
                        },
                    )
                ]
            )
        return scripted_response(messages, info)

    case["app"].state.screening.model_override = FunctionModel(attempted_tool)
    response = client.post(
        "/api/screening/checklists",
        headers=login("alice"),
        json={"profile_revision": case["profile"], "project_id": case["notice"].project_id},
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert forbidden and len(forbidden) <= 4
    assert result["state"] == "failed" and result["human_review_state"] == "unreviewed"
    assert OTHER_SECRET not in response.text
    assert all(
        a["status"] == "information_insufficient"
        for group in result["groups"]
        for a in group["assessment"]["assessments"]
    )
    body = case["app"].state.screening.repository.get(
        ToolContext(client.get("/api/auth/me").json()["user"]["subject_id"]),
        result["run_id"],
    )
    assert OTHER_SECRET not in str(body["raw_model_outputs"])
