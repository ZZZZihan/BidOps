"""Launcher boundaries and actual preview process; disposable engineering inputs only."""

import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from test_live_demo_launcher import UNIT_KEY, fingerprints
from test_live_demo_launcher import assets as assets
from test_workflow_demo import prepared as prepared

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bidops_workflow_launcher", ROOT / "scripts/workflow_demo.py"
)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def test_launcher_isolation_and_explicit_key_file(assets):
    repo, data, _ = assets
    key_file = repo / "explicit-unit-key.env"
    key_file.write_text(
        f"DEEPSEEK_API_KEY={UNIT_KEY}\nBIDOPS_ENTERPRISE_ENABLED=true\n"
        "BIDOPS_DEMO_MAX_COST_CNY=10\nBIDOPS_SCREENING_LEDGER_RESTORE_TOKEN=UNIT_FORBIDDEN\n"
    )
    before = fingerprints(data)
    config = launcher.prepare_launch(
        data,
        live=True,
        max_cost_cny=1,
        key_file=key_file,
        repo_root=repo,
        environ={
            "BIDOPS_ENTERPRISE_ENABLED": "true",
            "BIDOPS_NETWORK_ENABLED": "true",
            "BIDOPS_SCREENING_BUDGET_CNY": "10",
            "BIDOPS_WORKFLOW_LIVE": "false",
            "DEEPSEEK_API_KEY": "UNIT_WRONG_INHERITED_KEY",
            "UVICORN_WORKERS": "8",
            "WEB_CONCURRENCY": "8",
        },
    )
    assert config.environment["DEEPSEEK_API_KEY"] == UNIT_KEY
    assert config.environment["BIDOPS_WORKFLOW_LIVE"] == "true"
    assert config.environment["BIDOPS_WORKFLOW_MAX_COST_CNY"] == "1"
    # The global production app must remain disabled while its module imports.
    assert config.environment["BIDOPS_ENTERPRISE_ENABLED"] == "false"
    assert config.environment["BIDOPS_SCREENING_ENABLED"] == "false"
    assert config.environment["BIDOPS_NETWORK_ENABLED"] == "false"
    assert "BIDOPS_SCREENING_LEDGER_RESTORE_TOKEN" not in config.environment
    assert "BIDOPS_SCREENING_BUDGET_CNY" not in config.environment
    assert "UVICORN_WORKERS" not in config.environment
    assert "WEB_CONCURRENCY" not in config.environment
    assert UNIT_KEY not in repr(config)
    assert fingerprints(data) == before


def test_preview_does_not_read_key_file_or_rebuild_missing_db(assets, tmp_path):
    repo, data, database = assets
    config = launcher.prepare_launch(
        data, key_file=tmp_path / "does-not-exist", repo_root=repo, environ={}
    )
    assert "DEEPSEEK_API_KEY" not in config.environment
    assert config.environment["BIDOPS_WORKFLOW_LIVE"] == "false"
    database.unlink()
    before = fingerprints(data)
    with pytest.raises(launcher.LauncherError, match="数据库"):
        launcher.prepare_launch(data, repo_root=repo, environ={})
    assert fingerprints(data) == before and not database.exists()


@pytest.mark.parametrize("cap", [None, 0, 1.00001, float("inf"), float("nan")])
def test_budget_cannot_be_implicitly_enabled_or_increased(assets, cap):
    repo, data, _ = assets
    before = fingerprints(data)
    with pytest.raises(launcher.LauncherError):
        launcher.prepare_launch(
            data,
            live=True,
            max_cost_cny=cap,
            repo_root=repo,
            environ={"DEEPSEEK_API_KEY": UNIT_KEY},
        )
    assert fingerprints(data) == before


def test_main_executes_only_fixed_loopback_factory_one_worker(assets, monkeypatch):
    repo, data, _ = assets
    config = launcher.prepare_launch(data, repo_root=repo, environ={})
    monkeypatch.setattr(launcher, "prepare_launch", lambda *args, **kwargs: config)
    monkeypatch.setattr(launcher.os, "chdir", lambda path: None)
    executed = []
    monkeypatch.setattr(launcher.os, "execve", lambda *args: executed.append(args))
    assert launcher.main(["--data-dir", str(data)]) == 0
    args = executed[0][1]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--workers") + 1] == "1"
    assert "--factory" in args and "--no-proxy-headers" in args
    assert "bidops.local_demo.app:application" in args


def test_actual_preview_cli_starts_without_external_identity_or_new_fee(settings, store, prepared):
    if not (ROOT / "frontend/dist/index.html").is_file():
        pytest.skip("CLI requires the separately built frontend")
    database = settings.data_dir / "processed/bidops.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(store.db.engine.url.database)) as source:
        with sqlite3.connect(database) as target:
            source.backup(target)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = {
        **os.environ,
        "BIDOPS_ENTERPRISE_ENABLED": "true",
        "BIDOPS_SCREENING_LIVE_CALLS_ENABLED": "true",
        "BIDOPS_WORKFLOW_LIVE": "true",
        "UVICORN_WORKERS": "8",
        "WEB_CONCURRENCY": "8",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts/workflow_demo.py"),
            "--data-dir",
            str(settings.data_dir),
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Preview process exited: " + process.communicate()[1][-3000:])
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/auth/me", timeout=1) as response:
                    identity = json.load(response)
                    break
            except (URLError, TimeoutError):
                time.sleep(0.1)
        else:
            pytest.fail("Preview process did not become reachable")
        assert identity["mode"] == "enterprise" and identity["authenticated"] is False
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        final = [
            dict(row) for row in connection.execute("SELECT * FROM agent_runs ORDER BY run_id")
        ]
    assert final == prepared[1]
