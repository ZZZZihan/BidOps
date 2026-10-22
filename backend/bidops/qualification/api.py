from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime, Field

from bidops.company.authorization import ToolContext
from bidops.qualification.candidates import CandidateFilters
from bidops.qualification.contracts import Name, QualificationModel
from bidops.screening.schemas import VersionRef


class CandidateRequest(QualificationModel):
    profile_revision: VersionRef
    filters: CandidateFilters = Field(default_factory=CandidateFilters)
    as_of: AwareDatetime | None = None


class ChecklistRequest(QualificationModel):
    profile_revision: VersionRef
    project_id: Name
    target_package_ids: list[Name] = Field(default_factory=list, max_length=100)
    additional_material_revisions: list[VersionRef] = Field(default_factory=list, max_length=100)
    as_of: AwareDatetime | None = None


def screening_router():
    router = APIRouter(prefix="/api/screening", tags=["screening"])

    def current(request, permission="catalog:read"):
        identity = request.app.state.identity
        if not identity.settings.enabled:
            raise HTTPException(404)
        principal = identity.require(request, permission)
        if principal is None:
            raise HTTPException(401)
        context = ToolContext(principal.owner_id)
        service = request.app.state.screening
        service.sources.company.auth.require_subject(context)
        return service, context

    @router.get("/status")
    def status(request: Request):
        service, _ = current(request)
        return service.status()

    @router.post("/candidates", status_code=201)
    def candidates(payload: CandidateRequest, request: Request):
        service, context = current(request, "agent:use")
        return service.candidates(context, payload.profile_revision, payload.filters, payload.as_of)

    @router.get("/candidates/{batch_id}")
    def candidate_page(
        batch_id: str,
        request: Request,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        service, context = current(request)
        return service.candidate_page(context, batch_id, offset, limit)

    @router.post("/checklists", status_code=201)
    async def checklist(payload: ChecklistRequest, request: Request):
        service, context = current(request, "agent:use")
        return await service.checklist(context, **payload.model_dump())

    @router.get("/runs/{run_id}")
    def read(run_id: str, request: Request):
        service, context = current(request)
        return service.read(context, run_id)

    @router.get("/runs")
    def history(
        request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)
    ):
        service, context = current(request)
        service._enabled()
        return service.repository.list(context, offset=offset, limit=limit)

    @router.post("/runs/{run_id}/reauthorize")
    def reauthorize(run_id: str, request: Request):
        service, context = current(request)
        service._enabled()
        return service.repository.reauthorize(context, run_id)

    return router
