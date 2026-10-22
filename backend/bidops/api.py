from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from bidops.config import Settings
from bidops.models import Notice
from bidops.storage import Store


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(settings)
        yield
        app.state.store.db.engine.dispose()

    app = FastAPI(title="BidOps P0", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        app.state.store.db.rows("SELECT 1")
        return {"status": "ok", "phase": "P0", "network_enabled": settings.network_enabled}

    @app.get("/sources")
    def sources():
        return {"items": [s.model_dump() for s in app.state.store.sources]}

    @app.get("/notices")
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

    @app.get("/notices/{notice_id}", response_model=Notice)
    def notice_detail(notice_id: str):
        notice = app.state.store.get_notice(notice_id)
        if not notice:
            raise HTTPException(404, "Notice not found")
        return notice

    return app


app = create_app()
