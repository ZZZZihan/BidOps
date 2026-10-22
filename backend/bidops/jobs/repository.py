"""Immutable business inputs and CAS state, never a scheduler or execution engine.

Only get_system/transition/attempt and fixture methods are internal worker APIs.
Public callers must use get/list/request_cancel with a server-built ToolContext.
Private bodies are deleted independently of the durable backend's own history;
prune returns run IDs so the integration can also erase that backend history.
"""

import hashlib
import json
import re
import uuid

from pydantic import ValidationError
from sqlalchemy import text

from bidops.company.authorization import CompanyError, ToolContext, rows, transaction
from bidops.jobs.schemas import AttemptStart, JobState
from bidops.qualification.contracts import AssessmentGroup
from bidops.qualification.sources import ScreeningInputManifest, canonical_json
from bidops.screening.schemas import ReviewEvent, TaskExecution

TERMINAL = {"cancelled", "completed", "failed", "timed_out"}
TRANSITIONS = {
    "queued": {"running", "cancel_requested", "failed", "timed_out", "interrupted"},
    "running": {
        "waiting_review",
        "completed",
        "cancel_requested",
        "failed",
        "timed_out",
        "interrupted",
    },
    "waiting_review": {
        "running",
        "completed",
        "cancel_requested",
        "failed",
        "timed_out",
        "interrupted",
    },
    "cancel_requested": {"cancelled"},
    "interrupted": {"running", "waiting_review", "cancel_requested", "failed", "timed_out"},
}


def _json(value, *, limit=2_000_000):
    try:
        encoded = canonical_json(value)
    except (TypeError, ValueError, RecursionError):
        raise CompanyError("job_invalid_json", 422) from None
    if len(encoded.encode()) > limit:
        raise CompanyError("job_body_too_large", 413)
    return encoded


def _sha(encoded):
    return hashlib.sha256(encoded.encode()).hexdigest()


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise CompanyError(f"job_invalid_{name}", 422)
    return value


def _version(value):
    if type(value) is not int or value < 0:
        raise CompanyError("job_invalid_state_version", 422)


class JobsRepository:
    def __init__(self, db, source_adapter):
        self.db, self.sources = db, source_adapter
        self.auth = source_adapter.company.auth

    def _row(self, run_id, conn=None):
        found = rows(
            self.db,
            "SELECT j.*,s.status,s.state_version,s.progress_json,s.result_id,s.result_revision,"
            "s.error_code,s.updated_at,s.quarantined,s.purge_state FROM durable_jobs j "
            "JOIN durable_job_state s ON s.run_id=j.run_id WHERE j.run_id=:id",
            {"id": run_id},
            conn,
        )
        if not found:
            raise CompanyError("job_not_found")
        return found[0]

    def _authorize(self, context, run_id, conn=None, *, quarantine=False):
        self.auth.require_subject(context, conn)
        record = self._row(run_id, conn)
        if (
            record["owner_id"] != context.owner_id
            or record["purge_state"] != "live"
            or (record["quarantined"] and not quarantine)
        ):
            raise CompanyError("job_not_found")
        for ref in rows(
            self.db,
            "SELECT object_id FROM durable_job_sources WHERE run_id=:id",
            {"id": run_id},
            conn,
        ):
            self.auth.require(context, ref["object_id"], "read", conn)
        return record

    def _body(self, record, conn=None):
        found = rows(
            self.db,
            "SELECT body_json FROM durable_job_inputs WHERE run_id=:id",
            {"id": record["run_id"]},
            conn,
        )
        if not found or record["purge_state"] != "live":
            raise CompanyError("job_not_found")
        encoded = found[0]["body_json"]
        if _sha(encoded) != record["body_sha256"]:
            raise CompanyError("job_integrity_failed", 409)
        body = json.loads(encoded)
        try:
            manifest = ScreeningInputManifest.model_validate(body["manifest"])
        except (KeyError, TypeError, ValueError):
            raise CompanyError("job_integrity_failed", 409) from None
        if (
            _sha(_json(body["request"])) != record["input_sha256"]
            or manifest.closure_sha256 != record["inputs_manifest_sha256"]
        ):
            raise CompanyError("job_integrity_failed", 409)
        refs = rows(
            self.db,
            "SELECT object_id,version_id,sha256 FROM durable_job_sources WHERE run_id=:id",
            {"id": record["run_id"]},
            conn,
        )
        if {tuple(ref[k] for k in ("object_id", "version_id", "sha256")) for ref in refs} != {
            ref.key() for ref in manifest.company_revisions
        }:
            raise CompanyError("job_integrity_failed", 409)
        return body

    def _render(self, record, body):
        state = JobState(
            execution=TaskExecution(
                run_id=record["run_id"],
                status=record["status"],
                result_revision=record["result_revision"],
                failure_reason=record["error_code"],
            ),
            state_version=record["state_version"],
            progress=json.loads(record["progress_json"]),
            result_id=record["result_id"],
            updated_at=record["updated_at"],
            quarantined=bool(record["quarantined"]),
            purge_state=record["purge_state"],
        )
        return {
            **{
                key: record[key]
                for key in (
                    "run_id",
                    "owner_id",
                    "idempotency_key",
                    "input_sha256",
                    "inputs_manifest_sha256",
                    "created_at",
                )
            },
            "status": state.execution.status,
            "state_version": state.state_version,
            "progress": state.progress,
            "result_id": state.result_id,
            "result_revision": state.execution.result_revision,
            "error_code": state.execution.failure_reason,
            "updated_at": state.updated_at,
            "quarantined": state.quarantined,
            "purge_state": state.purge_state,
            **body,
        }

    def get_system(self, run_id):
        """Trusted worker inspection only; this deliberately makes no ACL decision."""
        record = self._row(run_id)
        return self._render(record, self._body(record))

    def list_system(self, *, statuses=None):
        """Internal recovery inventory, including quarantined live jobs."""
        found = rows(
            self.db,
            "SELECT run_id,status FROM durable_job_state "
            "WHERE purge_state='live' ORDER BY updated_at,run_id",
        )
        return [
            self.get_system(row["run_id"])
            for row in found
            if statuses is None or row["status"] in statuses
        ]

    def get(self, context, run_id):
        record = self._authorize(context, run_id)
        body = self._body(record)
        self.sources.recheck(context, body["manifest"], for_execution=False)
        source_state = self.sources.changes(context, body["manifest"])
        with transaction(self.db) as conn:
            record = self._authorize(context, run_id, conn)
            body = self._body(record, conn)
            result = self._render(record, body)
        return {**result, "source_state": source_state}

    def get_or_create(
        self, context, *, idempotency_key, request, prepare, max_active_jobs=None, link=None
    ):
        """Hash the untouched request before resolving defaults or source versions.

        prepare is called only for an absent owner/key. Concurrent first submits
        may both prepare; the serialized winner alone fixes the persisted as_of.
        A replay never replaces that winner's manifest/settings/versions. Admission
        is serialized with insertion; replaying an existing key takes no capacity.
        """
        _identifier(idempotency_key, "idempotency_key")
        if max_active_jobs is not None and (
            type(max_active_jobs) is not int or not 1 <= max_active_jobs <= 100
        ):
            raise CompanyError("job_invalid_capacity", 422)
        if not isinstance(request, dict):
            raise CompanyError("job_request_must_be_object", 422)
        request_json = _json(request)
        input_sha = _sha(request_json)
        self.auth.require_creator(context)
        existing = rows(
            self.db,
            "SELECT run_id,input_sha256 FROM durable_jobs "
            "WHERE owner_id=:owner AND idempotency_key=:key",
            {"owner": context.owner_id, "key": idempotency_key},
        )
        if existing:
            if existing[0]["input_sha256"] != input_sha:
                raise CompanyError("job_idempotency_conflict", 409)
            if link is not None:
                with transaction(self.db) as conn:
                    record = self._authorize(context, existing[0]["run_id"], conn)
                    link(conn, self._render(record, self._body(record, conn)))
            return {**self.get(context, existing[0]["run_id"]), "created": False}
        prepared = prepare()
        if not isinstance(prepared, dict) or set(prepared) != {"manifest", "settings", "versions"}:
            raise CompanyError("job_prepared_input_invalid", 422)
        if any(
            not isinstance(prepared[key], dict) or not prepared[key]
            for key in ("settings", "versions")
        ):
            raise CompanyError("job_settings_and_versions_required", 422)
        manifest = ScreeningInputManifest.model_validate(prepared["manifest"])
        self.sources.recheck(context, manifest)
        body = {
            "request": json.loads(request_json),
            "manifest": manifest.model_dump(mode="json"),
            "settings": prepared["settings"],
            "versions": prepared["versions"],
        }
        encoded = _json(body)
        run_id, now = uuid.uuid4().hex, self.auth.clock()
        with transaction(self.db) as conn:
            self.auth.require_creator(context, conn)
            for ref in manifest.company_revisions:
                self.auth.require(context, ref.object_id, "read", conn)
            found = rows(
                self.db,
                "SELECT run_id,input_sha256 FROM durable_jobs "
                "WHERE owner_id=:owner AND idempotency_key=:key",
                {"owner": context.owner_id, "key": idempotency_key},
                conn,
            )
            created = not found
            if found:
                if found[0]["input_sha256"] != input_sha:
                    raise CompanyError("job_idempotency_conflict", 409)
                run_id = found[0]["run_id"]
            else:
                if max_active_jobs is not None:
                    active = rows(
                        self.db,
                        "SELECT COUNT(*) AS n FROM durable_job_state WHERE purge_state='live' "
                        "AND status IN ('queued','running','waiting_review',"
                        "'interrupted','cancel_requested')",
                        conn=conn,
                    )[0]["n"]
                    if active >= max_active_jobs:
                        raise CompanyError("job_capacity_exhausted", 429)
                conn.execute(
                    text(
                        "INSERT INTO durable_jobs "
                        "VALUES (:id,:owner,:key,:input,:manifest,:body,:now)"
                    ),
                    {
                        "id": run_id,
                        "owner": context.owner_id,
                        "key": idempotency_key,
                        "input": input_sha,
                        "manifest": manifest.closure_sha256,
                        "body": _sha(encoded),
                        "now": now,
                    },
                )
                conn.execute(
                    text("INSERT INTO durable_job_inputs VALUES (:id,:body)"),
                    {"id": run_id, "body": encoded},
                )
                conn.execute(
                    text("INSERT INTO durable_job_state(run_id,updated_at) VALUES (:id,:now)"),
                    {"id": run_id, "now": now},
                )
                for ref in manifest.company_revisions:
                    conn.execute(
                        text("INSERT INTO durable_job_sources VALUES (:id,:object,:version,:sha)"),
                        {
                            "id": run_id,
                            "object": ref.object_id,
                            "version": ref.version_id,
                            "sha": ref.sha256,
                        },
                    )
            if link is not None:
                record = self._authorize(context, run_id, conn)
                link(conn, self._render(record, self._body(record, conn)))
        return {**self.get(context, run_id), "created": created}

    def list(self, context, *, offset=0, limit=20):
        self.auth.require_subject(context)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise CompanyError("job_invalid_pagination", 422)
        allowed = []
        for row in rows(
            self.db,
            "SELECT run_id FROM durable_jobs WHERE owner_id=:owner ORDER BY created_at DESC,run_id",
            {"owner": context.owner_id},
        ):
            try:
                record = self.get(context, row["run_id"])
            except CompanyError as error:
                if error.status_code in {403, 404, 409}:
                    continue
                raise
            allowed.append(
                {
                    key: record[key]
                    for key in (
                        "run_id",
                        "status",
                        "state_version",
                        "created_at",
                        "updated_at",
                        "result_id",
                        "result_revision",
                    )
                }
            )
        with transaction(self.db) as conn:
            self.auth.require_subject(context, conn)
            for record in allowed:
                self._authorize(context, record["run_id"], conn)
        return {
            "items": allowed[offset : offset + limit],
            "total": len(allowed),
            "offset": offset,
            "limit": limit,
        }

    def require_execution(self, run_id):
        record = self.get_system(run_id)
        context = ToolContext(record["owner_id"])
        self.sources.recheck(context, record["manifest"])
        with transaction(self.db) as conn:
            self.auth.require_creator(context, conn)
            current = self._authorize(context, run_id, conn)
            if current["status"] in TERMINAL | {"cancel_requested"}:
                raise CompanyError("job_not_executable", 409)
            record = self._render(current, self._body(current, conn))
        return record

    def _validate_result(self, current, result_id, result_revision, status, conn):
        if result_id is None:
            if result_revision is not None:
                raise CompanyError("job_result_reference_required", 422)
            return
        if result_revision is None:
            raise CompanyError("job_result_reference_required", 422)
        found = rows(
            self.db,
            "SELECT r.owner_id,r.kind,r.body_sha256,b.body_json,"
            "l.quarantined,l.purge_state FROM qualification_results r "
            "JOIN qualification_result_bodies b ON b.result_id=r.result_id "
            "JOIN qualification_result_lifecycle l ON l.result_id=r.result_id "
            "WHERE r.result_id=:id",
            {"id": result_id},
            conn,
        )
        if not found:
            raise CompanyError("job_result_reference_invalid", 409)
        row = found[0]
        if (
            row["owner_id"] != current["owner_id"]
            or row["kind"] != "checklist"
            or row["quarantined"]
            or row["purge_state"] != "live"
            or _sha(row["body_json"]) != row["body_sha256"]
        ):
            raise CompanyError("job_result_reference_invalid", 409)
        try:
            body = json.loads(row["body_json"])
            manifest = ScreeningInputManifest.model_validate(body["manifest"])
            result = body["result"]
            valid = (
                manifest.closure_sha256 == current["inputs_manifest_sha256"]
                and result["kind"] == "checklist"
                and result["result_revision"] == result_revision
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise CompanyError("job_result_reference_invalid", 409)
        expected_state = "completed" if status == "waiting_review" else status
        prior_result = (
            current["result_id"] == result_id and current["result_revision"] == result_revision
        )
        if (
            expected_state in {"completed", "failed", "timed_out", "interrupted"}
            and result.get("state") != expected_state
            and not (prior_result and expected_state != "completed")
        ):
            raise CompanyError("job_result_state_mismatch", 409)
        return result

    def transition(
        self,
        run_id,
        expected_version,
        status,
        *,
        progress=None,
        result_id=None,
        result_revision=None,
        error=None,
        _context=None,
    ):
        _version(expected_version)
        if error is not None:
            _identifier(error, "error_code")
        if result_id is not None:
            _identifier(result_id, "result_id")
            if result_id == run_id:
                raise CompanyError("job_result_id_must_differ", 422)
        if result_revision is not None:
            _identifier(result_revision, "result_revision")
        if progress is not None and not isinstance(progress, dict):
            raise CompanyError("job_invalid_progress", 422)
        encoded_progress = _json(progress) if progress is not None else None
        with transaction(self.db) as conn:
            current = self._row(run_id, conn)
            if _context is not None:
                self._authorize(_context, run_id, conn)
            if current["state_version"] != expected_version:
                raise CompanyError("job_state_conflict", 409)
            if current["purge_state"] != "live" or current["quarantined"]:
                raise CompanyError("job_not_found")
            same_nonterminal = status == current["status"] and status not in TERMINAL
            if not same_nonterminal and status not in TRANSITIONS.get(current["status"], set()):
                raise CompanyError("job_transition_forbidden", 409)
            for key, value in (("result_id", result_id), ("result_revision", result_revision)):
                if current[key] is not None and value not in (None, current[key]):
                    raise CompanyError("job_result_reference_immutable", 409)
            result_id = result_id or current["result_id"]
            result_revision = result_revision or current["result_revision"]
            try:
                TaskExecution(
                    run_id=run_id,
                    status=status,
                    result_revision=result_revision,
                    failure_reason=error,
                )
            except ValidationError:
                raise CompanyError("job_invalid_execution", 422) from None
            if status in {"completed", "waiting_review"} and result_id is None:
                raise CompanyError("job_result_reference_required", 422)
            if (
                result_id is not None
                and current["result_id"] is None
                and status
                not in {"completed", "waiting_review", "failed", "timed_out", "interrupted"}
            ):
                raise CompanyError("job_result_reference_invalid", 409)
            self._validate_result(current, result_id, result_revision, status, conn)
            if status in {"running", "waiting_review", "completed"}:
                owner = ToolContext(current["owner_id"])
                self.auth.require_creator(owner, conn)
                self._authorize(owner, run_id, conn)
            conn.execute(
                text(
                    "UPDATE durable_job_state SET status=:status,state_version=state_version+1,"
                    "progress_json=COALESCE(:progress,progress_json),result_id=:result,result_revision=:revision,"
                    "error_code=:error,updated_at=:now WHERE run_id=:id AND state_version=:expected"
                ),
                {
                    "status": status,
                    "progress": encoded_progress,
                    "result": result_id,
                    "revision": result_revision,
                    "error": error,
                    "now": self.auth.clock(),
                    "id": run_id,
                    "expected": expected_version,
                },
            )
        return self.get_system(run_id)

    def request_cancel(self, context, run_id, expected_version):
        self.get(context, run_id)
        return self.transition(run_id, expected_version, "cancel_requested", _context=context)

    def record_attempt_started(self, run_id, seq=None, *, ledger_id=None, request_sha256):
        with transaction(self.db) as conn:
            current = self._row(run_id, conn)
            self._authorize(ToolContext(current["owner_id"]), run_id, conn)
            if current["status"] != "running":
                raise CompanyError("job_attempt_not_running", 409)
            if seq is None:
                seq = rows(
                    self.db,
                    "SELECT COALESCE(MAX(seq),0)+1 AS seq "
                    "FROM durable_model_attempts WHERE run_id=:id",
                    {"id": run_id},
                    conn,
                )[0]["seq"]
            value = AttemptStart(
                run_id=run_id, seq=seq, ledger_id=ledger_id, request_sha256=request_sha256
            )
            if ledger_id is not None and not rows(
                self.db, "SELECT run_id FROM agent_runs WHERE run_id=:id", {"id": ledger_id}, conn
            ):
                raise CompanyError("job_attempt_ledger_not_found", 409)
            found = rows(
                self.db,
                "SELECT * FROM durable_model_attempts WHERE run_id=:id AND seq=:seq",
                {"id": run_id, "seq": seq},
                conn,
            )
            if found:
                if (
                    found[0]["ledger_id"] != value.ledger_id
                    or found[0]["request_sha256"] != request_sha256
                ):
                    raise CompanyError("job_attempt_conflict", 409)
            else:
                conn.execute(
                    text(
                        "INSERT INTO durable_model_attempts"
                        "(run_id,seq,ledger_id,request_sha256,status,started_at) "
                        "VALUES (:id,:seq,:ledger,:request,'started',:now)"
                    ),
                    {
                        "id": run_id,
                        "seq": seq,
                        "ledger": value.ledger_id,
                        "request": request_sha256,
                        "now": self.auth.clock(),
                    },
                )
        return next(a for a in self._attempts_system(run_id) if a["seq"] == seq)

    def finish_attempt(self, run_id, seq, *, response=None, usage=None, unknown=False):
        if type(seq) is not int or seq < 1 or type(unknown) is not bool:
            raise CompanyError("job_attempt_invalid", 422)
        if unknown == (response is not None):
            raise CompanyError("job_attempt_response_required", 422)
        if usage is not None and not isinstance(usage, dict):
            raise CompanyError("job_invalid_usage", 422)
        response_json = _json(response, limit=10_000_000) if not unknown else None
        usage_json = _json(usage or {})
        status = "unknown" if unknown else "response_received"
        with transaction(self.db) as conn:
            current = self._row(run_id, conn)
            if current["purge_state"] != "live":
                raise CompanyError("job_not_found")
            found = rows(
                self.db,
                "SELECT * FROM durable_model_attempts WHERE run_id=:id AND seq=:seq",
                {"id": run_id, "seq": seq},
                conn,
            )
            if not found:
                raise CompanyError("job_attempt_not_found")
            attempt = found[0]
            response_sha = _sha(response_json) if response_json is not None else None
            if attempt["status"] != "started":
                if (attempt["status"], attempt["response_sha256"], attempt["usage_json"]) != (
                    status,
                    response_sha,
                    usage_json,
                ):
                    raise CompanyError("job_attempt_already_finished", 409)
            else:
                conn.execute(
                    text(
                        "UPDATE durable_model_attempts SET status=:status,completed_at=:now,"
                        "response_sha256=:sha,usage_json=:usage WHERE run_id=:id AND seq=:seq"
                    ),
                    {
                        "status": status,
                        "now": self.auth.clock(),
                        "sha": response_sha,
                        "usage": usage_json,
                        "id": run_id,
                        "seq": seq,
                    },
                )
                if response_json is not None:
                    conn.execute(
                        text("INSERT INTO durable_attempt_bodies VALUES (:id,:seq,:body)"),
                        {"id": run_id, "seq": seq, "body": response_json},
                    )
        return next(a for a in self._attempts_system(run_id) if a["seq"] == seq)

    def _attempts_system(self, run_id):
        current = self._row(run_id)
        found = rows(
            self.db,
            "SELECT a.*,b.response_json FROM durable_model_attempts a LEFT JOIN "
            "durable_attempt_bodies b ON b.run_id=a.run_id AND b.seq=a.seq "
            "WHERE a.run_id=:id ORDER BY a.seq",
            {"id": run_id},
        )
        result = []
        for row in found:
            encoded = row.pop("response_json")
            if (
                encoded is None
                and row["response_sha256"] is not None
                and current["purge_state"] == "live"
            ):
                raise CompanyError("job_attempt_integrity_failed", 409)
            if encoded is not None and _sha(encoded) != row["response_sha256"]:
                raise CompanyError("job_attempt_integrity_failed", 409)
            row["response"] = json.loads(encoded) if encoded is not None else None
            row["usage"] = json.loads(row.pop("usage_json"))
            result.append(row)
        return result

    def list_attempts(self, context, run_id):
        self.get(context, run_id)
        attempts = self._attempts_system(run_id)
        with transaction(self.db) as conn:
            self._authorize(context, run_id, conn)
        return attempts

    def list_attempts_system(self, run_id):
        """Trusted worker replay inspection; never expose this as a public API."""
        self._row(run_id)
        return self._attempts_system(run_id)

    def quarantine_all(self):
        with transaction(self.db) as conn:
            conn.execute(
                text(
                    "UPDATE durable_job_state SET quarantined=1,state_version=state_version+1 "
                    "WHERE purge_state='live'"
                )
            )

    def reauthorize(self, context, run_id, expected_version):
        _version(expected_version)
        self.auth.require_creator(context)
        record = self._authorize(context, run_id, quarantine=True)
        body = self._body(record)
        self.sources.recheck(context, body["manifest"], for_execution=False)
        with transaction(self.db) as conn:
            self.auth.require_creator(context, conn)
            current = self._authorize(context, run_id, conn, quarantine=True)
            if current["state_version"] != expected_version:
                raise CompanyError("job_state_conflict", 409)
            self._body(current, conn)
            if current["quarantined"]:
                if current["result_id"] is not None:
                    # Both visibility changes commit together only after validating the
                    # original state's immutable result. Failure rolls back this update.
                    conn.execute(
                        text(
                            "UPDATE qualification_result_lifecycle SET quarantined=0 "
                            "WHERE result_id=:id"
                        ),
                        {"id": current["result_id"]},
                    )
                    self._validate_result(
                        current,
                        current["result_id"],
                        current["result_revision"],
                        current["status"],
                        conn,
                    )
                status, error = current["status"], current["error_code"]
                if status in {"queued", "running", "waiting_review"}:
                    status, error = "interrupted", "job_restored_needs_resume"
                elif status == "cancel_requested":
                    status, error = "cancelled", None
                conn.execute(
                    text(
                        "UPDATE durable_job_state SET quarantined=0,status=:status,"
                        "error_code=:error,state_version=state_version+1,updated_at=:now "
                        "WHERE run_id=:id"
                    ),
                    {"status": status, "error": error, "now": self.auth.clock(), "id": run_id},
                )
        return self.get(context, run_id)

    def record_review_fixture(self, run_id, event, expected_version):
        """Internal B05 test fixture only. This is not a public human approval API."""
        event = ReviewEvent.model_validate(event)
        _version(expected_version)
        if event.origin != "test_fixture" or event.run_id != run_id:
            raise CompanyError("job_fixture_only", 422)
        encoded = _json(event.model_dump(mode="json"))
        with transaction(self.db) as conn:
            current = self._row(run_id, conn)
            self._authorize(ToolContext(current["owner_id"]), run_id, conn)
            old = rows(
                self.db,
                "SELECT * FROM durable_review_fixtures WHERE event_id=:event "
                "OR (run_id=:id AND idempotency_key=:key)",
                {"event": event.event_id, "id": run_id, "key": event.command.idempotency_key},
                conn,
            )
            if old:
                if len(old) != 1 or old[0]["event_sha256"] != _sha(encoded):
                    raise CompanyError("job_review_conflict", 409)
                return {"event_id": event.event_id, "created": False}
            if current["state_version"] != expected_version:
                raise CompanyError("job_state_conflict", 409)
            if (
                current["status"] != "waiting_review"
                or event.result_revision != current["result_revision"]
                or event.inputs_manifest_sha256 != current["inputs_manifest_sha256"]
                or event.occurred_at.timestamp() < current["created_at"]
                or event.occurred_at.timestamp() > self.auth.clock() + 30
            ):
                raise CompanyError("job_review_stale", 409)
            result = self._validate_result(
                current, current["result_id"], current["result_revision"], "waiting_review", conn
            )
            manifest = ScreeningInputManifest.model_validate(self._body(current, conn)["manifest"])
            try:
                groups = [AssessmentGroup.model_validate(value) for value in result["groups"]]
                ids = {group.assessment.assessment_id for group in groups}
                valid = len(ids) == len(groups) and event.assessment_id in ids
                for group in groups:
                    assessment, inputs = group.assessment, group.assessment.inputs
                    valid = valid and (
                        assessment.result_revision == current["result_revision"]
                        and inputs.project_id == manifest.project_id
                        and inputs.project_family_id == manifest.project_id
                        and inputs.as_of == manifest.as_of
                        and inputs.notice_revision in manifest.notice_revisions
                        and inputs.profile_revision == manifest.profile_revision
                        and inputs.material_revisions == manifest.material_revisions
                        and inputs.attachment_revisions == manifest.attachment_revisions
                    )
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise CompanyError("job_review_assessment_invalid", 409)
            conn.execute(
                text("INSERT INTO durable_review_fixtures VALUES (:event,:id,:key,:sha)"),
                {
                    "event": event.event_id,
                    "id": run_id,
                    "key": event.command.idempotency_key,
                    "sha": _sha(encoded),
                },
            )
            conn.execute(
                text("INSERT INTO durable_review_fixture_bodies VALUES (:event,:body)"),
                {"event": event.event_id, "body": encoded},
            )
            conn.execute(
                text(
                    "UPDATE durable_job_state SET state_version=state_version+1,updated_at=:now "
                    "WHERE run_id=:id"
                ),
                {"id": run_id, "now": self.auth.clock()},
            )
        return {"event_id": event.event_id, "created": True}

    def prune(self):
        with transaction(self.db) as conn:
            expired = rows(
                self.db,
                "SELECT DISTINCT s.run_id FROM durable_job_sources s "
                "LEFT JOIN company_objects o ON o.object_id=s.object_id "
                "JOIN durable_job_state j ON j.run_id=s.run_id "
                "WHERE j.purge_state='live' AND (o.object_id IS NULL OR o.deleted_at IS NOT NULL "
                "OR o.retention_until<=:now OR o.purge_state!='live')",
                {"now": self.auth.clock()},
                conn,
            )
            for row in expired:
                params = {"id": row["run_id"], "now": self.auth.clock()}
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
                    text(
                        "UPDATE durable_job_state SET purge_state='purged',purged_at=:now,"
                        "progress_json='{}',state_version=state_version+1,"
                        "updated_at=:now WHERE run_id=:id"
                    ),
                    params,
                )
        return {
            "status": "PASS",
            "purged_ids": [row["run_id"] for row in expired],
            "failure_ids": [],
        }
