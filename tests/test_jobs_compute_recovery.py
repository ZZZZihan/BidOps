"""Actual two-compute SQLite recovery/ledger/backup, with local models only."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest
from bidops.company.authorization import CompanyError
from bidops.enterprise.backup import backup, restore
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import CALCULATION_QUEUE, QUEUE, JobsService
from bidops.qualification.service import ScreeningService
from dbos import DBOS
from pydantic_ai.models.function import FunctionModel
from test_company_access import ADMIN, ALICE, BOB
from test_jobs_backup import reopened
from test_jobs_runtime import wait_for
from test_qualification_service import scripted_response
from test_qualification_service import service as service
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests/fixtures/jobs_compute_worker.py"


def bootstrap(service, settings, tmp_path):
    _, notice, profile, _, _ = service
    directory = tmp_path / "two-compute-process-evidence"
    directory.mkdir(mode=0o700)
    (directory / "bootstrap.json").write_text(
        json.dumps(
            {
                "database_url": settings.database_url,
                "data_dir": str(settings.data_dir),
                "request": {"profile_revision": profile, "project_id": notice.project_id},
            }
        )
    )
    return directory


def child(directory, mode):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DBOS_", "OTEL_", "BIDOPS_", "DEEPSEEK_"))
    }
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT / "backend"), str(ROOT / "tests")])
    path = directory / f"{mode}.log"
    with path.open("w") as output:
        try:
            result = subprocess.run(
                [sys.executable, "-u", str(WORKER), str(directory), mode],
                cwd=ROOT,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=65,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"Bounded {mode} worker timed out; inspect {path}")
    return result


def events(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]


def snapshot(service):
    db = service[0].repository.db
    return {
        "jobs": db.rows("SELECT * FROM durable_jobs ORDER BY run_id"),
        "attempts": db.rows("SELECT * FROM durable_model_attempts ORDER BY run_id,seq"),
        "results": db.rows("SELECT * FROM qualification_results ORDER BY result_id"),
        "ledger": db.rows("SELECT * FROM agent_runs ORDER BY run_id"),
    }


def recover_three(service, settings, tmp_path):
    directory = bootstrap(service, settings, tmp_path)
    first = child(directory, "crash")
    assert first.returncode == -signal.SIGKILL, (directory / "crash.log").read_text()
    crashed = snapshot(service)
    assert len(crashed["jobs"]) == 3 and not crashed["results"]
    assert len(crashed["attempts"]) == len(crashed["ledger"]) == 2
    assert all(row["status"] == "started" for row in crashed["attempts"])
    assert all(row["state"] == "started" for row in crashed["ledger"])
    second = child(directory, "recover")
    assert second.returncode == 0, (directory / "recover.log").read_text()
    return directory, crashed


def test_two_inflight_sigkill_recovers_native_queue_context_results_and_retained_costs(
    service, settings, tmp_path
):
    directory, crashed = recover_three(service, settings, tmp_path)
    recovered = json.loads((directory / "recover.json").read_text())
    after = snapshot(service)
    assert recovered["queued_child_observed"] and recovered["peak_active"] == 2
    assert len(after["jobs"]) == len(after["results"]) == 3
    assert len({job["result_id"] for job in recovered["jobs"]}) == 3
    assert all(job["run_id"] != job["result_id"] for job in recovered["jobs"])
    assert all(job["status"] == "waiting_review" for job in recovered["jobs"])
    assert len(after["attempts"]) == len(after["ledger"]) == 29
    assert Counter(row["status"] for row in after["attempts"]) == {
        "unknown": 2,
        "response_received": 27,
    }
    old = {row["run_id"]: row for row in crashed["ledger"]}
    assert {row["run_id"]: row for row in after["ledger"] if row["run_id"] in old} == old
    completed = [row for row in after["ledger"] if row["state"] == "completed"]
    assert sum(Decimal(str(row["estimated_cny"])) for row in completed) == Decimal("0.00756")
    assert all(Decimal(str(row["reserved_cny"])) > 0 for row in old.values())

    # Actual saved model receipts retain each concurrent task's own marker;
    # neither ACTIVE_MODEL nor result ownership may bleed into another run.
    ids = {job["run_id"] for job in recovered["jobs"]}
    for run_id in ids:
        attempts = [row for row in after["attempts"] if row["run_id"] == run_id]
        assert len(attempts) in {9, 10}
        confirmed = [row for row in attempts if row["status"] == "response_received"]
        assert len(confirmed) == 9
        for row in confirmed[1:]:
            body = service[0].repository.db.rows(
                "SELECT response_json FROM durable_attempt_bodies WHERE run_id=:run AND seq=:seq",
                {"run": run_id, "seq": row["seq"]},
            )[0]["response_json"]
            assert "isolated:" + run_id in body
            assert all("isolated:" + other not in body for other in ids - {run_id})
    native = recovered["native"]
    assert len(native) == 6
    assert all(
        row[2] == (CALCULATION_QUEUE if row[0].endswith("-calculation") else QUEUE)
        for row in native
    )
    third = child(directory, "read-saved")
    assert third.returncode == 0, (directory / "read-saved.log").read_text()
    reread = json.loads((directory / "read-saved.json").read_text())
    assert reread["jobs"] == recovered["jobs"] and not reread["calls"]
    assert snapshot(service) == after
    observed = events(directory)
    starts = [item for item in observed if item["event"] == "starting"]
    assert len({item["pid"] for item in starts}) == 3
    assert len({item["application_version"] for item in starts}) == 1
    assert all(item["real_model_calls"] == 0 and item["actual_cost_cny"] == "0" for item in starts)
    assert Counter(item["mode"] for item in observed if item["event"] == "local_model_call") == {
        "crash": 2,
        "recover": 27,
    }


def test_two_native_calculations_compete_for_one_reservation_without_releasing_unknown(
    service, settings, tmp_path
):
    directory = bootstrap(service, settings, tmp_path)
    result = child(directory, "budget")
    assert result.returncode == 0, (directory / "budget.log").read_text()
    summary = json.loads((directory / "budget.json").read_text())
    saved = snapshot(service)
    assert len(saved["jobs"]) == 3
    assert len(saved["attempts"]) == len(saved["ledger"]) == 1
    assert summary["peak_active"] == sum(summary["calls"].values()) == 1
    assert set(summary["error_codes"]) == {"screening_budget_exhausted", "screening_model_failed"}
    assert summary["error_codes"].count("screening_budget_exhausted") == 2
    assert saved["attempts"][0]["status"] == "unknown"
    assert saved["ledger"][0]["state"] == "unconfirmed"
    assert Decimal(str(saved["ledger"][0]["reserved_cny"])) == Decimal(summary["reservation_cny"])
    assert any(item["event"] == "competing_reservation_rejected" for item in events(directory))


def test_two_compute_v4_restore_keeps_queue_mapping_acl_quarantine_and_budget_barrier(
    service, settings, tmp_path
):
    directory, _ = recover_three(service, settings, tmp_path)
    old = snapshot(service)
    bundle, destination = tmp_path / "bundle", tmp_path / "restored"
    backup(settings, bundle)
    metadata = json.loads((bundle / "manifest.json").read_text())
    assert metadata["version"] == 4
    with closing(sqlite3.connect(bundle / "private/jobs/dbos.sqlite3")) as conn:
        assert conn.execute(
            "SELECT concurrency,worker_concurrency FROM queues WHERE name=?",
            (CALCULATION_QUEUE,),
        ).fetchone() == (2, 2)
        assert (
            conn.execute(
                "SELECT count(*) FROM workflow_status WHERE queue_name=?", (CALCULATION_QUEUE,)
            ).fetchone()[0]
            == 3
        )
    report = restore(bundle, destination)
    assert report["quarantined_jobs"] == 3 and report["budget_review_required"]
    db, repository = reopened(destination)
    provider_calls = []

    def provider(messages, info):
        provider_calls.append(True)
        return scripted_response(messages, info)

    screening = ScreeningService(
        repository.sources.company,
        repository.sources.notices,
        service[0].settings.model_copy(
            update={"live_calls_enabled": True, "ledger_verified": True}
        ),
        FunctionModel(provider, model_name="deepseek-flash"),
    )
    screening._model = lambda: (screening.model_override, False)
    runtime = JobsService(
        screening, destination, JobsSettings(_env_file=None, enabled=True, compute_concurrency=2)
    )
    try:
        runtime.start()
        ids = json.loads((directory / "run-ids.json").read_text())
        assert all(repository.get_system(run_id)["quarantined"] for run_id in ids)
        for actor in (ALICE, BOB, ADMIN):
            with pytest.raises(CompanyError):
                runtime.read(actor, ids[0])
        assert db.rows("SELECT * FROM agent_runs ORDER BY run_id") == old["ledger"]
        db.execute("UPDATE enterprise_subjects SET fresh_until=:now", {"now": time.time() + 300})
        with pytest.raises(CompanyError):
            runtime.read(ALICE, ids[0])
        original = repository.get_system(ids[0])
        for ref in [
            original["manifest"]["profile_revision"],
            *original["manifest"]["material_revisions"],
        ]:
            repository.auth.reauthorize(
                ADMIN, ref["object_id"], ALICE.owner_id, ["read", "write", "grant", "delete"]
            )
        # Object reauthorization alone must not release any restored task.
        assert all(repository.get_system(run_id)["quarantined"] for run_id in ids)
        with pytest.raises(CompanyError):
            runtime.read(ALICE, ids[0])
        payload = json.loads((directory / "bootstrap.json").read_text())["request"]
        for token in (None, "0" * 32):
            screening.settings.ledger_restore_token = token
            created = runtime.submit(ALICE, payload, "barrier-" + str(token))
            stopped = wait_for(runtime, created["run_id"], "failed")
            assert stopped["error_code"] == "screening_budget_review_required"
        assert not provider_calls
        assert db.rows("SELECT * FROM agent_runs ORDER BY run_id") == old["ledger"]
        assert all(repository.get_system(run_id)["quarantined"] for run_id in ids)
    finally:
        runtime.close()
        DBOS.destroy(destroy_registry=True)
        db.engine.dispose()
