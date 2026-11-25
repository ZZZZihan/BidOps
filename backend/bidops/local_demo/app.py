"""Loopback-only workflow entry point with the real business/authentication services."""

import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.config import Settings
from bidops.enterprise.auth import auth_router
from bidops.enterprise.config import EnterpriseSettings
from bidops.enterprise.health import HealthSettings
from bidops.ingestion.config import IngestionSettings
from bidops.jobs.config import JobsSettings
from bidops.local_demo.identity import CLIENT_ID, CLIENT_SECRET, ISSUER, LocalIdentityProvider
from bidops.local_demo.seed import DESCRIPTION, KIND, LABEL, WorkflowSeed
from bidops.qualification.config import ScreeningSettings
from bidops.review.config import ReviewSettings


def create_workflow_app(
    settings: Settings,
    *,
    origin: str,
    live: bool = False,
    max_cost_cny: Decimal = Decimal("1"),
    api_key: str | None = None,
):
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("local workflow requires an explicit 127.0.0.1 HTTP origin")
    if not max_cost_cny.is_finite() or not Decimal("0.25") <= max_cost_cny <= Decimal("1"):
        raise ValueError("local workflow cumulative estimate must be within 0.25–1 yuan")
    if live and not api_key:
        raise ValueError("live local workflow requires an explicitly provided provider credential")
    provider = LocalIdentityProvider(origin)
    enterprise = EnterpriseSettings(
        enabled=True,
        public_url=origin,
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
        cookie_secret=secrets.token_urlsafe(48),
        allow_insecure_loopback=True,
        organization="BidOps 本机流程演示",
        _env_file=None,
    )
    app = create_app(
        settings,
        DemoSettings(
            enabled=live,
            DEEPSEEK_API_KEY=api_key,
            max_cost_cny=float(max_cost_cny),
            budget_restore_token=None,
            _env_file=None,
        ),
        enterprise,
        ScreeningSettings(
            enabled=True,
            live_calls_enabled=live,
            ledger_verified=live,
            ledger_restore_token=None,
            DEEPSEEK_API_KEY=api_key,
            budget_cny=max_cost_cny,
            max_request_bytes=40_000,
            max_output_tokens=2_000,
            max_requests=12,
            max_seconds=180,
            _env_file=None,
        ),
        JobsSettings(enabled=True, compute_concurrency=1, _env_file=None),
        ReviewSettings(enabled=True, _env_file=None),
        IngestionSettings(enabled=False, _env_file=None),
        HealthSettings(enabled=False, _env_file=None),
    )
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
                provider.handle
            )
            app.state.workflow_seed = WorkflowSeed(app.state.company, settings.data_dir)
            yield

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def loopback_only(request, call_next):
        if (
            request.client is None
            or request.client.host not in {"127.0.0.1", "::1"}
            or request.headers.get("host") != parsed.netloc
        ):
            return JSONResponse({"detail": "local_workflow_loopback_required"}, status_code=403)
        response = await call_next(request)
        # Keep the original login route, state cookie and callback intact. Only
        # replace this app's IdP redirect with its one-use local authorization code.
        if request.url.path == "/api/auth/login" and response.status_code == 302:
            response.headers["location"] = provider.callback_location(response.headers["location"])
        return response

    router = APIRouter(prefix="/api/demo/workflow", tags=["local workflow demo"])
    identity_router = APIRouter()
    original_me = next(
        route.endpoint for route in auth_router().routes if route.path == "/api/auth/me"
    )

    @identity_router.get("/api/auth/me")
    def local_identity(request: Request):
        return {
            **original_me(request),
            "demo": {
                "kind": KIND,
                "label": LABEL,
                "description": DESCRIPTION,
                "start_view": "screening",
            },
        }

    @router.get("/context")
    def context(request: Request):
        principal = request.app.state.identity.require(request, "catalog:read")
        return request.app.state.workflow_seed.get(principal.owner_id)

    @router.post("/context")
    def bootstrap(request: Request):
        principal = request.app.state.identity.require(request, "agent:use")
        return request.app.state.workflow_seed.create(principal.owner_id)

    @router.get("/status")
    def status(request: Request):
        request.app.state.identity.require(request, "catalog:read")
        return {
            "kind": "local_workflow",
            "live_calls_enabled": live,
            "screening": request.app.state.screening.status(),
            "jobs": request.app.state.jobs.status(),
            "review": request.app.state.review.status(),
        }

    # create_app mounts the frontend last. Insert our API before that catch-all.
    before = len(app.router.routes)
    app.include_router(router)
    app.include_router(identity_router)
    routes = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[0:0] = routes
    return app


def application():
    """Uvicorn factory; only the explicit launcher provides these bounded settings."""
    data = Path(os.environ["BIDOPS_DATA_DIR"]).resolve()
    database = data / "processed" / "bidops.sqlite3"
    if not database.is_file():
        raise ValueError("local workflow requires the existing database; no empty replacement")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        if connection.execute("SELECT count(*) FROM notices").fetchone()[0] < 1:
            raise ValueError("local workflow requires imported notices")
        connection.execute("SELECT count(*) FROM agent_runs").fetchone()
    settings = Settings(
        data_dir=data,
        database_url=f"sqlite:///{database}",
        source_registry=Path(os.environ["BIDOPS_SOURCE_REGISTRY"]),
        network_enabled=False,
        _env_file=None,
    )
    return create_workflow_app(
        settings,
        origin=os.environ["BIDOPS_WORKFLOW_ORIGIN"],
        live=os.environ.get("BIDOPS_WORKFLOW_LIVE") == "true",
        max_cost_cny=Decimal(os.environ["BIDOPS_WORKFLOW_MAX_COST_CNY"]),
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
    )
