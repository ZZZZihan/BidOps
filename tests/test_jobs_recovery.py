"""Real SIGKILL -> fresh interpreter -> saved read, with network-denied local models."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest
from test_qualification_service import service as service
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests/fixtures/jobs_worker.py"
WINDOWS = [
    "before_model",
    "response_before_receipt",
    "receipt_before_accounting",
    "accounting_before_checkpoint",
    "before_result_commit",
    "after_result_commit",
]


def events(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]


def calls(directory):
    return [item for item in events(directory) if item["event"] == "local_model_call"]


def worker(directory, mode, *, window, synthetic_ledger):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DBOS_", "OTEL_", "BIDOPS_", "DEEPSEEK_"))
    }
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT / "backend"), str(ROOT / "tests")])
    arguments = [sys.executable, "-u", str(WORKER), str(directory), mode, "--window", window]
    if synthetic_ledger:
        arguments.append("--synthetic-ledger")
    with (directory / f"{mode}.log").open("w") as log:
        try:
            return subprocess.run(
                arguments,
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=55,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"{mode} child timed out; inspect {directory / f'{mode}.log'}")


def bootstrap(service, settings, tmp_path):
    _, notice, profile, _, _ = service
    directory = tmp_path / "process-evidence"
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


def snapshot(service):
    db = service[0].repository.db
    return {
        "jobs": db.rows("SELECT * FROM durable_jobs"),
        "attempts": db.rows("SELECT * FROM durable_model_attempts ORDER BY seq"),
        "results": db.rows("SELECT * FROM qualification_results"),
        "ledger": db.rows("SELECT * FROM agent_runs ORDER BY rowid"),
    }


def assert_same_business_and_saved_result(directory, service, before_third):
    recovered = json.loads((directory / "recover.json").read_text())
    reread = json.loads((directory / "read-saved.json").read_text())
    assert all(
        reread[key] == recovered[key]
        for key in (
            "run_id",
            "status",
            "result_id",
            "result_revision",
            "result_body_sha256",
            "inputs_manifest_sha256",
            "usage",
        )
    )
    assert recovered["run_id"] != recovered["result_id"]
    assert recovered["status"] == "waiting_review"
    assert snapshot(service) == before_third
    assert len(before_third["jobs"]) == len(before_third["results"]) == 1
    starts = [item for item in events(directory) if item["event"] == "starting"]
    assert len({item["pid"] for item in starts}) == 3
    assert len({item["application_version"] for item in starts}) == 1
    assert all(item["real_model_calls"] == 0 and item["actual_cost_cny"] == "0" for item in starts)
    assert any("model" in row[2] and row[3] and not row[4] for row in recovered["checkpoints"])


@pytest.mark.parametrize("window", WINDOWS)
def test_sigkill_each_business_boundary_recovers_native_steps_and_one_result(
    service, settings, tmp_path, window
):
    directory = bootstrap(service, settings, tmp_path)
    first = worker(directory, "crash", window=window, synthetic_ledger=True)
    assert first.returncode == -signal.SIGKILL, (directory / "crash.log").read_text()
    assert [item["window"] for item in events(directory) if item["event"] == "sigkill"] == [window]
    crashed = snapshot(service)
    assert len(crashed["jobs"]) == 1
    assert len(crashed["results"]) == (window == "after_result_commit")
    if window in {"before_model", "response_before_receipt"}:
        assert crashed["attempts"][0]["status"] == "started"
        assert crashed["ledger"][0]["state"] == "started"
    second = worker(directory, "recover", window=window, synthetic_ledger=True)
    assert second.returncode == 0, (directory / "recover.log").read_text()
    recovered = snapshot(service)
    observed_calls = len(calls(directory))
    assert observed_calls == (10 if window == "response_before_receipt" else 9)
    unknown = [a for a in recovered["attempts"] if a["status"] == "unknown"]
    assert len(unknown) == (window in {"before_model", "response_before_receipt"})
    assert len(recovered["attempts"]) == (10 if unknown else 9)
    completed = [row for row in recovered["ledger"] if row["state"] == "completed"]
    retained = [row for row in recovered["ledger"] if row["state"] != "completed"]
    assert len(completed) == 9 and len(retained) == len(unknown)
    assert all(Decimal(str(row["reserved_cny"])) > 0 for row in retained)
    assert sum(Decimal(str(row["estimated_cny"])) for row in completed) == Decimal("0.00252")
    if retained:
        assert retained[0] == crashed["ledger"][0]
    else:
        assert all(a["status"] == "response_received" for a in recovered["attempts"])
    before_third = snapshot(service)
    third = worker(directory, "read-saved", window=window, synthetic_ledger=True)
    assert third.returncode == 0, (directory / "read-saved.log").read_text()
    assert len(calls(directory)) == observed_calls
    assert_same_business_and_saved_result(directory, service, before_third)


def test_sigkill_while_waiting_review_restart_and_saved_read_never_repeat_model_steps(
    service, settings, tmp_path
):
    directory = bootstrap(service, settings, tmp_path)
    first = worker(directory, "crash", window="waiting_review", synthetic_ledger=False)
    assert first.returncode == -signal.SIGKILL, (directory / "crash.log").read_text()
    before = snapshot(service)
    counts = Counter(item["prompt_sha256"] for item in calls(directory))
    assert len(calls(directory)) == 9 and max(counts.values()) == 1
    assert len(before["jobs"]) == len(before["results"]) == 1 and before["ledger"] == []
    for mode in ("recover", "read-saved"):
        child = worker(directory, mode, window="waiting_review", synthetic_ledger=False)
        assert child.returncode == 0, (directory / f"{mode}.log").read_text()
        assert Counter(item["prompt_sha256"] for item in calls(directory)) == counts
        assert snapshot(service) == before
    assert_same_business_and_saved_result(directory, service, before)
    with closing(sqlite3.connect(settings.data_dir / "private/jobs/dbos.sqlite3")) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM workflow_status "
                "WHERE name='bidops_qualification_calculation_v1'"
            ).fetchone()[0]
            == 1
        )
