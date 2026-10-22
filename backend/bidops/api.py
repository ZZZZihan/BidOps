import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from bidops.agent_demo.runtime import AgentService, DemoFailure, DemoSettings
from bidops.agent_demo.schemas import ChatRequest, ChatResponse
from bidops.config import Settings
from bidops.enterprise.auth import IdentityService, auth_router
from bidops.enterprise.config import EnterpriseSettings
from bidops.enterprise.operations import Operations, OperationsMiddleware, SingleWorker
from bidops.enterprise.sessions import PersistentConversations, prune_private_history
from bidops.models import Notice
from bidops.storage import Store


def create_app(
    settings: Settings | None = None,
    demo_settings: DemoSettings | None = None,
    enterprise_settings: EnterpriseSettings | None = None,
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
            app.state.conversations = PersistentConversations(app.state.agent_service, enterprise)
            if enterprise.enabled:
                prune_private_history(app.state.store.db, enterprise.history_days)
            app.state.maintenance_failed = False

            async def retention():
                while True:
                    await asyncio.sleep(3600)
                    try:
                        prune_private_history(app.state.store.db, enterprise.history_days)
                        app.state.maintenance_failed = False
                    except Exception:
                        app.state.maintenance_failed = True
                        logging.getLogger("bidops.operations").error('{"event":"retention_failed"}')

            maintenance = asyncio.create_task(retention()) if enterprise.enabled else None
            try:
                yield
            finally:
                if maintenance:
                    maintenance.cancel()
                    with suppress(asyncio.CancelledError):
                        await maintenance
                await app.state.agent_service.close()
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
        request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        app.state.identity.require(request, "catalog:read")
        store = app.state.store
        rows = store.db.rows(
            "SELECT payload_json FROM notices ORDER BY notice_id LIMIT :l OFFSET :o",
            {"l": limit, "o": offset},
        )
        return {
            "items": [Notice.model_validate_json(row["payload_json"]) for row in rows],
            "total": store.db.rows("SELECT COUNT(*) AS n FROM notices")[0]["n"],
            "limit": limit,
            "offset": offset,
        }

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
