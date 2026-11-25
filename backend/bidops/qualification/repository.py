"""Immutable business history, protected by owner AND current full-source access.

This stores no work queue or execution status. Private bodies and raw outputs live
inside the backed-up SQLite database and are erased together on source expiry.
"""

import hashlib
import json
import re
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from bidops.company.authorization import CompanyError, rows, transaction
from bidops.qualification.candidates import validate_candidate_batch
from bidops.qualification.sources import ScreeningInputManifest, canonical_json

RESULT_MAX_BYTES = 10_000_000
CANDIDATE_BATCH_MAX_BYTES = 50_000_000


def prune_results(db, now):
    """Erase derived private bodies even when a source-file erasure must retry."""
    with transaction(db) as conn:
        expired = rows(
            db,
            "SELECT DISTINCT s.result_id FROM qualification_result_sources s "
            "LEFT JOIN company_objects o ON o.object_id=s.object_id "
            "JOIN qualification_result_lifecycle l ON l.result_id=s.result_id "
            "WHERE l.purge_state='live' AND (o.object_id IS NULL OR o.deleted_at IS NOT NULL "
            "OR o.retention_until<=:now OR o.purge_state!='live')",
            {"now": now},
            conn,
        )
        for row in expired:
            conn.execute(
                text("DELETE FROM qualification_result_bodies WHERE result_id=:id"),
                {"id": row["result_id"]},
            )
            conn.execute(
                text(
                    "UPDATE qualification_result_lifecycle SET purge_state='purged',"
                    "purged_at=:now WHERE result_id=:id"
                ),
                {"id": row["result_id"], "now": now},
            )
    return {"status": "PASS", "purged_ids": [r["result_id"] for r in expired], "failure_ids": []}


class QualificationRepository:
    def __init__(self, db, source_adapter):
        self.db, self.sources = db, source_adapter
        self.auth = source_adapter.company.auth

    def _authorize(self, context, result_id, *, quarantine=False, conn=None):
        self.auth.require_subject(context, conn)
        found = rows(
            self.db,
            "SELECT r.*,l.quarantined,l.purge_state FROM qualification_results r "
            "JOIN qualification_result_lifecycle l ON l.result_id=r.result_id "
            "WHERE r.result_id=:id AND r.owner_id=:owner AND l.purge_state='live'",
            {"id": result_id, "owner": context.owner_id},
            conn,
        )
        if not found or (found[0]["quarantined"] and not quarantine):
            raise CompanyError("screening_result_not_found")
        deps = rows(
            self.db,
            "SELECT object_id FROM qualification_result_sources WHERE result_id=:id",
            {"id": result_id},
            conn,
        )
        for dep in deps:
            self.auth.require(context, dep["object_id"], "read", conn)
        return found[0]

    def _evidence(self, context, manifest, value):
        if isinstance(value, dict):
            if "evidence_id" in value and "version" in value:
                self.sources.resolve_evidence(context, manifest, value)
            else:
                for child in value.values():
                    self._evidence(context, manifest, child)
        elif isinstance(value, list):
            for child in value:
                self._evidence(context, manifest, child)

    def save(
        self,
        context,
        manifest,
        result,
        *,
        raw_model_outputs=(),
        provenance=None,
        result_id=None,
        authorize_extra=None,
    ):
        return self._save(
            context,
            manifest,
            result,
            raw_model_outputs=raw_model_outputs,
            provenance=provenance,
            result_id=result_id,
            authorize_extra=authorize_extra,
            max_bytes=RESULT_MAX_BYTES,
        )

    def save_candidate_batch(self, context, manifest, result, *, provenance=None, result_id=None):
        """Only complete deterministic batches receive the larger envelope limit.

        There is deliberately no raw-output parameter. Ordinary save callers,
        including legacy candidate fixtures, retain the original ten-MB bound.
        """
        manifest = ScreeningInputManifest.model_validate(manifest)
        self.sources.recheck(context, manifest)
        validate_candidate_batch(manifest, result, self.sources.notices)
        # Only the deterministic public candidate inventory receives extra room;
        # private inputs/provenance cannot use this channel as a large raw blob.
        try:
            metadata = canonical_json(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "provenance": provenance or {},
                    "raw_model_outputs": [],
                }
            )
        except (TypeError, ValueError):
            raise CompanyError("screening_result_not_json", 422) from None
        if len(metadata.encode()) > RESULT_MAX_BYTES:
            raise CompanyError("screening_result_too_large", 413)
        return self._save(
            context,
            manifest,
            result,
            provenance=provenance,
            result_id=result_id,
            max_bytes=CANDIDATE_BATCH_MAX_BYTES,
        )

    def _save(
        self,
        context,
        manifest,
        result,
        *,
        raw_model_outputs=(),
        provenance=None,
        result_id=None,
        authorize_extra=None,
        max_bytes,
    ):
        manifest = ScreeningInputManifest.model_validate(manifest)
        if not isinstance(result, dict) or not isinstance(result.get("kind"), str):
            raise CompanyError("screening_result_kind_required", 422)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", result["kind"]):
            raise CompanyError("screening_result_kind_invalid", 422)
        self.sources.recheck(context, manifest)
        self._evidence(context, manifest, result)
        body = {
            "manifest": manifest.model_dump(mode="json"),
            "result": result,
            "provenance": provenance or {},
            "raw_model_outputs": list(raw_model_outputs),
        }
        try:
            encoded = canonical_json(body)
        except (TypeError, ValueError):
            raise CompanyError("screening_result_not_json", 422) from None
        if len(encoded.encode()) > max_bytes:
            raise CompanyError("screening_result_too_large", 413)
        result_id = result_id or uuid.uuid4().hex
        if not isinstance(result_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", result_id):
            raise CompanyError("screening_result_id_invalid", 422)
        now = self.auth.clock()
        try:
            with transaction(self.db) as conn:
                self.auth.require_creator(context, conn)
                for ref in manifest.company_revisions:
                    self.auth.require(context, ref.object_id, "read", conn)
                if authorize_extra:
                    authorize_extra(conn)
                conn.execute(
                    text("INSERT INTO qualification_results VALUES (:id,:owner,:kind,:sha,:now)"),
                    {
                        "id": result_id,
                        "owner": context.owner_id,
                        "kind": result["kind"],
                        "sha": hashlib.sha256(encoded.encode()).hexdigest(),
                        "now": now,
                    },
                )
                conn.execute(
                    text("INSERT INTO qualification_result_bodies VALUES (:id,:body)"),
                    {"id": result_id, "body": encoded},
                )
                conn.execute(
                    text("INSERT INTO qualification_result_lifecycle(result_id) VALUES (:id)"),
                    {"id": result_id},
                )
                for ref in manifest.company_revisions:
                    conn.execute(
                        text(
                            "INSERT INTO qualification_result_sources VALUES (:id,:oid,:vid,:sha)"
                        ),
                        {
                            "id": result_id,
                            "oid": ref.object_id,
                            "vid": ref.version_id,
                            "sha": ref.sha256,
                        },
                    )
        except IntegrityError:
            raise CompanyError("screening_result_already_exists", 409) from None
        return self.get(context, result_id)

    def get(self, context, result_id):
        record = self._authorize(context, result_id)
        found = rows(
            self.db,
            "SELECT body_json FROM qualification_result_bodies WHERE result_id=:id",
            {"id": result_id},
        )
        if not found:
            raise CompanyError("screening_result_not_found")
        encoded = found[0]["body_json"]
        if hashlib.sha256(encoded.encode()).hexdigest() != record["body_sha256"]:
            raise CompanyError("screening_result_integrity_failed", 409)
        body = json.loads(encoded)
        self.sources.recheck(context, body["manifest"], for_execution=False)
        source_state = self.sources.changes(context, body["manifest"])
        # Atomic final authorization for the owner and all inputs before release.
        with transaction(self.db) as conn:
            self._authorize(context, result_id, conn=conn)
        return {
            "result_id": result_id,
            "run_id": result_id,
            "created_at": record["created_at"],
            "source_state": source_state,
            **body,
        }

    def list(self, context, *, offset=0, limit=20):
        self.auth.require_subject(context)
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
            raise CompanyError("screening_invalid_pagination", 422)
        allowed = []
        for row in rows(
            self.db,
            "SELECT result_id FROM qualification_results WHERE owner_id=:owner "
            "ORDER BY created_at DESC,result_id",
            {"owner": context.owner_id},
        ):
            try:
                record = self.get(context, row["result_id"])
            except CompanyError as error:
                if error.status_code in {403, 404, 409}:
                    continue
                raise
            allowed.append(
                {
                    "result_id": record["result_id"],
                    "run_id": record["run_id"],
                    "kind": record["result"]["kind"],
                    "created_at": record["created_at"],
                }
            )
        # The listing itself discloses private result existence; recheck the released page.
        page = allowed[offset : offset + limit]
        with transaction(self.db) as conn:
            for record in allowed:
                self._authorize(context, record["result_id"], conn=conn)
        return {"items": page, "total": len(allowed), "offset": offset, "limit": limit}

    def reauthorize(self, context, result_id):
        """Explicit owner action after every source has been reauthorized after restore.

        This restores result visibility only. It grants no source access and records
        no semantic or human approval.
        """
        record = self._authorize(context, result_id, quarantine=True)
        if not record["quarantined"]:
            return {"result_id": result_id, "quarantined": False}
        bodies = rows(
            self.db,
            "SELECT body_json FROM qualification_result_bodies WHERE result_id=:id",
            {"id": result_id},
        )
        if not bodies:
            raise CompanyError("screening_result_not_found")
        body = bodies[0]["body_json"]
        if hashlib.sha256(body.encode()).hexdigest() != record["body_sha256"]:
            raise CompanyError("screening_result_integrity_failed", 409)
        self.sources.recheck(context, json.loads(body)["manifest"], for_execution=False)
        with transaction(self.db) as conn:
            self._authorize(context, result_id, quarantine=True, conn=conn)
            conn.execute(
                text("UPDATE qualification_result_lifecycle SET quarantined=0 WHERE result_id=:id"),
                {"id": result_id},
            )
        return {"result_id": result_id, "quarantined": False}

    def prune(self):
        return prune_results(self.db, self.auth.clock())


# Short public name for integration callers.
Repository = QualificationRepository
