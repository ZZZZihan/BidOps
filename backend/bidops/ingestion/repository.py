"""Permission-bound acquisition records and atomic pages, never a work queue.

Only the native runtime performs scheduling, waiting, retries and network I/O.
Internal records contain private commands; public projections never return them.
"""

import hashlib
import json
import re
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import text

from bidops.company.authorization import CompanyError, ToolContext, rows, transaction
from bidops.ingestion.schemas import (
    MAX_REQUEST_BYTES,
    HttpRun,
    ManualImport,
    ScheduleCommand,
    decode_body,
)
from bidops.notice_data.attachment_parser import PreparedAttachment
from bidops.sources import canonical_url, load_sources, network_permission
from bidops.storage import PreparedNotice

ACTIVE = {"queued", "running", "cancel_requested", "interrupted"}
TERMINAL = {"cancelled", "completed", "partial", "failed"}
COUNT_KEYS = (
    "pages",
    "items",
    "imported",
    "unchanged",
    "failed",
    "attachments",
    "attachment_failed",
)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _json(value, limit=MAX_REQUEST_BYTES):
    try:
        encoded = canonical(value)
    except (TypeError, ValueError, RecursionError):
        raise CompanyError("ingestion_invalid_json", 422) from None
    if len(encoded.encode()) > limit:
        raise CompanyError("ingestion_request_too_large", 413)
    return encoded


def _version(value):
    if type(value) is not int or value < 0:
        raise CompanyError("ingestion_invalid_state_version", 422)


def _page(value):
    if type(value) is not int or not 0 <= value < 100:
        raise CompanyError("ingestion_invalid_page_index", 422)


def _code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value):
        raise CompanyError("ingestion_invalid_error_code", 422)
    return value


def _time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise CompanyError("ingestion_invalid_observation_time", 422) from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CompanyError("ingestion_invalid_observation_time", 422)
    return value.astimezone(UTC)


class IngestionRepository:
    def __init__(self, store, auth, settings, versions, *, clock=time.time):
        self.store, self.db, self.auth = store, store.db, auth
        self.settings, self.versions, self.clock = settings, dict(versions), clock

    def require_admin(self, context, conn=None):
        subject = self.auth.require_subject(context, conn)
        if "source:manage" not in self.auth.permissions(subject):
            raise CompanyError("ingestion_admin_required", 403)
        return subject

    def _source(self, source_id):
        try:
            sources = load_sources(self.store.settings.source_registry)
            found = next((source for source in sources if source.id == source_id), None)
        except (OSError, ValueError, TypeError):
            raise CompanyError("ingestion_source_registry_invalid", 409) from None
        if found is None:
            raise CompanyError("ingestion_source_not_found", 404)
        return found

    def _permit(self, source, payload, method, trigger_kind):
        instant = datetime.fromtimestamp(self.clock(), UTC)
        if method == "http_fetch":
            if not self.store.settings.network_enabled:
                raise CompanyError("network_disabled", 409)
            mode = "scheduled_fetch" if trigger_kind == "schedule" else "manual_fetch"
            urls = [item["source_url"] for item in payload["items"]]
            urls += [
                a["source_url"] for item in payload["items"] for a in item.get("attachments", [])
            ]
            for url in urls:
                try:
                    if canonical_url(url, [source]) != url:
                        raise ValueError("noncanonical")
                    allowed, reason = network_permission(url, [source], now=instant, mode=mode)
                except ValueError:
                    raise CompanyError("ingestion_source_scope_denied", 403) from None
                if not allowed:
                    raise CompanyError(reason, 403)
        else:
            # A declared unknown network license does not block independently
            # attested manual receipt. Explicit policy prohibitions still apply.
            policy = source.policy
            if policy is not None:
                if policy.status == "denied" or "manual_import" not in policy.acquisition_modes:
                    raise CompanyError("ingestion_manual_import_denied", 403)
                if instant < policy.reviewed_at or instant < policy.valid_from:
                    raise CompanyError("source_policy_not_yet_valid", 403)
                if instant >= policy.valid_until:
                    raise CompanyError("source_policy_expired", 403)
            urls = [item["source_url"] for page in payload["pages"] for item in page["items"]]
            urls += [
                a["source_url"]
                for page in payload["pages"]
                for item in page["items"]
                for a in item.get("attachments", [])
                if a.get("source_url") is not None
            ]
            for url in urls:
                try:
                    clean = canonical_url(url, [source])
                except ValueError:
                    raise CompanyError("ingestion_source_scope_denied", 403) from None
                if clean != url or url not in source.allowed_urls:
                    raise CompanyError("ingestion_source_scope_denied", 403)
                if policy is not None and url not in policy.url_scope:
                    raise CompanyError("ingestion_source_scope_denied", 403)
        return source

    def _row(self, run_id, conn=None):
        found = rows(
            self.db,
            "SELECT r.*,s.status,s.state_version,s.updated_at,s.error_code,s.next_page,"
            "s.request_count,s.counts_json,s.quarantined FROM ingestion_runs r "
            "JOIN ingestion_run_state s ON s.run_id=r.run_id WHERE r.run_id=:id",
            {"id": run_id},
            conn,
        )
        if not found:
            raise CompanyError("ingestion_run_not_found")
        return found[0]

    def _body(self, record, conn=None):
        found = rows(
            self.db,
            "SELECT body_json FROM ingestion_inputs WHERE run_id=:id",
            {"id": record["run_id"]},
            conn,
        )
        policies = rows(
            self.db,
            "SELECT body_json FROM ingestion_policy_revisions WHERE policy_sha256=:p",
            {"p": record["policy_sha256"]},
            conn,
        )
        try:
            encoded, policy_json = found[0]["body_json"], policies[0]["body_json"]
            body, source = json.loads(encoded), json.loads(policy_json)
            if (
                sha(encoded) != record["body_sha256"]
                or sha(canonical(body["request"])) != record["input_sha256"]
                or sha(policy_json) != record["policy_sha256"]
                or source["id"] != record["source_id"]
                or body["versions"]["application_version"] != record["application_version"]
                or (source.get("policy") or {}).get("revision") != record["policy_revision"]
            ):
                raise ValueError("binding")
        except (IndexError, KeyError, ValueError, TypeError):
            raise CompanyError("ingestion_integrity_failed", 409) from None
        return {**body, "source": source}

    def _internal(self, record, conn=None):
        return {
            **{key: value for key, value in record.items() if key != "counts_json"},
            "counts": json.loads(record["counts_json"]),
            **self._body(record, conn),
        }

    def get_system(self, run_id, conn=None):
        """Private worker inspection only, never a public authorization decision."""
        return self._internal(self._row(run_id, conn), conn)

    def _authorize(self, context, record, conn=None):
        subject = self.auth.require_subject(context, conn)
        if record["owner_id"] != context.owner_id and "source:manage" not in self.auth.permissions(
            subject
        ):
            raise CompanyError("ingestion_run_not_found")
        return record

    def _public(self, record, conn=None):
        body = self._body(record, conn)
        payload = body["request"]
        items = (
            payload["items"]
            if record["method"] == "http_fetch"
            else [item for page in payload["pages"] for item in page["items"]]
        )
        result = {
            key: value for key, value in record.items() if key not in {"counts_json", "body_sha256"}
        }
        return {
            **result,
            "quarantined": bool(record["quarantined"]),
            "counts": json.loads(record["counts_json"]),
            "source_urls": [item["source_url"] for item in items],
        }

    def read(self, context, run_id):
        with transaction(self.db) as conn:
            record = self._authorize(context, self._row(run_id, conn), conn)
            result = self._public(record, conn)
            pages = rows(
                self.db,
                "SELECT page_seq FROM ingestion_pages WHERE run_id=:id ORDER BY page_seq",
                {"id": run_id},
                conn,
            )
            return {
                **result,
                "pages": [
                    {
                        "page_seq": page["page_seq"],
                        "receipt": self._receipt(run_id, page["page_seq"], conn),
                    }
                    for page in pages
                ],
                "failures": rows(
                    self.db,
                    "SELECT event_id,page_index,code,observed_at FROM ingestion_failures "
                    "WHERE run_id=:id ORDER BY observed_at,event_id",
                    {"id": run_id},
                    conn,
                ),
            }

    def list(self, context, offset=0, limit=20):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise CompanyError("ingestion_invalid_pagination", 422)
        with transaction(self.db) as conn:
            subject = self.auth.require_subject(context, conn)
            admin = "source:manage" in self.auth.permissions(subject)
            condition = "" if admin else " WHERE owner_id=:owner"
            params = {"owner": context.owner_id, "limit": limit, "offset": offset}
            total = rows(
                self.db, "SELECT COUNT(*) AS n FROM ingestion_runs" + condition, params, conn
            )[0]["n"]
            found = rows(
                self.db,
                "SELECT run_id FROM ingestion_runs"
                + condition
                + " ORDER BY created_at DESC,run_id LIMIT :limit OFFSET :offset",
                params,
                conn,
            )
            result = [
                self._public(self._authorize(context, self._row(r["run_id"], conn), conn), conn)
                for r in found
            ]
            return {
                "items": result,
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < total,
            }

    def sources(self, context):
        with transaction(self.db) as conn:
            self.auth.require_subject(context, conn)
            try:
                configured = load_sources(self.store.settings.source_registry)
            except (OSError, ValueError, TypeError):
                raise CompanyError("ingestion_source_registry_invalid", 409) from None
            result = []
            for source in configured:
                state = rows(
                    self.db,
                    "SELECT * FROM ingestion_source_state WHERE source_id=:id",
                    {"id": source.id},
                    conn,
                )
                entry = state[0] if state else {}
                result.append(
                    {
                        **source.model_dump(mode="json"),
                        "policy_sha256": sha(canonical(source.model_dump(mode="json"))),
                        "policy_revision": source.policy.revision if source.policy else None,
                        "cursor_version": entry.get("cursor_version", 0),
                        "counts": json.loads(entry.get("counts_json", "{}")),
                        **{
                            key: entry.get(key)
                            for key in (
                                "last_attempt",
                                "last_success",
                                "active_run_id",
                                "failure_count",
                                "last_error_code",
                            )
                        },
                    }
                )
            return result

    def create(
        self,
        context,
        payload,
        method,
        *,
        trigger_kind="manual",
        native_id=None,
        schedule_native_name=None,
    ):
        if method not in {"manual_import", "http_fetch"} or trigger_kind not in {
            "manual",
            "schedule",
        }:
            raise CompanyError("ingestion_invalid_method", 422)
        if method == "manual_import" and trigger_kind != "manual":
            raise CompanyError("ingestion_invalid_method", 422)
        if (trigger_kind == "schedule" and not isinstance(schedule_native_name, str)) or (
            trigger_kind == "manual" and schedule_native_name is not None
        ):
            raise CompanyError("ingestion_invalid_schedule_binding", 422)
        if not isinstance(payload, dict):
            raise CompanyError("ingestion_invalid_request", 422)
        encoded = _json(payload)
        try:
            (ManualImport if method == "manual_import" else HttpRun).model_validate(payload)
        except ValidationError:
            raise CompanyError("ingestion_invalid_request", 422) from None
        input_sha = sha(encoded)
        with transaction(self.db) as conn:
            self.require_admin(context, conn)
            existing = rows(
                self.db,
                "SELECT run_id,input_sha256,method,trigger_kind,schedule_native_name "
                "FROM ingestion_runs "
                "WHERE owner_id=:owner AND idempotency_key=:key",
                {"owner": context.owner_id, "key": payload["idempotency_key"]},
                conn,
            )
            if existing:
                current = existing[0]
                if (
                    current["input_sha256"],
                    current["method"],
                    current["trigger_kind"],
                    current["schedule_native_name"],
                ) != (
                    input_sha,
                    method,
                    trigger_kind,
                    schedule_native_name,
                ):
                    raise CompanyError("ingestion_idempotency_conflict", 409)
                record = self.get_system(current["run_id"], conn)
                if trigger_kind == "schedule":
                    self._require_schedule(record, conn)
                return {**record, "created": False}
            source = self._permit(self._source(payload["source_id"]), payload, method, trigger_kind)
            source_json = canonical(source.model_dump(mode="json"))
            policy_sha = sha(source_json)
            if trigger_kind == "schedule":
                self._require_schedule(
                    {
                        "owner_id": context.owner_id,
                        "source_id": source.id,
                        "policy_sha256": policy_sha,
                        "application_version": self.versions["application_version"],
                        "schedule_native_name": schedule_native_name,
                        "request": payload,
                    },
                    conn,
                )
            source_state = rows(
                self.db,
                "SELECT * FROM ingestion_source_state WHERE source_id=:id",
                {"id": source.id},
                conn,
            )
            if source_state and source_state[0]["active_run_id"] is not None:
                raise CompanyError("ingestion_source_busy", 409)
            active = rows(
                self.db,
                "SELECT COUNT(*) AS n FROM ingestion_run_state "
                "WHERE status IN ('queued','running','cancel_requested','interrupted')",
                conn=conn,
            )[0]["n"]
            if active >= self.settings.max_active_runs:
                raise CompanyError("ingestion_capacity_reached", 429)
            now, run_id = self.clock(), uuid.uuid4().hex
            native_id = native_id or run_id
            if not isinstance(native_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,256}", native_id
            ):
                raise CompanyError("ingestion_invalid_native_id", 422)
            if rows(
                self.db,
                "SELECT run_id FROM ingestion_runs WHERE native_id=:id",
                {"id": native_id},
                conn,
            ):
                raise CompanyError("ingestion_native_id_conflict", 409)
            body_json = _json(
                {"request": payload, "versions": self.versions}, MAX_REQUEST_BYTES + 100000
            )
            params = {
                "id": run_id,
                "native": native_id,
                "owner": context.owner_id,
                "source": source.id,
                "method": method,
                "trigger": trigger_kind,
                "schedule": schedule_native_name,
                "policy": policy_sha,
                "revision": source.policy.revision if source.policy else None,
                "input": input_sha,
                "body": sha(body_json),
                "app": self.versions["application_version"],
                "now": now,
                "key": payload["idempotency_key"],
            }
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO ingestion_policy_revisions "
                    "VALUES (:policy,:source,:revision,:body,:now)"
                ),
                {**params, "body": source_json},
            )
            conn.execute(
                text("INSERT OR IGNORE INTO ingestion_source_state(source_id) VALUES (:source)"),
                params,
            )
            conn.execute(
                text(
                    "INSERT INTO ingestion_runs(run_id,native_id,owner_id,source_id,method,"
                    "trigger_kind,schedule_native_name,policy_sha256,policy_revision,input_sha256,body_sha256,"
                    "application_version,created_at,idempotency_key) VALUES "
                    "(:id,:native,:owner,:source,:method,:trigger,:schedule,:policy,:revision,"
                    ":input,:body,:app,:now,:key)"
                ),
                params,
            )
            conn.execute(
                text("INSERT INTO ingestion_inputs VALUES (:id,:body)"),
                {"id": run_id, "body": body_json},
            )
            conn.execute(
                text(
                    "INSERT INTO ingestion_run_state(run_id,status,updated_at) "
                    "VALUES (:id,'queued',:now)"
                ),
                params,
            )
            conn.execute(
                text("UPDATE ingestion_source_state SET active_run_id=:id WHERE source_id=:source"),
                params,
            )
            # Registry can be replaced during preparation: do not admit under an old policy.
            self._require_execution(run_id, conn)
            return {**self.get_system(run_id, conn), "created": True}

    def _require_schedule(self, record, conn):
        """A recovered run retains the exact generation that admitted it.

        Pausing or replacing a schedule also stops already admitted work. The
        immutable generation proves origin; the mutable head authorizes execution.
        """
        name = record["schedule_native_name"]
        generations = rows(
            self.db,
            "SELECT * FROM ingestion_schedule_versions WHERE native_name=:name",
            {"name": name},
            conn,
        )
        current = (
            rows(
                self.db,
                "SELECT * FROM ingestion_schedules WHERE schedule_id=:id",
                {"id": generations[0]["schedule_id"]},
                conn,
            )
            if generations
            else []
        )
        if not current or not current[0]["enabled"] or current[0]["quarantined"]:
            raise CompanyError("ingestion_schedule_not_current", 409)
        frozen, head = generations[0], current[0]
        bindings = (
            "native_name",
            "schedule_id",
            "state_version",
            "owner_id",
            "source_id",
            "body_json",
            "body_sha256",
            "policy_sha256",
            "application_version",
        )
        if any(frozen[key] != head[key] for key in bindings) or any(
            record[key] != frozen[key]
            for key in ("owner_id", "source_id", "policy_sha256", "application_version")
        ):
            raise CompanyError("ingestion_schedule_not_current", 409)
        try:
            body = ScheduleCommand.model_validate_json(frozen["body_json"])
            request = HttpRun.model_validate(record["request"])
            expected_name = (
                "source-schedule-"
                + sha(
                    canonical(
                        [frozen["schedule_id"], frozen["state_version"], frozen["body_sha256"]]
                    )
                )[:32]
            )
            if (
                sha(frozen["body_json"]) != frozen["body_sha256"]
                or expected_name != name
                or body.schedule_id != frozen["schedule_id"]
                or body.source_id != frozen["source_id"]
                or body.expected_state_version + 1 != frozen["state_version"]
                or not body.enabled
                or [item.model_dump(mode="json") for item in body.items]
                != [item.model_dump(mode="json") for item in request.items]
            ):
                raise ValueError("schedule binding")
        except (ValidationError, ValueError, TypeError):
            raise CompanyError("ingestion_schedule_integrity_failed", 409) from None

    def _require_execution(self, run_id, conn, *, allow_terminal=False):
        record = self.get_system(run_id, conn)
        self.require_admin(ToolContext(record["owner_id"]), conn)
        if record["quarantined"]:
            raise CompanyError("ingestion_run_quarantined", 409)
        if record["application_version"] != self.versions["application_version"]:
            raise CompanyError("ingestion_application_version_changed", 409)
        if record["status"] == "cancel_requested" or record["status"] == "cancelled":
            raise CompanyError("ingestion_cancelled", 409)
        if not allow_terminal and record["status"] not in ACTIVE:
            raise CompanyError("ingestion_run_not_active", 409)
        if record["trigger_kind"] == "schedule":
            self._require_schedule(record, conn)
        source = self._permit(
            self._source(record["source_id"]),
            record["request"],
            record["method"],
            record["trigger_kind"],
        )
        if sha(canonical(source.model_dump(mode="json"))) != record["policy_sha256"]:
            raise CompanyError("ingestion_source_policy_changed", 409)
        if not allow_terminal:
            state = rows(
                self.db,
                "SELECT active_run_id FROM ingestion_source_state WHERE source_id=:id",
                {"id": record["source_id"]},
                conn,
            )
            if not state or state[0]["active_run_id"] != run_id:
                raise CompanyError("ingestion_source_ownership_changed", 409)
        return record

    def require_execution(self, run_id, conn=None):
        if conn is not None:
            return self._require_execution(run_id, conn)
        with transaction(self.db) as connection:
            return self._require_execution(run_id, connection)

    def _update(self, conn, record, **changes):
        values = {
            **changes,
            "updated_at": self.clock(),
            "state_version": record["state_version"] + 1,
        }
        params = {**values, "id": record["run_id"], "expected": record["state_version"]}
        assignments = ",".join(f"{key}=:{key}" for key in values)
        result = conn.execute(
            text(
                "UPDATE ingestion_run_state SET "
                + assignments
                + " WHERE run_id=:id AND state_version=:expected"
            ),
            params,
        )
        if result.rowcount != 1:
            raise CompanyError("ingestion_state_conflict", 409)

    def start(self, run_id):
        with transaction(self.db) as conn:
            record = self._require_execution(run_id, conn)
            if record["status"] not in {"queued", "running", "interrupted"}:
                raise CompanyError("ingestion_state_conflict", 409)
            if record["status"] != "running":
                self._update(conn, record, status="running", error_code=None)
            return self.get_system(run_id, conn)

    def _receipt(self, run_id, page_seq, conn):
        found = rows(
            self.db,
            "SELECT * FROM ingestion_pages WHERE run_id=:id AND page_seq=:page",
            {"id": run_id, "page": page_seq},
            conn,
        )
        if not found:
            return None
        row = found[0]
        if sha(row["receipt_json"]) != row["receipt_sha256"]:
            raise CompanyError("ingestion_integrity_failed", 409)
        return json.loads(row["receipt_json"])

    def begin_page(self, run_id, page_seq):
        _page(page_seq)
        with transaction(self.db) as conn:
            existing = self._receipt(run_id, page_seq, conn)
            record = self._require_execution(run_id, conn, allow_terminal=existing is not None)
            if existing is not None:
                return {
                    "existing_receipt": existing,
                    "expected_cursor_version": existing["from_cursor_version"],
                    "cursor_before": None,
                    "observed_at": existing["observed_at"],
                }
            if record["status"] != "running" or page_seq != record["next_page"]:
                raise CompanyError("ingestion_page_conflict", 409)
            state = rows(
                self.db,
                "SELECT * FROM ingestion_source_state WHERE source_id=:id",
                {"id": record["source_id"]},
                conn,
            )[0]
            attempt = rows(
                self.db,
                "SELECT * FROM ingestion_page_attempts WHERE run_id=:id AND page_seq=:page",
                {"id": run_id, "page": page_seq},
                conn,
            )
            if not attempt:
                conn.execute(
                    text(
                        "INSERT INTO ingestion_page_attempts "
                        "VALUES (:id,:page,:version,:cursor,:now)"
                    ),
                    {
                        "id": run_id,
                        "page": page_seq,
                        "version": state["cursor_version"],
                        "cursor": state["cursor_json"],
                        "now": datetime.fromtimestamp(self.clock(), UTC).timestamp(),
                    },
                )
                attempt = rows(
                    self.db,
                    "SELECT * FROM ingestion_page_attempts WHERE run_id=:id AND page_seq=:page",
                    {"id": run_id, "page": page_seq},
                    conn,
                )
            conn.execute(
                text("UPDATE ingestion_source_state SET last_attempt=:now WHERE source_id=:source"),
                {"now": self.clock(), "source": record["source_id"]},
            )
            first = attempt[0]
            return {
                "existing_receipt": None,
                "expected_cursor_version": first["from_cursor_version"],
                "cursor_before": json.loads(first["cursor_before_json"]),
                "observed_at": datetime.fromtimestamp(first["observed_at"], UTC).isoformat(),
            }

    def _page_members(self, record, page_seq):
        model = ManualImport if record["method"] == "manual_import" else HttpRun
        request = model.model_validate(record["request"]).model_dump(mode="json")
        if record["method"] == "manual_import":
            if page_seq >= len(request["pages"]):
                raise CompanyError("ingestion_page_conflict", 409)
            return request["pages"][page_seq]["items"]
        if page_seq != 0:
            raise CompanyError("ingestion_page_conflict", 409)
        return request["items"]

    def commit_page(
        self,
        run_id,
        page_seq,
        *,
        expected_cursor_version,
        page_sha256,
        items,
        cursor_after,
        observed_at,
    ):
        _page(page_seq)
        _version(expected_cursor_version)
        if not isinstance(page_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", page_sha256):
            raise CompanyError("ingestion_invalid_page_hash", 422)
        instant = _time(observed_at)
        cursor_json = _json(cursor_after, 4096)
        with transaction(self.db) as conn:
            existing = self._receipt(run_id, page_seq, conn)
            record = self._require_execution(run_id, conn, allow_terminal=existing is not None)
            if existing is not None:
                if (
                    existing["page_sha256"],
                    existing["from_cursor_version"],
                    existing["cursor_after"],
                    existing["observed_at"],
                ) != (page_sha256, expected_cursor_version, cursor_after, instant.isoformat()):
                    raise CompanyError("ingestion_page_replay_conflict", 409)
                return existing
            if record["status"] != "running" or record["next_page"] != page_seq:
                raise CompanyError("ingestion_page_conflict", 409)
            attempt = rows(
                self.db,
                "SELECT * FROM ingestion_page_attempts WHERE run_id=:id AND page_seq=:page",
                {"id": run_id, "page": page_seq},
                conn,
            )
            source_state = rows(
                self.db,
                "SELECT * FROM ingestion_source_state WHERE source_id=:id",
                {"id": record["source_id"]},
                conn,
            )[0]
            if (
                not attempt
                or attempt[0]["from_cursor_version"] != expected_cursor_version
                or (
                    source_state["cursor_version"] != expected_cursor_version
                    or attempt[0]["observed_at"] != instant.timestamp()
                )
            ):
                raise CompanyError("ingestion_cursor_conflict", 409)
            expected = self._page_members(record, page_seq)
            if record["method"] == "manual_import" and cursor_after != record["request"]["pages"][
                page_seq
            ].get("cursor_after"):
                raise CompanyError("ingestion_cursor_binding_mismatch", 409)
            if not isinstance(items, list) or len(items) != len(expected):
                raise CompanyError("ingestion_page_members_mismatch", 409)
            counts = dict.fromkeys(COUNT_KEYS, 0)
            counts["pages"] = 1
            version_ids, attachment_ids = [], []
            complete = True
            for supplied, declared in zip(items, expected, strict=True):
                if not isinstance(supplied, (list, tuple)) or len(supplied) != 3:
                    raise CompanyError("ingestion_invalid_prepared_page", 422)
                prepared, attachments, source_complete = supplied
                if (
                    type(prepared) is not PreparedNotice
                    or type(source_complete) is not bool
                    or not isinstance(attachments, list)
                ):
                    raise CompanyError("ingestion_invalid_prepared_page", 422)
                snapshot = prepared.snapshot
                if (
                    snapshot.source_url != declared["source_url"]
                    or snapshot.fetched_at != instant
                    or (
                        snapshot.access_method != record["method"]
                        or (record["method"] == "http_fetch" and snapshot.http_status != 200)
                        or (record["method"] == "manual_import" and not snapshot.manual_attestation)
                    )
                ):
                    raise CompanyError("ingestion_source_binding_mismatch", 409)
                if record["method"] == "manual_import" and snapshot.sha256 != sha(
                    decode_body(declared["body_base64"])
                ):
                    raise CompanyError("ingestion_source_binding_mismatch", 409)
                if record["method"] == "manual_import" and (
                    snapshot.content_type != declared["content_type"]
                    or snapshot.encoding != declared["encoding"]
                ):
                    raise CompanyError("ingestion_source_binding_mismatch", 409)
                expected_attachments = declared.get("attachments", [])
                if len(attachments) != len(expected_attachments):
                    raise CompanyError("ingestion_attachment_members_mismatch", 409)
                parsed = True
                for attachment, target in zip(attachments, expected_attachments, strict=True):
                    if type(attachment) is not PreparedAttachment:
                        raise CompanyError("ingestion_invalid_prepared_page", 422)
                    value = attachment.input
                    expected_media = target["content_type"]
                    if (
                        record["method"] == "http_fetch"
                        and expected_media == "application/octet-stream"
                    ):
                        expected_media = value.content_type
                    if (
                        value.attachment_key,
                        value.filename,
                        value.source_url,
                        value.content_type,
                        value.observed_at,
                    ) != (
                        target["attachment_key"],
                        target["filename"],
                        target.get("source_url"),
                        expected_media,
                        instant,
                    ):
                        raise CompanyError("ingestion_attachment_binding_mismatch", 409)
                    if record["method"] == "manual_import" and not target.get("failed", False):
                        if attachment.data is None or sha(attachment.data) != sha(
                            decode_body(target["body_base64"])
                        ):
                            raise CompanyError("ingestion_attachment_binding_mismatch", 409)
                    elif record["method"] == "manual_import" and (
                        value.acquisition_status != "failed"
                        or attachment.data is not None
                        or value.error_code != target["error_code"]
                    ):
                        raise CompanyError("ingestion_attachment_binding_mismatch", 409)
                    if (
                        attachment.receipt is not None
                        and attachment.receipt.expected_page_count
                        != target.get("expected_page_count")
                    ):
                        raise CompanyError("ingestion_attachment_binding_mismatch", 409)
                    success = (
                        value.acquisition_status == "downloaded" and value.parse_status == "parsed"
                    )
                    parsed = parsed and success
                    counts["attachment_failed"] += int(not success)
                    counts["failed"] += int(not success)
                allowed_complete = bool(declared.get("source_complete", False))
                if source_complete and not allowed_complete:
                    raise CompanyError("ingestion_completeness_not_attested", 409)
                persisted = self.store.persist(
                    prepared, conn, source_complete=source_complete and parsed
                )
                version_ids.append(persisted.version_id)
                counts["items"] += 1
                counts["imported" if persisted.created else "unchanged"] += 1
                counts["attachments"] += len(attachments)
                for attachment in attachments:
                    bound = replace(
                        attachment,
                        input=attachment.input.model_copy(
                            update={"parent_version_id": persisted.version_id}
                        ),
                    )
                    saved = self.store.notice_data.record_attachment(bound, conn=conn)
                    attachment_ids.append(saved.record_id)
                complete = complete and parsed
            # A registry/identity change during item persistence rolls back the
            # complete page, observations, attachments and audit with its cursor.
            self._require_execution(run_id, conn)
            receipt = {
                "run_id": run_id,
                "page_seq": page_seq,
                "status": "completed" if complete else "partial",
                "counts": counts,
                "version_ids": version_ids,
                "attachment_record_ids": attachment_ids,
                "page_sha256": page_sha256,
                "from_cursor_version": expected_cursor_version,
                "cursor_advanced": complete,
                "cursor_version": expected_cursor_version + int(complete),
                "cursor_after": cursor_after,
                "observed_at": instant.isoformat(),
            }
            receipt_json = canonical(receipt)
            conn.execute(
                text(
                    "INSERT INTO ingestion_pages "
                    "VALUES (:id,:page,:sha,:version,:cursor,:at,:receipt,:receipt_sha)"
                ),
                {
                    "id": run_id,
                    "page": page_seq,
                    "sha": page_sha256,
                    "version": expected_cursor_version,
                    "cursor": cursor_json,
                    "at": instant.timestamp(),
                    "receipt": receipt_json,
                    "receipt_sha": sha(receipt_json),
                },
            )
            cumulative = {key: record["counts"].get(key, 0) + counts[key] for key in COUNT_KEYS}
            self._update(
                conn,
                record,
                counts_json=canonical(cumulative),
                next_page=page_seq + 1,
                **(
                    {"status": "partial", "error_code": "ingestion_attachments_incomplete"}
                    if not complete
                    else {}
                ),
            )
            if complete:
                conn.execute(
                    text(
                        "UPDATE ingestion_source_state SET cursor_json=:cursor,"
                        "cursor_version=cursor_version+1,last_success=:now,"
                        "failure_count=0,last_error_code=NULL,counts_json=:counts "
                        "WHERE source_id=:source AND cursor_version=:version AND active_run_id=:id"
                    ),
                    {
                        "cursor": cursor_json,
                        "now": self.clock(),
                        "counts": canonical(cumulative),
                        "source": record["source_id"],
                        "version": expected_cursor_version,
                        "id": run_id,
                    },
                )
            else:
                conn.execute(
                    text(
                        "UPDATE ingestion_source_state SET active_run_id=NULL,"
                        "failure_count=failure_count+1,"
                        "last_error_code='ingestion_attachments_incomplete' "
                        "WHERE source_id=:source AND active_run_id=:id"
                    ),
                    {"source": record["source_id"], "id": run_id},
                )
            return receipt

    def finish(self, run_id, status, error_code=None):
        if status not in TERMINAL | {"interrupted"}:
            raise CompanyError("ingestion_invalid_status", 422)
        if error_code is not None:
            _code(error_code)
        if status in {"failed", "interrupted", "partial"} and error_code is None:
            raise CompanyError("ingestion_failure_reason_required", 422)
        with transaction(self.db) as conn:
            record = self._row(run_id, conn)
            if record["status"] in TERMINAL:
                if record["status"] != status:
                    raise CompanyError("ingestion_terminal_state_conflict", 409)
                return self.get_system(run_id, conn)
            if record["status"] == "cancel_requested" and status != "cancelled":
                raise CompanyError("ingestion_cancelled", 409)
            if status == "completed":
                self._require_execution(run_id, conn)
                body = self._body(record, conn)["request"]
                count = len(body["pages"]) if record["method"] == "manual_import" else 1
                if record["next_page"] != count:
                    raise CompanyError("ingestion_pages_incomplete", 409)
            self._update(conn, record, status=status, error_code=error_code)
            if status in TERMINAL:
                conn.execute(
                    text(
                        "UPDATE ingestion_source_state SET active_run_id=NULL "
                        "WHERE source_id=:source AND active_run_id=:id"
                    ),
                    {"source": record["source_id"], "id": run_id},
                )
            return self.get_system(run_id, conn)

    def request_cancel(self, context, run_id, expected_version):
        _version(expected_version)
        with transaction(self.db) as conn:
            record = self._authorize(context, self._row(run_id, conn), conn)
            if record["state_version"] != expected_version:
                raise CompanyError("ingestion_state_conflict", 409)
            if record["status"] not in TERMINAL | {"cancel_requested"}:
                self._update(conn, record, status="cancel_requested")
            return self._public(self._row(run_id, conn), conn)

    def _failure(self, conn, record, code, page_index):
        conn.execute(
            text("INSERT INTO ingestion_failures VALUES (:event,:id,:source,:page,:code,:now)"),
            {
                "event": uuid.uuid4().hex,
                "id": record["run_id"],
                "source": record["source_id"],
                "page": page_index,
                "code": code,
                "now": self.clock(),
            },
        )
        conn.execute(
            text(
                "UPDATE ingestion_source_state SET last_attempt=:now,failure_count=failure_count+1,"
                "last_error_code=:code WHERE source_id=:source"
            ),
            {"now": self.clock(), "code": code, "source": record["source_id"]},
        )
        counts = json.loads(record["counts_json"])
        counts["failed"] = counts.get("failed", 0) + 1
        self._update(conn, record, counts_json=canonical(counts))

    def record_failure(self, run_id, code, page_index=None):
        _code(code)
        if page_index is not None:
            _page(page_index)
        with transaction(self.db) as conn:
            record = self._row(run_id, conn)
            self._failure(conn, record, code, page_index)

    def reserve_request(self, run_id, source_id, policy_revision):
        with transaction(self.db) as conn:
            record = self._require_execution(run_id, conn)
            if record["status"] != "running":
                raise CompanyError("ingestion_state_conflict", 409)
            if (
                record["source_id"] != source_id
                or record["policy_revision"] != policy_revision
                or record["method"] != "http_fetch"
            ):
                raise CompanyError("ingestion_source_binding_mismatch", 409)
            source = self._source(source_id)
            state = rows(
                self.db,
                "SELECT * FROM ingestion_source_state WHERE source_id=:id",
                {"id": source_id},
                conn,
            )[0]
            if record["request_count"] >= source.policy.max_requests:
                raise CompanyError("ingestion_request_limit", 429)
            now = self.clock()
            if state["next_request_at"] > now:
                return state["next_request_at"] - now
            self._update(conn, record, request_count=record["request_count"] + 1)
            conn.execute(
                text(
                    "UPDATE ingestion_source_state SET next_request_at=:next,"
                    "last_attempt=:now WHERE source_id=:id"
                ),
                {
                    "id": source_id,
                    "next": now
                    + max(
                        source.policy.rate_limit_seconds,
                        self.store.settings.http_rate_limit_seconds,
                    ),
                    "now": now,
                },
            )
            self._require_execution(run_id, conn)
            return 0.0
