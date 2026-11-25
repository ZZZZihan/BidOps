"""Enterprise ingestion entry points; imports authenticate before reading bytes."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from bidops.company.authorization import ToolContext
from bidops.ingestion.schemas import (
    MAX_REQUEST_BYTES,
    CancelCommand,
    HttpRun,
    ManualImport,
    ScheduleCommand,
)


def ingestion_router():
    router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])

    def current(request, permission="catalog:read", *, allow_disabled=False):
        identity = request.app.state.identity
        if not identity.settings.enabled:
            raise HTTPException(404)
        principal = identity.require(request, permission)
        if principal is None:
            raise HTTPException(401)
        context = ToolContext(principal.owner_id)
        request.app.state.company.auth.require_subject(context)
        service = request.app.state.ingestion
        if not allow_disabled:
            service.enabled()
        return service, context

    @router.get("/status")
    def status(request: Request):
        service, _ = current(request, allow_disabled=True)
        return service.status()

    @router.get("/sources")
    def sources(request: Request):
        service, context = current(request)
        return service.sources(context)

    @router.get("/runs")
    def history(
        request: Request,
        offset: int = Query(0, ge=0, le=2_147_483_647),
        limit: int = Query(20, ge=1, le=100),
    ):
        service, context = current(request)
        return service.list(context, offset, limit)

    @router.get("/runs/{run_id}")
    def read(run_id: str, request: Request):
        service, context = current(request)
        return service.read(context, run_id)

    @router.post("/runs", status_code=202)
    def submit(payload: HttpRun, request: Request):
        service, context = current(request, "source:manage")
        return service.submit(context, payload.model_dump(mode="json"))

    @router.post("/imports", status_code=202)
    async def import_manual(request: Request):
        service, context = current(request, "source:manage")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                raise HTTPException(413, "ingestion_request_too_large")
            body.extend(chunk)
        try:
            payload = ManualImport.model_validate_json(body)
        except ValidationError as exc:
            # Never echo a private bundle/base64 value in validation responses.
            raise HTTPException(
                422, exc.errors(include_input=False, include_context=False, include_url=False)
            ) from None
        return await run_in_threadpool(
            service.import_manual, context, payload.model_dump(mode="json")
        )

    @router.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, payload: CancelCommand, request: Request):
        service, context = current(request)
        return service.cancel(context, run_id, payload.expected_state_version)

    @router.post("/schedules")
    def schedule(payload: ScheduleCommand, request: Request):
        service, context = current(request, "source:manage")
        return service.schedule(context, payload.model_dump(mode="json"))

    @router.get("/schedules")
    def schedules(request: Request):
        service, context = current(request, "source:manage")
        return service.schedules(context)

    return router
