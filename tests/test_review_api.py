"""Actual signed OIDC, HTTP, SQLite and native DBOS; authored offline answers only."""

import copy
import time
from datetime import UTC, datetime

import httpx2
import pytest
from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.company.authorization import ToolContext
from bidops.enterprise.config import EnterpriseSettings
from bidops.review.config import ReviewSettings
from bidops.review.service import ReviewService
from dbos import DBOS
from fastapi.testclient import TestClient
from pydantic_ai import models
from test_enterprise import ORIGIN, ProtocolProvider, enterprise
from test_jobs_api import accepted, body, waiting
from test_jobs_api import jobs_http as jobs_http
from test_notice_versions import BODY, persist
from test_qualification_api import assert_no_private
from test_qualification_api import screening_http as screening_http


@pytest.fixture
def company_http(settings, monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    app = create_app(settings, DemoSettings(_env_file=None), enterprise())
    provider, cookies = ProtocolProvider(), {}
    with TestClient(app, base_url=ORIGIN) as client:
        app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
            provider.handle
        )
        for actor, roles in (
            ("alice", ["analyst", "reviewer"]),
            ("bob", ["analyst", "reviewer"]),
            ("admin", ["admin"]),
            ("viewer", ["viewer"]),
        ):
            client.cookies.clear()
            provider.subject, provider.roles = actor, roles
            assert provider.login(client).headers["location"] == "/"
            cookies[actor] = client.cookies.get("bidops_session")

        def login(actor):
            client.cookies.clear()
            if actor:
                client.cookies.set("bidops_session", cookies[actor], domain="bidops.test", path="/")
            me = client.get("/api/auth/me").json()
            return {"Origin": ORIGIN, "X-CSRF-Token": me["csrf_token"] or ""}

        yield app, client, login


@pytest.fixture
def review_http(jobs_http):
    case = jobs_http
    service = ReviewService(
        case["app"].state.screening, case["jobs"], ReviewSettings(enabled=True, _env_file=None)
    )
    case["app"].state.review = service
    case["login"]("alice")
    owner = case["client"].get("/api/auth/me").json()["user"]["subject_id"]
    case["login"]("bob")
    bob = case["client"].get("/api/auth/me").json()["user"]["subject_id"]
    headers = case["login"]("alice")
    for ref in (case["doc"], case["profile"]):
        case["app"].state.company.auth.grant(ToolContext(owner), ref["object_id"], bob, ["read"])
    return {**case, "review": service, "owner": owner, "bob": bob, "headers": headers}


def make_case(case):
    run = waiting(case, accepted(case)["run_id"])
    response = case["client"].post(
        "/api/reviews/cases",
        headers=case["headers"],
        json={
            "run_id": run["run_id"],
            "candidate_batch_id": case["batch"]["batch_id"],
            "reviewer_ids": [case["owner"], case["bob"]],
            "idempotency_key": "create-case",
        },
    )
    assert response.status_code == 201, response.text
    return run, response.json()


def command(case, item=None, *, key="decision", decision="confirm"):
    item = item or case["items"][0]
    head = next(r for r in case["revisions"] if r["is_head"])
    return {
        "revision_id": head["revision_id"],
        "assessment_id": item["assessment_id"],
        "condition_id": item["condition_id"],
        "expected_version": case["review_version"],
        "command": {
            "decision": decision,
            "expected_result_revision": head["result_revision"],
            "reason": "Explicit synthetic reviewer evidence check",
            "idempotency_key": key,
        },
        "supersedes_event_id": None,
    }


def url(case, suffix=""):
    return f"/api/reviews/cases/{case['case_id']}" + suffix


def wait_native(run_id, status):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = DBOS.get_workflow_status(run_id)
        if result.status == status:
            return result
        time.sleep(0.02)
    pytest.fail(f"native workflow did not become {status}: {result.status}")


def test_real_http_each_item_close_native_success_and_full_export_without_recompute(review_http):
    case = review_http
    run, review = make_case(case)
    before, original = len(case["calls"]), copy.deepcopy(review["revisions"])

    def close(value):
        return {
            "expected_version": value["review_version"],
            "idempotency_key": "close",
            "reason": "Complete",
        }

    denied = case["client"].post(url(review, "/close"), headers=case["headers"], json=close(review))
    assert denied.status_code == 409
    for number, item in enumerate(review["items"]):
        payload = command(review, item, key=f"item-{number}")
        response = case["client"].post(
            url(review, "/decisions"), headers=case["headers"], json=payload
        )
        assert response.status_code == 200, response.text
        review = response.json()
        duplicate = case["client"].post(
            url(review, "/decisions"), headers=case["headers"], json=payload
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["review_version"] == review["review_version"]
    assert DBOS.get_workflow_status(run["run_id"]).status == "PENDING"
    response = case["client"].post(
        url(review, "/close"), headers=case["headers"], json=close(review)
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"
    wait_native(run["run_id"], "SUCCESS")
    saved = case["client"].get(url(review)).json()
    assert saved["delivery"]["pending_count"] == 0
    assert saved["revisions"] == original
    assert all(item["effective"] for item in saved["items"])
    exported = case["client"].get(url(review, "/export"))
    assert exported.status_code == 200
    assert "no-store" in exported.headers["cache-control"].split(", ")
    assert "raw_model_outputs" not in exported.text
    assert len(exported.json()["case"]["events"]) == len(review["items"]) + 1
    assert len(case["calls"]) == before
    job = case["client"].get(f"/api/jobs/runs/{run['run_id']}").json()
    assert job["status"] == "completed" and job["result"]["human_review_state"] == "unreviewed"


@pytest.mark.parametrize("mutation", ["missing_csrf", "bad_origin", "spoof_actor", "bulk"])
def test_real_write_boundary_rejects_forged_actor_csrf_origin_and_bulk(review_http, mutation):
    case = review_http
    _, review = make_case(case)
    headers, payload = dict(case["headers"]), command(review)
    if mutation == "missing_csrf":
        headers.pop("X-CSRF-Token")
    elif mutation == "bad_origin":
        headers["Origin"] = "https://evil.example"
    elif mutation == "spoof_actor":
        payload["actor"] = {"principal_id": case["owner"], "actor_kind": "human"}
    else:
        payload["approve_all"] = True
    response = case["client"].post(url(review, "/decisions"), headers=headers, json=payload)
    assert response.status_code == (403 if mutation in {"missing_csrf", "bad_origin"} else 422)
    assert case["app"].state.store.db.rows("SELECT * FROM review_events") == []
    assert_no_private(response)


@pytest.mark.parametrize("actor,status", [(None, 401), ("admin", 404), ("viewer", 404)])
def test_all_private_reads_deny_unknown_users_and_no_alias(review_http, actor, status):
    case = review_http
    _, review = make_case(case)
    case["login"](actor)
    for path in (url(review), url(review, "/export")):
        response = case["client"].get(path)
        assert response.status_code == status, response.text
        assert_no_private(response)
        response = case["client"].get(path.removeprefix("/api"))
        assert response.status_code == 404
    response = case["client"].get("/api/reviews/cases")
    if actor is None:
        assert response.status_code == 401
    else:
        assert response.status_code == 200 and response.json()["items"] == []


def test_stopped_worker_accepts_human_event_and_restart_delivers_without_model_recompute(
    review_http,
):
    case = review_http
    run, review = make_case(case)
    before = len(case["calls"])
    case["jobs"].close()
    response = case["client"].post(
        url(review, "/decisions"), headers=case["headers"], json=command(review)
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["delivery"]["pending_count"] == 1
    assert not saved["delivery"]["worker_available"]
    case["jobs"].start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if case["client"].get(url(review)).json()["delivery"]["pending_count"] == 0:
            break
        time.sleep(0.02)
    else:
        pytest.fail("durable human outbox was not consumed on startup")
    assert len(case["calls"]) == before
    assert (
        case["client"].get(f"/api/jobs/runs/{run['run_id']}").json()["status"] == "waiting_review"
    )


def test_final_release_rejects_new_restore_epoch_and_body_after_projection(
    review_http, monkeypatch
):
    case = review_http
    _, review = make_case(case)
    repo = case["review"].repository
    original = repo.get

    def restore_after_projection(context, case_id):
        result = original(context, case_id)
        repo.db.execute(
            "UPDATE review_case_state SET quarantined=1,review_epoch='restored' WHERE case_id=:id",
            {"id": case_id},
        )
        return result

    monkeypatch.setattr(repo, "get", restore_after_projection)
    response = case["client"].get(url(review))
    assert response.status_code == 409
    assert_no_private(response)
    assert "candidate_batch" not in response.text


def test_default_off_and_local_mode_never_expose_human_review(company_http, settings):
    app, client, login = company_http
    login("alice")
    assert client.get("/api/reviews/status").json() == {
        "enabled": False,
        "configured": False,
        "worker_available": False,
    }
    assert client.get("/api/reviews/cases").status_code == 503
    local = create_app(
        settings,
        DemoSettings(enabled=False, _env_file=None),
        EnterpriseSettings(enabled=False, _env_file=None),
        review_settings=ReviewSettings(enabled=True, _env_file=None),
    )
    with TestClient(local, base_url=ORIGIN) as local_client:
        assert local_client.get("/api/reviews/status").status_code == 404
        assert local_client.get("/api/reviews/cases").status_code == 404
        assert not local.state.store.db.rows("SELECT * FROM review_cases")


def test_every_write_entry_checks_csrf_and_spoofed_actor_headers_do_not_authenticate(review_http):
    case = review_http
    run, review = make_case(case)
    case_command = {
        "expected_version": review["review_version"],
        "idempotency_key": "every-route",
        "reason": "Synthetic boundary check",
    }
    entries = [
        (
            "/api/reviews/cases",
            {
                "run_id": run["run_id"],
                "candidate_batch_id": case["batch"]["batch_id"],
                "idempotency_key": "create-other",
            },
        ),
        (url(review, "/assignments"), {**case_command, "reviewer_ids": [case["bob"]]}),
        (url(review, "/decisions"), command(review)),
        (
            url(review, "/successors"),
            {
                **case_command,
                "revision_id": review["head_revision_id"],
                "trigger_event_id": "unknown",
                "request": body(case),
            },
        ),
        (url(review, "/revisions"), {**case_command, "run_id": run["run_id"]}),
        (url(review, "/close"), case_command),
        (url(review, "/reauthorize"), case_command),
    ]
    before = len(case["calls"])
    for path, payload in entries:
        response = case["client"].post(path, json=payload, headers={"Origin": ORIGIN})
        assert response.status_code == 403, (path, response.text)
        assert_no_private(response)
        alias = case["client"].post(path.removeprefix("/api"), json=payload)
        assert alias.status_code in {404, 405}
    case["login"](None)
    for path, payload in entries:
        response = case["client"].post(
            path,
            json=payload,
            headers={"Origin": ORIGIN, "X-Owner-ID": case["owner"], "X-Roles": "reviewer,analyst"},
        )
        assert response.status_code == 401, (path, response.text)
        assert_no_private(response)
    assert len(case["calls"]) == before
    assert not case["app"].state.store.db.rows("SELECT * FROM review_events")


def test_assignment_never_supplies_source_grants_and_revocation_clears_reads(review_http):
    case = review_http
    _, review = make_case(case)
    case["login"]("bob")
    assert case["client"].get(url(review)).status_code == 200
    case["app"].state.company.auth.revoke(
        ToolContext(case["owner"]), case["doc"]["object_id"], case["bob"]
    )
    headers = case["login"]("bob")
    for path in (url(review), url(review, "/export")):
        response = case["client"].get(path)
        assert response.status_code == 404
        assert_no_private(response)
    response = case["client"].post(url(review, "/decisions"), json=command(review), headers=headers)
    assert response.status_code == 404
    assert case["client"].get("/api/reviews/cases").json()["items"] == []


def test_missing_model_keeps_review_history_and_human_decisions_available(review_http):
    case = review_http
    _, review = make_case(case)
    before = len(case["calls"])
    case["jobs"].close()
    screening = case["app"].state.screening
    screening.model_override = None
    screening.settings.api_key = None
    screening.settings.live_calls_enabled = False
    case["jobs"].start()
    assert not case["jobs"].started
    assert case["client"].get("/api/reviews/status").json() == {
        "enabled": True,
        "configured": True,
        "worker_available": False,
    }
    response = case["client"].post(
        url(review, "/decisions"), headers=case["headers"], json=command(review)
    )
    assert response.status_code == 200, response.text
    assert response.json()["delivery"]["pending_count"] == 1
    assert response.json()["items"][0]["latest_decision"] == "confirm"
    assert case["client"].get(url(review, "/export")).status_code == 200
    assert len(case["calls"]) == before


@pytest.mark.parametrize("kind", ["更正公告", "终止公告"])
def test_actual_notice_change_invalidates_review_and_export_preserving_original(review_http, kind):
    case = review_http
    run, review = make_case(case)
    response = case["client"].post(
        url(review, "/decisions"), headers=case["headers"], json=command(review)
    )
    assert response.status_code == 200
    reviewed = response.json()
    original = copy.deepcopy(reviewed["revisions"][0]["result"])
    before = len(case["calls"])
    changed = persist(
        case["app"].state.screening.sources.notices,
        BODY.replace("招标公告", kind) + "\nUNIT-1",
        path="changed-notice",
        at=datetime.now(UTC),
    )
    assert changed.project_id == case["notice"].project_id
    response = case["client"].get(url(review))
    assert response.status_code == 200, response.text
    stale = response.json()
    assert not stale["source_state"]["current"]
    assert stale["items"][0]["latest_decision"] == "confirm"
    assert not stale["items"][0]["effective"]
    assert "source_changed" in stale["items"][0]["ineffective_reasons"]
    assert "decide" not in stale["allowed_actions"]
    assert stale["events"] == reviewed["events"]
    assert stale["revisions"][0]["result"] == original
    exported = case["client"].get(url(review, "/export"))
    assert exported.status_code == 200 and not exported.json()["current"]
    assert exported.json()["case"]["events"] == reviewed["events"]
    assert len(case["calls"]) == before
    assert case["client"].get(f"/api/jobs/runs/{run['run_id']}").status_code == 200
