"""Read-only notice version/evidence API; the same RBAC applies to both aliases."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime

from bidops.screening.schemas import EvidenceRef


def notice_router() -> APIRouter:
    router = APIRouter()

    def repository(request: Request):
        request.app.state.identity.require(request, "catalog:read")
        return request.app.state.store.notice_data

    def checked(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except (ValueError, KeyError, OSError):
            # Never echo raw content, paths or user-controlled validation input.
            raise HTTPException(422, "notice_version_or_evidence_unavailable") from None

    def page(items, limit, offset):
        selected = items[offset : offset + limit]
        return {
            "items": selected,
            "total": len(items),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(selected) < len(items),
            "truncated": len(selected) < len(items),
        }

    @router.get("/notices/{notice_id}/versions")
    def versions(
        notice_id: str,
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        repo = repository(request)
        return page(checked(repo.versions_for_notice, notice_id), limit, offset)

    @router.get("/notice-versions/{version_id}")
    def version(version_id: str, request: Request):
        repo = repository(request)
        return checked(repo.get_version, version_id)

    @router.get("/projects/{project_id}/versions")
    def project_versions(
        project_id: str,
        request: Request,
        as_of: AwareDatetime | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        repo = repository(request)
        return page(
            checked(repo.project_versions, project_id, as_of or datetime.now(UTC)), limit, offset
        )

    @router.get("/projects/{project_id}/status")
    def status(project_id: str, request: Request, as_of: AwareDatetime | None = None):
        repo = repository(request)
        return checked(repo.project_status, project_id, as_of or datetime.now(UTC))

    @router.get("/notice-versions/{version_id}/evidence")
    def evidence(
        version_id: str, request: Request, start: int = Query(ge=0), end: int = Query(gt=0)
    ):
        repo = repository(request)
        return checked(repo.make_evidence, version_id, start, end)

    @router.post("/notice-evidence/resolve")
    def resolve(evidence: EvidenceRef, request: Request):
        repo = repository(request)
        return checked(repo.resolve_evidence, evidence)

    @router.get("/notice-versions/{version_id}/attachments")
    def attachments(version_id: str, request: Request, as_of: AwareDatetime | None = None):
        repo = repository(request)
        checked(repo.get_version, version_id)
        items = checked(repo.list_attachments, version_id, as_of=as_of)
        return {
            "items": items,
            "total": len(items),
            "missing": checked(repo.missing_attachments, version_id, as_of=as_of),
        }

    @router.get("/notice-attachments/{record_id}/evidence")
    def attachment_evidence(
        record_id: str, request: Request, start: int = Query(ge=0), end: int = Query(gt=0)
    ):
        repo = repository(request)
        return checked(repo.make_evidence, record_id, start, end, object_kind="attachment")

    return router
