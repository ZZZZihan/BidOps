"""Current database decisions, including tool contexts with no cached privileges."""

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import text

from bidops.company.schemas import Action
from bidops.enterprise.auth import ROLE_PERMISSIONS, audit

ACTIONS = ("read", "write", "grant", "delete")


class CompanyError(HTTPException):
    def __init__(self, code: str, status: int = 404):
        super().__init__(status_code=status, detail=code)
        self.code = code


@dataclass(frozen=True)
class ToolContext:
    # Construct this from authenticated server state, never from model arguments.
    owner_id: str


def rows(db, sql, params=None, conn=None):
    if conn is None:
        return db.rows(sql, params)
    return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]


@contextmanager
def transaction(db):
    with db.engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


class CompanyAuthorization:
    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock

    def require_subject(self, context: ToolContext, conn=None) -> dict:
        found = rows(
            self.db,
            "SELECT * FROM enterprise_subjects WHERE owner_id=:owner "
            "AND status='active' AND fresh_until>:now",
            {"owner": context.owner_id, "now": self.clock()},
            conn,
        )
        if not found:
            raise CompanyError("current_identity_required", 401)
        return found[0]

    def require_creator(self, context, conn=None):
        subject = self.require_subject(context, conn)
        if "agent:use" not in self.permissions(subject):
            raise CompanyError("company_create_denied", 403)

    @staticmethod
    def permissions(subject):
        return set().union(
            *(ROLE_PERMISSIONS.get(role, set()) for role in json.loads(subject["roles_json"]))
        )

    def require(self, context: ToolContext, object_id: str, action: Action, conn=None) -> dict:
        subject = self.require_subject(context, conn)
        permission = "catalog:read" if action == "read" else "agent:use"
        if permission not in self.permissions(subject):
            raise CompanyError("company_object_not_found")
        found = rows(
            self.db,
            "SELECT o.* FROM company_objects o JOIN company_grants g "
            "ON g.object_id=o.object_id AND g.subject_id=:owner AND g.action=:action "
            "WHERE o.object_id=:id AND o.deleted_at IS NULL AND o.quarantined=0 "
            "AND o.retention_until>:now AND o.purge_state='live'",
            {"id": object_id, "owner": context.owner_id, "action": action, "now": self.clock()},
            conn,
        )
        if not found:
            raise CompanyError("company_object_not_found")
        return found[0]

    def require_many(self, context, object_ids):
        """One current authorization snapshot for a derived result's full source set."""
        with transaction(self.db) as conn:
            self.require_subject(context, conn)
            for object_id in set(object_ids):
                self.require(context, object_id, "read", conn)

    def grant(self, context, object_id, subject_id, actions):
        if not actions or any(action not in ACTIONS for action in actions):
            raise CompanyError("invalid_grant", 422)
        with transaction(self.db) as conn:
            self.require(context, object_id, "grant", conn)
            self.require_subject(ToolContext(subject_id), conn)
            for action in actions:
                conn.execute(
                    text("INSERT OR IGNORE INTO company_grants VALUES (:id,:subject,:action)"),
                    {"id": object_id, "subject": subject_id, "action": action},
                )
        audit(
            self.db,
            "company_granted",
            context.owner_id,
            object_id=object_id,
            subject_id=subject_id,
            actions=actions,
        )

    def revoke(self, context, object_id, subject_id):
        with transaction(self.db) as conn:
            self.require(context, object_id, "grant", conn)
            conn.execute(
                text("DELETE FROM company_grants WHERE object_id=:id AND subject_id=:subject"),
                {"id": object_id, "subject": subject_id},
            )
        audit(
            self.db, "company_revoked", context.owner_id, object_id=object_id, subject_id=subject_id
        )

    def reauthorize(self, context, object_id, subject_id, actions):
        """An explicit new per-object grant; never restores the backed-up ACL."""
        if not actions or any(action not in ACTIONS for action in actions):
            raise CompanyError("invalid_grant", 422)
        with transaction(self.db) as conn:
            actor = self.require_subject(context, conn)
            if "admin" not in json.loads(actor["roles_json"]):
                raise CompanyError("recovery_admin_required", 403)
            self.require_subject(ToolContext(subject_id), conn)
            found = rows(
                self.db,
                "SELECT * FROM company_objects WHERE object_id=:id "
                "AND quarantined=1 AND deleted_at IS NULL AND retention_until>:now "
                "AND purge_state='live'",
                {"id": object_id, "now": self.clock()},
                conn,
            )
            if not found:
                raise CompanyError("quarantined_object_not_found")
            conn.execute(text("DELETE FROM company_grants WHERE object_id=:id"), {"id": object_id})
            for action in actions:
                conn.execute(
                    text("INSERT INTO company_grants VALUES (:id,:subject,:action)"),
                    {"id": object_id, "subject": subject_id, "action": action},
                )
            conn.execute(
                text("UPDATE company_objects SET quarantined=0 WHERE object_id=:id"),
                {"id": object_id},
            )
        audit(
            self.db,
            "company_reauthorized",
            context.owner_id,
            object_id=object_id,
            subject_id=subject_id,
            actions=actions,
        )

    def visible(self, context, kind, *, limit=50, offset=0):
        subject = self.require_subject(context)
        if "catalog:read" not in self.permissions(subject):
            raise CompanyError("company_read_denied", 403)
        if kind not in {"profile", "material"} or not 1 <= limit <= 100 or offset < 0:
            raise CompanyError("invalid_pagination", 422)
        predicate = (
            "FROM company_objects o JOIN company_grants g ON g.object_id=o.object_id "
            "AND g.subject_id=:owner AND g.action='read' WHERE o.kind=:kind "
            "AND o.deleted_at IS NULL AND o.quarantined=0 AND o.retention_until>:now "
            "AND o.purge_state='live'"
        )
        params = {
            "owner": context.owner_id,
            "kind": kind,
            "now": self.clock(),
            "limit": limit,
            "offset": offset,
        }
        total = self.db.rows("SELECT COUNT(*) AS n " + predicate, params)[0]["n"]
        items = self.db.rows(
            "SELECT o.* "
            + predicate
            + " ORDER BY o.created_at,o.object_id LIMIT :limit OFFSET :offset",
            params,
        )
        # Check again before releasing the result, including account freshness.
        self.require_many(context, [item["object_id"] for item in items])
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        }
