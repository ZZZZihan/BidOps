"""Offline launcher boundaries using disposable SQLite and synthetic credentials."""

import hashlib
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bidops_live_demo_launcher", ROOT / "scripts/live_demo.py"
)
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)

UNIT_KEY = "UNIT_FAKE_CREDENTIAL_NOT_A_PROVIDER_KEY"


@pytest.fixture
def assets(tmp_path):
    repo = tmp_path / "repo"
    (repo / "frontend/dist").mkdir(parents=True)
    (repo / "frontend/dist/index.html").write_text("<p>UNIT launcher build</p>")
    (repo / "config").mkdir()
    (repo / "config/sources.json").write_text("[]")
    data = tmp_path / "existing-data"
    (data / "processed").mkdir(parents=True)
    (data / "raw").mkdir()
    (data / "raw/unit-source").write_bytes(b"UNIT launcher evidence, not procurement data")
    database = data / "processed/bidops.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE notices(notice_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);"
            "INSERT INTO notices VALUES ('unit-notice','{}');"
            "CREATE TABLE agent_runs(run_id TEXT PRIMARY KEY,state TEXT NOT NULL,"
            "estimated_cny REAL,reserved_cny REAL NOT NULL);"
        )
        connection.executemany(
            "INSERT INTO agent_runs VALUES (?,?,?,?)",
            [
                ("unit-completed", "completed", 0.03, 0.25),
                ("unit-failed", "failed", None, 0.25),
                ("unit-inflight", "started", None, 0.25),
            ],
        )
    return repo, data, database


def fingerprints(folder):
    return {
        str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in folder.rglob("*")
        if path.is_file()
    }


def prepare(assets, **kwargs):
    repo, data, _ = assets
    return launcher.prepare_launch(data, repo_root=repo, environ={}, **kwargs)


def test_missing_data_directory_is_not_created(tmp_path):
    data = tmp_path / "not-created"
    with pytest.raises(launcher.LauncherError, match="数据库"):
        launcher.prepare_launch(data, repo_root=tmp_path, environ={})
    assert not data.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "missing",
    ["database", "build", "sources", "raw_directory", "raw_content", "notices", "agent_runs"],
)
def test_incomplete_assets_fail_without_mutating_data(assets, missing):
    repo, data, database = assets
    if missing == "database":
        database.unlink()
    elif missing == "build":
        (repo / "frontend/dist/index.html").unlink()
    elif missing == "sources":
        (repo / "config/sources.json").unlink()
    elif missing in {"raw_directory", "raw_content"}:
        (data / "raw/unit-source").unlink()
        if missing == "raw_directory":
            (data / "raw").rmdir()
    else:
        with sqlite3.connect(database) as connection:
            if missing == "notices":
                connection.execute("DELETE FROM notices")
            else:
                connection.execute("DROP TABLE agent_runs")
    before = fingerprints(data)
    with pytest.raises(launcher.LauncherError):
        prepare(assets)
    assert fingerprints(data) == before


def test_corrupt_database_is_not_replaced(assets):
    _, data, database = assets
    database.write_bytes(b"UNIT intentionally invalid database")
    before = fingerprints(data)
    with pytest.raises(launcher.LauncherError, match="数据库"):
        prepare(assets)
    assert fingerprints(data) == before


@pytest.mark.parametrize("budget", [None, -1.0, 0.0, 0.249, 10.001, float("nan"), float("inf")])
def test_live_rejects_missing_or_invalid_explicit_budget(assets, budget):
    repo, data, _ = assets
    before = fingerprints(data)
    with pytest.raises(launcher.LauncherError):
        launcher.prepare_launch(
            data,
            repo_root=repo,
            live=True,
            max_cost_cny=budget,
            environ={"DEEPSEEK_API_KEY": UNIT_KEY, "BIDOPS_DEMO_MAX_COST_CNY": "10"},
        )
    assert fingerprints(data) == before


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_port_does_not_touch_data(assets, port):
    _, data, _ = assets
    before = fingerprints(data)
    with pytest.raises(launcher.LauncherError, match="端口"):
        prepare(assets, port=port)
    assert fingerprints(data) == before


def test_preview_never_loads_private_credentials(assets, monkeypatch):
    import dotenv

    def forbidden(*args, **kwargs):
        raise AssertionError("Preview must not load a credential file")

    monkeypatch.setattr(dotenv, "dotenv_values", forbidden)
    config = prepare(assets)
    assert config.environment["BIDOPS_DEMO_ENABLED"] == "false"
    assert "DEEPSEEK_API_KEY" not in config.environment


def test_live_missing_key_fails_without_data_changes(assets):
    _, data, _ = assets
    before = fingerprints(data)
    with pytest.raises(launcher.LauncherError, match="密钥"):
        prepare(assets, live=True, max_cost_cny=1)
    assert fingerprints(data) == before


def test_explicit_key_does_not_read_a_private_file_or_appear_in_repr(assets, monkeypatch):
    import dotenv

    def forbidden(*args, **kwargs):
        raise AssertionError("An explicit synthetic key must take precedence")

    monkeypatch.setattr(dotenv, "dotenv_values", forbidden)
    repo, data, _ = assets
    config = launcher.prepare_launch(
        data,
        repo_root=repo,
        live=True,
        max_cost_cny=1,
        environ={"DEEPSEEK_API_KEY": UNIT_KEY},
    )
    assert config.environment["DEEPSEEK_API_KEY"] == UNIT_KEY
    assert UNIT_KEY not in repr(config)


@pytest.mark.parametrize("live", [False, True])
def test_inherited_configuration_cannot_redirect_data_or_enable_other_features(assets, live):
    repo, data, database = assets
    inherited = {
        "PATH": "/unit/bin",
        "UNIT_UNRELATED": "retained",
        "BIDOPS_DATA_DIR": "/unit/forbidden-data",
        "BIDOPS_DATABASE_URL": "sqlite:////unit/forbidden.sqlite3",
        "BIDOPS_SOURCE_REGISTRY": "/unit/forbidden-sources.json",
        "BIDOPS_DEMO_ENABLED": "true",
        "BIDOPS_DEMO_MAX_COST_CNY": "10",
        "BIDOPS_DEMO_MAX_SESSIONS": "100",
        "BIDOPS_DEMO_MAX_TURNS": "20",
        "BIDOPS_DEMO_BUDGET_RESTORE_TOKEN": "UNIT_UNAPPROVED_TOKEN",
        "BIDOPS_SCREENING_LEDGER_VERIFIED": "true",
        "BIDOPS_SCREENING_LEDGER_RESTORE_TOKEN": "UNIT_UNAPPROVED_TOKEN",
        "BIDOPS_UNKNOWN_FEATURE": "true",
        "DEEPSEEK_API_KEY": UNIT_KEY,
        "DEEPSEEK_BASE_URL": "https://unit.invalid",
        "WEB_CONCURRENCY": "8",
        "UVICORN_WORKERS": "8",
        "UVICORN_RELOAD": "true",
    }
    disabled = [
        "BIDOPS_NETWORK_ENABLED",
        "BIDOPS_ENTERPRISE_ENABLED",
        "BIDOPS_SCREENING_ENABLED",
        "BIDOPS_SCREENING_LIVE_CALLS_ENABLED",
        "BIDOPS_JOBS_ENABLED",
        "BIDOPS_REVIEW_ENABLED",
        "BIDOPS_INGESTION_ENABLED",
        "BIDOPS_OPERATIONS_ENABLED",
    ]
    inherited.update(dict.fromkeys(disabled, "true"))
    before = fingerprints(data)
    config = launcher.prepare_launch(
        data, repo_root=repo, live=live, max_cost_cny=1 if live else None, environ=inherited
    )
    environment = config.environment
    assert environment["BIDOPS_DATA_DIR"] == str(data)
    assert environment["BIDOPS_DATABASE_URL"] == f"sqlite:///{database}"
    assert environment["BIDOPS_SOURCE_REGISTRY"] == str(repo / "config/sources.json")
    assert environment["BIDOPS_DEMO_ENABLED"] == ("true" if live else "false")
    assert environment["BIDOPS_DEMO_MAX_COST_CNY"] == "1"
    assert environment["BIDOPS_DEMO_MAX_SESSIONS"] == "50"
    assert environment["BIDOPS_DEMO_MAX_TURNS"] == "8"
    assert all(environment[name] == "false" for name in disabled)
    assert environment.get("BIDOPS_DEMO_BUDGET_RESTORE_TOKEN") in {None, ""}
    assert "BIDOPS_SCREENING_LEDGER_VERIFIED" not in environment
    assert "BIDOPS_SCREENING_LEDGER_RESTORE_TOKEN" not in environment
    assert "BIDOPS_UNKNOWN_FEATURE" not in environment
    assert "DEEPSEEK_BASE_URL" not in environment
    assert "WEB_CONCURRENCY" not in environment
    assert not any(name.startswith("UVICORN_") for name in environment)
    assert environment.get("DEEPSEEK_API_KEY") == (UNIT_KEY if live else None)
    assert environment["UNIT_UNRELATED"] == "retained"
    assert environment["PATH"] == "/unit/bin"
    assert inherited["BIDOPS_DATA_DIR"] == "/unit/forbidden-data"
    assert fingerprints(data) == before


def test_private_env_only_supplies_key_not_old_budget_or_restore_configuration(assets):
    repo, data, _ = assets
    (repo / "private").mkdir()
    (repo / "private/deepseek.env").write_text(
        f"DEEPSEEK_API_KEY={UNIT_KEY}\n"
        "BIDOPS_DEMO_ENABLED=false\n"
        "BIDOPS_DEMO_MAX_COST_CNY=10\n"
        "BIDOPS_DEMO_BUDGET_RESTORE_TOKEN=UNIT_UNAPPROVED_TOKEN\n"
        "BIDOPS_NETWORK_ENABLED=true\n"
    )
    config = launcher.prepare_launch(data, repo_root=repo, live=True, max_cost_cny=0.5, environ={})
    assert config.environment["DEEPSEEK_API_KEY"] == UNIT_KEY
    assert config.environment["BIDOPS_DEMO_MAX_COST_CNY"] == "0.5"
    assert config.environment["BIDOPS_DEMO_ENABLED"] == "true"
    assert config.environment["BIDOPS_DEMO_BUDGET_RESTORE_TOKEN"] == ""
    assert config.environment["BIDOPS_NETWORK_ENABLED"] == "false"


def test_repeated_launch_preparation_preserves_completed_failed_and_inflight_ledger(assets):
    repo, data, database = assets
    before = fingerprints(data)
    configs = [
        launcher.prepare_launch(
            data,
            repo_root=repo,
            live=True,
            max_cost_cny=1,
            environ={"DEEPSEEK_API_KEY": UNIT_KEY},
        )
        for _ in range(2)
    ]
    assert configs[0] == configs[1]
    assert configs[0].environment["BIDOPS_DATABASE_URL"] == f"sqlite:///{database}"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT * FROM agent_runs ORDER BY run_id").fetchall()
        accounted = connection.execute(
            "SELECT sum(CASE WHEN state='completed' THEN estimated_cny ELSE reserved_cny END) "
            "FROM agent_runs"
        ).fetchone()[0]
    assert rows == [
        ("unit-completed", "completed", 0.03, 0.25),
        ("unit-failed", "failed", None, 0.25),
        ("unit-inflight", "started", None, 0.25),
    ]
    assert accounted == pytest.approx(0.53)
    assert fingerprints(data) == before


def test_main_failure_never_executes_or_changes_directory(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("An invalid launch must stop before execution")

    monkeypatch.setattr(launcher.os, "execve", forbidden)
    monkeypatch.setattr(launcher.os, "chdir", forbidden)
    data = tmp_path / "not-created"
    assert launcher.main(["--data-dir", str(data)]) == 2
    assert not data.exists()
    assert "数据库" in capsys.readouterr().err


def test_main_uses_validated_environment_and_fixed_loopback_single_worker(tmp_path, monkeypatch):
    calls = {}
    environment = {"UNIT_ENVIRONMENT": "validated"}
    config = launcher.LaunchConfig(environment=environment, cwd=tmp_path, port=9876)

    def prepared(data_dir, **kwargs):
        calls["prepare"] = (data_dir, kwargs)
        return config

    def execute(executable, arguments, child_environment):
        calls["execute"] = (executable, arguments, child_environment)

    monkeypatch.setattr(launcher, "prepare_launch", prepared)
    monkeypatch.setattr(launcher.os, "chdir", lambda cwd: calls.update(cwd=cwd))
    monkeypatch.setattr(launcher.os, "execve", execute)
    assert (
        launcher.main(
            ["--data-dir", str(tmp_path), "--port", "9876", "--live", "--max-cost-cny", "1"]
        )
        == 0
    )
    assert calls["prepare"] == (tmp_path, {"live": True, "max_cost_cny": 1.0, "port": 9876})
    assert calls["cwd"] == tmp_path
    executable, arguments, child_environment = calls["execute"]
    assert executable == sys.executable
    assert arguments[:4] == [sys.executable, "-m", "uvicorn", "bidops.api:app"]
    assert arguments[arguments.index("--host") + 1] == "127.0.0.1"
    assert arguments[arguments.index("--port") + 1] == "9876"
    assert arguments[arguments.index("--workers") + 1] == "1"
    assert child_environment is environment
