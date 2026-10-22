"""Append-only human decisions and projections over fixed, authorized results.

This repository is not a queue. The outbox contains IDs for the native workflow
integration. Publication and successor linking use the same business transaction
as the case CAS; no model or workflow is launched here.
"""

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import text

from bidops.company.authorization import CompanyError, ToolContext, rows, transaction
from bidops.enterprise.auth import ROLE_PERMISSIONS
from bidops.qualification.contracts import AssessmentGroup
from bidops.qualification.sources import ScreeningInputManifest, canonical_json
from bidops.review.authorization import ReviewAuthorization
from bidops.review.schemas import (
    AssignmentCommand,
    AttachRevision,
    CaseCommand,
    CreateCase,
    ItemDecision,
    SuccessorCommand,
)
from bidops.screening.schemas import ReviewEvent


def _json(value):
    try:
        encoded = canonical_json(value)
    except (TypeError, ValueError, RecursionError):
        raise CompanyError("review_invalid_json", 422) from None
    if len(encoded.encode()) > 2_000_000:
        raise CompanyError("review_body_too_large", 413)
    return encoded


def _sha(encoded):
    return hashlib.sha256(encoded.encode()).hexdigest()


def _parse(model, value):
    try:
        return model.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        raise CompanyError("review_invalid_command", 422) from None


class ReviewRepository:
    def __init__(self, db, source_adapter):
        self.db, self.sources = db, source_adapter
        self.authorization = ReviewAuthorization(db, source_adapter)
        self.auth = self.authorization
        self.clock = self.auth.clock

    @contextmanager
    def _checked_transaction(self, context, manifests, *, current=False):
        """Validate bytes outside the write lock; reject any intervening commit.

        SourceAdapter calls an existing company read that takes its own final
        authorization transaction. Holding BEGIN IMMEDIATE while calling it
        would deadlock SQLite. data_version on this SAME connection detects any
        other committed write across that preflight, before the final live ACL
        and case CAS checks acquire the database write lock.
        """
        with self.db.engine.connect() as conn:
            for attempt in range(3):
                before = conn.exec_driver_sql("PRAGMA data_version").scalar_one()
                states = []
                for manifest in manifests:
                    if current:
                        states.append(self.sources.changes(ToolContext(context.owner_id), manifest))
                    else:
                        self.sources.recheck(
                            ToolContext(context.owner_id), manifest, for_execution=False
                        )
                conn.rollback()
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                if conn.exec_driver_sql("PRAGMA data_version").scalar_one() == before:
                    break
                conn.rollback()
                if attempt == 2:
                    raise CompanyError("review_sources_changed", 409)
            try:
                yield conn, states
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _sources(self, conn, case_id, manifest):
        for ref in manifest.company_revisions:
            conn.execute(
                text("INSERT OR IGNORE INTO review_sources VALUES (:case,:object,:version,:sha)"),
                {
                    "case": case_id,
                    "object": ref.object_id,
                    "version": ref.version_id,
                    "sha": ref.sha256,
                },
            )

    def _result(self, result_id, owner_id, conn=None):
        """Dedicated review projection: actor ACLs are checked by the caller.

        The recorded owner binds provenance only. We never manufacture an owner
        ToolContext to bypass another reviewer's lack of source access.
        """
        found = rows(
            self.db,
            "SELECT r.*,b.body_json,l.quarantined,l.purge_state FROM qualification_results r "
            "JOIN qualification_result_bodies b ON b.result_id=r.result_id "
            "JOIN qualification_result_lifecycle l ON l.result_id=r.result_id "
            "WHERE r.result_id=:id AND r.owner_id=:owner",
            {"id": result_id, "owner": owner_id},
            conn,
        )
        if not found or found[0]["quarantined"] or found[0]["purge_state"] != "live":
            raise CompanyError("review_result_unavailable")
        row = found[0]
        if _sha(row["body_json"]) != row["body_sha256"]:
            raise CompanyError("review_result_integrity_failed", 409)
        try:
            body = json.loads(row["body_json"])
            manifest = ScreeningInputManifest.model_validate(body["manifest"])
            result = body["result"]
            if result["kind"] != row["kind"]:
                raise ValueError("kind")
            actual_refs = rows(
                self.db,
                "SELECT object_id,version_id,sha256 FROM qualification_result_sources "
                "WHERE result_id=:id",
                {"id": result_id},
                conn,
            )
            if {
                tuple(ref[k] for k in ("object_id", "version_id", "sha256")) for ref in actual_refs
            } != {ref.key() for ref in manifest.company_revisions}:
                raise ValueError("source closure")
        except (KeyError, TypeError, ValueError):
            raise CompanyError("review_result_integrity_failed", 409) from None
        return {
            "result_id": result_id,
            "manifest": manifest.model_dump(mode="json"),
            "result": result,
            "provenance": body.get("provenance", {}),
            "created_at": row["created_at"],
        }

    def _job_result(self, run_id, owner_id, conn, *, ready=True):
        found = rows(
            self.db,
            "SELECT j.*,s.status,s.result_id,s.result_revision,s.quarantined,s.purge_state "
            "FROM durable_jobs j JOIN durable_job_state s ON s.run_id=j.run_id WHERE j.run_id=:id",
            {"id": run_id},
            conn,
        )
        if not found:
            raise CompanyError("review_job_unavailable")
        job = found[0]
        if (
            job["owner_id"] != owner_id
            or job["quarantined"]
            or job["purge_state"] != "live"
            or (ready and job["status"] not in {"waiting_review", "completed"})
            or not job["result_id"]
        ):
            raise CompanyError("review_job_unavailable")
        record = self._result(job["result_id"], owner_id, conn)
        try:
            manifest = ScreeningInputManifest.model_validate(record["manifest"])
            result = record["result"]
            groups = [AssessmentGroup.model_validate(value) for value in result["groups"]]
            if (
                not groups
                or result["kind"] != "checklist"
                or result["run_id"] != job["result_id"]
                or record["provenance"].get("business_run_id") != run_id
                or result["result_revision"] != job["result_revision"]
                or manifest.closure_sha256 != job["inputs_manifest_sha256"]
            ):
                raise ValueError("binding")
            keys = set()
            for group in groups:
                assessment = group.assessment
                if (
                    assessment.result_revision != job["result_revision"]
                    or assessment.inputs.profile_revision != manifest.profile_revision
                    or assessment.inputs.project_id != manifest.project_id
                    or assessment.inputs.notice_revision not in manifest.notice_revisions
                    or assessment.inputs.material_revisions != manifest.material_revisions
                    or assessment.inputs.attachment_revisions != manifest.attachment_revisions
                    or assessment.inputs.as_of != manifest.as_of
                ):
                    raise ValueError("assessment inputs")
                for item in assessment.assessments:
                    key = assessment.assessment_id, item.condition_id
                    if key in keys:
                        raise ValueError("duplicate item")
                    keys.add(key)
        except (KeyError, TypeError, ValueError):
            raise CompanyError("review_result_binding_mismatch", 409) from None
        return job, record

    def _revision(self, revision_id, case_id, conn=None):
        found = rows(
            self.db,
            "SELECT * FROM review_revisions WHERE revision_id=:revision AND case_id=:case",
            {"revision": revision_id, "case": case_id},
            conn,
        )
        if not found:
            raise CompanyError("review_revision_not_found")
        return found[0]

    def _insert_revision(self, conn, case_id, job, *, parent=None, trigger=None):
        if rows(
            self.db,
            "SELECT 1 FROM review_revisions WHERE run_id=:run",
            {"run": job["run_id"]},
            conn,
        ):
            raise CompanyError("review_job_already_linked", 409)
        revision_id = uuid.uuid4().hex
        conn.execute(
            text(
                "INSERT INTO review_revisions VALUES "
                "(:id,:case,:parent,:run,:result,:revision,:sha,:trigger,:now)"
            ),
            {
                "id": revision_id,
                "case": case_id,
                "parent": parent,
                "run": job["run_id"],
                "result": job["result_id"],
                "revision": job["result_revision"],
                "sha": job["inputs_manifest_sha256"],
                "trigger": trigger,
                "now": self.clock(),
            },
        )
        return revision_id

    def _check_reviewers(self, reviewer_ids, conn):
        for reviewer in set(reviewer_ids):
            subject = self.sources.company.auth.require_subject(ToolContext(reviewer), conn)
            if "review:decide" not in self.sources.company.auth.permissions(subject):
                raise CompanyError("review_assignee_role_required", 422)

    @staticmethod
    def _cas(case, expected_version):
        if case["review_version"] != expected_version:
            raise CompanyError("review_version_conflict", 409)

    def _replay(self, context, case, key, request_sha, conn):
        found = rows(
            self.db,
            "SELECT * FROM review_events WHERE case_id=:case AND actor_id=:actor "
            "AND idempotency_key=:key",
            {"case": case["case_id"], "actor": context.owner_id, "key": key},
            conn,
        )
        if not found:
            return None
        event = found[0]
        if event["request_sha256"] != request_sha:
            raise CompanyError("review_idempotency_conflict", 409)
        if event["review_epoch"] != case["review_epoch"]:
            raise CompanyError("review_event_epoch_changed", 409)
        return self._event(event, conn)

    def _event(self, metadata, conn=None):
        found = rows(
            self.db,
            "SELECT body_json FROM review_event_bodies WHERE event_id=:id",
            {"id": metadata["event_id"]},
            conn,
        )
        if not found:
            raise CompanyError("review_history_unavailable")
        encoded = found[0]["body_json"]
        if _sha(encoded) != metadata["event_sha256"]:
            raise CompanyError("review_event_integrity_failed", 409)
        try:
            body = json.loads(encoded)
            if body["kind"] != metadata["event_kind"]:
                raise ValueError("kind")
            if body["kind"] == "decision":
                event = ReviewEvent.model_validate(body["review_event"])
                revision = self._revision(metadata["revision_id"], metadata["case_id"], conn)
                if (
                    event.origin != "server_authenticated"
                    or event.event_id != metadata["event_id"]
                    or event.actor.principal_id != metadata["actor_id"]
                    or event.actor.authorization_context_id != metadata["authorization_context_id"]
                    or event.assessment_id != metadata["assessment_id"]
                    or event.supersedes_event_id != metadata["supersedes_event_id"]
                    or body["condition_id"] != metadata["condition_id"]
                    or event.occurred_at.timestamp() != metadata["occurred_at"]
                    or event.run_id != revision["run_id"]
                    or event.result_revision != revision["result_revision"]
                    or event.inputs_manifest_sha256 != revision["inputs_manifest_sha256"]
                    or event.command.idempotency_key != metadata["idempotency_key"]
                    or event.command.reason != body["reason"]
                ):
                    raise ValueError("binding")
        except (KeyError, TypeError, ValueError):
            raise CompanyError("review_event_integrity_failed", 409) from None
        # Session hashes are audit-only; they are never login credentials.
        return {**metadata, **body}

    def _append(
        self,
        conn,
        context,
        case,
        *,
        kind,
        request,
        body,
        revision_id=None,
        assessment_id=None,
        condition_id=None,
        supersedes=None,
        outbox=None,
    ):
        event_id = body.get("review_event", {}).get("event_id") or uuid.uuid4().hex
        encoded = _json({"kind": kind, **body})
        now = (
            datetime.fromisoformat(body["review_event"]["occurred_at"]).timestamp()
            if "review_event" in body
            else self.clock()
        )
        conn.execute(
            text(
                "INSERT INTO review_events VALUES "
                "(:id,:case,:revision,:assessment,:condition,:kind,"
                ":actor,:context,:now,:version,:request,:event,:key,:supersedes,:epoch)"
            ),
            {
                "id": event_id,
                "case": case["case_id"],
                "revision": revision_id or case["head_revision_id"],
                "assessment": assessment_id,
                "condition": condition_id,
                "kind": kind,
                "actor": context.owner_id,
                "context": context.token_hash,
                "now": now,
                "version": case["review_version"] + 1,
                "request": _sha(_json(request)),
                "event": _sha(encoded),
                "key": request["idempotency_key"],
                "supersedes": supersedes,
                "epoch": case["review_epoch"],
            },
        )
        conn.execute(
            text("INSERT INTO review_event_bodies VALUES (:id,:body)"),
            {"id": event_id, "body": encoded},
        )
        if outbox:
            conn.execute(
                text(
                    "INSERT INTO review_outbox(event_id,run_id,kind,review_epoch) VALUES "
                    "(:id,:run,:kind,:epoch)"
                ),
                {
                    "id": event_id,
                    "run": outbox[0],
                    "kind": outbox[1],
                    "epoch": case["review_epoch"],
                },
            )
        return event_id

    def _advance(self, conn, case, *, status=None, head=None):
        conn.execute(
            text(
                "UPDATE review_case_state SET "
                "review_version=review_version+1,status=COALESCE(:status,status),"
                "head_revision_id=COALESCE(:head,head_revision_id),updated_at=:now "
                "WHERE case_id=:id AND review_version=:version"
            ),
            {
                "id": case["case_id"],
                "version": case["review_version"],
                "status": status,
                "head": head,
                "now": self.clock(),
            },
        )

    def create(self, context, *, run_id, candidate_batch_id, idempotency_key, reviewer_ids=()):
        command = _parse(
            CreateCase,
            {
                "run_id": run_id,
                "candidate_batch_id": candidate_batch_id,
                "idempotency_key": idempotency_key,
                "reviewer_ids": list(reviewer_ids),
            },
        )
        request = command.model_dump(mode="json")
        request["reviewer_ids"] = sorted(set(request["reviewer_ids"]))
        request_sha = _sha(_json(request))
        self.auth.require_human(context, "agent:use")
        _, preview = self._job_result(command.run_id, context.owner_id, None, ready=False)
        batch_preview = self._result(command.candidate_batch_id, context.owner_id)
        with self._checked_transaction(
            context, [preview["manifest"], batch_preview["manifest"]]
        ) as checked:
            conn, _ = checked
            self.auth.require_human(context, "agent:use", conn)
            existing = rows(
                self.db,
                "SELECT case_id,request_sha256 FROM review_cases WHERE owner_id=:owner "
                "AND create_key=:key",
                {"owner": context.owner_id, "key": command.idempotency_key},
                conn,
            )
            if existing:
                if existing[0]["request_sha256"] != request_sha:
                    raise CompanyError("review_idempotency_conflict", 409)
                case_id = existing[0]["case_id"]
                self.auth.require_case(context, case_id, action="manage", conn=conn)
            else:
                job, record = self._job_result(command.run_id, context.owner_id, conn)
                candidate = self._result(command.candidate_batch_id, context.owner_id, conn)
                manifest = ScreeningInputManifest.model_validate(record["manifest"])
                candidate_manifest = ScreeningInputManifest.model_validate(candidate["manifest"])
                result = candidate["result"]
                if (
                    result.get("kind") != "candidate_batch"
                    or candidate_manifest.profile_revision != manifest.profile_revision
                    or not any(
                        item.get("project_id") == manifest.project_id
                        for item in result.get("items", [])
                    )
                ):
                    raise CompanyError("review_candidate_binding_mismatch", 409)
                self._check_reviewers(request["reviewer_ids"], conn)
                # Full byte and live ACL checks for the actual caller, not recorded owner.
                case_id = uuid.uuid4().hex
                conn.execute(
                    text("INSERT INTO review_cases VALUES (:id,:owner,:batch,:key,:sha,:now)"),
                    {
                        "id": case_id,
                        "owner": context.owner_id,
                        "batch": command.candidate_batch_id,
                        "key": command.idempotency_key,
                        "sha": request_sha,
                        "now": self.clock(),
                    },
                )
                revision_id = self._insert_revision(conn, case_id, job)
                conn.execute(
                    text(
                        "INSERT INTO "
                        "review_case_state(case_id,head_revision_id,review_epoch,updated_at) "
                        "VALUES (:id,:head,:epoch,:now)"
                    ),
                    {
                        "id": case_id,
                        "head": revision_id,
                        "epoch": uuid.uuid4().hex,
                        "now": self.clock(),
                    },
                )
                for reviewer in request["reviewer_ids"]:
                    conn.execute(
                        text("INSERT INTO review_assignments VALUES (:case,:subject)"),
                        {"case": case_id, "subject": reviewer},
                    )
                self._sources(conn, case_id, manifest)
                self._sources(conn, case_id, candidate_manifest)
                self.auth.require_case(context, case_id, action="manage", conn=conn)
        return self.get(context, case_id)

    def _items(self, revision, record, events, case, source_state):
        latest = {}
        for event in events:
            if (
                event["event_kind"] == "decision"
                and event["revision_id"] == revision["revision_id"]
            ):
                latest[event["assessment_id"], event["condition_id"]] = event
        items = []
        for raw in record["result"]["groups"]:
            group = AssessmentGroup.model_validate(raw)
            conditions = {value.condition_id: value for value in group.assessment.conditions}
            for original in group.assessment.assessments:
                event = latest.get((group.assessment.assessment_id, original.condition_id))
                reasons = []
                if event and event["review_epoch"] != case["review_epoch"]:
                    reasons.append("review_epoch_changed")
                if revision["revision_id"] != case["head_revision_id"]:
                    reasons.append("new_revision")
                if not source_state["current"]:
                    reasons.append("source_changed")
                if case["status"] == "recomputing":
                    reasons.append("successor_pending")
                items.append(
                    {
                        "revision_id": revision["revision_id"],
                        "assessment_id": group.assessment.assessment_id,
                        "condition_id": original.condition_id,
                        "condition": conditions[original.condition_id].model_dump(mode="json"),
                        "agent_assessment": original.model_dump(mode="json"),
                        "latest_decision": event["review_event"]["command"]["decision"]
                        if event
                        else None,
                        "latest_event": event,
                        "effective": bool(event) and not reasons,
                        "ineffective_reasons": reasons,
                    }
                )
        return items

    @staticmethod
    def _candidate_page(candidate, offset, limit):
        """A bounded view, explicitly distinct from the complete stored/exported body."""
        result = candidate["result"]
        items = result["items"][offset : offset + limit]
        selected = {ref["version_id"] for item in items for ref in item.get("notice_revisions", [])}
        versions = result.get("public_versions", [])
        return {
            **candidate,
            "result": {
                **result,
                "items": items,
                "public_versions": [v for v in versions if v["version"]["version_id"] in selected],
            },
            "page": {
                "offset": offset,
                "limit": limit,
                "total": len(result["items"]),
                "has_more": offset + limit < len(result["items"]),
                "complete_record": False,
                "public_versions_total": len(versions),
                "full_record_in_export": True,
            },
        }

    def _projection(self, context, case, *, candidate_offset=0, candidate_limit=20):
        events = [
            self._event(meta)
            for meta in rows(
                self.db,
                "SELECT * FROM review_events WHERE case_id=:id ORDER BY review_version,event_id",
                {"id": case["case_id"]},
            )
        ]
        revisions = []
        states = []
        for revision in rows(
            self.db,
            "SELECT * FROM review_revisions WHERE case_id=:id ORDER BY created_at,revision_id",
            {"id": case["case_id"]},
        ):
            record = self._result(revision["result_id"], case["owner_id"])
            manifest = ScreeningInputManifest.model_validate(record["manifest"])
            if (
                manifest.closure_sha256 != revision["inputs_manifest_sha256"]
                or record["result"].get("result_revision") != revision["result_revision"]
                or record["result"].get("run_id") != revision["result_id"]
                or record["provenance"].get("business_run_id") != revision["run_id"]
            ):
                raise CompanyError("review_result_binding_mismatch", 409)
            changes = self.sources.changes(ToolContext(context.owner_id), manifest)
            states.append(changes)
            revisions.append(
                {
                    **revision,
                    **record,
                    "source_state": changes,
                    "is_head": revision["revision_id"] == case["head_revision_id"],
                }
            )
        candidate = self._result(case["candidate_batch_id"], case["owner_id"])
        self.sources.recheck(
            ToolContext(context.owner_id), candidate["manifest"], for_execution=False
        )
        if candidate_limit is not None:
            candidate = self._candidate_page(candidate, candidate_offset, candidate_limit)
        head = next(value for value in revisions if value["is_head"])
        source_state = head["source_state"]
        items = self._items(head, head, events, case, source_state)
        assignments = [
            r["subject_id"]
            for r in rows(
                self.db,
                "SELECT subject_id FROM review_assignments WHERE case_id=:id ORDER BY subject_id",
                {"id": case["case_id"]},
            )
        ]
        identity = self.auth.require_human(context)
        actions = ["export"]
        if context.owner_id == case["owner_id"] and "agent:use" in identity["permissions"]:
            actions += ["assign"]
            if case["status"] != "recomputing":
                actions.append("successor")
            else:
                actions.append("attach")
            if self._closable(case, items, source_state):
                actions.append("close")
        if (
            context.owner_id in assignments
            and "review:decide" in identity["permissions"]
            and case["status"] not in {"closed", "recomputing"}
            and source_state["current"]
        ):
            actions.append("decide")
        return {
            **case,
            "source_state": source_state,
            "allowed_actions": actions,
            "candidate_batch": candidate,
            "revisions": revisions,
            "items": items,
            "events": events,
            "assignments": assignments,
            "successors": rows(
                self.db,
                "SELECT * FROM review_successors WHERE case_id=:id ORDER BY created_at,run_id",
                {"id": case["case_id"]},
            ),
        }

    def get(self, context, case_id, *, candidate_offset=0, candidate_limit=20):
        if (
            type(candidate_offset) is not int
            or candidate_offset < 0
            or (
                candidate_limit is not None
                and (type(candidate_limit) is not int or not 1 <= candidate_limit <= 100)
            )
            or (candidate_limit is None and candidate_offset != 0)
        ):
            raise CompanyError("review_invalid_page", 422)
        case = self.auth.require_case(context, case_id, quarantine=True)
        if case["quarantined"]:
            # No private history is released before explicit restoration review.
            identity = self.auth.require_human(context)
            actions = (
                ["reauthorize"]
                if context.owner_id == case["owner_id"] and "agent:use" in identity["permissions"]
                else []
            )
            with transaction(self.db) as conn:
                self.auth.require_case(context, case_id, conn=conn, quarantine=True)
            return {
                **case,
                "allowed_actions": actions,
                "source_state": {"current": False, "stale_reasons": ["restore_quarantine"]},
                "candidate_batch": None,
                "revisions": [],
                "items": [],
                "events": [],
                "assignments": [],
                "successors": [],
            }
        result = self._projection(
            context, case, candidate_offset=candidate_offset, candidate_limit=candidate_limit
        )
        # Re-read all source ACLs and session state after every derived quote was loaded.
        with transaction(self.db) as conn:
            current = self.auth.require_case(context, case_id, conn=conn)
            if (
                current["review_version"] != case["review_version"]
                or current["review_epoch"] != case["review_epoch"]
            ):
                raise CompanyError("review_version_conflict", 409)
        return result

    def list(self, context, *, limit=50, offset=0):
        self.auth.require_human(context)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise CompanyError("review_invalid_page", 422)
        visible = []
        for found in rows(
            self.db,
            "SELECT DISTINCT c.case_id,c.created_at FROM review_cases c "
            "LEFT JOIN review_assignments a ON a.case_id=c.case_id WHERE c.owner_id=:owner "
            "OR a.subject_id=:owner ORDER BY c.created_at DESC,c.case_id",
            {"owner": context.owner_id},
        ):
            try:
                # Determine visibility without loading every historical candidate body.
                self.auth.require_case(context, found["case_id"], quarantine=True)
                visible.append(found["case_id"])
            except CompanyError as exc:
                if exc.status_code != 404:
                    raise
        items = [self.get(context, case_id) for case_id in visible[offset : offset + limit]]
        with transaction(self.db) as conn:
            self.auth.require_human(context, conn=conn)
            for item in items:
                current = self.auth.require_case(
                    context, item["case_id"], conn=conn, quarantine=True
                )
                if any(
                    current[field] != item[field]
                    for field in (
                        "review_version",
                        "review_epoch",
                        "quarantined",
                        "purge_state",
                        "head_revision_id",
                        "status",
                    )
                ):
                    raise CompanyError("review_version_conflict", 409)
        return {
            "items": items,
            "total": len(visible),
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < len(visible),
        }

    def assign(self, context, case_id, *, reviewer_ids, expected_version, idempotency_key, reason):
        cmd = _parse(
            AssignmentCommand,
            {
                "reviewer_ids": list(reviewer_ids),
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
                "reason": reason,
            },
        )
        request = {"kind": "assignment", **cmd.model_dump(mode="json")}
        request["reviewer_ids"] = sorted(set(request["reviewer_ids"]))
        with transaction(self.db) as conn:
            case = self.auth.require_case(context, case_id, action="manage", conn=conn)
            if not self._replay(context, case, cmd.idempotency_key, _sha(_json(request)), conn):
                self._cas(case, cmd.expected_version)
                self._check_reviewers(request["reviewer_ids"], conn)
                self._append(
                    conn,
                    context,
                    case,
                    kind="assignment",
                    request=request,
                    body={"reason": cmd.reason, "reviewer_ids": request["reviewer_ids"]},
                )
                conn.execute(
                    text("DELETE FROM review_assignments WHERE case_id=:id"), {"id": case_id}
                )
                for reviewer in request["reviewer_ids"]:
                    conn.execute(
                        text("INSERT INTO review_assignments VALUES (:id,:subject)"),
                        {"id": case_id, "subject": reviewer},
                    )
                self._advance(conn, case)
        return self.get(context, case_id)

    def decide(
        self,
        context,
        case_id,
        *,
        revision_id,
        assessment_id,
        condition_id,
        expected_version,
        command,
        supersedes_event_id=None,
    ):
        cmd = _parse(
            ItemDecision,
            {
                "revision_id": revision_id,
                "assessment_id": assessment_id,
                "condition_id": condition_id,
                "expected_version": expected_version,
                "command": command,
                "supersedes_event_id": supersedes_event_id,
            },
        )
        if len(cmd.command.reason) > 4000 or len(cmd.command.idempotency_key) > 128:
            raise CompanyError("review_invalid_command", 422)
        request = {
            "kind": "decision",
            **cmd.model_dump(mode="json"),
            "idempotency_key": cmd.command.idempotency_key,
        }
        preview_case = self.auth.require_case(context, case_id, action="decide")
        preview_revision = self._revision(preview_case["head_revision_id"], case_id)
        preview = self._result(preview_revision["result_id"], preview_case["owner_id"])
        with self._checked_transaction(context, [preview["manifest"]], current=True) as checked:
            conn, states = checked
            case = self.auth.require_case(context, case_id, action="decide", conn=conn)
            if not self._replay(
                context, case, cmd.command.idempotency_key, _sha(_json(request)), conn
            ):
                self._cas(case, cmd.expected_version)
                if case["head_revision_id"] != cmd.revision_id or case["status"] in {
                    "closed",
                    "recomputing",
                }:
                    raise CompanyError("review_revision_not_current", 409)
                revision = self._revision(cmd.revision_id, case_id, conn)
                record = self._result(revision["result_id"], case["owner_id"], conn)
                changes = states[0]
                if not changes["current"]:
                    raise CompanyError("review_sources_changed", 409)
                matches = [
                    item
                    for item in self._items(revision, record, [], case, changes)
                    if item["assessment_id"] == cmd.assessment_id
                    and item["condition_id"] == cmd.condition_id
                ]
                if (
                    len(matches) != 1
                    or cmd.command.expected_result_revision != revision["result_revision"]
                ):
                    raise CompanyError("review_item_binding_mismatch", 409)
                latest = rows(
                    self.db,
                    "SELECT event_id FROM review_events WHERE case_id=:case AND "
                    "revision_id=:revision "
                    "AND assessment_id=:assessment AND condition_id=:condition AND "
                    "event_kind='decision' "
                    "AND review_epoch=:epoch ORDER BY review_version DESC LIMIT 1",
                    {
                        "case": case_id,
                        "revision": revision_id,
                        "assessment": assessment_id,
                        "condition": condition_id,
                        "epoch": case["review_epoch"],
                    },
                    conn,
                )
                required = latest[0]["event_id"] if latest else None
                if cmd.supersedes_event_id != required:
                    raise CompanyError("review_supersedes_required", 409)
                event = ReviewEvent(
                    event_id=uuid.uuid4().hex,
                    run_id=revision["run_id"],
                    assessment_id=cmd.assessment_id,
                    result_revision=revision["result_revision"],
                    inputs_manifest_sha256=revision["inputs_manifest_sha256"],
                    command=cmd.command,
                    actor={
                        "actor_kind": "human",
                        "principal_id": context.owner_id,
                        "authorization_context_id": context.token_hash,
                    },
                    occurred_at=datetime.fromtimestamp(self.clock(), UTC),
                    origin="server_authenticated",
                    supersedes_event_id=cmd.supersedes_event_id,
                )
                self._append(
                    conn,
                    context,
                    case,
                    kind="decision",
                    request=request,
                    body={
                        "reason": cmd.command.reason,
                        "review_event": event.model_dump(mode="json"),
                        "condition_id": cmd.condition_id,
                        "item_sha256": _sha(
                            _json({k: matches[0][k] for k in ("condition", "agent_assessment")})
                        ),
                    },
                    assessment_id=cmd.assessment_id,
                    condition_id=cmd.condition_id,
                    supersedes=cmd.supersedes_event_id,
                    outbox=(revision["run_id"], "decision"),
                )
                self._advance(
                    conn,
                    case,
                    status="awaiting_materials"
                    if cmd.command.decision == "request_materials"
                    else "open",
                )
                self.auth.require_case(context, case_id, action="decide", conn=conn)
        return self.get(context, case_id)

    @staticmethod
    def _closable(case, items, source_state):
        return (
            case["status"] in {"open", "awaiting_materials"}
            and source_state["current"]
            and bool(items)
            and all(
                item["effective"] and item["latest_decision"] in {"confirm", "reject"}
                for item in items
            )
        )

    def close(self, context, case_id, *, expected_version, idempotency_key, reason):
        cmd = _parse(
            CaseCommand,
            {
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
                "reason": reason,
            },
        )
        request = {"kind": "close", **cmd.model_dump(mode="json")}
        preview_case = self.auth.require_case(context, case_id, action="manage")
        preview_revision = self._revision(preview_case["head_revision_id"], case_id)
        preview = self._result(preview_revision["result_id"], preview_case["owner_id"])
        with self._checked_transaction(context, [preview["manifest"]], current=True) as checked:
            conn, states = checked
            case = self.auth.require_case(context, case_id, action="manage", conn=conn)
            if not self._replay(context, case, cmd.idempotency_key, _sha(_json(request)), conn):
                self._cas(case, cmd.expected_version)
                revision = self._revision(case["head_revision_id"], case_id, conn)
                record = self._result(revision["result_id"], case["owner_id"], conn)
                changes = states[0]
                events = [
                    self._event(meta, conn)
                    for meta in rows(
                        self.db,
                        "SELECT * FROM review_events WHERE case_id=:id ORDER BY review_version",
                        {"id": case_id},
                        conn,
                    )
                ]
                if not self._closable(
                    case, self._items(revision, record, events, case, changes), changes
                ):
                    raise CompanyError("review_not_fully_decided", 409)
                self._append(
                    conn,
                    context,
                    case,
                    kind="close",
                    request=request,
                    body={"reason": cmd.reason},
                    outbox=(revision["run_id"], "closed"),
                )
                self._advance(conn, case, status="closed")
                self.auth.require_case(context, case_id, action="manage", conn=conn)
        return self.get(context, case_id)

    def link_successor(
        self,
        conn,
        context,
        case_id,
        *,
        expected_version,
        revision_id,
        trigger_event_id,
        new_job_record,
        idempotency_key,
        reason,
    ):
        """Call inside JobsRepository's insert/replay transaction, before enqueue.

        A failed review CAS rolls back the new job too. An idempotent job replay
        invokes this again and validates the original immutable link before CAS.
        """
        cmd = _parse(
            SuccessorCommand,
            {
                "expected_version": expected_version,
                "revision_id": revision_id,
                "trigger_event_id": trigger_event_id,
                "idempotency_key": idempotency_key,
                "reason": reason,
            },
        )
        try:
            manifest = ScreeningInputManifest.model_validate(new_job_record["manifest"])
            run_id = new_job_record["run_id"]
        except (KeyError, ValueError, TypeError):
            raise CompanyError("review_successor_binding_mismatch", 409) from None
        request = {
            "kind": "successor_linked",
            **cmd.model_dump(mode="json"),
            "run_id": run_id,
            "inputs_manifest_sha256": manifest.closure_sha256,
        }
        case = self.auth.require_case(context, case_id, action="manage", conn=conn)
        if self._replay(context, case, cmd.idempotency_key, _sha(_json(request)), conn):
            return {"case_id": case_id, "run_id": run_id, "replayed": True}
        self._cas(case, cmd.expected_version)
        if case["head_revision_id"] != cmd.revision_id or case["status"] == "recomputing":
            raise CompanyError("review_revision_not_current", 409)
        parent = self._revision(cmd.revision_id, case_id, conn)
        old_record = self._result(parent["result_id"], case["owner_id"], conn)
        old_manifest = ScreeningInputManifest.model_validate(old_record["manifest"])
        trigger = rows(
            self.db,
            "SELECT * FROM review_events WHERE event_id=:id AND case_id=:case AND "
            "revision_id=:revision "
            "AND event_kind='decision' AND review_epoch=:epoch",
            {
                "id": cmd.trigger_event_id,
                "case": case_id,
                "revision": cmd.revision_id,
                "epoch": case["review_epoch"],
            },
            conn,
        )
        if (
            not trigger
            or self._event(trigger[0], conn)["review_event"]["command"]["decision"]
            != "request_materials"
        ):
            raise CompanyError("review_material_request_required", 409)
        newer = rows(
            self.db,
            "SELECT 1 FROM review_events WHERE supersedes_event_id=:id",
            {"id": cmd.trigger_event_id},
            conn,
        )
        persisted = rows(
            self.db,
            "SELECT j.owner_id,j.inputs_manifest_sha256,s.status,s.quarantined,s.purge_state "
            "FROM durable_jobs j JOIN durable_job_state s ON s.run_id=j.run_id WHERE j.run_id=:id",
            {"id": run_id},
            conn,
        )
        old_input = rows(
            self.db,
            "SELECT j.body_sha256,b.body_json FROM durable_jobs j JOIN durable_job_inputs b "
            "ON b.run_id=j.run_id WHERE j.run_id=:id",
            {"id": parent["run_id"]},
            conn,
        )
        new_input = rows(
            self.db,
            "SELECT j.body_sha256,b.body_json FROM durable_jobs j JOIN durable_job_inputs b "
            "ON b.run_id=j.run_id WHERE j.run_id=:id",
            {"id": run_id},
            conn,
        )
        try:
            if (
                not old_input
                or not new_input
                or any(
                    _sha(row["body_json"]) != row["body_sha256"]
                    for row in [old_input[0], new_input[0]]
                )
            ):
                raise ValueError("input body")
            original_request = json.loads(old_input[0]["body_json"])["request"]
            actual_request = json.loads(new_input[0]["body_json"])["request"]
            if actual_request != new_job_record["request"]:
                raise ValueError("request binding")
            old_packages = original_request.get("target_package_ids", [])
            new_packages = actual_request.get("target_package_ids", [])
            if (
                not isinstance(old_packages, list)
                or not isinstance(new_packages, list)
                or sorted(set(old_packages)) != sorted(set(new_packages))
            ):
                raise ValueError("package scope")
            changed_refs = any(
                getattr(manifest, field) != getattr(old_manifest, field)
                for field in (
                    "profile_revision",
                    "material_revisions",
                    "notice_revisions",
                    "attachment_revisions",
                )
            )
        except (KeyError, ValueError, TypeError):
            raise CompanyError("review_successor_binding_mismatch", 409) from None
        if (
            newer
            or not persisted
            or persisted[0]["owner_id"] != case["owner_id"]
            or persisted[0]["inputs_manifest_sha256"] != manifest.closure_sha256
            or persisted[0]["status"] != "queued"
            or persisted[0]["quarantined"]
            or persisted[0]["purge_state"] != "live"
            or run_id == parent["run_id"]
            or manifest.project_id != old_manifest.project_id
            or manifest.closure_sha256 == old_manifest.closure_sha256
            or not changed_refs
        ):
            raise CompanyError("review_successor_binding_mismatch", 409)
        # The JobsRepository prepare callback has validated the fixed input bytes
        # before its transaction. Recheck every new private input under that lock.
        for ref in manifest.company_revisions:
            self.sources.company.auth.require(
                ToolContext(context.owner_id), ref.object_id, "read", conn
            )
        self._sources(conn, case_id, manifest)
        event_id = self._append(
            conn,
            context,
            case,
            kind="successor_linked",
            request=request,
            body={
                "reason": cmd.reason,
                "run_id": run_id,
                "trigger_event_id": cmd.trigger_event_id,
                "inputs_manifest_sha256": manifest.closure_sha256,
            },
            outbox=(parent["run_id"], "superseded"),
        )
        conn.execute(
            text(
                "INSERT INTO review_successors VALUES "
                "(:run,:case,:parent,:trigger,:event,:sha,:now)"
            ),
            {
                "run": run_id,
                "case": case_id,
                "parent": cmd.revision_id,
                "trigger": cmd.trigger_event_id,
                "event": event_id,
                "sha": manifest.closure_sha256,
                "now": self.clock(),
            },
        )
        # These notifications cannot apply after this revision is superseded.
        # Freeze them with the link so a crash cannot leave stale work pending;
        # keep audit bodies and the new superseded notification intact.
        conn.execute(
            text(
                "UPDATE review_outbox SET frozen=1 WHERE run_id=:run "
                "AND kind IN ('decision','closed') AND consumed_at IS NULL "
                "AND event_id IN (SELECT event_id FROM review_events WHERE case_id=:case)"
            ),
            {"run": parent["run_id"], "case": case_id},
        )
        self._advance(conn, case, status="recomputing")
        self.auth.require_case(context, case_id, action="manage", conn=conn)
        return {"case_id": case_id, "run_id": run_id, "replayed": False}

    def attach_revision(
        self, context, case_id, *, run_id, expected_version, idempotency_key, reason
    ):
        cmd = _parse(
            AttachRevision,
            {
                "run_id": run_id,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
                "reason": reason,
            },
        )
        request = {"kind": "revision_attached", **cmd.model_dump(mode="json")}
        self.auth.require_case(context, case_id, action="manage")
        _, preview = self._job_result(cmd.run_id, context.owner_id, None, ready=False)
        with self._checked_transaction(context, [preview["manifest"]]) as checked:
            conn, _ = checked
            case = self.auth.require_case(context, case_id, action="manage", conn=conn)
            if not self._replay(context, case, cmd.idempotency_key, _sha(_json(request)), conn):
                self._cas(case, cmd.expected_version)
                links = rows(
                    self.db,
                    "SELECT * FROM review_successors WHERE run_id=:run AND case_id=:case "
                    "AND parent_revision_id=:parent",
                    {"run": cmd.run_id, "case": case_id, "parent": case["head_revision_id"]},
                    conn,
                )
                if not links or case["status"] != "recomputing":
                    raise CompanyError("review_successor_not_linked", 409)
                job, record = self._job_result(cmd.run_id, case["owner_id"], conn)
                if job["inputs_manifest_sha256"] != links[0]["inputs_manifest_sha256"]:
                    raise CompanyError("review_successor_binding_mismatch", 409)
                revision_id = self._insert_revision(
                    conn,
                    case_id,
                    job,
                    parent=case["head_revision_id"],
                    trigger=links[0]["trigger_event_id"],
                )
                self._append(
                    conn,
                    context,
                    case,
                    kind="revision_attached",
                    request=request,
                    revision_id=revision_id,
                    body={"reason": cmd.reason, "run_id": cmd.run_id},
                )
                self._advance(conn, case, status="open", head=revision_id)
                self.auth.require_case(context, case_id, action="manage", conn=conn)
        return self.get(context, case_id)

    def reauthorize(self, context, case_id, *, expected_version, idempotency_key, reason):
        cmd = _parse(
            CaseCommand,
            {
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
                "reason": reason,
            },
        )
        request = {"kind": "reauthorized", **cmd.model_dump(mode="json")}
        preview_case = self.auth.require_case(context, case_id, action="manage", quarantine=True)
        previews = [
            self._job_result(row["run_id"], preview_case["owner_id"], None, ready=False)[1]
            for row in rows(
                self.db, "SELECT run_id FROM review_revisions WHERE case_id=:id", {"id": case_id}
            )
        ]
        previews.append(self._result(preview_case["candidate_batch_id"], preview_case["owner_id"]))
        with self._checked_transaction(
            context, [record["manifest"] for record in previews]
        ) as checked:
            conn, _ = checked
            case = self.auth.require_case(
                context, case_id, action="manage", conn=conn, quarantine=True
            )
            if not self._replay(context, case, cmd.idempotency_key, _sha(_json(request)), conn):
                self._cas(case, cmd.expected_version)
                if not case["quarantined"]:
                    raise CompanyError("review_case_not_quarantined", 409)
                for revision in rows(
                    self.db,
                    "SELECT run_id FROM review_revisions WHERE case_id=:id",
                    {"id": case_id},
                    conn,
                ):
                    job, record = self._job_result(
                        revision["run_id"], case["owner_id"], conn, ready=False
                    )
                self._result(case["candidate_batch_id"], case["owner_id"], conn)
                self._append(
                    conn,
                    context,
                    case,
                    kind="reauthorized",
                    request=request,
                    body={"reason": cmd.reason},
                )
                conn.execute(
                    text("DELETE FROM review_assignments WHERE case_id=:id"), {"id": case_id}
                )
                conn.execute(
                    text("UPDATE review_case_state SET quarantined=0 WHERE case_id=:id"),
                    {"id": case_id},
                )
                pending = rows(
                    self.db,
                    "SELECT 1 FROM review_successors s WHERE s.case_id=:case "
                    "AND s.parent_revision_id=:head AND NOT EXISTS "
                    "(SELECT 1 FROM review_revisions r WHERE r.run_id=s.run_id)",
                    {"case": case_id, "head": case["head_revision_id"]},
                    conn,
                )
                self._advance(conn, case, status="recomputing" if pending else "open")
                self.auth.require_case(context, case_id, action="manage", conn=conn)
        return self.get(context, case_id)

    def export(self, context, case_id):
        case = self.get(context, case_id, candidate_limit=None)
        # Entire fixed candidate batch and all historical revisions are included.
        # Any missing body or revoked historical source rejects the whole export.
        return {
            "format_version": "bidops_review_v1",
            "exported_at": datetime.fromtimestamp(self.clock(), UTC).isoformat(),
            "case": case,
            "agent_judgments_preserved": True,
            "current": case["source_state"]["current"],
            "automatic_bidding": False,
        }

    def pending_events(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise CompanyError("review_invalid_page", 422)
        return rows(
            self.db,
            "SELECT o.* FROM review_outbox o JOIN review_events e ON e.event_id=o.event_id "
            "JOIN review_case_state s ON s.case_id=e.case_id WHERE o.frozen=0 AND "
            "o.consumed_at IS NULL "
            "AND s.quarantined=0 AND s.purge_state='live' AND o.review_epoch=s.review_epoch "
            "ORDER BY o.attempts,e.occurred_at,e.event_id LIMIT :limit",
            {"limit": limit},
        )

    def _worker_event(self, event_id, run_id, review_epoch, conn):
        found = rows(
            self.db,
            "SELECT e.*,o.run_id AS outbox_run_id,o.kind AS "
            "outbox_kind,o.review_epoch AS outbox_epoch,"
            "o.frozen,o.consumed_at FROM review_events e JOIN review_outbox o ON "
            "o.event_id=e.event_id "
            "WHERE e.event_id=:id",
            {"id": event_id},
            conn,
        )
        if not found:
            raise CompanyError("review_event_not_found")
        meta = found[0]
        state = rows(
            self.db,
            "SELECT * FROM review_case_state WHERE case_id=:id",
            {"id": meta["case_id"]},
            conn,
        )[0]
        if (
            state["quarantined"]
            or state["purge_state"] != "live"
            or meta["frozen"]
            or meta["outbox_run_id"] != run_id
            or meta["review_epoch"] != review_epoch
            or meta["outbox_epoch"] != review_epoch
            or state["review_epoch"] != review_epoch
            or (
                state["head_revision_id"] != meta["revision_id"]
                and meta["outbox_kind"] != "superseded"
            )
        ):
            raise CompanyError("review_event_not_current", 409)
        # The historical login proves submission, not indefinite browser presence.
        # Native consumption uses the actor's CURRENT bounded subject and grants.
        actor = ToolContext(meta["actor_id"])
        subject = self.sources.company.auth.require_subject(actor, conn)
        permissions = set().union(
            *(ROLE_PERMISSIONS.get(role, set()) for role in json.loads(subject["roles_json"]))
        )
        owner = rows(
            self.db,
            "SELECT owner_id FROM review_cases WHERE case_id=:id",
            {"id": meta["case_id"]},
            conn,
        )[0]["owner_id"]
        if meta["outbox_kind"] == "decision":
            assigned = rows(
                self.db,
                "SELECT 1 FROM review_assignments WHERE case_id=:case AND subject_id=:actor",
                {"case": meta["case_id"], "actor": meta["actor_id"]},
                conn,
            )
            if not assigned or "review:decide" not in permissions:
                raise CompanyError("review_decide_denied", 403)
        elif meta["actor_id"] != owner or "agent:use" not in permissions:
            raise CompanyError("review_manage_denied", 403)
        for dep in self.auth.source_refs(meta["case_id"], conn):
            self.sources.company.auth.require(actor, dep["object_id"], "read", conn)
        return {
            **self._event(meta, conn),
            "consumed": meta["consumed_at"] is not None,
            "outbox_kind": meta["outbox_kind"],
            "run_id": run_id,
        }

    def load_event_for_worker(self, event_id, *, run_id, review_epoch):
        with transaction(self.db) as conn:
            return self._worker_event(event_id, run_id, review_epoch, conn)

    def mark_delivered(self, event_id, *, review_epoch, error_code=None):
        with transaction(self.db) as conn:
            conn.execute(
                text(
                    "UPDATE review_outbox SET attempts=attempts+1,last_error_code=:error,"
                    "delivered_at=CASE WHEN :error IS NULL THEN :now ELSE delivered_at END "
                    "WHERE event_id=:id AND review_epoch=:epoch AND frozen=0"
                ),
                {"id": event_id, "epoch": review_epoch, "error": error_code, "now": self.clock()},
            )

    def consume_event(self, event_id, *, run_id, review_epoch, apply=None):
        meta = rows(
            self.db,
            "SELECT e.actor_id,o.kind,r.result_id,c.owner_id FROM review_events e "
            "JOIN review_outbox o ON o.event_id=e.event_id "
            "JOIN review_revisions r ON r.revision_id=e.revision_id AND r.case_id=e.case_id "
            "JOIN review_cases c ON c.case_id=e.case_id WHERE e.event_id=:id",
            {"id": event_id},
        )
        if not meta:
            raise CompanyError("review_event_not_found")
        event_meta = meta[0]
        manifests = []
        if event_meta["kind"] == "closed":
            manifests.append(
                self._result(event_meta["result_id"], event_meta["owner_id"])["manifest"]
            )
        # A native guard runs before this transaction. Closing also needs a
        # current-source check whose data_version remains stable until the
        # shared review/job write lock is held; ordinary/superseded events only
        # need their fixed inputs, current actor, epoch and source ACLs.
        with self._checked_transaction(
            ToolContext(event_meta["actor_id"]), manifests, current=True
        ) as (conn, states):
            if any(not state["current"] for state in states):
                raise CompanyError("review_sources_changed", 409)
            event = self._worker_event(event_id, run_id, review_epoch, conn)
            if apply is not None:
                apply(conn, event)
            conn.execute(
                text(
                    "UPDATE review_outbox SET consumed_at=COALESCE(consumed_at,:now) "
                    "WHERE event_id=:id AND review_epoch=:epoch AND frozen=0"
                ),
                {"id": event_id, "epoch": review_epoch, "now": self.clock()},
            )
            return event
