"""Feature-independent company maintenance and live native DBOS erasure; offline data."""

import asyncio
import builtins
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Event

import pytest
from bidops.company.authorization import CompanyError
from bidops.company.retention import prune_company_materials
from bidops.jobs import retention
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import JobsService
from bidops.qualification.repository import QualificationRepository
from dbos import DBOS, DBOSClient
from pydantic_ai.models.function import FunctionModel
from test_company_access import ALICE
from test_jobs_backup import (
    SECRET_INPUT,
    SECRET_OUTPUT,
    add_calculation,
    add_checkpoint,
    attempt,
    running,
)
from test_jobs_backup import case as case
from test_jobs_backup import system_template as system_template
from test_jobs_repository import assessment_group, review
from test_jobs_runtime import wait_for
from test_qualification_service import scripted_response
from test_qualification_service import service as service
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources

SECRET_REVIEW = "SYNTHETIC_PRIVATE_REVIEW_REASON_754"


def seed(case):
    repo, job, system, _ = case
    current = running(case)
    attempt(case)
    result = QualificationRepository(repo.db, repo.sources).save(
        ALICE,
        job["manifest"],
        {
            "kind": "checklist",
            "state": "completed",
            "result_revision": "retention-v1",
            "groups": [assessment_group(repo, job, "retention-v1")],
        },
        raw_model_outputs=[{"fixture": SECRET_OUTPUT}],
        result_id="retention-result",
    )
    waiting = repo.transition(
        job["run_id"],
        current["state_version"],
        "waiting_review",
        progress={"private_fixture": SECRET_INPUT},
        result_id=result["result_id"],
        result_revision="retention-v1",
    )
    event = review(waiting)
    event["command"]["reason"] = SECRET_REVIEW
    repo.record_review_fixture(job["run_id"], event, waiting["state_version"])
    add_calculation(system, job["run_id"])
    add_checkpoint(system, job["run_id"])
    add_checkpoint(system, job["run_id"] + "-calculation")
    return (
        repo.db.rows("SELECT * FROM agent_runs"),
        repo.db.rows("SELECT * FROM durable_model_attempts"),
        repo.db.rows("SELECT * FROM durable_review_fixtures"),
    )


def expire(case):
    case[0].db.execute(
        "UPDATE company_objects SET retention_until=1 WHERE object_id=:id",
        {"id": case[3]["object_id"]},
    )


def assert_business_erased(case, preserved):
    repo, job, _, _ = case
    for table in (
        "durable_job_inputs",
        "durable_attempt_bodies",
        "durable_review_fixture_bodies",
        "qualification_result_bodies",
    ):
        assert repo.db.rows(f"SELECT * FROM {table}") == []
    assert repo.db.rows("SELECT * FROM agent_runs") == preserved[0]
    assert repo.db.rows("SELECT * FROM durable_model_attempts") == preserved[1]
    assert repo.db.rows("SELECT * FROM durable_review_fixtures") == preserved[2]
    state = repo.db.rows("SELECT * FROM durable_job_state")[0]
    assert state["purge_state"] == "purged" and state["progress_json"] == "{}"
    assert state["result_id"] == "retention-result"
    with pytest.raises(CompanyError):
        repo.get(ALICE, job["run_id"])
    with pytest.raises(CompanyError):
        repo.require_execution(job["run_id"])


def assert_native_erased(path):
    with closing(sqlite3.connect(path)) as conn:
        for table in ("workflow_status", "workflow_input", "workflow_output", "operation_outputs"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_company_maintenance_erases_full_job_closure_while_jobs_are_disabled(case, monkeypatch):
    assert JobsSettings(_env_file=None).enabled is False
    preserved = seed(case)
    expire(case)

    def no_worker(*args, **kwargs):
        pytest.fail("retention launched a worker")

    monkeypatch.setattr(DBOS, "launch", no_worker)
    report = prune_company_materials(case[0].sources.company)
    assert report["status"] == "PASS" and report["purged_job_ids"] == [case[1]["run_id"]]
    assert report["deleted_job_history_ids"] == [case[1]["run_id"]]
    assert report["job_failure_ids"] == report["job_failures"] == []
    assert_business_erased(case, preserved)
    assert_native_erased(case[2])


@pytest.mark.parametrize("failure", ["overlay_unavailable", "native_delete_failed"])
def test_backend_failure_reports_failure_and_next_maintenance_retries_all_purged(
    case, monkeypatch, failure
):
    preserved = seed(case)
    expire(case)
    with monkeypatch.context() as patch:
        if failure == "overlay_unavailable":

            def broken(*args, **kwargs):
                raise ModuleNotFoundError("synthetic unavailable DBOS overlay")

            patch.setattr(retention, "_client", broken)
        else:

            def broken(*args, **kwargs):
                raise OSError("SYNTHETIC provider secret must not enter report")

            patch.setattr(DBOSClient, "delete_workflow", broken)
        report = prune_company_materials(case[0].sources.company)
    assert report["status"] == "FAIL" and report["job_failure_ids"] == [case[1]["run_id"]]
    assert "provider secret" not in str(report)
    assert_business_erased(case, preserved)
    # There are no newly expired inputs left, but framework history still needs deletion.
    assert case[0].prune()["purged_ids"] == []
    retried = prune_company_materials(case[0].sources.company)
    assert retried["status"] == "PASS" and retried["purged_job_ids"] == [case[1]["run_id"]]
    assert retried["deleted_job_history_ids"] == [case[1]["run_id"]]
    assert_business_erased(case, preserved)
    assert_native_erased(case[2])


def test_private_unlink_failure_occurs_after_job_and_framework_erasure(case, monkeypatch):
    preserved = seed(case)
    expire(case)
    company = case[0].sources.company
    original = Path.unlink

    def failed_unlink(path, *args, **kwargs):
        if path.parent == company.materials.root / "blob":
            raise PermissionError("synthetic file erasure unavailable")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", failed_unlink)
        report = prune_company_materials(company)
    assert report["status"] == "FAIL" and case[3]["object_id"] in report["failure_ids"]
    assert report["job_failure_ids"] == []
    assert_business_erased(case, preserved)
    assert_native_erased(case[2])
    assert prune_company_materials(company)["status"] == "PASS"


@pytest.mark.parametrize("old_schema", [False, True])
def test_empty_history_does_not_import_overlay_or_create_system_database(
    domain, monkeypatch, old_schema
):
    if old_schema:
        domain.db.execute("DROP TABLE durable_jobs")
    imported = builtins.__import__

    def no_overlay(name, *args, **kwargs):
        if name == "dbos" or name.startswith("dbos."):
            pytest.fail("empty/base installation imported the DBOS overlay")
        return imported(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_overlay)
    report = prune_company_materials(domain)
    assert report["status"] == "PASS" and report["purged_job_ids"] == []
    assert not (domain.materials.data_dir / "private/jobs").exists()


@pytest.mark.parametrize("damage", ["missing", "invalid", "symlink"])
def test_missing_or_invalid_system_db_still_erases_business_bodies_and_reports_failure(
    case, damage
):
    preserved = seed(case)
    expire(case)
    system = case[2]
    system.unlink()
    if damage == "invalid":
        with closing(sqlite3.connect(system)) as conn:
            conn.execute("CREATE TABLE unrelated(value TEXT)")
    elif damage == "symlink":
        other = system.parent / "outside.sqlite3"
        other.write_bytes(b"SYNTHETIC DO NOT MODIFY")
        system.symlink_to(other)
    report = prune_company_materials(case[0].sources.company)
    assert report["status"] == "FAIL" and report["job_failure_ids"] == [case[1]["run_id"]]
    assert_business_erased(case, preserved)
    if damage == "missing":
        assert not system.exists()
    elif damage == "symlink":
        assert other.read_bytes() == b"SYNTHETIC DO NOT MODIFY"
    else:
        with closing(sqlite3.connect(system)) as conn:
            assert conn.execute("SELECT name FROM sqlite_master").fetchall() == [("unrelated",)]


def test_live_waiting_review_maintenance_removes_native_private_checkpoints_and_fixture(
    service, settings
):
    screening, notice, profile, material, _ = service
    runtime = JobsService(screening, settings.data_dir, JobsSettings(enabled=True))
    try:
        runtime.start()
        job = runtime.submit(
            ALICE, {"profile_revision": profile, "project_id": notice.project_id}, "expire-review"
        )
        waiting = wait_for(runtime, job["run_id"], "waiting_review")
        event = review(waiting)
        event["command"]["decision"] = "request_materials"
        event["command"]["reason"] = SECRET_REVIEW
        # Persist the event body without sending it; retention must erase it too.
        runtime.repository.record_review_fixture(job["run_id"], event, waiting["state_version"])
        before = screening.repository.db.rows("SELECT * FROM durable_model_attempts")
        with closing(sqlite3.connect(runtime.path)) as conn:
            assert conn.execute("SELECT count(*) FROM operation_outputs").fetchone()[0] > 0
        screening.repository.db.execute(
            "UPDATE company_objects SET retention_until=1 WHERE object_id=:id",
            {"id": material["object_id"]},
        )
        report = prune_company_materials(screening.sources.company)
        assert report["status"] == "PASS" and report["purged_job_ids"] == [job["run_id"]]
        assert screening.repository.db.rows("SELECT * FROM durable_model_attempts") == before
        assert screening.repository.db.rows("SELECT * FROM qualification_result_bodies") == []
        assert screening.repository.db.rows("SELECT * FROM durable_review_fixture_bodies") == []
        with pytest.raises(CompanyError):
            runtime.read(ALICE, job["run_id"])
    finally:
        runtime.close()
        DBOS.destroy(destroy_registry=True)
    assert_native_erased(runtime.path)


def test_live_inflight_model_cannot_recreate_bodies_results_or_checkpoint_after_expiry(
    service, settings, monkeypatch
):
    screening, notice, profile, material, _ = service
    entered, release, finished = Event(), Event(), Event()
    calls = []

    async def blocked(messages, info):
        calls.append(1)
        entered.set()
        await asyncio.to_thread(release.wait, 10)
        return scripted_response(messages, info)

    screening.model_override = FunctionModel(blocked, model_name="offline-unit")
    runtime = JobsService(screening, settings.data_dir, JobsSettings(enabled=True))
    original = runtime.repository.finish_attempt

    def late_receipt(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(runtime.repository, "finish_attempt", late_receipt)
    try:
        runtime.start()
        job = runtime.submit(
            ALICE, {"profile_revision": profile, "project_id": notice.project_id}, "expire-inflight"
        )
        assert entered.wait(10), "worker did not reach the actual local model step"
        ledger = screening.repository.db.rows("SELECT * FROM agent_runs")
        screening.repository.db.execute(
            "UPDATE company_objects SET retention_until=1 WHERE object_id=:id",
            {"id": material["object_id"]},
        )
        report = prune_company_materials(screening.sources.company)
        assert report["status"] == "PASS"
        release.set()
        assert finished.wait(10), "late model receipt never reached the purged business boundary"
        with pytest.raises(CompanyError):
            runtime.repository.require_execution(job["run_id"])
        assert calls == [1]
        assert screening.repository.db.rows("SELECT * FROM agent_runs") == ledger
    finally:
        release.set()
        runtime.close()
        DBOS.destroy(destroy_registry=True)
    for table in ("durable_job_inputs", "durable_attempt_bodies", "qualification_result_bodies"):
        assert screening.repository.db.rows(f"SELECT * FROM {table}") == []
    assert screening.repository.db.rows("SELECT * FROM qualification_results") == []
    assert_native_erased(runtime.path)
