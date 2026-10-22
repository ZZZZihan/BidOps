"""Offline DBOS snapshot binding and private-history cleanup, never a worker.

The caller owns SingleWorker for the complete operation. SDK clients below do
not register workflows, launch execution, or deserialize persisted outputs.
Paired random markers detect mixed controlled snapshots; they are not a signature
or an authenticity guarantee for an untrusted SQLite/pickle database bundle.
"""

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from bidops.qualification.sources import ScreeningInputManifest, canonical_json
from bidops.review import backup as review_backup

SYSTEM_DATABASE = "private/jobs/dbos.sqlite3"
WORKFLOW_NAME = "bidops_qualification_job_v1"
CALCULATION_WORKFLOW_NAME = "bidops_qualification_calculation_v1"
BINDING_TABLE = "bidops_snapshot_binding"


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _read(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _check(conn):
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Durable backup database integrity failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Durable backup database references are invalid")


def _rows(conn, query, parameters=()):
    cursor = conn.execute(query, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def _table_digest(conn, name):
    # Hash opaque serialized values; never unpickle private framework state.
    escaped = '"' + name.replace('"', '""') + '"'
    records = [
        [{"sqlite_blob_hex": value.hex()} if isinstance(value, bytes) else value for value in row]
        for row in conn.execute(f"SELECT * FROM {escaped}")
    ]
    encoded = sorted(canonical_json(row) for row in records)
    columns = conn.execute(f"PRAGMA table_info({escaped})").fetchall()
    return _sha(canonical_json({"columns": columns, "rows": encoded}))


def has_jobs(path):
    with closing(_read(path)) as conn:
        return "durable_jobs" in _tables(conn) and bool(
            conn.execute("SELECT 1 FROM durable_jobs LIMIT 1").fetchone()
        )


def copy_system_database(data_dir: Path, destination: Path, *, required=False):
    source = data_dir / SYSTEM_DATABASE
    if not source.exists():
        if required:
            raise ValueError("Durable jobs require their DBOS system database")
        return None
    if source.is_symlink() or not source.resolve().is_relative_to(data_dir.resolve()):
        raise ValueError("Invalid durable system database path")
    target = destination / SYSTEM_DATABASE
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(_read(source)) as original, closing(sqlite3.connect(target)) as copied:
        original.backup(copied)
        _check(copied)
    target.chmod(0o600)
    return target


def _job_inventory(conn):
    tables = _tables(conn)
    if "durable_jobs" not in tables:
        return []
    jobs = _rows(conn, "SELECT * FROM durable_jobs ORDER BY run_id")
    states = {row["run_id"]: row for row in _rows(conn, "SELECT * FROM durable_job_state")}
    if set(states) != {job["run_id"] for job in jobs}:
        raise ValueError("Durable backup job state closure failed")
    result = []
    for job in jobs:
        run_id, state = job["run_id"], states[job["run_id"]]
        if state["status"] in {"waiting_review", "completed"} and state["result_id"] is None:
            raise ValueError("Durable backup task result is missing")
        bodies = _rows(conn, "SELECT body_json FROM durable_job_inputs WHERE run_id=?", (run_id,))
        body = None
        if state["purge_state"] == "live":
            if len(bodies) != 1 or _sha(bodies[0]["body_json"]) != job["body_sha256"]:
                raise ValueError("Durable backup input integrity failed")
            try:
                body = json.loads(bodies[0]["body_json"])
                manifest = ScreeningInputManifest.model_validate(body["manifest"])
                application_version = body["versions"]["application_version"]
                if (
                    _sha(canonical_json(body["request"])) != job["input_sha256"]
                    or manifest.closure_sha256 != job["inputs_manifest_sha256"]
                    or not isinstance(application_version, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", application_version)
                ):
                    raise ValueError("invalid input")
            except (KeyError, TypeError, ValueError):
                raise ValueError("Durable backup fixed inputs are invalid") from None
            actual = set(
                conn.execute(
                    "SELECT object_id,version_id,sha256 FROM durable_job_sources WHERE run_id=?",
                    (run_id,),
                )
            )
            if actual != {ref.key() for ref in manifest.company_revisions}:
                raise ValueError("Durable backup source closure failed")
        elif bodies:
            raise ValueError("Purged durable job retains private input")
        if state["result_id"] is not None:
            records = _rows(
                conn,
                "SELECT * FROM qualification_results WHERE result_id=?",
                (state["result_id"],),
            )
            if len(records) != 1 or records[0]["owner_id"] != job["owner_id"]:
                raise ValueError("Durable backup result reference failed")
            if state["result_id"] == run_id:
                raise ValueError("Durable task and result identities must differ")
            saved = _rows(
                conn,
                "SELECT body_json FROM qualification_result_bodies WHERE result_id=?",
                (state["result_id"],),
            )
            lifecycle = conn.execute(
                "SELECT purge_state FROM qualification_result_lifecycle WHERE result_id=?",
                (state["result_id"],),
            ).fetchone()
            if lifecycle is None or (not saved and lifecycle[0] != "purged"):
                raise ValueError("Durable backup result body is missing")
            if saved:
                if _sha(saved[0]["body_json"]) != records[0]["body_sha256"]:
                    raise ValueError("Durable backup result integrity failed")
                try:
                    result_body = json.loads(saved[0]["body_json"])
                    valid = (
                        result_body["manifest"]["closure_sha256"] == job["inputs_manifest_sha256"]
                        and result_body["result"].get("result_revision") == state["result_revision"]
                    )
                except (KeyError, TypeError, ValueError):
                    valid = False
                if not valid:
                    raise ValueError("Durable backup result version mismatch")
        attempts = _rows(
            conn, "SELECT * FROM durable_model_attempts WHERE run_id=? ORDER BY seq", (run_id,)
        )
        for attempt in attempts:
            if (
                attempt["ledger_id"] is not None
                and not conn.execute(
                    "SELECT 1 FROM agent_runs WHERE run_id=?", (attempt["ledger_id"],)
                ).fetchone()
            ):
                raise ValueError("Durable backup ledger reference failed")
            saved = conn.execute(
                "SELECT response_json FROM durable_attempt_bodies WHERE run_id=? AND seq=?",
                (run_id, attempt["seq"]),
            ).fetchone()
            if saved and (
                state["purge_state"] != "live" or _sha(saved[0]) != attempt["response_sha256"]
            ):
                raise ValueError("Durable backup response integrity failed")
            if (
                state["purge_state"] == "live"
                and attempt["status"] == "response_received"
                and saved is None
            ):
                raise ValueError("Durable backup response is missing")
        result.append(
            {
                **job,
                "status": state["status"],
                "state_version": state["state_version"],
                "purge_state": state["purge_state"],
                "result_id": state["result_id"],
                "result_revision": state["result_revision"],
                "workflow_id": run_id,
                "application_version": body["versions"]["application_version"] if body else None,
                "attempts": attempts,
            }
        )
    return result


def _inventory(business_path, system_path):
    with closing(_read(business_path)) as business:
        _check(business)
        jobs = _job_inventory(business)
        reviews = review_backup.inventory(business)
        tables = _tables(business)
        business_digests = {
            name: _table_digest(business, name)
            for name in sorted(tables)
            if name.startswith(("durable_", "qualification_", "review_")) or name == "agent_runs"
        }
    if system_path is None:
        if jobs:
            raise ValueError("Durable jobs require their DBOS system database")
        return None
    with closing(_read(system_path)) as system:
        _check(system)
        tables = _tables(system)
        if "workflow_status" not in tables or "dbos_migrations" not in tables:
            raise ValueError("Invalid DBOS system database")
        workflows = _rows(
            system,
            "SELECT workflow_uuid,status,name,application_version,parent_workflow_id "
            "FROM workflow_status ORDER BY workflow_uuid",
        )
        by_id = {job["run_id"]: job for job in jobs}
        seen, calculations = set(), set()
        for workflow in workflows:
            workflow_id = workflow["workflow_uuid"]
            child = workflow_id.endswith("-calculation")
            run_id = workflow_id.removesuffix("-calculation") if child else workflow_id
            if (
                run_id not in by_id
                or workflow["name"] != (CALCULATION_WORKFLOW_NAME if child else WORKFLOW_NAME)
                or workflow["parent_workflow_id"] != (run_id if child else None)
                or (
                    by_id[run_id]["purge_state"] != "purged"
                    and workflow["application_version"] != by_id[run_id]["application_version"]
                )
            ):
                raise ValueError("Durable backup workflow mapping failed")
            (calculations if child else seen).add(run_id)
        if not calculations <= seen:
            raise ValueError("Durable backup calculation parent is missing")
        for job in jobs:
            if (
                job["run_id"] not in seen
                and job["status"] != "queued"
                and job["purge_state"] != "purged"
            ):
                raise ValueError("Durable backup workflow is missing")
            if (
                job["status"] == "waiting_review"
                and job["purge_state"] != "purged"
                and job["run_id"] not in calculations
            ):
                raise ValueError("Durable backup completed calculation is missing")
        system_digests = {
            name: _table_digest(system, name)
            for name in sorted(tables)
            if name != BINDING_TABLE and not name.startswith("sqlite_")
        }
    result = {
        "jobs": jobs,
        "workflows": workflows,
        "business_table_sha256": business_digests,
        "system_table_sha256": system_digests,
    }
    if reviews is not None:
        result["reviews"] = reviews
    return result


def _stamp(path, binding):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {BINDING_TABLE} "
            "(id INTEGER PRIMARY KEY CHECK(id=1),bundle_id TEXT NOT NULL,"
            "mapping_sha256 TEXT NOT NULL)"
        )
        conn.execute(f"DELETE FROM {BINDING_TABLE}")
        conn.execute(
            f"INSERT INTO {BINDING_TABLE} VALUES (1,?,?)",
            (binding["bundle_id"], binding["mapping_sha256"]),
        )


def _compact(path):
    # Remove old private bytes and WAL content after logical deletion.
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _client(path):
    from dbos import DBOSClient
    from sqlalchemy import create_engine, event

    engine = create_engine(f"sqlite:///{Path(path).resolve()}")

    @event.listens_for(engine, "connect")
    def secure_delete(connection, _):
        connection.execute("PRAGMA secure_delete=ON")

    try:
        client = DBOSClient(system_database_engine=engine, retry_connection_errors=False)
    except Exception:
        engine.dispose()
        raise
    return client, engine


def prune_expired_jobs(business_path, system_path, *, now):
    """Erase expired/private job bodies and retry all previously purged histories.

    Kept attempt metadata and the full cost/reservation ledger are never changed.
    Failure is fatal to an offline restore; no partially cleaned bundle is released.
    """
    review_backup.prune_expired_reviews(business_path, now=now)
    with closing(sqlite3.connect(business_path)) as conn, conn:
        if "durable_jobs" not in _tables(conn):
            return []
        conn.execute("PRAGMA secure_delete=ON")
        expired = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT s.run_id FROM durable_job_sources s "
                "LEFT JOIN company_objects o ON o.object_id=s.object_id "
                "WHERE o.object_id IS NULL OR o.deleted_at IS NOT NULL "
                "OR o.retention_until<=? OR o.purge_state!='live'",
                (now,),
            )
        ]
        conn.executemany(
            "UPDATE durable_job_state SET purge_state='purged',purged_at=COALESCE(purged_at,?),"
            "quarantined=1,progress_json='{}',state_version=state_version+1,updated_at=? "
            "WHERE run_id=? AND purge_state!='purged'",
            [(now, now, run_id) for run_id in expired],
        )
        purged = [
            row[0]
            for row in conn.execute(
                "SELECT run_id FROM durable_job_state WHERE purge_state='purged' ORDER BY run_id"
            )
        ]
        for run_id in purged:
            conn.execute("DELETE FROM durable_job_inputs WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM durable_attempt_bodies WHERE run_id=?", (run_id,))
            conn.execute(
                "DELETE FROM durable_review_fixture_bodies WHERE event_id IN "
                "(SELECT event_id FROM durable_review_fixtures WHERE run_id=?)",
                (run_id,),
            )
            conn.execute(
                "UPDATE durable_job_state SET progress_json='{}' WHERE run_id=?", (run_id,)
            )
            conn.execute(
                "DELETE FROM qualification_result_bodies WHERE result_id IN "
                "(SELECT result_id FROM durable_job_state WHERE run_id=?)",
                (run_id,),
            )
            conn.execute(
                "UPDATE qualification_result_lifecycle SET purge_state='purged',"
                "purged_at=COALESCE(purged_at,?) WHERE result_id IN "
                "(SELECT result_id FROM durable_job_state WHERE run_id=?)",
                (now, run_id),
            )
    if purged and system_path is not None:
        client, engine = _client(system_path)
        try:
            for run_id in purged:
                client.cancel_workflow(run_id, cancel_children=True)
                client.delete_workflow(run_id, delete_children=True)
        finally:
            client.destroy()
            engine.dispose()
        _compact(system_path)
    if purged:
        _compact(business_path)
    return purged


def prepare_backup(business_path, system_path, *, now):
    # Validate before cleanup too: expiration must not conceal mismatched databases.
    _inventory(business_path, system_path)
    prune_expired_jobs(business_path, system_path, now=now)
    mapping = _inventory(business_path, system_path)
    if mapping is None:
        return None
    binding = {
        "bundle_id": uuid.uuid4().hex,
        "mapping_sha256": _sha(canonical_json(mapping)),
        "mapping": mapping,
    }
    for path in (business_path, system_path):
        _stamp(path, binding)
    return binding


def verify_backup(business_path, system_path, binding):
    mapping = _inventory(business_path, system_path)
    if mapping is None:
        if binding is not None:
            raise ValueError("Unexpected durable backup binding")
        return
    if (
        not isinstance(binding, dict)
        or set(binding) != {"bundle_id", "mapping_sha256", "mapping"}
        or not isinstance(binding["bundle_id"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", binding["bundle_id"])
        or binding["mapping"] != mapping
        or binding["mapping_sha256"] != _sha(canonical_json(mapping))
    ):
        raise ValueError("Durable backup snapshot mapping mismatch")
    for path in (business_path, system_path):
        with closing(_read(path)) as conn:
            if BINDING_TABLE not in _tables(conn) or conn.execute(
                f"SELECT id,bundle_id,mapping_sha256 FROM {BINDING_TABLE}"
            ).fetchall() != [(1, binding["bundle_id"], binding["mapping_sha256"])]:
                raise ValueError("Durable backup database pairing mismatch")


def quarantine_after_restore(business_path, system_path, *, now):
    with closing(sqlite3.connect(business_path)) as conn, conn:
        if "durable_jobs" not in _tables(conn):
            return {"quarantined_jobs": 0, "purged_job_ids": []}
        count = conn.execute(
            "UPDATE durable_job_state SET quarantined=1,state_version=state_version+1"
        ).rowcount
        run_ids = [
            row[0] for row in conn.execute("SELECT run_id FROM durable_jobs ORDER BY run_id")
        ]
    if system_path is not None:
        client, engine = _client(system_path)
        try:
            for run_id in run_ids:
                client.cancel_workflow(run_id, cancel_children=True)
        finally:
            client.destroy()
            engine.dispose()
    purged = prune_expired_jobs(business_path, system_path, now=now)
    if system_path is not None:
        _compact(system_path)
    return {"quarantined_jobs": count, "purged_job_ids": purged}
