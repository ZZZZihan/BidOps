"""Local adapter integration: signed OIDC and real services, no external model/IdP."""

import functools
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from bidops.company.authorization import CompanyError, ToolContext
from bidops.local_demo.app import create_workflow_app
from bidops.notice_data import NoticeRepository
from bidops.qualification.model_budget import GuardedModel
from dbos import DBOS
from fastapi.testclient import TestClient
from pydantic_ai.models.function import FunctionModel
from test_notice_versions import BODY, persist
from test_qualification_service import scripted_response
from test_review_api import command

ORIGIN = "http://127.0.0.1:8765"
CONTEXT = "/api/demo/workflow/context"


def login(client):
    start = client.get("/api/auth/login", follow_redirects=False)
    assert start.status_code == 302, start.text
    assert start.headers["location"].startswith(ORIGIN + "/api/auth/callback?")
    finish = client.get(start.headers["location"], follow_redirects=False)
    assert finish.status_code == 303 and finish.headers["location"] == "/", finish.text
    me = client.get("/api/auth/me").json()
    assert me["authenticated"], me
    assert me["user"]["roles"] == ["analyst", "reviewer"]
    return me, {"Origin": ORIGIN, "X-CSRF-Token": me["csrf_token"]}


@pytest.fixture
def prepared(settings, store):
    notice = persist(
        NoticeRepository(store.db, settings.data_dir / "raw"),
        BODY + "\nUNIT-1",
        path="explicit-synthetic-workflow-unit",
        at=datetime.now(UTC),
    )
    store.db.execute(
        "INSERT INTO agent_runs(run_id,created_at,state,question_sha256,"
        "reserved_cny,estimated_cny) "
        "VALUES ('prior-timeout','2026-10-25T18:06:10Z','failed','prior-sha',0.25,0)"
    )
    store.db.execute(
        "INSERT INTO agent_runs(run_id,created_at,state,question_sha256,"
        "reserved_cny,estimated_cny) "
        "VALUES ('prior-success','2026-10-25T18:10:11Z','completed','prior-sha2',0.25,0.025244)"
    )
    return notice, store.db.rows("SELECT * FROM agent_runs ORDER BY run_id")


@pytest.fixture
def local(settings, prepared):
    app = create_workflow_app(settings, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)) as client:
        yield app, client, prepared


def test_signed_login_seed_csrf_idempotence_and_unchanged_existing_ledger(local):
    app, client, (_, baseline) = local
    anonymous = client.get("/api/auth/me").json()
    assert anonymous["authenticated"] is False
    assert anonymous["demo"]["kind"] == "local_workflow"
    assert "虚构" in anonymous["demo"]["description"]
    assert client.get(CONTEXT).status_code == 401
    me, headers = login(client)
    assert client.get(CONTEXT).json()["seeded"] is False
    assert client.post(CONTEXT).status_code == 403
    assert (
        client.post(CONTEXT, headers={**headers, "Origin": "http://evil.test"}).status_code == 403
    )
    assert app.state.store.db.rows("SELECT * FROM company_objects") == []
    seed = client.post(CONTEXT, headers=headers)
    assert seed.status_code == 200, seed.text
    seed = seed.json()
    assert seed["kind"] == "local_workflow" and seed["seeded"] is True
    assert seed["reviewer_subject_id"] == me["user"]["subject_id"]
    assert "虚构" in seed["company_name"]
    assert len(seed["materials"]) == len(seed["additional_material_revisions"]) == 2
    assert client.post(CONTEXT, headers=headers).json() == seed
    assert client.get(CONTEXT).json() == seed
    rows = app.state.store.db.rows("SELECT * FROM company_objects")
    assert len(rows) == 3
    profile = app.state.company.repo.get(
        ToolContext(seed["reviewer_subject_id"]), seed["profile_revision"]["object_id"]
    )
    assert profile["payload"]["provenance_kind"] == "engineering_fixture"
    assert not any(fact["status"] == "document_supported" for fact in profile["payload"]["facts"])
    assert any(
        fact["status"] == "unknown" and fact["evidence"] for fact in profile["payload"]["facts"]
    )
    status = client.get("/api/demo/workflow/status").json()
    assert status["live_calls_enabled"] is False
    assert status["jobs"]["configured"] is False
    assert app.state.store.db.rows("SELECT * FROM agent_runs ORDER BY run_id") == baseline


def test_login_reentry_and_one_use_signed_callback(local):
    _, client, _ = local
    first, headers = login(client)
    seed = client.post(CONTEXT, headers=headers).json()
    second, headers = login(client)
    assert second["user"]["subject_id"] == first["user"]["subject_id"]
    assert second["csrf_token"] != first["csrf_token"]
    assert client.post(CONTEXT, headers=headers).json() == seed
    start = client.get("/api/auth/login", follow_redirects=False)
    callback = start.headers["location"]
    client.get(callback, follow_redirects=False)
    assert client.get("/api/auth/me").json()["authenticated"] is True
    client.get(callback, follow_redirects=False)
    assert client.get("/api/auth/me").json()["authenticated"] is False
    third, headers = login(client)
    assert third["user"]["subject_id"] == first["user"]["subject_id"]
    assert client.get(CONTEXT).json() == seed


def test_expired_identity_can_reauthenticate_without_replacing_seed(local):
    app, client, _ = local
    me, headers = login(client)
    seed = client.post(CONTEXT, headers=headers).json()
    app.state.store.db.execute(
        "UPDATE enterprise_subjects SET fresh_until=0 WHERE owner_id=:owner",
        {"owner": me["user"]["subject_id"]},
    )
    assert client.get(CONTEXT).status_code == 401
    login(client)
    assert client.get(CONTEXT).json() == seed


def test_seed_survives_restart_and_does_not_overwrite_user_edits(settings, prepared):
    with TestClient(
        create_workflow_app(settings, origin=ORIGIN),
        base_url=ORIGIN,
        client=("127.0.0.1", 50000),
    ) as client:
        _, headers = login(client)
        seed = client.post(CONTEXT, headers=headers).json()
        ref = seed["profile_revision"]
        updated = client.put(
            "/api/company/profiles/" + ref["object_id"],
            headers=headers,
            json={
                "expected_version": ref["version_id"],
                "payload": {"display_name": "用户编辑后的虚构示例", "facts": []},
            },
        )
        assert updated.status_code == 200, updated.text
        head = updated.json()
    app = create_workflow_app(settings, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)) as client:
        _, headers = login(client)
        assert client.get(CONTEXT).json() == seed
        assert client.post(CONTEXT, headers=headers).json() == seed
        current = client.get("/api/company/profiles/" + ref["object_id"]).json()
        assert current["version_id"] == head["version_id"]
        assert current["payload"]["display_name"] == "用户编辑后的虚构示例"
        assert len(app.state.store.db.rows("SELECT * FROM company_objects")) == 3
        assert app.state.store.db.rows("SELECT * FROM agent_runs ORDER BY run_id") == prepared[1]


def test_non_loopback_client_and_rebound_host_are_rejected(settings, prepared):
    app = create_workflow_app(settings, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN, client=("203.0.113.3", 50000)) as client:
        assert client.get("/api/auth/login").status_code == 403
    app = create_workflow_app(settings, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/auth/login", headers={"Host": "evil.test"}).status_code == 403


def test_real_jobs_review_and_export_preserve_unknowns_and_existing_costs(local):
    app, client, (notice, baseline) = local
    _, headers = login(client)
    seed = client.post(CONTEXT, headers=headers).json()
    # The override exists only in this test; the local application contains no
    # authored procurement answers or synthetic model implementation.
    app.state.screening.model_override = FunctionModel(
        functools.partial(scripted_response, state="information_insufficient"),
        model_name="offline-unit",
    )
    jobs = app.state.jobs
    try:
        jobs.start()
        assert client.get("/api/demo/workflow/status").json()["jobs"]["configured"] is True
        batch = client.post(
            "/api/screening/candidates",
            headers=headers,
            json={"profile_revision": seed["profile_revision"], "filters": {}},
        )
        assert batch.status_code == 201, batch.text
        batch = batch.json()
        submitted = client.post(
            "/api/jobs/runs",
            headers={**headers, "Idempotency-Key": "workflow-integration"},
            json={
                "profile_revision": seed["profile_revision"],
                "project_id": notice.project_id,
                "additional_material_revisions": seed["additional_material_revisions"],
            },
        )
        assert submitted.status_code == 202, submitted.text
        run_id = submitted.json()["run_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = client.get("/api/jobs/runs/" + run_id)
            assert current.status_code == 200, current.text
            current = current.json()
            if current["status"] == "waiting_review":
                break
            assert current["status"] not in {"failed", "timed_out", "interrupted"}, current
            time.sleep(0.05)
        assert current["status"] == "waiting_review", current
        assert current["usage"]["offline"] is True
        assert current["usage"]["model_calls"] > 0
        created = client.post(
            "/api/reviews/cases",
            headers=headers,
            json={
                "run_id": run_id,
                "candidate_batch_id": batch["batch_id"],
                "reviewer_ids": [seed["reviewer_subject_id"]],
                "idempotency_key": "workflow-case",
            },
        )
        assert created.status_code == 201, created.text
        case = created.json()
        assert case["items"]
        assert all(
            item["agent_assessment"]["status"] == "information_insufficient"
            for item in case["items"]
        )
        path = "/api/reviews/cases/" + case["case_id"]
        for index, item in enumerate(case["items"]):
            decision = client.post(
                path + "/decisions",
                headers=headers,
                json=command(case, item, key=f"demo-decision-{index}"),
            )
            assert decision.status_code == 200, decision.text
            case = decision.json()
        closed = client.post(
            path + "/close",
            headers=headers,
            json={
                "expected_version": case["review_version"],
                "idempotency_key": "workflow-close",
                "reason": "工程测试：保持信息不足，已逐项人工查看。",
            },
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"
        exported = client.get(path + "/export")
        assert exported.status_code == 200, exported.text
        assert exported.json()["case"]["events"]
        assert "raw_model_outputs" not in exported.text
        history = client.get("/api/reviews/cases")
        assert history.status_code == 200 and case["case_id"] in history.text
        assert app.state.store.db.rows("SELECT * FROM agent_runs ORDER BY run_id") == baseline
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if client.get("/api/jobs/runs/" + run_id).json()["status"] == "completed":
                break
            time.sleep(0.05)
        assert client.get("/api/jobs/runs/" + run_id).json()["status"] == "completed"
    finally:
        jobs.close()
        DBOS.destroy(destroy_registry=True)


def test_screening_admission_counts_existing_qa_failure_and_success(local):
    app, _, (_, baseline) = local
    # Test only the actual admission transaction; never invoke a provider.
    limits = app.state.screening.settings.model_copy(update={"budget_cny": Decimal("0.4")})
    guard = GuardedModel(
        FunctionModel(scripted_response),
        db=app.state.store.db,
        settings=limits,
        adapter=app.state.screening.sources,
        context=None,
        bundle=None,
        run_id="workflow-budget-probe",
        offline=False,
    )
    with pytest.raises(CompanyError, match="screening_budget_exhausted"):
        guard._reserve("must-not-be-written", Decimal("0.337536"))
    assert app.state.store.db.rows("SELECT * FROM agent_runs ORDER BY run_id") == baseline


@pytest.mark.parametrize("checkpoint", [2, 4])
def test_retry_recovers_committed_seed_after_manifest_checkpoint_failure(
    local, monkeypatch, checkpoint
):
    app, client, _ = local
    me, headers = login(client)
    seed = app.state.workflow_seed
    original = seed._save
    calls = 0

    def fail_once(value):
        nonlocal calls
        calls += 1
        if calls == checkpoint:
            raise OSError("UNIT simulated checkpoint disk failure")
        return original(value)

    monkeypatch.setattr(seed, "_save", fail_once)
    with pytest.raises(OSError, match="checkpoint"):
        seed.create(me["user"]["subject_id"])
    result = client.post(CONTEXT, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["seeded"] is True
    assert len(app.state.store.db.rows("SELECT * FROM company_objects")) == 3
    assert len(app.state.store.db.rows("SELECT * FROM company_profile_revisions")) == 1
    assert len(app.state.store.db.rows("SELECT * FROM company_material_revisions")) == 2


def test_invalid_or_symlink_seed_manifest_does_not_replace_objects(local, tmp_path):
    app, client, _ = local
    me, headers = login(client)
    seed = client.post(CONTEXT, headers=headers).json()
    before = app.state.store.db.rows("SELECT * FROM company_objects ORDER BY object_id")
    path = app.state.workflow_seed.root / (me["user"]["subject_id"] + ".json")
    path.write_text('{"version": 9}')
    assert client.get(CONTEXT).status_code == 409
    assert client.post(CONTEXT, headers=headers).status_code == 409
    path.unlink()
    outside = tmp_path / "outside-manifest"
    outside.write_text("UNIT outside file must remain unchanged")
    path.symlink_to(outside)
    assert client.post(CONTEXT, headers=headers).status_code == 409
    assert outside.read_text() == "UNIT outside file must remain unchanged"
    assert app.state.store.db.rows("SELECT * FROM company_objects ORDER BY object_id") == before
    assert seed["seeded"] is True


@pytest.mark.parametrize("broken", [{"materials": [{}]}, {"profile_revision": None}])
def test_malformed_references_return_409_and_keep_public_demo_identity(local, broken):
    app, client, _ = local
    me, headers = login(client)
    client.post(CONTEXT, headers=headers)
    path = app.state.workflow_seed.root / (me["user"]["subject_id"] + ".json")
    import json

    value = json.loads(path.read_text())
    value.update(broken)
    path.write_text(json.dumps(value))
    assert client.get(CONTEXT).status_code == 409
    assert client.post(CONTEXT, headers=headers).status_code == 409
    assert client.get("/api/auth/me").json()["demo"]["kind"] == "local_workflow"
    assert len(app.state.store.db.rows("SELECT * FROM company_objects")) == 3


def test_preview_settings_ignore_inherited_key_and_live_gate(settings, prepared, monkeypatch):
    for key, value in {
        "DEEPSEEK_API_KEY": "UNIT_INHERITED_KEY_MUST_BE_IGNORED",
        "BIDOPS_SCREENING_LIVE_CALLS_ENABLED": "true",
        "BIDOPS_SCREENING_LEDGER_VERIFIED": "true",
        "BIDOPS_SCREENING_LEDGER_RESTORE_TOKEN": "UNIT_NOT_A_RESTORE_TOKEN",
        "BIDOPS_DEMO_ENABLED": "true",
        "BIDOPS_DEMO_MAX_COST_CNY": "10",
        "BIDOPS_DEMO_BUDGET_RESTORE_TOKEN": "UNIT_NOT_A_RESTORE_TOKEN",
        "BIDOPS_INGESTION_ENABLED": "true",
        "BIDOPS_OPERATIONS_ENABLED": "true",
    }.items():
        monkeypatch.setenv(key, value)
    app = create_workflow_app(settings, origin=ORIGIN)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)):
        assert app.state.screening.settings.api_key is None
        assert app.state.screening.settings.live_calls_enabled is False
        assert app.state.screening.settings.ledger_verified is False
        assert app.state.screening.settings.ledger_restore_token is None
        assert app.state.agent_service.settings.api_key is None
        assert app.state.agent_service.settings.enabled is False
        assert app.state.agent_service.settings.max_cost_cny == 1
        assert app.state.agent_service.settings.budget_restore_token is None
        assert app.state.ingestion.settings.enabled is False


def test_live_settings_use_explicit_key_alias_and_shared_cap_without_calls(
    settings, prepared, monkeypatch
):
    from bidops.jobs.runtime import JobsService

    monkeypatch.setattr(JobsService, "start", lambda self: None)
    app = create_workflow_app(settings, origin=ORIGIN, live=True, api_key="UNIT_SYNTHETIC_KEY")
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)):
        assert app.state.screening.status()["configured"] is True
        assert app.state.agent_service.settings.api_key is not None
        assert app.state.screening.settings.api_key is not None
        assert app.state.screening.settings.budget_cny == Decimal("1")
        assert app.state.agent_service.settings.max_cost_cny == 1
        assert app.state.screening.settings.ledger_restore_token is None
        assert app.state.agent_service.settings.budget_restore_token is None
        assert app.state.store.db.rows("SELECT * FROM agent_runs ORDER BY run_id") == prepared[1]
