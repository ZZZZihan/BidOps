"""Online private-history erasure, independent of the optional jobs feature flag.

Business bodies are denied and erased before touching files or the framework.
The native client only cancels/deletes history; it never starts a worker or
loads serialized outputs. Every purged run is retried after a backend failure.
"""

import sqlite3
from contextlib import closing

from sqlalchemy import create_engine, event, text

from bidops.company.authorization import rows, transaction


def _purge_business(service):
    db, now = service.db, service.auth.clock()
    with transaction(db) as conn:
        if not conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='durable_jobs'")
        ).first():
            return [], []
        history = rows(db, "SELECT run_id FROM durable_jobs ORDER BY run_id", conn=conn)
        if not history:
            return [], []
        conn.exec_driver_sql("PRAGMA secure_delete=ON")
        expired = rows(
            db,
            "SELECT DISTINCT s.run_id FROM durable_job_sources s "
            "LEFT JOIN company_objects o ON o.object_id=s.object_id "
            "JOIN durable_job_state j ON j.run_id=s.run_id "
            "WHERE j.purge_state='live' AND (o.object_id IS NULL OR o.deleted_at IS NOT NULL "
            "OR o.retention_until<=:now OR o.purge_state!='live')",
            {"now": now},
            conn,
        )
        for row in expired:
            conn.execute(
                text(
                    "UPDATE durable_job_state SET purge_state='purged',purged_at=:now,"
                    "progress_json='{}',state_version=state_version+1,updated_at=:now "
                    "WHERE run_id=:id"
                ),
                {"id": row["run_id"], "now": now},
            )
        # Retry body erasure as well as native history erasure. Do not rely on
        # this pass having discovered the expiration or on the feature being on.
        purged = rows(
            db,
            "SELECT run_id FROM durable_job_state WHERE purge_state='purged' ORDER BY run_id",
            conn=conn,
        )
        for row in purged:
            params = {"id": row["run_id"]}
            conn.execute(text("DELETE FROM durable_job_inputs WHERE run_id=:id"), params)
            conn.execute(text("DELETE FROM durable_attempt_bodies WHERE run_id=:id"), params)
            conn.execute(
                text(
                    "DELETE FROM durable_review_fixture_bodies WHERE event_id IN "
                    "(SELECT event_id FROM durable_review_fixtures WHERE run_id=:id)"
                ),
                params,
            )
            conn.execute(
                text("UPDATE durable_job_state SET progress_json='{}' WHERE run_id=:id"), params
            )
    return [r["run_id"] for r in history], [r["run_id"] for r in purged]


def _client(path):
    # The base install has no DBOS dependency. Import the overlay only when
    # private job history actually needs its native deletion implementation.
    from dbos import DBOSClient

    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def secure_delete(connection, _):
        connection.execute("PRAGMA secure_delete=ON")

    try:
        client = DBOSClient(system_database_engine=engine, retry_connection_errors=False)
    except Exception:
        engine.dispose()
        raise
    return client, engine


def prune_job_history(service):
    """Return a retryable report; never release quota or rewrite usage metadata."""
    history, purged = _purge_business(service)
    report = {
        "status": "PASS",
        "purged_job_ids": purged,
        "deleted_history_ids": [],
        "failure_ids": [],
        "failures": [],
    }

    def fail(ids, code):
        report["status"] = "FAIL"
        report["failure_ids"].extend(ids)
        report["failures"].extend({"run_id": run_id, "code": code} for run_id in ids)

    if not history:
        return report
    data_dir = service.materials.data_dir
    path = data_dir / "private/jobs/dbos.sqlite3"
    if (
        path.is_symlink()
        or any(p.is_symlink() for p in path.parents)
        or not path.resolve().is_relative_to(data_dir)
    ):
        fail(purged or history, "job_history_path_invalid")
        return report
    if not path.is_file():
        fail(purged or history, "job_history_database_missing")
        return report
    if not purged:
        return report
    client = engine = None
    try:
        # Refuse to initialize a replacement schema in a missing/corrupt file.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not {"workflow_status", "dbos_migrations"} <= tables:
                fail(purged, "job_history_database_invalid")
                return report
        client, engine = _client(path)
        for run_id in purged:
            try:
                client.cancel_workflow(run_id, cancel_children=True)
                client.delete_workflow(run_id, delete_children=True)
                report["deleted_history_ids"].append(run_id)
            except Exception:
                fail([run_id], "job_history_cleanup_failed")
    except ImportError:
        fail(purged, "job_history_backend_unavailable")
    except Exception:
        fail(purged, "job_history_cleanup_failed")
    finally:
        if client is not None:
            try:
                client.destroy()
            except Exception:
                fail(purged, "job_history_client_close_failed")
        if engine is not None:
            engine.dispose()
    report["failure_ids"] = sorted(set(report["failure_ids"]))
    return report
