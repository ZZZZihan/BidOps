"""Human authority requires a current browser login and current object grants.

A ToolContext deliberately cannot satisfy this boundary. A hash identifies the
server login record; possessing an owner ID or declaring a human actor does not.
HTTP origin/CSRF checks remain mandatory in IdentityService.require at the route.
"""

import json
import re
from dataclasses import dataclass

from bidops.company.authorization import CompanyError, ToolContext, rows
from bidops.enterprise.auth import ROLE_PERMISSIONS, digest


@dataclass(frozen=True)
class HumanContext:
    owner_id: str
    token_hash: str


class ReviewAuthorization:
    def __init__(self, db, source_adapter):
        self.db, self.sources = db, source_adapter
        self.company = source_adapter.company.auth
        self.clock = self.company.clock

    def from_request(self, request):
        token = request.session.get("login")
        if not isinstance(token, str) or not token or len(token) > 100:
            raise CompanyError("review_human_session_required", 401)
        token_hash = digest(token)
        found = rows(
            self.db,
            "SELECT owner_id FROM enterprise_logins WHERE token_hash=:hash",
            {"hash": token_hash},
        )
        if not found:
            raise CompanyError("review_human_session_required", 401)
        context = HumanContext(found[0]["owner_id"], token_hash)
        self.require_human(context)
        return context

    def require_human(self, context, permission="catalog:read", conn=None):
        if (
            type(context) is not HumanContext
            or not isinstance(context.owner_id, str)
            or not isinstance(context.token_hash, str)
            or not re.fullmatch(r"[a-f0-9]{64}", context.token_hash)
        ):
            raise CompanyError("review_human_session_required", 401)
        found = rows(
            self.db,
            "SELECT l.owner_id,l.display_name,l.roles_json AS login_roles,s.roles_json "
            "FROM enterprise_logins l JOIN enterprise_login_identities i "
            "ON i.token_hash=l.token_hash JOIN enterprise_subjects s "
            "ON s.owner_id=l.owner_id AND s.issuer=i.issuer AND s.subject=i.subject "
            "WHERE l.token_hash=:hash AND l.owner_id=:owner AND l.expires_at>:now "
            "AND i.fresh_until>:now AND s.fresh_until>:now AND s.status='active'",
            {"hash": context.token_hash, "owner": context.owner_id, "now": self.clock()},
            conn,
        )
        if not found:
            raise CompanyError("review_human_session_required", 401)
        subject = found[0]
        try:
            roles = set(json.loads(subject["login_roles"])) & set(json.loads(subject["roles_json"]))
        except (ValueError, TypeError):
            raise CompanyError("review_human_session_required", 401) from None
        permissions = set().union(*(ROLE_PERMISSIONS.get(role, set()) for role in roles))
        if permission and permission not in permissions:
            raise CompanyError("review_permission_denied", 403)
        return {**subject, "roles": sorted(roles), "permissions": permissions}

    def require_sources(self, context, case_id, conn=None):
        self.require_human(context, conn=conn)
        for ref in self.source_refs(case_id, conn):
            self.company.require(ToolContext(context.owner_id), ref["object_id"], "read", conn)

    def source_refs(self, case_id, conn=None):
        """Exact immutable dependency union, also used by native event consumers."""
        actual = rows(
            self.db,
            "SELECT object_id,version_id,sha256 FROM review_sources WHERE case_id=:id",
            {"id": case_id},
            conn,
        )
        expected = rows(
            self.db,
            "SELECT s.object_id,s.version_id,s.sha256 FROM qualification_result_sources s "
            "JOIN review_cases c ON c.candidate_batch_id=s.result_id WHERE c.case_id=:id UNION "
            "SELECT s.object_id,s.version_id,s.sha256 FROM qualification_result_sources s "
            "JOIN review_revisions r ON r.result_id=s.result_id WHERE r.case_id=:id UNION "
            "SELECT s.object_id,s.version_id,s.sha256 FROM durable_job_sources s "
            "JOIN review_successors r ON r.run_id=s.run_id WHERE r.case_id=:id",
            {"id": case_id},
            conn,
        )

        def key(ref):
            return tuple(ref[k] for k in ("object_id", "version_id", "sha256"))

        if {key(ref) for ref in actual} != {key(ref) for ref in expected}:
            raise CompanyError("review_source_closure_mismatch", 409)
        return actual

    def require_case(self, context, case_id, *, action="read", conn=None, quarantine=False):
        identity = self.require_human(context, conn=conn)
        found = rows(
            self.db,
            "SELECT c.*,s.head_revision_id,s.review_version,s.status,s.review_epoch,"
            "s.quarantined,s.purge_state,s.purged_at,s.updated_at "
            "FROM review_cases c JOIN review_case_state s ON s.case_id=c.case_id "
            "WHERE c.case_id=:id",
            {"id": case_id},
            conn,
        )
        if not found:
            raise CompanyError("review_case_not_found")
        case = found[0]
        assigned = bool(
            rows(
                self.db,
                "SELECT 1 FROM review_assignments WHERE case_id=:id AND subject_id=:owner",
                {"id": case_id, "owner": context.owner_id},
                conn,
            )
        )
        if (
            case["purge_state"] != "live"
            or (case["quarantined"] and not quarantine)
            or (case["owner_id"] != context.owner_id and not assigned)
        ):
            raise CompanyError("review_case_not_found")
        if action == "manage" and (
            case["owner_id"] != context.owner_id or "agent:use" not in identity["permissions"]
        ):
            raise CompanyError("review_manage_denied", 403)
        if action == "decide" and (not assigned or "review:decide" not in identity["permissions"]):
            raise CompanyError("review_decide_denied", 403)
        self.require_sources(context, case_id, conn)
        return case
