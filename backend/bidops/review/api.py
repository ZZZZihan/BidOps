"""Session-authenticated, CSRF-protected human review commands."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from bidops.qualification.api import ChecklistRequest
from bidops.review.schemas import (
    AssignmentCommand,
    AttachRevision,
    CaseCommand,
    CreateCase,
    ItemDecision,
    SuccessorCommand,
)


class SuccessorPayload(SuccessorCommand):
    request: ChecklistRequest


def review_router():
    router = APIRouter(prefix="/api/reviews", tags=["reviews"])

    def current(request, permission="catalog:read", *, allow_disabled=False):
        identity = request.app.state.identity
        if not identity.settings.enabled:
            raise HTTPException(404)
        identity.require(request, permission)
        service = request.app.state.review
        context = service.repository.authorization.from_request(request)
        if not allow_disabled:
            service.enabled()
        return service, context

    @router.get("/status")
    def status(request: Request):
        service, _ = current(request, allow_disabled=True)
        return service.status()

    @router.get("/cases")
    def history(
        request: Request,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
    ):
        service, context = current(request)
        return service.repository.list(context, offset=offset, limit=limit)

    @router.post("/cases", status_code=201)
    def create(payload: CreateCase, request: Request):
        service, context = current(request, "agent:use")
        return service.create(context, **payload.model_dump())

    @router.get("/cases/{case_id}")
    def read(case_id: str, request: Request):
        service, context = current(request)
        return service.get(context, case_id)

    @router.post("/cases/{case_id}/assignments")
    def assign(case_id: str, payload: AssignmentCommand, request: Request):
        service, context = current(request, "agent:use")
        return service.mutate("assign", context, case_id, **payload.model_dump())

    @router.post("/cases/{case_id}/decisions")
    def decide(case_id: str, payload: ItemDecision, request: Request):
        service, context = current(request, "review:decide")
        return service.mutate("decide", context, case_id, **payload.model_dump())

    @router.post("/cases/{case_id}/successors", status_code=202)
    def successor(case_id: str, payload: SuccessorPayload, request: Request):
        service, context = current(request, "agent:use")
        return service.successor(
            context,
            case_id,
            request=payload.request.model_dump(mode="json", exclude_unset=True),
            **payload.model_dump(exclude={"request"}),
        )

    @router.post("/cases/{case_id}/revisions")
    def attach(case_id: str, payload: AttachRevision, request: Request):
        service, context = current(request, "agent:use")
        return service.mutate("attach_revision", context, case_id, **payload.model_dump())

    @router.post("/cases/{case_id}/close")
    def close(case_id: str, payload: CaseCommand, request: Request):
        service, context = current(request, "agent:use")
        return service.mutate("close", context, case_id, **payload.model_dump())

    @router.post("/cases/{case_id}/reauthorize")
    def reauthorize(case_id: str, payload: CaseCommand, request: Request):
        service, context = current(request, "agent:use")
        return service.mutate("reauthorize", context, case_id, **payload.model_dump())

    @router.get("/cases/{case_id}/export")
    def export(case_id: str, request: Request):
        service, context = current(request)
        return Response(
            service.export(context, case_id),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="bidops-human-review.json"',
                "Cache-Control": "no-store",
            },
        )

    return router
