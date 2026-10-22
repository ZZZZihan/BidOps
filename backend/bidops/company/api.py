from datetime import datetime
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import AwareDatetime, TypeAdapter, ValidationError

from bidops.company.authorization import CompanyError, ToolContext
from bidops.company.schemas import CitationRequest, GrantCommand, ProfileWrite, ValidityQuery


def company_router():
    router = APIRouter(prefix="/api/company", tags=["company"])

    def current(request):
        identity = request.app.state.identity
        if not identity.settings.enabled:
            raise HTTPException(404)
        principal = identity.require(request)
        if principal is None:
            raise HTTPException(401)
        service = request.app.state.company
        context = ToolContext(principal.owner_id)
        service.auth.require_subject(context)
        return service, context

    @router.get("/profiles")
    def profiles(
        request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        service, context = current(request)
        return service.auth.visible(context, "profile", limit=limit, offset=offset)

    @router.post("/profiles", status_code=201)
    def create_profile(payload: ProfileWrite, request: Request):
        service, context = current(request)
        if payload.expected_version is not None:
            raise CompanyError("new_object_cannot_have_expected_version", 422)
        return service.repo.put(context, payload.payload, retention_until=payload.retention_until)

    @router.get("/profiles/{object_id}")
    def profile(object_id: str, request: Request, version: str | None = None):
        service, context = current(request)
        return service.repo.get(context, object_id, version)

    @router.put("/profiles/{object_id}")
    def update_profile(object_id: str, payload: ProfileWrite, request: Request):
        service, context = current(request)
        return service.repo.put(
            context,
            payload.payload,
            object_id=object_id,
            expected_version=payload.expected_version,
            retention_until=payload.retention_until,
        )

    @router.post("/profiles/{object_id}/versions/{version_id}/facts/{fact_id}/validity")
    def validity(
        object_id: str, version_id: str, fact_id: str, payload: ValidityQuery, request: Request
    ):
        service, context = current(request)
        return service.repo.qualification_at(
            context, object_id, version_id, fact_id, payload.required_at
        )

    @router.get("/materials")
    def materials(
        request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        service, context = current(request)
        page = service.auth.visible(context, "material", limit=limit, offset=offset)
        page["items"] = [
            service.materials.metadata(context, obj["object_id"]) for obj in page["items"]
        ]
        service.auth.require_many(context, [item["object_id"] for item in page["items"]])
        return page

    @router.get("/materials/search")
    def search(
        request: Request,
        q: str = Query(min_length=1, max_length=200),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        service, context = current(request)
        return service.materials.search(context, q, limit=limit, offset=offset)

    async def upload(request, object_id=None):
        service, context = current(request)
        if object_id is None:
            service.auth.require_creator(context)
        else:
            service.auth.require(context, object_id, "write")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > service.materials.max_bytes:
                raise CompanyError("material_size_limit", 413)
            data.extend(chunk)
        expires = request.headers.get("x-retention-until")
        try:
            expires: datetime | None = (
                TypeAdapter(AwareDatetime).validate_python(expires) if expires else None
            )
        except ValidationError:
            raise CompanyError("invalid_retention_time", 422) from None
        filename = request.headers.get("x-filename", "")
        if "x-filename-encoded" in request.headers:
            try:
                filename = unquote(request.headers["x-filename-encoded"], errors="strict")
            except UnicodeError:
                raise CompanyError("invalid_filename_encoding", 422) from None
        attestation = request.headers.get("x-source-attestation")
        if "x-source-attestation-encoded" in request.headers:
            try:
                attestation = unquote(
                    request.headers["x-source-attestation-encoded"], errors="strict"
                )
            except UnicodeError:
                raise CompanyError("invalid_source_attestation_encoding", 422) from None
        return service.materials.put(
            context,
            filename=filename,
            content_type=request.headers.get("content-type", ""),
            data=bytes(data),
            object_id=object_id,
            expected_version=request.headers.get("if-match"),
            retention_until=expires,
            provenance_kind=request.headers.get("x-provenance-kind", "engineering_fixture"),
            source_attestation=attestation,
        )

    @router.post("/materials", status_code=201)
    async def create_material(request: Request):
        return await upload(request)

    @router.put("/materials/{object_id}")
    async def update_material(object_id: str, request: Request):
        return await upload(request, object_id)

    @router.get("/materials/{object_id}")
    def material(object_id: str, request: Request, version: str | None = None):
        service, context = current(request)
        return service.materials.metadata(context, object_id, version)

    @router.get("/materials/{object_id}/download")
    def download(object_id: str, request: Request, version: str | None = None):
        service, context = current(request)
        _, raw, _ = service.materials.read(context, object_id, version)
        return Response(
            raw,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="company-material.bin"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    @router.get("/materials/{object_id}/export")
    def export(object_id: str, request: Request, version: str | None = None):
        service, context = current(request)
        row, _, normalized = service.materials.read(context, object_id, version)
        return {
            "material": service.materials.metadata(context, object_id, row["version_id"]),
            "text": normalized,
        }

    @router.post("/materials/{object_id}/versions/{version_id}/citations")
    def citation(object_id: str, version_id: str, payload: CitationRequest, request: Request):
        service, context = current(request)
        return service.materials.citation(
            context, object_id, version_id, payload.start, payload.end
        )

    @router.post("/objects/{object_id}/grants")
    def grant(object_id: str, payload: GrantCommand, request: Request):
        service, context = current(request)
        service.auth.grant(context, object_id, payload.subject_id, payload.actions)
        return {"granted": True}

    @router.delete("/objects/{object_id}/grants/{subject_id}")
    def revoke(object_id: str, subject_id: str, request: Request):
        service, context = current(request)
        service.auth.revoke(context, object_id, subject_id)
        return {"revoked": True}

    @router.post("/objects/{object_id}/reauthorize")
    def reauthorize(object_id: str, payload: GrantCommand, request: Request):
        service, context = current(request)
        service.auth.reauthorize(context, object_id, payload.subject_id, payload.actions)
        return {"reauthorized": True}

    @router.delete("/objects/{object_id}")
    def delete(object_id: str, request: Request):
        service, context = current(request)
        return service.repo.delete(context, object_id)

    return router
