"""Installed SDK persists the native version before a queue worker executes."""

import sqlite3
from contextlib import closing
from types import SimpleNamespace

from bidops.ingestion.runtime import IngestionRuntime
from bidops.jobs import backup as durable
from dbos import DBOS
from test_ingestion_backup import native as native
from test_ingestion_repository import ADMIN, manual
from test_ingestion_repository import ingestion as ingestion
from test_jobs_backup import system_template as system_template


def test_real_client_enqueue_pins_version_without_launch_and_is_backup_valid(native, monkeypatch):
    repo, path, _ = native

    def forbidden_launch(*_args, **_kwargs):
        raise AssertionError("enqueue must not launch a workflow worker")

    monkeypatch.setattr(DBOS, "launch", forbidden_launch)
    record = repo.create(ADMIN, manual(repo), "manual_import")
    runtime = IngestionRuntime(SimpleNamespace(repository=repo, jobs=SimpleNamespace(path=path)))
    runtime.enqueue(record)
    runtime.enqueue(record)
    with closing(sqlite3.connect(path)) as conn:
        workflows = conn.execute(
            "SELECT workflow_uuid,status,application_version,name,queue_name FROM workflow_status"
        ).fetchall()
        assert workflows == [
            (
                record["native_id"],
                "ENQUEUED",
                record["application_version"],
                "bidops_ingestion_run_v1",
                "bidops_ingestion_v1",
            )
        ]
        assert conn.execute("SELECT COUNT(*) FROM operation_outputs").fetchone() == (0,)
    assert repo.get_system(record["run_id"])["status"] == "queued"
    assert repo.db.rows("SELECT * FROM ingestion_pages") == []
    assert repo.db.rows("SELECT * FROM nd_observations") == []
    mapping = durable._inventory(repo.db.engine.url.database, path)
    assert mapping["ingestion"]["runs"][0]["native_id"] == record["native_id"]
