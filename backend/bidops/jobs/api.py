from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from bidops.company.authorization import ToolContext
from bidops.jobs.schemas import JobModel
from bidops.qualification.api import ChecklistRequest


class VersionCommand(JobModel):
    expected_state_version: int = Field(ge=0, strict=True)


def jobs_router():
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])

    def current(request, permission="catalog:read", *, allow_disabled=False):
        identity = request.app.state.identity
        if not identity.settings.enabled:
            raise HTTPException(404)
        principal = identity.require(request, permission)
        if principal is None:
            raise HTTPException(401)
        context = ToolContext(principal.owner_id)
        request.app.state.company.auth.require_subject(context)
        service = request.app.state.jobs
        if service is None and not allow_disabled:
            raise HTTPException(503, "jobs_disabled")
        return service, context

    @router.get("/status")
    def status(request: Request):
        service, _ = current(request, allow_disabled=True)
        return (
            service.status()
            if service
            else {
                "enabled": False,
                "configured": False,
                "backend": "dbos",
                "live_calls_enabled": False,
            }
        )

    @router.post("/runs", status_code=202)
    def submit(payload: ChecklistRequest, request: Request):
        service, context = current(request, "agent:use")
        return service.submit(
            context,
            payload.model_dump(mode="json", exclude_unset=True),
            request.headers.get("idempotency-key"),
        )

    @router.get("/runs")
    def history(
        request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)
    ):
        service, context = current(request)
        return service.list(context, offset, limit)

    @router.get("/runs/{run_id}")
    def read(run_id: str, request: Request):
        service, context = current(request)
        return service.read(context, run_id)

    @router.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, payload: VersionCommand, request: Request):
        service, context = current(request, "agent:use")
        return service.cancel(context, run_id, payload.expected_state_version)

    @router.post("/runs/{run_id}/resume")
    def resume(run_id: str, payload: VersionCommand, request: Request):
        service, context = current(request, "agent:use")
        return service.resume(context, run_id, payload.expected_state_version)

    @router.post("/runs/{run_id}/reauthorize")
    def reauthorize(run_id: str, payload: VersionCommand, request: Request):
        service, context = current(request, "agent:use")
        service.enabled()
        service.repository.reauthorize(context, run_id, payload.expected_state_version)
        return service.read(context, run_id)

    return router
