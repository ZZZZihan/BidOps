import hashlib
import json
import uuid

from sqlalchemy import text

from bidops.company.authorization import ACTIONS, CompanyError, rows, transaction
from bidops.company.schemas import ProfilePayload
from bidops.enterprise.auth import audit


class CompanyRepository:
    def __init__(self, db, auth, materials):
        self.db, self.auth, self.materials = db, auth, materials

    def verify_evidence(self, context, payload):
        for fact in payload.facts:
            for ref in fact.evidence:
                row, _, content = self.materials.read(
                    context, ref.version.object_id, ref.version.version_id
                )
                if (
                    ref.locator != "normalized_text_v2"
                    or ref.page is not None
                    or ref.evidence_id != f"{row['version_id']}:{ref.start}:{ref.end}"
                    or ref.version.sha256 != row["sha256"]
                    or ref.text_sha256 != row["text_sha256"]
                    or ref.end > len(content)
                    or content[ref.start : ref.end] != ref.quote
                ):
                    raise CompanyError("company_evidence_mismatch", 422)

    def put(self, context, payload, *, object_id=None, expected_version=None, retention_until=None):
        payload = ProfilePayload.model_validate(payload)
        self.verify_evidence(context, payload)
        now = self.auth.clock()
        until = retention_until.timestamp() if retention_until is not None else now + 30 * 86400
        if until <= now:
            raise CompanyError("retention_must_be_future", 422)
        canonical = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        version, oid = uuid.uuid4().hex, object_id or uuid.uuid4().hex
        params = {
            "id": oid,
            "version": version,
            "sha": hashlib.sha256(canonical.encode()).hexdigest(),
            "payload": canonical,
            "now": now,
            "owner": context.owner_id,
            "until": until,
        }
        with transaction(self.db) as conn:
            if object_id is None:
                self.auth.require_creator(context, conn)
                conn.execute(
                    text(
                        "INSERT INTO company_objects(object_id,kind,owner_id,head_version,"
                        "retention_until,created_at) "
                        "VALUES (:id,'profile',:owner,:version,:until,:now)"
                    ),
                    params,
                )
                for action in ACTIONS:
                    conn.execute(
                        text("INSERT INTO company_grants VALUES (:id,:owner,:action)"),
                        {**params, "action": action},
                    )
            else:
                obj = self.auth.require(context, oid, "write", conn)
                if obj["kind"] != "profile":
                    raise CompanyError("profile_not_found")
                if expected_version != obj["head_version"]:
                    raise CompanyError("version_conflict", 409)
                if retention_until is None:
                    params["until"] = obj["retention_until"]
                conn.execute(
                    text(
                        "UPDATE company_objects SET head_version=:version,"
                        "retention_until=:until WHERE object_id=:id"
                    ),
                    params,
                )
            conn.execute(
                text(
                    "INSERT INTO company_profile_revisions VALUES "
                    "(:version,:id,:sha,:payload,:now,:owner)"
                ),
                params,
            )
            for fact in payload.facts:
                for ref in fact.evidence:
                    self.auth.require(context, ref.version.object_id, "read", conn)
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO company_profile_sources VALUES "
                            "(:profile,:id,:version)"
                        ),
                        {
                            "profile": version,
                            "id": ref.version.object_id,
                            "version": ref.version.version_id,
                        },
                    )
        audit(
            self.db, "company_profile_written", context.owner_id, object_id=oid, version_id=version
        )
        return {"object_id": oid, "version_id": version, "sha256": params["sha"]}

    def get(self, context, object_id, version_id=None):
        obj = self.auth.require(context, object_id, "read")
        if obj["kind"] != "profile":
            raise CompanyError("profile_not_found")
        found = rows(
            self.db,
            "SELECT * FROM company_profile_revisions WHERE object_id=:id AND version_id=:version",
            {"id": object_id, "version": version_id or obj["head_version"]},
        )
        if not found:
            raise CompanyError("profile_revision_not_found")
        row = found[0]
        if hashlib.sha256(row["payload_json"].encode()).hexdigest() != row["sha256"]:
            raise CompanyError("profile_integrity_failed", 409)
        payload = ProfilePayload.model_validate_json(row["payload_json"])
        self.verify_evidence(context, payload)
        self.auth.require_many(
            context,
            [object_id]
            + [ref.version.object_id for fact in payload.facts for ref in fact.evidence],
        )
        obj = self.auth.require(context, object_id, "read")
        return {
            "object_id": object_id,
            "version_id": row["version_id"],
            "sha256": row["sha256"],
            "payload": payload.model_dump(mode="json"),
            "created_at": row["created_at"],
            # Retention is the object's current policy, also for historical payloads.
            "retention_until": obj["retention_until"],
        }

    def qualification_at(self, context, object_id, version_id, fact_id, required_at):
        if required_at.tzinfo is None:
            raise CompanyError("qualification_time_requires_timezone", 422)
        try:
            profile = self.get(context, object_id, version_id)
        except CompanyError as error:
            if error.code not in {"material_integrity_failed", "material_source_unavailable"}:
                raise
            return {
                "fact_id": fact_id,
                "status": "information_insufficient",
                "reasons": [error.code],
                "required_at": required_at.isoformat(),
                "profile_version": version_id,
            }
        fact = next(
            (
                item
                for item in ProfilePayload(**profile["payload"]).facts
                if item.fact_id == fact_id
            ),
            None,
        )
        if fact is None:
            raise CompanyError("company_fact_not_found")
        status, reasons = "information_insufficient", []
        if fact.status == "conflicting":
            status, reasons = "evidence_conflict", [fact.reason]
        elif fact.status != "document_supported":
            reasons = ["document_support_missing"]
        elif any(ref.validity.covers(required_at, required_at) for ref in fact.evidence):
            status = "satisfied"
        elif all(ref.validity.kind == "interval" for ref in fact.evidence):
            status, reasons = "not_satisfied", ["outside_document_validity"]
        else:
            reasons = ["document_validity_unknown"]
        return {
            "fact_id": fact_id,
            "status": status,
            "reasons": reasons,
            "required_at": required_at.isoformat(),
            "profile_version": profile["version_id"],
        }

    def delete(self, context, object_id):
        with transaction(self.db) as conn:
            self.auth.require(context, object_id, "delete", conn)
            conn.execute(
                text(
                    "UPDATE company_objects SET deleted_at=:now,purge_state='pending' "
                    "WHERE object_id=:id"
                ),
                {"id": object_id, "now": self.auth.clock()},
            )
            conn.execute(text("DELETE FROM company_grants WHERE object_id=:id"), {"id": object_id})
        audit(self.db, "company_deleted", context.owner_id, object_id=object_id)
        return {"deleted": True, "purge_state": "pending"}
