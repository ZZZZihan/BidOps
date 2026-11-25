"""Real signed-OIDC HTTP/SQLite admission; authored offline model answers only."""

import pytest
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import JobsService
from test_enterprise import ORIGIN
from test_qualification_api import assert_no_private
from test_qualification_api import company_http as _company_http
from test_qualification_api import screening_http as _screening_http

company_http = _company_http
screening_http = _screening_http


def payload(case):
    return {"profile_revision": case["profile"], "project_id": case["notice"].project_id}


def configure_jobs(case, *, enabled=True):
    # A real service whose worker is deliberately not started: enabled and
    # configured are independent, and admission must use the former.
    jobs = JobsService(
        case["app"].state.screening,
        case["app"].state.store.settings.data_dir,
        JobsSettings(enabled=enabled, _env_file=None),
    )
    case["app"].state.jobs = jobs
    return jobs


def post(case, headers=None):
    return case["client"].post(
        "/api/screening/checklists",
        json=payload(case),
        headers=case["headers"] if headers is None else headers,
    )


def test_enabled_unstarted_jobs_refuse_sync_without_new_result_or_call(screening_http):
    case = screening_http
    jobs = configure_jobs(case)
    status = case["client"].get("/api/jobs/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is True and status.json()["configured"] is False
    before_calls = len(case["calls"])
    before_results = case["app"].state.store.db.rows("SELECT * FROM qualification_results")
    response = post(case)
    assert response.status_code == 409
    assert response.json() == {"detail": "screening_use_durable_jobs"}
    assert_no_private(response)
    assert len(case["calls"]) == before_calls
    assert case["app"].state.store.db.rows("SELECT * FROM qualification_results") == before_results
    assert case["app"].state.store.db.rows("SELECT * FROM durable_jobs") == []
    assert case["app"].state.store.db.rows("SELECT * FROM agent_runs") == []
    assert jobs.started is False and not jobs.path.exists()


def test_enabled_jobs_do_not_fall_back_when_model_configuration_is_missing(screening_http):
    case = screening_http
    configure_jobs(case)
    case["app"].state.screening.model_override = None
    case["app"].state.screening.settings.api_key = None
    before = len(case["calls"])
    response = post(case)
    assert response.status_code == 409
    assert response.json()["detail"] == "screening_use_durable_jobs"
    assert len(case["calls"]) == before


@pytest.mark.parametrize("installed", [False, True])
def test_jobs_disabled_preserve_the_original_offline_sync_endpoint(screening_http, installed):
    case = screening_http
    if installed:
        configure_jobs(case, enabled=False)
    else:
        assert case["app"].state.jobs is None
    before = len(case["calls"])
    response = post(case)
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "completed"
    assert response.json()["human_review_state"] == "unreviewed"
    assert response.json()["run_id"] != case["run"]["run_id"]
    assert len(case["calls"]) > before


@pytest.mark.parametrize("actor", [None, "viewer"])
def test_authentication_and_permission_precede_durable_admission(screening_http, actor):
    case = screening_http
    configure_jobs(case)
    headers = case["login"](actor)
    before = len(case["calls"])
    response = post(case, {**headers, "X-Roles": "admin", "X-Owner-ID": "alice"})
    assert response.status_code == (401 if actor is None else 403)
    assert response.json()["detail"] != "screening_use_durable_jobs"
    assert_no_private(response)
    assert len(case["calls"]) == before


@pytest.mark.parametrize("failure", ["missing", "wrong_token", "wrong_origin"])
def test_csrf_precedes_durable_admission(screening_http, failure):
    case = screening_http
    configure_jobs(case)
    headers = dict(case["headers"])
    if failure == "missing":
        headers.pop("X-CSRF-Token")
    elif failure == "wrong_token":
        headers["X-CSRF-Token"] = "wrong"
    else:
        headers["Origin"] = "https://untrusted.invalid"
    before = len(case["calls"])
    response = post(case, headers)
    assert response.status_code == 403
    assert response.json()["detail"] != "screening_use_durable_jobs"
    assert_no_private(response)
    assert len(case["calls"]) == before


def test_jobs_enabled_leave_candidates_and_saved_sync_history_usable(screening_http):
    case = screening_http
    configure_jobs(case)
    before = len(case["calls"])
    created = case["client"].post(
        "/api/screening/candidates",
        json={"profile_revision": case["profile"]},
        headers=case["headers"],
    )
    assert created.status_code == 201, created.text
    batch = created.json()
    page = case["client"].get(f"/api/screening/candidates/{batch['batch_id']}")
    assert page.status_code == 200 and page.json()["manifest_sha256"] == batch["manifest_sha256"]
    run = case["client"].get(f"/api/screening/runs/{case['run']['run_id']}")
    assert (
        run.status_code == 200 and run.json()["result_revision"] == case["run"]["result_revision"]
    )
    history = case["client"].get("/api/screening/runs")
    assert history.status_code == 200 and history.json()["total"] >= 1
    assert len(case["calls"]) == before
    assert case["client"].get("/api/auth/me", headers={"Origin": ORIGIN}).json()["authenticated"]
