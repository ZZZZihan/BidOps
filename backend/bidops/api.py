from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from bidops.agent_demo.runtime import AgentService, DemoFailure, DemoSettings
from bidops.agent_demo.schemas import ChatRequest, ChatResponse
from bidops.config import Settings
from bidops.models import Notice
from bidops.storage import Store


def create_app(
    settings: Settings | None = None, demo_settings: DemoSettings | None = None
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(settings)
        app.state.agent_service = AgentService(app.state.store, demo_settings)
        try:
            yield
        finally:
            await app.state.agent_service.close()
            app.state.store.db.engine.dispose()

    app = FastAPI(title="BidOps Agent Demo", version="0.2.0", lifespan=lifespan)
    router = APIRouter()

    @app.middleware("http")
    async def protect_local_chat(request: Request, call_next):
        if request.url.path.endswith("/agent/chat"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
                return JSONResponse({"error": "untrusted_origin"}, status_code=403)
        return await call_next(request)

    @router.get("/health")
    def health():
        app.state.store.db.rows("SELECT 1")
        return {"status": "ok", "phase": "P0", "network_enabled": settings.network_enabled}

    @router.get("/sources")
    def sources():
        return {"items": [s.model_dump() for s in app.state.store.sources]}

    @router.get("/notices")
    def notices(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
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
    def notice_detail(notice_id: str):
        notice = app.state.store.get_notice(notice_id)
        if not notice:
            raise HTTPException(404, "Notice not found")
        return notice

    @router.get("/agent/status")
    def agent_status():
        return app.state.agent_service.status()

    @router.post("/agent/chat", response_model=ChatResponse)
    async def agent_chat(payload: ChatRequest):
        try:
            return await app.state.agent_service.chat(payload)
        except DemoFailure as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status)

    app.include_router(router)
    app.include_router(router, prefix="/api", include_in_schema=False)
    frontend = Path("frontend/dist")
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
