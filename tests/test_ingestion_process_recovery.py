"""Three actual-process SIGKILL windows with durable SQLite/native DBOS replay."""

import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from test_ingestion_repository import ADMIN, document, encoded, http, manual
from test_ingestion_repository import ingestion as ingestion

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests/fixtures/ingestion_process_worker.py"
WINDOWS = ["before_page_commit", "after_page_commit", "after_schedule_run_commit"]
TABLES = (
    "ingestion_runs",
    "ingestion_inputs",
    "ingestion_run_state",
    "ingestion_pages",
    "ingestion_page_attempts",
    "ingestion_failures",
    "ingestion_source_state",
    "ingestion_policy_revisions",
    "ingestion_schedules",
    "ingestion_schedule_versions",
    "ingestion_schedule_commands",
    "ingestion_schedule_ticks",
    "snapshots",
    "notices",
    "nd_versions",
    "nd_observations",
    "nd_attachments",
    "nd_attachment_receipts",
    "agent_runs",
    "durable_model_attempts",
    "audit_events",
)


def snapshot(repo):
    return {table: repo.db.rows(f"SELECT * FROM {table} ORDER BY rowid") for table in TABLES}


def worker(directory, mode, window):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DBOS_", "OTEL_", "BIDOPS_", "DEEPSEEK_"))
    }
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT / "backend"), str(ROOT / "tests")])
    with (directory / f"{mode}.log").open("w") as log:
        try:
            return subprocess.run(
                [sys.executable, "-u", str(WORKER), str(directory), mode, "--window", window],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=50,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"ingestion {mode} process timed out; inspect {directory / f'{mode}.log'}")


def events(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]


def bootstrap(repo, tmp_path, window):
    directory = tmp_path / "ingestion-process-evidence"
    directory.mkdir(mode=0o700)
    payload = manual(repo, pages=2)
    payload["pages"][1]["items"][0]["body_base64"] = encoded(document("0", "更正公告"))
    value = {
        "database_url": str(repo.db.engine.url),
        "data_dir": str(repo.store.settings.data_dir),
        "source_registry": str(repo.store.settings.source_registry),
        "owner_id": ADMIN.owner_id,
        "request": payload,
    }
    if window == "after_schedule_run_commit":
        value["schedule"] = {
            **http(repo),
            "schedule_id": "process-generation",
            "expected_state_version": 0,
            "cron": "0 0 1 1 *",
            "enabled": True,
            "reason": "synthetic SIGKILL recovery engineering fixture",
        }
        value["http_body"] = document("SCHEDULE").decode() + (
            "\n采购人名称：合成工程中心\n采购内容：仅供离线恢复实验的合成招标公告正文。"
            "预算金额：100元；第1包；不得用于真实采购结论。\n"
        )
    target = directory / "bootstrap.json"
    target.write_text(json.dumps(value))
    target.chmod(0o600)
    return directory


@pytest.mark.parametrize("window", WINDOWS)
def test_sigkill_source_commit_windows_recover_one_run_and_fixed_receipts(
    ingestion, tmp_path, window
):
    repo = ingestion
    directory = bootstrap(repo, tmp_path, window)
    first = worker(directory, "crash", window)
    assert first.returncode == -signal.SIGKILL, (directory / "crash.log").read_text()
    assert [item["window"] for item in events(directory) if item["event"] == "sigkill"] == [window]
    crashed = snapshot(repo)
    assert len(crashed["ingestion_runs"]) == 1
    assert len(crashed["ingestion_pages"]) == (window == "after_page_commit")
    assert len(crashed["nd_versions"]) == (window == "after_page_commit")
    if window == "after_schedule_run_commit":
        assert crashed["ingestion_schedule_ticks"] == []
        assert crashed["ingestion_run_state"][0]["status"] == "queued"
    second = worker(directory, "recover", window)
    assert second.returncode == 0, (directory / "recover.log").read_text()
    recovered = snapshot(repo)
    expected_pages = 1 if window == "after_schedule_run_commit" else 2
    assert len(recovered["ingestion_runs"]) == 1
    assert len(recovered["ingestion_pages"]) == len(recovered["nd_versions"]) == expected_pages
    assert recovered["ingestion_run_state"][0]["status"] == "completed"
    assert recovered["ingestion_source_state"][0]["cursor_version"] == expected_pages
    assert recovered["agent_runs"] == recovered["durable_model_attempts"] == []
    if window == "after_page_commit":
        assert recovered["ingestion_pages"][0] == crashed["ingestion_pages"][0]
        assert recovered["nd_observations"][0] == crashed["nd_observations"][0]
    if window == "after_schedule_run_commit":
        assert len(recovered["ingestion_schedule_ticks"]) == 1
        assert (
            recovered["ingestion_schedule_ticks"][0]["run_id"]
            == recovered["ingestion_runs"][0]["run_id"]
        )
    prepared = [entry for entry in events(directory) if entry["event"] == "prepare_notice"]
    assert len(prepared) == (3 if window == "before_page_commit" else expected_pages)
    mock_calls = [entry for entry in events(directory) if entry["event"] == "mock_http_request"]
    assert len(mock_calls) == (window == "after_schedule_run_commit")
    for stored in recovered["snapshots"]:
        raw = repo.store.raw / stored["sha256"]
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == stored["sha256"]
    third = worker(directory, "read-saved", window)
    assert third.returncode == 0, (directory / "read-saved.log").read_text()
    assert snapshot(repo) == recovered
    assert len([e for e in events(directory) if e["event"] == "prepare_notice"]) == len(prepared)
    assert len([e for e in events(directory) if e["event"] == "mock_http_request"]) == len(
        mock_calls
    )
    final = json.loads((directory / "recover.json").read_text())
    reread = json.loads((directory / "read-saved.json").read_text())
    assert reread == final
    assert final["run"]["counts"]["pages"] == expected_pages
    assert final["run"]["counts"]["items"] == expected_pages
    assert final["run"]["counts"]["imported"] == expected_pages
    assert final["run"]["counts"]["unchanged"] == 0
    page_checkpoints = [row for row in final["checkpoints"] if row[2] == "bidops_ingestion_page_v1"]
    assert len(page_checkpoints) == expected_pages
    assert all(row[3] and not row[4] for row in page_checkpoints)
    starts = [entry for entry in events(directory) if entry["event"] == "starting"]
    assert len({entry["pid"] for entry in starts}) == 3
    assert len({entry["application_version"] for entry in starts}) == 1
    assert all(
        entry["real_http_requests"] == entry["real_model_calls"] == 0
        and entry["actual_cost_cny"] == "0"
        for entry in starts
    )
