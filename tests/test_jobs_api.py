"""Real ASGI HTTP, signed OIDC, SQLite/ACL, DBOS and offline Pydantic AI.

No SDK/model transport is mocked here. The reused qualification fixture supplies
authored FunctionModel answers and external signed-OIDC MockTransport only.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.enterprise.config import EnterpriseSettings
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import JobsService
from bidops.qualification.config import ScreeningSettings
from dbos import DBOS
from fastapi.testclient import TestClient
from test_enterprise import ORIGIN
from test_qualification_api import SECRET, assert_no_private
from test_qualification_api import company_http as _company_http
from test_qualification_api import screening_http as _screening_http

company_http = _company_http
screening_http = _screening_http


@pytest.fixture
def jobs_http(screening_http):
    case = screening_http
    app = case["app"]
    jobs = JobsService(
        app.state.screening,
        app.state.store.settings.data_dir,
        JobsSettings(enabled=True, _env_file=None),
    )
    app.state.jobs = jobs
    try:
        jobs.start()
        yield {**case, "jobs": jobs}
    finally:
        jobs.close()
        # Also clean up a partial start failure; each fixture owns the singleton.
        DBOS.destroy(destroy_registry=True)


def body(case, **updates):
    return {"profile_revision": case["profile"], "project_id": case["notice"].project_id, **updates}


def submit(case, *, key="jobs-http-unit", payload=None, headers=None):
    return case["client"].post(
        "/api/jobs/runs",
        json=payload or body(case),
        headers={**(headers if headers is not None else case["headers"]), "Idempotency-Key": key},
    )


def accepted(case, **kwargs):
    response = submit(case, **kwargs)
    assert response.status_code == 202, response.text
    return response.json()


def waiting(case, run_id):
    deadline = time.monotonic() + 15
    last = None
    while time.monotonic() < deadline:
        response = case["client"].get(f"/api/jobs/runs/{run_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == "waiting_review":
            return last
        assert last["status"] in {"queued", "running"}, last
        time.sleep(0.025)
    pytest.fail(f"DBOS did not reach native review wait: {last}")


def entries(case, run):
    return [
        ("GET", "/api/jobs/status", None),
        ("GET", "/api/jobs/runs", None),
        ("GET", f"/api/jobs/runs/{run['run_id']}", None),
        ("POST", "/api/jobs/runs", body(case)),
        (
            "POST",
            f"/api/jobs/runs/{run['run_id']}/cancel",
            {"expected_state_version": run["state_version"]},
        ),
        (
            "POST",
            f"/api/jobs/runs/{run['run_id']}/resume",
            {"expected_state_version": run["state_version"]},
        ),
        (
            "POST",
            f"/api/jobs/runs/{run['run_id']}/reauthorize",
            {"expected_state_version": run["state_version"]},
        ),
    ]


def test_default_disabled_and_local_mode_do_not_expose_jobs_or_start_workers(settings):
    for enabled in (False, True):
        app = create_app(
            settings,
            DemoSettings(enabled=False, _env_file=None),
            EnterpriseSettings(enabled=False, _env_file=None),
            screening_settings=ScreeningSettings(enabled=True, _env_file=None),
            jobs_settings=JobsSettings(enabled=enabled, _env_file=None),
        )
        with TestClient(app, base_url=ORIGIN) as client:
            for path in ("/api/jobs/status", "/api/jobs/runs", "/api/jobs/runs/unknown"):
                response = client.get(path)
                assert response.status_code == 404 and SECRET not in response.text
            assert app.state.jobs is None
            assert app.state.store.db.rows("SELECT * FROM durable_jobs") == []


def test_enterprise_default_jobs_are_disabled_without_starting_a_worker(company_http):
    app, client, login = company_http
    headers = login("alice")
    status = client.get("/api/jobs/status")
    assert status.status_code == 200
    assert status.json() == {
        "enabled": False,
        "configured": False,
        "backend": "dbos",
        "live_calls_enabled": False,
    }
    assert app.state.jobs is None
    assert client.get("/api/jobs/runs").status_code == 503
    assert client.post("/api/jobs/runs", headers=headers, json={}).status_code in {422, 503}
    assert app.state.store.db.rows("SELECT * FROM durable_jobs") == []


def test_actual_202_dbos_result_review_wait_and_gets_are_free_and_do_not_rerun(jobs_http):
    case = jobs_http
    runtime_status = case["client"].get("/api/jobs/status").json()
    assert runtime_status["enabled"] and runtime_status["configured"]
    assert runtime_status["backend"] == "dbos" and not runtime_status["live_calls_enabled"]
    baseline = len(case["calls"])
    first = accepted(case)
    run = waiting(case, first["run_id"])
    assert run["result_id"] != run["run_id"]
    assert run["result"]["human_review_state"] == "unreviewed"
    assert run["result"]["manifest"]["profile_revision"] == case["profile"]
    assert run["manifest"]["profile_revision"] == case["profile"]
    assessments = [
        item for group in run["result"]["groups"] for item in group["assessment"]["assessments"]
    ]
    assert assessments and all(
        item["notice_evidence"] and item["company_evidence"] for item in assessments
    )
    calls = len(case["calls"]) - baseline
    assert calls > 0 and run["usage"]["model_calls"] == calls
    assert run["usage"]["offline"] and run["usage"]["estimated_cost_cny"] == "0"
    assert run["usage"]["reserved_cost_cny"] == "0"
    assert DBOS.get_workflow_status(run["run_id"] + "-calculation").status == "SUCCESS"
    assert DBOS.get_workflow_status(run["run_id"]).status == "PENDING"
    for _ in range(3):
        read = case["client"].get(f"/api/jobs/runs/{run['run_id']}")
        assert read.status_code == 200
        assert read.json()["result_revision"] == run["result_revision"]
    assert len(case["calls"]) - baseline == calls
    # Persisted receipts and results remain readable after the offline injection
    # is gone. GET cannot require a currently configured or live-enabled model.
    case["app"].state.screening.model_override = None
    case["app"].state.screening.settings.api_key = None
    assert not case["app"].state.screening.settings.live_calls_enabled
    assert not case["client"].get("/api/jobs/status").json()["configured"]
    assert case["client"].get(f"/api/jobs/runs/{run['run_id']}").status_code == 200
    history = case["client"].get("/api/jobs/runs?offset=0&limit=1").json()
    assert history["total"] == 1 and history["items"][0]["run_id"] == run["run_id"]
    assert {"manifest", "result", "versions"}.isdisjoint(history["items"][0])


def test_unconfigured_start_keeps_history_readable_but_starts_no_worker_or_actions(jobs_http):
    case = jobs_http
    run = waiting(case, accepted(case)["run_id"])
    baseline = len(case["calls"])
    case["jobs"].close()
    screening = case["app"].state.screening
    screening.model_override = None
    screening.settings.api_key = None
    screening.settings.live_calls_enabled = False
    reader = JobsService(
        screening,
        case["app"].state.store.settings.data_dir,
        JobsSettings(enabled=True, _env_file=None),
    )
    case["app"].state.jobs = reader
    try:
        reader.start()
        assert not reader.started
        status = case["client"].get("/api/jobs/status")
        assert status.status_code == 200
        assert status.json()["enabled"] and not status.json()["configured"]
        assert not status.json()["live_calls_enabled"]
        read = case["client"].get(f"/api/jobs/runs/{run['run_id']}")
        assert read.status_code == 200
        assert read.json()["result_revision"] == run["result_revision"]
        assert read.json()["allowed_actions"] == []
        history = case["client"].get("/api/jobs/runs").json()
        assert history["total"] == 1 and history["items"][0]["allowed_actions"] == []
        for method, path, payload in entries(case, run):
            if method != "POST" or path.endswith("/reauthorize"):
                continue
            response = case["client"].request(
                method,
                path,
                json=payload,
                headers={**case["headers"], "Idempotency-Key": "reader-must-not-execute"},
            )
            assert response.status_code == 503
            assert response.json()["detail"] == "job_backend_unavailable"
        assert len(case["calls"]) == baseline
        assert len(case["app"].state.store.db.rows("SELECT * FROM durable_jobs")) == 1
    finally:
        reader.close()


def test_actual_http_concurrent_same_key_input_persists_one_job(jobs_http):
    case, gate = jobs_http, Barrier(3)

    def post(_):
        gate.wait(timeout=5)
        return submit(case, key="http-concurrent-same")

    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(post, range(3)))
    assert [response.status_code for response in responses] == [202] * 3
    records = [response.json() for response in responses]
    assert len({record["run_id"] for record in records}) == 1
    assert sorted(record["created"] for record in records) == [False, False, True]
    assert len(case["app"].state.store.db.rows("SELECT * FROM durable_jobs")) == 1
    assert len(case["app"].state.store.db.rows("SELECT * FROM durable_job_inputs")) == 1
    run = waiting(case, records[0]["run_id"])
    assert case["client"].get("/api/jobs/runs").json()["total"] == 1
    assert run["status"] == "waiting_review"


def test_actual_http_concurrent_same_key_different_input_has_one_409(jobs_http):
    case, gate = jobs_http, Barrier(2)

    def post(index):
        gate.wait(timeout=5)
        return submit(
            case,
            key="http-concurrent-conflict",
            payload=body(case, **({"as_of": datetime.now(UTC).isoformat()} if index else {})),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(post, range(2)))
    assert sorted(response.status_code for response in responses) == [202, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"] == "job_idempotency_conflict"
    assert len(case["app"].state.store.db.rows("SELECT * FROM durable_jobs")) == 1


def test_every_actual_jobs_entry_requires_login_despite_forged_actor_headers(jobs_http):
    case = jobs_http
    run = waiting(case, accepted(case)["run_id"])
    case["login"](None)
    baseline = len(case["calls"])
    for method, path, payload in entries(case, run):
        response = case["client"].request(
            method,
            path,
            json=payload,
            headers={
                "Origin": ORIGIN,
                "X-Owner-ID": "alice",
                "X-Subject-ID": "alice",
                "X-Roles": "admin",
                "Idempotency-Key": "forged-actor",
            },
        )
        assert response.status_code == 401, (path, response.text)
        assert_no_private(response)
    assert len(case["calls"]) == baseline


@pytest.mark.parametrize("actor", ["bob", "admin", "viewer"])
def test_other_subject_cannot_read_or_mutate_job_even_with_shared_sources(jobs_http, actor):
    case = jobs_http
    run = waiting(case, accepted(case)["run_id"])
    subject = case["app"].state.store.db.rows(
        "SELECT owner_id FROM enterprise_subjects WHERE subject=:subject", {"subject": actor}
    )[0]["owner_id"]
    for ref in (case["profile"], case["doc"]):
        granted = case["client"].post(
            f"/api/company/objects/{ref['object_id']}/grants",
            headers=case["login"]("alice"),
            json={"subject_id": subject, "actions": ["read"]},
        )
        assert granted.status_code == 200
    headers = case["login"](actor)
    baseline = len(case["calls"])
    assert case["client"].get("/api/jobs/status").status_code == 200
    assert case["client"].get("/api/jobs/runs").json() == {
        "items": [],
        "total": 0,
        "offset": 0,
        "limit": 20,
    }
    for method, path, payload in entries(case, run):
        if path.endswith("/status") or path == "/api/jobs/runs":
            continue
        response = case["client"].request(
            method,
            path,
            json=payload,
            headers={
                **headers,
                "X-Owner-ID": "alice",
                "X-Roles": "admin",
            },
        )
        assert response.status_code == (403 if actor == "viewer" and method == "POST" else 404)
        assert_no_private(response)
    assert len(case["calls"]) == baseline


def test_viewer_cannot_submit_with_shared_valid_profile_or_spoofed_permissions(jobs_http):
    case = jobs_http
    subject = case["app"].state.store.db.rows(
        "SELECT owner_id FROM enterprise_subjects WHERE subject='viewer'"
    )[0]["owner_id"]
    for ref in (case["profile"], case["doc"]):
        granted = case["client"].post(
            f"/api/company/objects/{ref['object_id']}/grants",
            headers=case["login"]("alice"),
            json={"subject_id": subject, "actions": ["read"]},
        )
        assert granted.status_code == 200
    headers = case["login"]("viewer")
    assert (
        case["client"].get(f"/api/company/profiles/{case['profile']['object_id']}").status_code
        == 200
    )
    response = submit(case, headers={**headers, "X-Roles": "admin", "X-Permissions": "agent:use"})
    assert response.status_code == 403
    assert_no_private(response)
    assert case["app"].state.store.db.rows("SELECT * FROM durable_jobs") == []


@pytest.mark.parametrize("failure", ["missing", "wrong_token", "wrong_origin"])
def test_all_actual_mutations_check_csrf_before_work(jobs_http, failure):
    case = jobs_http
    run = waiting(case, accepted(case)["run_id"])
    headers = dict(case["login"]("alice"))
    if failure == "missing":
        headers = {}
    elif failure == "wrong_token":
        headers["X-CSRF-Token"] = "forged"
    else:
        headers["Origin"] = "https://attacker.invalid"
    baseline = len(case["calls"])
    for method, path, payload in entries(case, run):
        if method != "POST":
            continue
        response = case["client"].request(
            method, path, json=payload, headers={**headers, "Idempotency-Key": "csrf"}
        )
        assert response.status_code == 403 and response.json()["detail"] == "csrf_failed"
        assert_no_private(response)
    assert len(case["calls"]) == baseline
    assert (
        case["client"].get(f"/api/jobs/runs/{run['run_id']}").json()["status"] == "waiting_review"
    )


def test_required_idempotency_and_actor_input_are_validated_without_jobs_or_model_calls(jobs_http):
    case = jobs_http
    baseline = len(case["calls"])
    response = case["client"].post("/api/jobs/runs", headers=case["headers"], json=body(case))
    assert response.status_code == 422, response.text
    response = submit(
        case, payload=body(case, owner_id="bob", roles=["admin"], context={"owner_id": "bob"})
    )
    assert response.status_code == 422
    assert {item["loc"][-1] for item in response.json()["detail"]} >= {
        "owner_id",
        "roles",
        "context",
    }
    assert case["app"].state.store.db.rows("SELECT * FROM durable_jobs") == []
    assert len(case["calls"]) == baseline


def test_cancel_is_terminal_and_no_http_fixture_approval_endpoint_exists(jobs_http):
    case = jobs_http
    run = waiting(case, accepted(case)["run_id"])
    baseline = len(case["calls"])
    stale = case["client"].post(
        f"/api/jobs/runs/{run['run_id']}/cancel",
        headers=case["headers"],
        json={"expected_state_version": run["state_version"] - 1},
    )
    assert stale.status_code == 409 and stale.json()["detail"] == "job_state_conflict"
    unchanged = case["client"].get(f"/api/jobs/runs/{run['run_id']}").json()
    assert (
        unchanged["status"] == "waiting_review"
        and unchanged["state_version"] == run["state_version"]
    )
    cancelled = case["client"].post(
        f"/api/jobs/runs/{run['run_id']}/cancel",
        headers=case["headers"],
        json={"expected_state_version": run["state_version"]},
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["allowed_actions"] == []
    resumed = case["client"].post(
        f"/api/jobs/runs/{run['run_id']}/resume",
        headers=case["headers"],
        json={"expected_state_version": cancelled.json()["state_version"]},
    )
    assert resumed.status_code == 409 and resumed.json()["detail"] == "job_resume_forbidden"
    for suffix in ("approve", "review", "review_fixture", "fixture-approve"):
        response = case["client"].post(
            f"/api/jobs/runs/{run['run_id']}/{suffix}",
            headers=case["headers"],
            json={"decision": "confirm", "origin": "test_fixture", "actor": {"roles": ["admin"]}},
        )
        # The root static mount returns 405 for an unknown POST. Neither refusal
        # is an approval endpoint; also prove there is no registered API route.
        assert response.status_code in {404, 405} and SECRET not in response.text
        assert all(
            getattr(route, "path", "") != f"/api/jobs/runs/{{run_id}}/{suffix}"
            for route in case["app"].routes
        )
    assert case["app"].state.store.db.rows("SELECT * FROM durable_review_fixtures") == []
    assert case["client"].get(f"/api/jobs/runs/{run['run_id']}").json()["status"] == "cancelled"
    assert len(case["calls"]) == baseline


def test_reauthorize_is_owner_cas_and_cannot_revive_a_cancelled_job_or_approve_it(jobs_http):
    case = jobs_http
    run = waiting(case, accepted(case)["run_id"])
    cancelled = case["client"].post(
        f"/api/jobs/runs/{run['run_id']}/cancel",
        headers=case["headers"],
        json={"expected_state_version": run["state_version"]},
    )
    assert cancelled.status_code == 200
    baseline = len(case["calls"])
    case["jobs"].repository.quarantine_all()
    quarantined = case["jobs"].repository.get_system(run["run_id"])
    assert quarantined["quarantined"] and quarantined["status"] == "cancelled"
    assert case["client"].get(f"/api/jobs/runs/{run['run_id']}").status_code == 404
    denied = case["client"].post(
        f"/api/jobs/runs/{run['run_id']}/reauthorize",
        headers=case["headers"],
        json={"expected_state_version": quarantined["state_version"] - 1},
    )
    assert denied.status_code == 409 and denied.json()["detail"] == "job_state_conflict"
    unchanged = case["jobs"].repository.get_system(run["run_id"])
    assert unchanged["quarantined"]
    assert unchanged["state_version"] == quarantined["state_version"]
    restored = case["client"].post(
        f"/api/jobs/runs/{run['run_id']}/reauthorize",
        headers=case["headers"],
        json={"expected_state_version": quarantined["state_version"]},
    )
    assert restored.status_code == 200, restored.text
    result = restored.json()
    assert not result["quarantined"] and result["status"] == "cancelled"
    assert result["state_version"] == quarantined["state_version"] + 1
    assert result["result_revision"] == run["result_revision"]
    assert result["result"]["human_review_state"] == "unreviewed"
    assert result["allowed_actions"] == []
    assert DBOS.get_workflow_status(run["run_id"]).status == "CANCELLED"
    assert case["app"].state.store.db.rows("SELECT * FROM durable_review_fixtures") == []
    assert len(case["calls"]) == baseline
