"""Versioned schedule commands; DBOS supplies the durable scheduler."""

import json
import threading
import uuid

from croniter import croniter
from dbos import DBOS
from pydantic import ValidationError
from sqlalchemy import text

from bidops.company.authorization import CompanyError, ToolContext, rows, transaction
from bidops.ingestion.repository import canonical, sha
from bidops.ingestion.schemas import HttpRun, ScheduleCommand
from bidops.sources import Source, canonical_url

_LOCK = threading.RLock()


def generation_name(schedule_id, state_version, body_sha256):
    return "source-schedule-" + sha(canonical([schedule_id, state_version, body_sha256]))[:32]


class Schedules:
    def __init__(self, service):
        self.service, self.repository, self.db = service, service.repository, service.repository.db
        self.workflow = None

    def public(self, row):
        body = json.loads(row["body_json"])
        return {
            "schedule_id": row["schedule_id"],
            "source_id": row["source_id"],
            "state_version": row["state_version"],
            "enabled": bool(row["enabled"]),
            "cron": body["cron"],
            "items": body["items"],
            "last_run_id": row["last_run_id"],
            "last_error_code": row["last_error_code"],
            "quarantined": bool(row["quarantined"]),
        }

    def list(self, context):
        with transaction(self.db) as conn:
            self.repository.require_admin(context, conn)
            entries = rows(
                self.db, "SELECT * FROM ingestion_schedules ORDER BY schedule_id", conn=conn
            )
            return {"items": [self.public(row) for row in entries], "total": len(entries)}

    def command(self, context, payload):
        try:
            body = ScheduleCommand.model_validate(payload).model_dump(mode="json")
        except ValidationError:
            raise CompanyError("ingestion_invalid_schedule", 422) from None
        if len(body["cron"].split()) != 5 or not croniter.is_valid(body["cron"]):
            raise CompanyError("ingestion_invalid_cron", 422)
        encoded, now = canonical(body), self.repository.clock()
        body_sha = sha(encoded)
        with _LOCK:
            with transaction(self.db) as conn:
                self.repository.require_admin(context, conn)
                existing = rows(
                    self.db,
                    "SELECT * FROM ingestion_schedule_commands WHERE owner_id=:owner AND "
                    "idempotency_key=:key",
                    {"owner": context.owner_id, "key": body["idempotency_key"]},
                    conn,
                )
                if existing:
                    if existing[0]["input_sha256"] != body_sha:
                        raise CompanyError("ingestion_idempotency_conflict", 409)
                    schedule_id = existing[0]["schedule_id"]
                else:
                    schedule_id = body["schedule_id"]
                    old = rows(
                        self.db,
                        "SELECT * FROM ingestion_schedules WHERE schedule_id=:id",
                        {"id": schedule_id},
                        conn,
                    )
                    if (old[0]["state_version"] if old else 0) != body["expected_state_version"]:
                        raise CompanyError("ingestion_state_conflict", 409)
                    if (
                        not old
                        and rows(self.db, "SELECT COUNT(*) n FROM ingestion_schedules", conn=conn)[
                            0
                        ]["n"]
                        >= self.service.settings.max_schedules
                    ):
                        raise CompanyError("ingestion_schedule_capacity_reached", 429)
                    prior_body = json.loads(old[0]["body_json"]) if old else None
                    stop_existing = bool(
                        old
                        and not body["enabled"]
                        and all(
                            body[key] == prior_body[key] for key in ("source_id", "cron", "items")
                        )
                    )
                    if stop_existing:
                        # Revocation or removal of a registry entry must not stop
                        # an administrator from disabling its existing schedule.
                        # This grants no permission to change or execute targets.
                        policies = rows(
                            self.db,
                            "SELECT body_json FROM ingestion_policy_revisions "
                            "WHERE policy_sha256=:sha",
                            {"sha": old[0]["policy_sha256"]},
                            conn,
                        )
                        if not policies or sha(policies[0]["body_json"]) != old[0]["policy_sha256"]:
                            raise CompanyError("ingestion_schedule_integrity_failed", 409)
                        source = Source.model_validate_json(policies[0]["body_json"])
                        if source.id != old[0]["source_id"]:
                            raise CompanyError("ingestion_schedule_integrity_failed", 409)
                    else:
                        source = self.repository._source(body["source_id"])
                    if body["enabled"]:
                        self.repository._permit(source, body, "http_fetch", "schedule")
                    else:
                        # Pausing is possible after policy expiry or denial. Scope remains exact.
                        urls = [item["source_url"] for item in body["items"]]
                        urls += [
                            a["source_url"] for item in body["items"] for a in item["attachments"]
                        ]
                        for url in urls:
                            try:
                                normalized = canonical_url(url, [source])
                            except ValueError:
                                raise CompanyError("ingestion_source_scope_denied", 403) from None
                            if normalized != url or url not in source.allowed_urls:
                                raise CompanyError("ingestion_source_scope_denied", 403)
                    source_json = canonical(source.model_dump(mode="json"))
                    policy_sha = sha(source_json)
                    version = body["expected_state_version"] + 1
                    name = generation_name(schedule_id, version, body_sha)
                    params = {
                        "id": schedule_id,
                        "name": name,
                        "owner": context.owner_id,
                        "source": source.id,
                        "version": version,
                        "body": encoded,
                        "sha": body_sha,
                        "policy": policy_sha,
                        "app": self.repository.versions["application_version"],
                        "enabled": int(body["enabled"]),
                        "now": now,
                    }
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO ingestion_policy_revisions VALUES "
                            "(:policy,:source,:revision,:body,:now)"
                        ),
                        {
                            **params,
                            "revision": source.policy.revision if source.policy else None,
                            "body": source_json,
                        },
                    )
                    conn.execute(
                        text(
                            "INSERT INTO ingestion_schedules(schedule_id,native_name,owne"
                            "r_id,source_id,state_version,body_json,body_sha256,policy_sh"
                            "a256,application_version,enabled,updated_at) VALUES (:id,:na"
                            "me,:owner,:source,:version,:body,:sha,:policy,:app,:enabled,"
                            ":now) ON CONFLICT(schedule_id) DO UPDATE SET native_name=exc"
                            "luded.native_name,owner_id=excluded.owner_id,source_id=exclu"
                            "ded.source_id,state_version=excluded.state_version,body_json"
                            "=excluded.body_json,body_sha256=excluded.body_sha256,policy_"
                            "sha256=excluded.policy_sha256,application_version=excluded.a"
                            "pplication_version,enabled=excluded.enabled,updated_at=exclu"
                            "ded.updated_at,quarantined=0,last_error_code=NULL"
                        ),
                        params,
                    )
                    conn.execute(
                        text(
                            "INSERT INTO ingestion_schedule_versions(native_name,schedule"
                            "_id,state_version,owner_id,source_id,body_json,body_sha256,p"
                            "olicy_sha256,application_version,created_at) VALUES (:name,:"
                            "id,:version,:owner,:source,:body,:sha,:policy,:app,:now)"
                        ),
                        params,
                    )
                    conn.execute(
                        text(
                            "INSERT INTO ingestion_schedule_commands VALUES "
                            "(:owner,:key,:sha,:id,:app,:now)"
                        ),
                        {**params, "key": body["idempotency_key"]},
                    )
            self.reconcile()
            with transaction(self.db) as conn:
                self.repository.require_admin(context, conn)
                return self.public(
                    rows(
                        self.db,
                        "SELECT * FROM ingestion_schedules WHERE schedule_id=:id",
                        {"id": schedule_id},
                        conn,
                    )[0]
                )

    def require(self, native_name, conn=None):
        self.service.enabled()
        found = rows(
            self.db,
            "SELECT * FROM ingestion_schedules WHERE native_name=:name",
            {"name": native_name},
            conn,
        )
        if not found or not found[0]["enabled"] or found[0]["quarantined"]:
            raise CompanyError("ingestion_schedule_paused", 409)
        row = found[0]
        self.repository.require_admin(ToolContext(row["owner_id"]), conn)
        body = json.loads(row["body_json"])
        if (
            sha(row["body_json"]) != row["body_sha256"]
            or generation_name(row["schedule_id"], row["state_version"], row["body_sha256"])
            != native_name
        ):
            raise CompanyError("ingestion_schedule_integrity_failed", 409)
        if row["application_version"] != self.repository.versions["application_version"]:
            raise CompanyError("ingestion_application_version_changed", 409)
        source = self.repository._permit(
            self.repository._source(row["source_id"]), body, "http_fetch", "schedule"
        )
        if sha(canonical(source.model_dump(mode="json"))) != row["policy_sha256"]:
            raise CompanyError("ingestion_source_policy_changed", 409)
        return row, body

    def reconcile(self):
        # The business record is authoritative even between these two databases.
        for generation in self.db.rows(
            "SELECT * FROM ingestion_schedule_versions ORDER BY created_at"
        ):
            name = generation["native_name"]
            body = json.loads(generation["body_json"])
            current = False
            try:
                self.require(name)
                current = True
            except CompanyError:
                pass
            if DBOS.get_schedule(name) is None:
                DBOS.create_schedule(
                    schedule_name=name,
                    workflow_fn=self.workflow,
                    schedule=body["cron"],
                    context={"native_name": name},
                    automatic_backfill=False,
                    cron_timezone="UTC",
                    queue_name="bidops_ingestion_schedule_v1",
                )
            if current:
                DBOS.resume_schedule(name)
            else:
                DBOS.pause_schedule(name)

    def admit(self, native_name, native_id):
        prior = self.db.rows(
            "SELECT * FROM ingestion_schedule_ticks WHERE native_id=:id", {"id": native_id}
        )
        if prior:
            return {"run_id": prior[0]["run_id"], "error_code": prior[0]["error_code"]}
        generation = self.db.rows(
            "SELECT * FROM ingestion_schedule_versions WHERE native_name=:name",
            {"name": native_name},
        )
        if not generation:
            raise CompanyError("ingestion_schedule_not_found", 404)
        run_id, error = None, None
        try:
            row, body = self.require(native_name)
            payload = HttpRun.model_validate(
                {
                    "source_id": body["source_id"],
                    "items": body["items"],
                    "idempotency_key": "schedule-" + sha(native_id),
                }
            ).model_dump(mode="json")
            record = self.repository.create(
                ToolContext(row["owner_id"]),
                payload,
                "http_fetch",
                trigger_kind="schedule",
                native_id="source-" + uuid.uuid4().hex,
                schedule_native_name=native_name,
            )
            run_id = record["run_id"]
            if self.service.fault:
                self.service.fault("after_schedule_run_commit")
            self.require(native_name)
        except CompanyError as exc:
            error = exc.code
            if run_id:
                self.repository.finish(run_id, "failed", error)
        with transaction(self.db) as conn:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO ingestion_schedule_ticks VALUES "
                    "(:native,:schedule,:app,:now,:run,:error)"
                ),
                {
                    "native": native_id,
                    "schedule": generation[0]["schedule_id"],
                    "app": generation[0]["application_version"],
                    "now": self.repository.clock(),
                    "run": run_id,
                    "error": error,
                },
            )
            conn.execute(
                text(
                    "UPDATE ingestion_schedules SET "
                    "last_run_id=:run,last_error_code=:error WHERE schedule_id=:schedule "
                    "AND native_name=:name"
                ),
                {
                    "run": run_id,
                    "error": error,
                    "schedule": generation[0]["schedule_id"],
                    "name": native_name,
                },
            )
        return {"run_id": run_id, "error_code": error}
