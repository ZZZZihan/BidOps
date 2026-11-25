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
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bidops.notice_data.schemas import AttachmentInput
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
        tables = _tables(conn)
        return any(
            name in tables and conn.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()
            for name in (
                "durable_jobs",
                "ingestion_runs",
                "ingestion_schedules",
                "ingestion_schedule_versions",
                "ingestion_schedule_ticks",
            )
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


def _attachment_inventory(conn):
    from bidops.notice_data.attachment_parser import OPTIONS, canonical, sha
    from bidops.notice_data.schemas import AttachmentRecord, PDFParseReceipt

    tables = _tables(conn)
    if "nd_attachment_receipts" not in tables:
        migrated = (
            "schema_migrations" in tables
            and conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version='011_attachment_receipts.sql'"
            ).fetchone()
        )
        declared = False
        if "nd_attachments" in tables:
            try:
                declared = any(
                    json.loads(row[0]).get("parser_receipt_sha256") is not None
                    for row in conn.execute("SELECT metadata_json FROM nd_attachments")
                )
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Attachment backup metadata integrity failed") from None
        if migrated or declared:
            raise ValueError("Attachment backup receipt table is missing")
        return
    if "nd_attachments" not in tables:
        raise ValueError("Attachment backup parent table is missing")
    receipts = {r["record_id"]: r for r in _rows(conn, "SELECT * FROM nd_attachment_receipts")}
    seen = set()
    for row in _rows(conn, "SELECT * FROM nd_attachments"):
        try:
            record = AttachmentRecord.model_validate_json(row["metadata_json"])
            if record.parser_receipt_sha256 is None:
                if record.record_id in receipts:
                    raise ValueError("unexpected receipt")
                continue
            saved = receipts[record.record_id]
            receipt = PDFParseReceipt.model_validate_json(saved["receipt_json"])
            normalized = row["normalized_text"]
            metadata = {
                k: v
                for k, v in record.model_dump(mode="json").items()
                if k in AttachmentInput.model_fields
            }
            metadata["observed_at"] = record.observed_at.astimezone(UTC).isoformat()
            expected_id = sha(
                canonical(
                    [
                        "attachment_record_v2",
                        record.attachment_id,
                        metadata,
                        record.raw_sha256,
                        record.text_sha256,
                        record.parser_receipt_sha256,
                    ]
                )
            )
            if (
                record.record_id != row["record_id"]
                or record.record_id != expected_id
                or record.parent_version_id != row["parent_version_id"]
                or record.attachment_id != row["attachment_id"]
                or record.acquisition_status != row["acquisition_status"]
                or record.normalizer_version != "normalized_text_v2"
                or saved["receipt_sha256"] != record.parser_receipt_sha256
                or sha(saved["receipt_json"]) != record.parser_receipt_sha256
                or receipt.raw_sha256 != row["raw_sha256"]
                or record.raw_sha256 != receipt.raw_sha256
                or receipt.text_sha256 != row["text_sha256"]
                or record.text_sha256 != receipt.text_sha256
                or not isinstance(normalized, str)
                or sha(normalized) != receipt.text_sha256
                or receipt.options_sha256 != sha(canonical(OPTIONS))
                or receipt.page_map_sha256
                != sha(canonical([p.model_dump() for p in receipt.pages]))
                or receipt.status != record.parse_status
                or receipt.status != row["parse_status"]
                or receipt.error_code != record.error_code
                or len(normalized) > receipt.limits.max_text_chars
            ):
                raise ValueError("receipt binding")
            offset = 0
            for page in receipt.pages:
                if page.status == "text" and offset:
                    if normalized[offset : offset + 1] != "\n":
                        raise ValueError("separator")
                    offset += 1
                if (
                    page.start != offset
                    or page.end > len(normalized)
                    or sha(normalized[page.start : page.end]) != page.text_sha256
                    or page.end - page.start > receipt.limits.max_page_chars
                ):
                    raise ValueError("span")
                offset = page.end
            if offset != len(normalized):
                raise ValueError("coverage")
            seen.add(record.record_id)
        except (ValueError, TypeError, KeyError):
            raise ValueError("Attachment backup receipt integrity failed") from None
    if seen != set(receipts):
        raise ValueError("Attachment backup receipt closure failed")


def _ingestion_inventory(conn):
    from bidops.ingestion.schemas import HttpRun, ManualImport, ScheduleCommand, decode_body
    from bidops.sources import Source

    tables = _tables(conn)
    if "ingestion_runs" not in tables:
        return None
    runs = _rows(conn, "SELECT * FROM ingestion_runs ORDER BY run_id")
    states = {r["run_id"]: r for r in _rows(conn, "SELECT * FROM ingestion_run_state")}
    if set(states) != {r["run_id"] for r in runs}:
        raise ValueError("Ingestion backup state closure failed")
    policies = {
        r["policy_sha256"]: r for r in _rows(conn, "SELECT * FROM ingestion_policy_revisions")
    }
    for policy in policies.values():
        try:
            source = Source.model_validate_json(policy["body_json"])
            if (
                _sha(policy["body_json"]) != policy["policy_sha256"]
                or source.id != policy["source_id"]
                or (source.policy.revision if source.policy else None) != policy["revision"]
            ):
                raise ValueError("policy")
        except (ValueError, TypeError):
            raise ValueError("Ingestion backup policy integrity failed") from None
    result = []
    requests = {}
    for run in runs:
        try:
            bodies = _rows(
                conn, "SELECT body_json FROM ingestion_inputs WHERE run_id=?", (run["run_id"],)
            )
            if len(bodies) != 1 or _sha(bodies[0]["body_json"]) != run["body_sha256"]:
                raise ValueError("body")
            body = json.loads(bodies[0]["body_json"])
            request = body["request"]
            requests[run["run_id"]] = request
            (ManualImport if run["method"] == "manual_import" else HttpRun).model_validate(request)
            policy = policies[run["policy_sha256"]]
            if (
                _sha(canonical_json(request)) != run["input_sha256"]
                or request["source_id"] != run["source_id"]
                or policy["source_id"] != run["source_id"]
                or policy["revision"] != run["policy_revision"]
                or body["versions"]["application_version"] != run["application_version"]
                or not re.fullmatch(r"[a-f0-9]{64}", run["application_version"])
                or request["idempotency_key"] != run["idempotency_key"]
            ):
                raise ValueError("inputs")
            pages = _rows(
                conn,
                "SELECT * FROM ingestion_pages WHERE run_id=? ORDER BY page_seq",
                (run["run_id"],),
            )
            if [p["page_seq"] for p in pages] != list(range(states[run["run_id"]]["next_page"])):
                raise ValueError("page sequence")
            for page in pages:
                attempt = _rows(
                    conn,
                    "SELECT * FROM ingestion_page_attempts WHERE run_id=? AND page_seq=?",
                    (run["run_id"], page["page_seq"]),
                )
                receipt = json.loads(page["receipt_json"])
                if (
                    len(attempt) != 1
                    or _sha(page["receipt_json"]) != page["receipt_sha256"]
                    or attempt[0]["from_cursor_version"] != page["from_cursor_version"]
                    or attempt[0]["observed_at"] != page["observed_at"]
                    or receipt["run_id"] != run["run_id"]
                    or receipt["page_seq"] != page["page_seq"]
                    or receipt["page_sha256"] != page["page_sha256"]
                    or receipt["from_cursor_version"] != page["from_cursor_version"]
                    or canonical_json(receipt["cursor_after"]) != page["cursor_after_json"]
                ):
                    raise ValueError("page binding")
                expected = (
                    request["pages"][page["page_seq"]]["items"]
                    if run["method"] == "manual_import"
                    else request["items"]
                )
                if (
                    len(receipt["version_ids"]) != len(expected)
                    or receipt["observed_at"]
                    != datetime.fromtimestamp(page["observed_at"], UTC).isoformat()
                    or len(receipt["attachment_record_ids"])
                    != sum(len(i.get("attachments", [])) for i in expected)
                ):
                    raise ValueError("page members")
                for version, declared in zip(receipt["version_ids"], expected, strict=True):
                    stored = conn.execute(
                        "SELECT v.raw_sha256,n.source_url FROM nd_versions v "
                        "JOIN nd_logical_notices n USING(logical_notice_id) "
                        "WHERE v.version_id=?",
                        (version,),
                    ).fetchone()
                    if (
                        not stored
                        or stored[1] != declared["source_url"]
                        or (
                            run["method"] == "manual_import"
                            and stored[0]
                            != hashlib.sha256(decode_body(declared["body_base64"])).hexdigest()
                        )
                    ):
                        raise ValueError("notice reference")
                for attachment in receipt["attachment_record_ids"]:
                    parent = conn.execute(
                        "SELECT parent_version_id FROM nd_attachments WHERE record_id=?",
                        (attachment,),
                    ).fetchone()
                    if not parent or parent[0] not in receipt["version_ids"]:
                        raise ValueError("attachment reference")
            result.append(
                {
                    **{k: v for k, v in run.items() if k != "idempotency_key"},
                    "status": states[run["run_id"]]["status"],
                    "state_version": states[run["run_id"]]["state_version"],
                    "quarantined": states[run["run_id"]]["quarantined"],
                }
            )
        except (ValueError, TypeError, KeyError):
            raise ValueError("Ingestion backup fixed input closure failed") from None
    versions = (
        _rows(conn, "SELECT * FROM ingestion_schedule_versions ORDER BY native_name")
        if "ingestion_schedule_versions" in tables
        else []
    )
    schedules = _rows(conn, "SELECT * FROM ingestion_schedules ORDER BY schedule_id")
    for version in versions:
        try:
            command = ScheduleCommand.model_validate_json(version["body_json"])
            if (
                _sha(version["body_json"]) != version["body_sha256"]
                or command.schedule_id != version["schedule_id"]
                or command.source_id != version["source_id"]
                or command.expected_state_version + 1 != version["state_version"]
                or version["native_name"]
                != "source-schedule-"
                + _sha(
                    canonical_json(
                        [version["schedule_id"], version["state_version"], version["body_sha256"]]
                    )
                )[:32]
                or policies[version["policy_sha256"]]["source_id"] != version["source_id"]
                or not re.fullmatch(r"[a-f0-9]{64}", version["application_version"])
            ):
                raise ValueError("generation")
        except (ValueError, TypeError, KeyError):
            raise ValueError("Ingestion backup schedule integrity failed") from None
    by_name = {v["native_name"]: v for v in versions}
    for run in runs:
        generation = by_name.get(run.get("schedule_native_name"))
        if run["trigger_kind"] == "schedule":
            if (
                generation is None
                or run["method"] != "http_fetch"
                or any(
                    run[key] != generation[key]
                    for key in ("owner_id", "source_id", "policy_sha256", "application_version")
                )
                or requests[run["run_id"]]["items"] != json.loads(generation["body_json"])["items"]
            ):
                raise ValueError("Ingestion backup run schedule generation failed")
        elif run.get("schedule_native_name") is not None:
            raise ValueError("Ingestion backup manual run has a schedule generation")
    commands = {
        (row["owner_id"], row["idempotency_key"]): row
        for row in _rows(conn, "SELECT * FROM ingestion_schedule_commands")
    }
    for generation in versions:
        body = json.loads(generation["body_json"])
        command = commands.get((generation["owner_id"], body["idempotency_key"]))
        if command is None or any(
            command[key] != generation[value]
            for key, value in (
                ("schedule_id", "schedule_id"),
                ("input_sha256", "body_sha256"),
                ("application_version", "application_version"),
            )
        ):
            raise ValueError("Ingestion backup schedule command closure failed")
    for schedule in schedules:
        version = by_name.get(schedule["native_name"])
        if version is None or any(
            schedule[k] != version[k]
            for k in (
                "schedule_id",
                "owner_id",
                "source_id",
                "body_json",
                "body_sha256",
                "policy_sha256",
                "application_version",
            )
        ):
            raise ValueError("Ingestion backup schedule generation failed")
    return {
        "runs": result,
        "schedule_versions": [{k: v for k, v in r.items() if k != "body_json"} for r in versions],
        "schedules": [{k: v for k, v in r.items() if k != "body_json"} for r in schedules],
        "ticks": _rows(conn, "SELECT * FROM ingestion_schedule_ticks ORDER BY native_id"),
    }


def _known_inputs(run_id):
    # Serialize only a server-known scalar; never unpickle a backup value.
    from dbos._serialization import DefaultSerializer

    return DefaultSerializer().serialize({"args": (run_id,), "kwargs": {}})


def _known_schedule_inputs(native_id, native_name):
    """Rebuild DBOS 3.3 scheduler arguments from a verified generation and ISO ID.

    Cron/backfill uses ZoneInfo('UTC'); trigger_schedule uses timezone.utc.
    The input is never decoded, evaluated, or unpickled, including at restore.
    """
    from dbos._serialization import DefaultSerializer

    prefix = "sched-" + native_name + "-"
    if not native_id.startswith(prefix):
        raise ValueError("Ingestion backup scheduled identity failed")
    stamp = native_id[len(prefix) :]
    trigger = stamp.startswith("trigger-")
    stamp = stamp.removeprefix("trigger-") if trigger else stamp
    try:
        if not 20 <= len(stamp) <= 40:
            raise ValueError("stamp")
        scheduled = datetime.fromisoformat(stamp)
        if (
            scheduled.tzinfo is None
            or scheduled.utcoffset().total_seconds() != 0
            or scheduled.isoformat() != stamp
        ):
            raise ValueError("stamp")
    except (ValueError, TypeError):
        raise ValueError("Ingestion backup scheduled identity failed") from None
    scheduled = scheduled.replace(tzinfo=UTC if trigger else ZoneInfo("UTC"))
    return DefaultSerializer().serialize(
        {"args": (scheduled, {"native_name": native_name}), "kwargs": {}}
    )


def _check_ingestion_workflows(system, workflows, inventory, business):
    from dbos._serialization import DefaultSerializer

    runs = {r["native_id"]: r for r in inventory["runs"]}
    versions = {v["native_name"]: v for v in inventory["schedule_versions"]}
    ticks = {t["native_id"]: t for t in inventory["ticks"]}
    schedules = _rows(system, "SELECT * FROM workflow_schedules")
    schedules = {s["schedule_name"]: s for s in schedules}
    seen_runs, seen_ticks = set(), set()
    for native in schedules.values():
        version = versions.get(native["schedule_name"])
        if version is None:
            raise ValueError("Ingestion backup unknown native schedule")
        command = json.loads(
            business.execute(
                "SELECT body_json FROM ingestion_schedule_versions WHERE native_name=?",
                (native["schedule_name"],),
            ).fetchone()[0]
        )
        if (
            native["workflow_name"] != "bidops_ingestion_schedule_v1"
            or native["workflow_class_name"] is not None
            or native["queue_name"] != "bidops_ingestion_schedule_v1"
            or native["schedule"] != command["cron"]
            or native["cron_timezone"] != "UTC"
            or native["automatic_backfill"]
            or native["application_name"] != "bidops-jobs"
            or native["context"]
            != DefaultSerializer().serialize({"native_name": native["schedule_name"]})
        ):
            raise ValueError("Ingestion backup native schedule mapping failed")
    for workflow in workflows:
        native_id = workflow["workflow_uuid"]
        if native_id in runs:
            run = runs[native_id]
            if (
                workflow["name"] != "bidops_ingestion_run_v1"
                or workflow["parent_workflow_id"] is not None
                or workflow["application_version"] != run["application_version"]
                or workflow["queue_name"] != "bidops_ingestion_v1"
                or workflow["schedule_name"] is not None
                or workflow["class_name"] is not None
                or workflow["config_name"] is not None
                or workflow["application_name"] != "bidops-jobs"
                or workflow["serialization"] != "py_pickle"
                or workflow["inputs"] != _known_inputs(run["run_id"])
            ):
                raise ValueError("Ingestion backup workflow mapping failed")
            seen_runs.add(native_id)
        else:
            version = versions.get(workflow["schedule_name"])
            tick = ticks.get(native_id)
            if (
                version is None
                or workflow["schedule_name"] not in schedules
                or workflow["name"] != "bidops_ingestion_schedule_v1"
                or workflow["queue_name"] != "bidops_ingestion_schedule_v1"
                or workflow["parent_workflow_id"] is not None
                or workflow["class_name"] is not None
                or workflow["config_name"] is not None
                or workflow["application_name"] != "bidops-jobs"
                or workflow["application_version"] != version["application_version"]
                or (
                    tick is not None
                    and (
                        tick["schedule_id"] != version["schedule_id"]
                        or tick["application_version"] != version["application_version"]
                    )
                )
            ):
                raise ValueError("Ingestion backup scheduled workflow mapping failed")
            if workflow["serialization"] not in {None, "py_pickle"} or workflow[
                "inputs"
            ] != _known_schedule_inputs(native_id, version["native_name"]):
                raise ValueError("Ingestion backup scheduled inputs failed")
            if tick is not None and tick["run_id"] is not None:
                linked = next(
                    (run for run in runs.values() if run["run_id"] == tick["run_id"]), None
                )
                if (
                    linked is None
                    or linked["schedule_native_name"] != version["native_name"]
                    or linked["trigger_kind"] != "schedule"
                ):
                    raise ValueError("Ingestion backup scheduled run linkage failed")
            seen_ticks.add(native_id)
    for run in runs.values():
        if run["native_id"] not in seen_runs and run["status"] != "queued":
            raise ValueError("Ingestion backup workflow is missing")
    if set(ticks) - seen_ticks:
        raise ValueError("Ingestion backup schedule tick workflow is missing")
    return seen_runs | seen_ticks


def _inventory(business_path, system_path, *, legacy=False):
    with closing(_read(business_path)) as business:
        _check(business)
        jobs = _job_inventory(business)
        reviews = review_backup.inventory(business)
        ingestion = _ingestion_inventory(business)
        _attachment_inventory(business)
        ingestion_records = ingestion is not None and any(ingestion.values())
        if legacy and (
            ingestion_records
            or (
                "nd_attachment_receipts" in _tables(business)
                and business.execute("SELECT 1 FROM nd_attachment_receipts LIMIT 1").fetchone()
            )
        ):
            raise ValueError("Legacy durable mapping cannot contain ingestion records")
        tables = _tables(business)
        business_digests = {
            name: _table_digest(business, name)
            for name in sorted(tables)
            if name.startswith(("durable_", "qualification_", "review_"))
            or name == "agent_runs"
            or (not legacy and name.startswith(("ingestion_", "nd_")))
        }
    if system_path is None:
        if jobs or ingestion_records:
            raise ValueError("Durable jobs require their DBOS system database")
        return None
    with closing(_read(system_path)) as system:
        _check(system)
        tables = _tables(system)
        if "workflow_status" not in tables or "dbos_migrations" not in tables:
            raise ValueError("Invalid DBOS system database")
        inputs_join = ""
        input_column = "w.inputs"
        if "workflow_input" in tables:
            if system.execute(
                "SELECT 1 FROM workflow_status w JOIN workflow_input i USING(workflow_uuid) "
                "WHERE w.inputs IS NOT NULL AND i.inputs IS NOT NULL AND w.inputs!=i.inputs LIMIT 1"
            ).fetchone():
                raise ValueError("Durable backup workflow inputs conflict")
            inputs_join = " LEFT JOIN workflow_input i USING(workflow_uuid)"
            input_column = "COALESCE(i.inputs,w.inputs)"
        workflows = _rows(
            system,
            "SELECT w.workflow_uuid,w.status,w.name,w.application_version,w.parent_workflow_id,"
            "w.queue_name,w.schedule_name,w.serialization," + input_column + " AS inputs,"
            "w.class_name,w.config_name,w.application_name FROM workflow_status w"
            + inputs_join
            + " ORDER BY w.workflow_uuid",
        )
        by_id = {job["run_id"]: job for job in jobs}
        seen, calculations = set(), set()
        other_workflows = []
        for workflow in workflows:
            workflow_id = workflow["workflow_uuid"]
            child = workflow_id.endswith("-calculation")
            run_id = workflow_id.removesuffix("-calculation") if child else workflow_id
            if run_id not in by_id:
                other_workflows.append(workflow)
                continue
            if (
                workflow["name"] != (CALCULATION_WORKFLOW_NAME if child else WORKFLOW_NAME)
                or workflow["parent_workflow_id"] != (run_id if child else None)
                or (
                    by_id[run_id]["purge_state"] != "purged"
                    and workflow["application_version"] != by_id[run_id]["application_version"]
                )
            ):
                raise ValueError("Durable backup workflow mapping failed")
            (calculations if child else seen).add(run_id)
        with closing(_read(business_path)) as business:
            _check_ingestion_workflows(
                system,
                other_workflows,
                ingestion or {"runs": [], "schedules": [], "schedule_versions": [], "ticks": []},
                business,
            )
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
        "workflows": [
            {
                k: w[k]
                for k in (
                    "workflow_uuid",
                    "status",
                    "name",
                    "application_version",
                    "parent_workflow_id",
                )
            }
            for w in workflows
        ],
        "business_table_sha256": business_digests,
        "system_table_sha256": system_digests,
    }
    if reviews is not None:
        result["reviews"] = reviews
    if ingestion is not None and not legacy:
        result["ingestion"] = ingestion
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


def verify_standalone_database(business_path):
    _inventory(business_path, None)


def verify_backup(business_path, system_path, binding):
    stored = binding.get("mapping", {}) if isinstance(binding, dict) else {}
    legacy = "ingestion" not in stored and not any(
        name.startswith(("ingestion_", "nd_")) for name in stored.get("business_table_sha256", {})
    )
    mapping = _inventory(business_path, system_path, legacy=legacy)
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
        tables = _tables(conn)
        count = 0
        run_ids = []
        ingestion_count = schedule_count = 0
        if "durable_jobs" in tables:
            count = conn.execute(
                "UPDATE durable_job_state SET quarantined=1,state_version=state_version+1"
            ).rowcount
            run_ids = [row[0] for row in conn.execute("SELECT run_id FROM durable_jobs")]
        if "ingestion_runs" in tables:
            ingestion_count = conn.execute(
                "UPDATE ingestion_run_state SET quarantined=1,state_version=state_version+1,"
                "updated_at=?",
                (now,),
            ).rowcount
            schedule_count = conn.execute(
                "UPDATE ingestion_schedules SET quarantined=1,enabled=0,"
                "state_version=state_version+1,updated_at=?",
                (now,),
            ).rowcount
            conn.execute("UPDATE ingestion_source_state SET active_run_id=NULL")
    if system_path is not None:
        client, engine = _client(system_path)
        try:
            for run_id in run_ids:
                client.cancel_workflow(run_id, cancel_children=True)
            with closing(_read(system_path)) as system:
                ingestion_ids = [
                    row[0]
                    for row in system.execute(
                        "SELECT workflow_uuid FROM workflow_status WHERE name IN "
                        "('bidops_ingestion_run_v1','bidops_ingestion_schedule_v1')"
                    )
                ]
                schedule_names = [
                    row[0] for row in system.execute("SELECT schedule_name FROM workflow_schedules")
                ]
            for name in schedule_names:
                client.pause_schedule(name)
            for native_id in ingestion_ids:
                client.cancel_workflow(native_id, cancel_children=True)
        finally:
            client.destroy()
            engine.dispose()
    purged = prune_expired_jobs(business_path, system_path, now=now)
    if system_path is not None:
        _compact(system_path)
    return {
        "quarantined_jobs": count,
        "purged_job_ids": purged,
        "quarantined_ingestion_runs": ingestion_count,
        "quarantined_ingestion_schedules": schedule_count,
    }
