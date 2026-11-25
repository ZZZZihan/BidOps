"""Local dependency faults, bounded metrics and separate-process death receipts."""

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.enterprise.health import COMPONENTS, DependencyHealth, HealthSettings
from bidops.operations_acceptance.watchdog import Watchdog, check, main, origin
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, generate_latest
from sqlalchemy.engine import make_url
from test_enterprise import ORIGIN, ProtocolProvider, enterprise


@pytest.fixture
def health(store, settings):
    now = [time.time()]
    state = SimpleNamespace(
        jobs=None,
        maintenance_failed=False,
        ingestion=SimpleNamespace(settings=SimpleNamespace(enabled=True)),
    )
    registry = CollectorRegistry()
    monitor = DependencyHealth(
        HealthSettings(enabled=True, backlog_count=2, oldest_queued_seconds=10),
        registry,
        state,
        settings,
        clock=lambda: now[0],
    )
    return monitor, now, registry


def test_sqlite_write_lock_alert_and_recovery_are_actual_bounded_probes(health, settings):
    monitor, _, _ = health
    assert monitor.sample()["ready"]
    connection = sqlite3.connect(make_url(settings.database_url).database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        failed = monitor.sample()
        assert time.monotonic() - started < 2
        assert not failed["ready"] and not failed["checks"]["database"]
        assert any(
            e["component"] == "database" and e["state"] == "firing" for e in failed["events"]
        )
    finally:
        connection.rollback()
        connection.close()
    recovered = monitor.sample()
    assert recovered["ready"]
    assert any(
        e["component"] == "database" and e["state"] == "resolved" for e in recovered["events"]
    )


def test_missing_database_is_not_recreated_and_snapshot_expires(health, settings, tmp_path):
    monitor, now, _ = health
    assert monitor.sample()["ready"]
    now[0] += 121
    assert monitor.snapshot()["checks"]["observation_current"] is False
    assert monitor.snapshot()["ready"] is False
    missing = tmp_path / "missing.sqlite3"
    monitor.data_settings = settings.model_copy(update={"database_url": f"sqlite:///{missing}"})
    assert monitor.sample()["checks"]["database"] is False
    assert not missing.exists()


def test_worker_workflow_database_disk_and_maintenance_faults_recover(
    health, settings, monkeypatch, tmp_path
):
    import bidops.enterprise.health as module

    monitor, _, _ = health
    monitor.state.jobs = SimpleNamespace(started=False, path=tmp_path / "missing-workflow.db")
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    monitor.state.maintenance_failed = True
    failed = monitor.sample()
    for component in ("worker", "workflow_database", "disk", "maintenance"):
        assert failed["checks"][component] is False
        assert any(e["component"] == component and e["state"] == "firing" for e in failed["events"])
    monitor.state.jobs.started = True
    monitor.state.jobs.path = make_url(settings.database_url).database
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=2**40))
    monitor.state.maintenance_failed = False
    recovered = monitor.sample()
    assert recovered["ready"]
    for component in ("worker", "workflow_database", "disk", "maintenance"):
        assert any(
            e["component"] == component and e["state"] == "resolved" for e in recovered["events"]
        )


def test_real_queue_age_count_and_source_failure_hide_private_identifiers(health, settings, caplog):
    monitor, now, registry = health
    assert monitor.sample()["ready"]
    connection = sqlite3.connect(make_url(settings.database_url).database)
    try:
        connection.execute(
            "INSERT INTO ingestion_source_state(source_id,failure_count,last_error_code) "
            "VALUES (?,?,?)",
            ("PRIVATE_SOURCE", 1, "PRIVATE_BODY"),
        )
        for number in range(2):
            identifier = f"PRIVATE_JOB_{number}"
            connection.execute(
                "INSERT INTO durable_jobs VALUES (?,?,?,?,?,?,?)",
                (identifier, "PRIVATE_OWNER", str(number), "a" * 64, "b" * 64, "c" * 64, now[0]),
            )
            connection.execute(
                "INSERT INTO durable_job_state(run_id,updated_at) VALUES (?,?)",
                (identifier, now[0]),
            )
        connection.commit()
        failed = monitor.sample()
        assert not failed["checks"]["queue_backlog"] and not failed["checks"]["source"]
        connection.execute("UPDATE ingestion_source_state SET failure_count=0")
        connection.execute(
            "UPDATE durable_job_state SET status='completed' WHERE run_id='PRIVATE_JOB_1'"
        )
        connection.commit()
        assert monitor.sample()["ready"]
        now[0] += 11
        assert monitor.sample()["checks"]["queue_backlog"] is False
        connection.execute("UPDATE durable_job_state SET status='completed'")
        connection.commit()
        assert monitor.sample()["ready"]
    finally:
        connection.close()
    output = generate_latest(registry).decode() + caplog.text + json.dumps(monitor.snapshot())
    assert "PRIVATE_" not in output
    assert 'component="queue_backlog"' in output and 'component="source"' in output


def test_event_history_is_bounded(health):
    monitor, now, _ = health
    for iteration in range(140):
        monitor.state.maintenance_failed = iteration % 2 == 0
        now[0] += 1
        monitor.sample()
    snapshot = monitor.snapshot()
    assert len(snapshot["events"]) == 128
    assert set(snapshot["checks"]) == {*COMPONENTS, "observation_current"}
    assert snapshot["delivery"] == "local_log_only"
    assert snapshot["external_recipient_verified"] is False


def test_source_only_queue_stall_and_aggregate_queue_count_are_observed(health, settings):
    monitor, now, _ = health
    assert monitor.sample()["ready"]
    connection = sqlite3.connect(make_url(settings.database_url).database)
    try:
        connection.execute(
            "INSERT INTO ingestion_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "PRIVATE_INGEST",
                "native",
                "owner",
                "source",
                "http_fetch",
                "manual",
                None,
                "a" * 64,
                "policy1",
                "b" * 64,
                "c" * 64,
                "app1",
                now[0] - 11,
                "key",
            ),
        )
        connection.execute(
            "INSERT INTO ingestion_run_state(run_id,status,updated_at) VALUES (?,?,?)",
            ("PRIVATE_INGEST", "queued", now[0]),
        )
        connection.commit()
        assert monitor.sample()["checks"]["queue_backlog"] is False
        connection.execute("UPDATE ingestion_run_state SET status='completed'")
        connection.commit()
        assert monitor.sample()["ready"]
    finally:
        connection.close()


def test_health_route_requires_auditor_and_readiness_exposes_only_fixed_checks(
    settings, monkeypatch
):
    app = create_app(
        settings,
        DemoSettings(_env_file=None),
        enterprise(),
        health_settings=HealthSettings(enabled=True, interval_seconds=60),
    )
    provider = ProtocolProvider()
    with TestClient(app, base_url=ORIGIN) as client:
        app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
            provider.handle
        )
        assert client.get("/api/operations/health").status_code == 401
        assert provider.login(client).status_code == 303
        assert client.get("/api/operations/health").status_code == 403
        provider.roles = ["auditor"]
        provider.subject = "independent-auditor"
        assert provider.login(client).status_code == 303
        assert client.get("/api/operations/health").status_code == 200
        # Dependency readiness is checked independently from a live model. No
        # real model readiness is inferred from this explicit unit replacement.
        monkeypatch.setattr(
            app.state.agent_service,
            "status",
            lambda: {
                "configured": True,
                "enabled": True,
                "notice_count": 1,
            },
        )
        app.state.dependency_health.sample()
        assert client.get("/api/ready").status_code == 200
        app.state.maintenance_failed = True
        app.state.dependency_health.sample()
        response = client.get("/api/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["maintenance"] is False
        assert set(response.json()) == {"ready", "checks"}
        assert "events" not in response.text


def test_default_off_preserves_readiness_contract_and_hides_health_route(settings):
    app = create_app(
        settings,
        DemoSettings(_env_file=None),
        enterprise(),
        health_settings=HealthSettings(enabled=False),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/operations/health").status_code == 404
        assert client.get("/api/ready").json() == {"ready": False}


class Chunks(httpx2.SyncByteStream):
    def __init__(self, values):
        self.values, self.read_count = values, 0

    def __iter__(self):
        for value in self.values:
            self.read_count += 1
            yield value


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, b'{"ready":true}', True),
        (503, b'{"ready":false}', False),
        (200, b'{"ready":true,"checks":{"worker":false}}', False),
        (302, b"{}", False),
        (200, b"[]", False),
        (200, b"bad", False),
    ],
)
def test_watchdog_fail_closed_status_and_payload(status, body, expected):
    with httpx2.Client(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(status, stream=Chunks([body])))
    ) as client:
        assert check(client, "https://unit.test")[0] is expected


def test_watchdog_stops_reading_oversize_and_encoded_bodies():
    stream = Chunks([b"x" * 16384, b"y", b"PRIVATE_UNREAD_BODY"])
    with httpx2.Client(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, stream=stream))
    ) as client:
        assert check(client, "https://unit.test") == (False, ["unavailable"])
    assert stream.read_count == 2
    stream = Chunks([b"PRIVATE_UNREAD_BODY"])
    with httpx2.Client(
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, headers={"Content-Encoding": "gzip"}, stream=stream)
        )
    ) as client:
        assert check(client, "https://unit.test")[0] is False
    assert stream.read_count == 0


def test_watchdog_guard_and_transitions_do_not_repeat_or_echo_input(tmp_path, capsys):
    for invalid in (
        "http://remote.test",
        "https://user:password@unit.test",
        "https://unit.test?secret=x",
        "https://unit.test/path",
    ):
        with pytest.raises(ValueError):
            origin(invalid)
    watcher = Watchdog()
    assert watcher.observe(True, at="1") is None
    assert watcher.observe(False, at="2")["state"] == "firing"
    assert watcher.observe(False, at="3") is None
    assert watcher.observe(True, at="4")["state"] == "resolved"
    output = tmp_path / "existing.jsonl"
    output.write_text("preserve")
    assert main(["--origin", "https://unit.test", "--output", str(output)]) == 2
    assert output.read_text() == "preserve"
    assert str(tmp_path) not in capsys.readouterr().out


def test_watchdog_alert_timestamp_is_after_completed_fault_observation(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    import bidops.operations_acceptance.watchdog as module

    observed = [1]
    clock = iter([0, 2])

    class WallClock:
        @staticmethod
        def now(_):
            return datetime.fromtimestamp(observed[0], UTC)

    def fault(*_):
        observed[0] = 2
        return False, ["unavailable"]

    monkeypatch.setattr(module, "datetime", WallClock)
    monkeypatch.setattr(module, "check", fault)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    output = tmp_path / "causal.jsonl"
    assert (
        main(["--origin", "https://unit.test", "--duration-seconds", "1", "--output", str(output)])
        == 0
    )
    receipt = json.loads(output.read_text())
    assert receipt["observed_at"] == datetime.fromtimestamp(2, UTC).isoformat()
    assert receipt["alert"]["observed_at"] == receipt["observed_at"]


def test_independent_watchdog_observes_actual_process_kill_and_restart(tmp_path):
    """Only loopback HTTP in child processes; no provider/source requests."""
    server_code = """
import http.server, sys
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'{"ready":true}'
        self.send_response(200)
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self,*args): pass
server=http.server.HTTPServer(("127.0.0.1",int(sys.argv[1])),Handler)
print(server.server_port,flush=True)
server.serve_forever()
"""
    children = []

    def launch(port):
        child = subprocess.Popen(
            [sys.executable, "-c", server_code, str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        children.append(child)
        return child, int(child.stdout.readline())

    output = tmp_path / "actual-watchdog.jsonl"
    root = Path(__file__).resolve().parents[1]

    def wait_for(predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            lines = (
                [json.loads(line) for line in output.read_text().splitlines()]
                if output.exists()
                else []
            )
            if predicate(lines):
                return lines
            time.sleep(0.05)
        pytest.fail("loopback watchdog observation deadline exceeded")

    try:
        server, port = launch(0)
        watcher = subprocess.Popen(
            [
                sys.executable,
                str(root / "scripts/operations_watch.py"),
                "--origin",
                f"http://127.0.0.1:{port}",
                "--duration-seconds",
                "8",
                "--interval-seconds",
                "1",
                "--output",
                str(output),
            ],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={k: v for k, v in os.environ.items() if not k.startswith("BIDOPS_")},
        )
        children.append(watcher)
        wait_for(lambda rows: rows and rows[-1]["healthy"])
        fault_at = time.time()
        server.kill()
        server.wait(timeout=2)
        wait_for(
            lambda rows: any(row["alert"] and row["alert"]["state"] == "firing" for row in rows)
        )
        detected_at = time.time()
        launch(port)
        receipts = wait_for(
            lambda rows: any(row["alert"] and row["alert"]["state"] == "resolved" for row in rows)
        )
        assert detected_at - fault_at < 4
        assert watcher.wait(timeout=10) == 0
        assert output.stat().st_mode & 0o777 == 0o600
        assert [row["alert"]["state"] for row in receipts if row["alert"]] == ["firing", "resolved"]
        (tmp_path / "fault-receipt.json").write_text(
            json.dumps(
                {
                    "fault_at": fault_at,
                    "detected_at": detected_at,
                    "recovered_at": time.time(),
                    "external_delivery": False,
                }
            )
        )
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=5)
