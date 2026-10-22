import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from bidops.agent_demo.runtime import AgentService, DemoFailure, DemoSettings
from bidops.agent_demo.schemas import ChatRequest, ChatResponse
from bidops.agent_demo.schemas import Query as NoticeQuery
from bidops.catalog_query import query_notices
from bidops.company import CompanyService
from bidops.company.api import company_router
from bidops.company.retention import prune_company_materials
from bidops.config import Settings
from bidops.enterprise.auth import IdentityService, auth_router
from bidops.enterprise.config import EnterpriseSettings
from bidops.enterprise.operations import Operations, OperationsMiddleware, SingleWorker
from bidops.enterprise.sessions import PersistentConversations, prune_private_history
from bidops.jobs.api import jobs_router
from bidops.jobs.config import JobsSettings
from bidops.models import Notice
from bidops.notice_api import notice_router
from bidops.notice_data import NoticeRepository
from bidops.qualification.api import screening_router
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.service import ScreeningService
from bidops.review.api import review_router
from bidops.review.config import ReviewSettings
from bidops.review.service import ReviewService
from bidops.storage import Store


def create_app(
    settings: Settings | None = None,
    demo_settings: DemoSettings | None = None,
    enterprise_settings: EnterpriseSettings | None = None,
    screening_settings: ScreeningSettings | None = None,
    jobs_settings: JobsSettings | None = None,
    review_settings: ReviewSettings | None = None,
) -> FastAPI:
    settings = settings or Settings()
    enterprise = enterprise_settings or EnterpriseSettings()
    operations = Operations()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = SingleWorker(settings.database_url) if enterprise.enabled else None
        try:
            app.state.store = Store(settings)
            app.state.agent_service = AgentService(app.state.store, demo_settings)
            app.state.identity = IdentityService(app.state.store.db, enterprise)
            app.state.company = CompanyService(
                app.state.store.db, settings.data_dir, max_bytes=settings.max_bytes
            )
            app.state.conversations = PersistentConversations(app.state.agent_service, enterprise)
            app.state.screening = ScreeningService(
                app.state.company,
                NoticeRepository(app.state.store.db, settings.data_dir / "raw"),
                screening_settings,
            )
            app.state.maintenance_failed = False
            app.state.jobs = None
            if enterprise.enabled:
                prune_private_history(app.state.store.db, enterprise.history_days)
                company_prune = prune_company_materials(app.state.company)
                app.state.maintenance_failed = company_prune["status"] != "PASS"
                if (jobs_settings or JobsSettings()).enabled:
                    from bidops.jobs.runtime import JobsService

                    app.state.jobs = JobsService(
                        app.state.screening, settings.data_dir, jobs_settings
                    )
            app.state.review = ReviewService(app.state.screening, app.state.jobs, review_settings)
            if app.state.jobs:
                await asyncio.to_thread(app.state.jobs.start)

            async def review_delivery():
                while True:
                    await asyncio.sleep(30)
                    try:
                        delivery = asyncio.create_task(
                            asyncio.to_thread(app.state.review.deliver_pending)
                        )
                        try:
                            await asyncio.shield(delivery)
                        except asyncio.CancelledError:
                            # Cancelling the waiter does not stop its native thread.
                            # Join it before closing DBOS, the database or worker lock.
                            with suppress(Exception):
                                await delivery
                            raise
                    except Exception:
                        logging.getLogger("bidops.operations").error(
                            '{"event":"review_delivery_failed"}'
                        )

            async def retention():
                while True:
                    await asyncio.sleep(3600)
                    try:
                        prune_private_history(app.state.store.db, enterprise.history_days)
                        if prune_company_materials(app.state.company)["status"] != "PASS":
                            raise RuntimeError("company_retention_cleanup_failed")
                        app.state.maintenance_failed = False
                    except Exception:
                        app.state.maintenance_failed = True
                        logging.getLogger("bidops.operations").error('{"event":"retention_failed"}')

            maintenance = asyncio.create_task(retention()) if enterprise.enabled else None
            relay = (
                asyncio.create_task(review_delivery())
                if enterprise.enabled and app.state.review.settings.enabled
                else None
            )
            try:
                yield
            finally:
                if maintenance:
                    maintenance.cancel()
                    with suppress(asyncio.CancelledError):
                        await maintenance
                if relay:
                    relay.cancel()
                    with suppress(asyncio.CancelledError):
                        await relay
                if app.state.jobs:
                    await asyncio.to_thread(app.state.jobs.close)
                await app.state.agent_service.close()
                await app.state.screening.close()
                app.state.store.db.engine.dispose()
        finally:
            if worker:
                worker.close()

    app = FastAPI(
        title="BidOps",
        version="0.3.0",
        lifespan=lifespan,
        docs_url=None if enterprise.enabled else "/docs",
        redoc_url=None if enterprise.enabled else "/redoc",
        openapi_url=None if enterprise.enabled else "/openapi.json",
    )
    router = APIRouter()

    @app.middleware("http")
    async def protect_local_chat(request: Request, call_next):
        if not enterprise.enabled and request.url.path.endswith("/agent/chat"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
                return JSONResponse({"error": "untrusted_origin"}, status_code=403)
        return await call_next(request)

    @router.get("/health")
    def health():
        app.state.store.db.rows("SELECT 1")
        return {
            "status": "ok",
            "phase": "enterprise_pilot" if enterprise.enabled else "P0",
            "network_enabled": settings.network_enabled,
        }

    @router.get("/sources")
    def sources(request: Request):
        app.state.identity.require(request, "catalog:read")
        return {"items": [s.model_dump() for s in app.state.store.sources]}

    @router.get("/notices")
    def notices(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=2_147_483_647),
        q: str = Query("", max_length=120),
        sort_by: Literal["project_code", "budget", "deadline", "publication"] = "project_code",
        descending: bool = False,
    ):
        app.state.identity.require(request, "catalog:read")
        return query_notices(
            app.state.store.db,
            NoticeQuery(keyword=q, offset=offset, sort_by=sort_by, descending=descending),
            datetime.now(UTC),
            limit=limit,
        )

    @router.get("/notices/{notice_id}", response_model=Notice)
    def notice_detail(notice_id: str, request: Request):
        app.state.identity.require(request, "catalog:read")
        notice = app.state.store.get_notice(notice_id)
        if not notice:
            raise HTTPException(404, "Notice not found")
        return notice

    @router.get("/agent/status")
    def agent_status(request: Request):
        app.state.identity.require(request, "catalog:read")
        return app.state.agent_service.status()

    @router.post("/agent/chat", response_model=ChatResponse)
    async def agent_chat(payload: ChatRequest, request: Request):
        principal = app.state.identity.require(request, "agent:use")
        try:
            if enterprise.enabled:
                return await app.state.conversations.chat(
                    principal, payload, request.headers.get("idempotency-key")
                )
            return await app.state.agent_service.chat(payload)
        except DemoFailure as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status)

    @app.exception_handler(DemoFailure)
    async def demo_failure(request: Request, exc: DemoFailure):
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    @app.get("/api/sessions")
    def sessions(request: Request):
        if not enterprise.enabled:
            raise HTTPException(404)
        principal = app.state.identity.require(request, "agent:use")
        return {"items": app.state.conversations.list(principal)}

    @app.get("/api/sessions/{session_id}")
    def history(session_id: str, request: Request):
        if not enterprise.enabled:
            raise HTTPException(404)
        principal = app.state.identity.require(request, "agent:use")
        return {
            "session_id": session_id,
            "turns": app.state.conversations.history(principal, session_id),
        }

    @app.get("/api/audit")
    def audit_log(
        request: Request,
        before: int | None = Query(None, ge=1),
        limit: int = Query(50, ge=1, le=100),
    ):
        if not enterprise.enabled:
            raise HTTPException(404)
        app.state.identity.require(request, "audit:read")
        return {
            "items": app.state.store.db.rows(
                "SELECT * FROM enterprise_audit WHERE (:before IS NULL OR event_id<:before) "
                "ORDER BY event_id DESC LIMIT :limit",
                {"before": before, "limit": limit},
            )
        }

    @app.get("/api/metrics")
    def metrics(request: Request):
        if not enterprise.enabled:
            raise HTTPException(404)
        app.state.identity.require(request, "audit:read")
        return Response(
            generate_latest(operations.registry), headers={"Content-Type": CONTENT_TYPE_LATEST}
        )

    @app.get("/api/ready")
    def ready():
        try:
            status = app.state.agent_service.status()
            ok = bool(
                status["configured"]
                and status["enabled"]
                and status["notice_count"]
                and not app.state.maintenance_failed
            )
        except Exception:
            ok = False
        return JSONResponse({"ready": ok}, status_code=200 if ok else 503)

    app.include_router(auth_router())
    app.include_router(company_router())
    app.include_router(screening_router())
    app.include_router(jobs_router())
    app.include_router(review_router())
    router.include_router(notice_router())
    app.include_router(router)
    app.include_router(router, prefix="/api", include_in_schema=False)
    frontend = Path("frontend/dist")
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    if enterprise.enabled:
        app.add_middleware(
            SessionMiddleware,
            secret_key=enterprise.cookie_secret.get_secret_value(),
            session_cookie="bidops_session",
            same_site="lax",
            https_only=enterprise.secure_cookies,
            max_age=enterprise.session_seconds,
        )
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=[urlsplit(enterprise.public_url).hostname]
        )
    app.add_middleware(OperationsMiddleware, operations=operations, enterprise=enterprise.enabled)
    return app


app = create_app()
