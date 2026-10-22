import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from bidops.enterprise.config import EnterpriseSettings

ROLE_PERMISSIONS = {
    "viewer": {"catalog:read"},
    "analyst": {"catalog:read", "agent:use"},
    "auditor": {"catalog:read", "audit:read"},
    "admin": {"catalog:read", "agent:use", "audit:read"},
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    owner_id: str
    display_name: str
    roles: tuple[str, ...]
    csrf: str

    @property
    def permissions(self) -> set[str]:
        return set().union(*(ROLE_PERMISSIONS.get(role, set()) for role in self.roles))


def audit(db, event: str, owner_id: str | None = None, **metadata):
    db.execute(
        "INSERT INTO enterprise_audit(observed_at,owner_id,event,metadata_json) "
        "VALUES (:at,:owner,:event,:meta)",
        {"at": time.time(), "owner": owner_id, "event": event, "meta": json.dumps(metadata)},
    )


class OAuthStateCache:
    """Authlib cache adapter. Atomic consumption prevents a replayed signed state cookie."""

    def __init__(self, db):
        self.db = db

    async def set(self, key, value, expires):
        self.db.execute(
            "INSERT OR REPLACE INTO enterprise_oauth_state VALUES (:k,:v,:exp)",
            {"k": key, "v": value, "exp": time.time() + min(expires, 300)},
        )

    async def get(self, key):
        with self.db.engine.begin() as conn:
            row = conn.execute(
                text("DELETE FROM enterprise_oauth_state WHERE key=:k RETURNING value,expires_at"),
                {"k": key},
            ).first()
        return row[0] if row and row[1] > time.time() else None

    async def delete(self, key):
        self.db.execute("DELETE FROM enterprise_oauth_state WHERE key=:k", {"k": key})


class IdentityService:
    def __init__(self, db, settings: EnterpriseSettings):
        self.db, self.settings = db, settings
        self.oauth = OAuth(cache=OAuthStateCache(db))
        if settings.enabled:
            self.oauth.register(
                "enterprise",
                client_id=settings.oidc_client_id,
                client_secret=settings.oidc_client_secret.get_secret_value(),
                server_metadata_url=settings.oidc_issuer + "/.well-known/openid-configuration",
                client_kwargs={
                    "scope": "openid profile",
                    "code_challenge_method": "S256",
                    "timeout": 10,
                },
            )

    def resolve(self, request: Request) -> Principal | None:
        if not self.settings.enabled:
            return None
        token = request.session.get("login", "")
        if not isinstance(token, str) or len(token) > 100:
            return None
        rows = self.db.rows(
            "SELECT * FROM enterprise_logins WHERE token_hash=:hash AND expires_at>:now",
            {"hash": digest(token), "now": time.time()},
        )
        if not rows:
            return None
        row = rows[0]
        return Principal(
            row["owner_id"], row["display_name"], tuple(json.loads(row["roles_json"])), row["csrf"]
        )

    def require(self, request: Request, permission: str | None = None) -> Principal | None:
        if not self.settings.enabled:
            return None
        principal = self.resolve(request)
        if not principal:
            raise HTTPException(401, "authentication_required")
        if permission and permission not in principal.permissions:
            audit(self.db, "access_denied", principal.owner_id, permission=permission)
            raise HTTPException(403, "permission_denied")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != self.settings.public_url or not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), principal.csrf
            ):
                raise HTTPException(403, "csrf_failed")
        request.state.owner_id = principal.owner_id
        return principal

    async def trusted_client(self):
        client = self.oauth.create_client("enterprise")
        metadata = await client.load_server_metadata()
        if metadata.get("issuer") != self.settings.oidc_issuer:
            raise ValueError("issuer_mismatch")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            endpoint = urlsplit(metadata.get(field, ""))
            local = self.settings.allow_insecure_loopback and endpoint.hostname in {
                "localhost",
                "127.0.0.1",
            }
            if (
                not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or (endpoint.scheme != "https" and not (endpoint.scheme == "http" and local))
            ):
                raise ValueError("invalid_oidc_endpoint")
        # Only asymmetric signatures. This client profile intentionally supports RS256.
        client.server_metadata["id_token_signing_alg_values_supported"] = ["RS256"]
        return client

    def establish(self, request: Request, claims: dict):
        roles = claims.get("bidops_roles")
        if (
            claims.get("iss") != self.settings.oidc_issuer
            or not isinstance(claims.get("sub"), str)
            or not claims["sub"]
            or not isinstance(roles, list)
            or not all(isinstance(role, str) for role in roles)
        ):
            raise ValueError("invalid_identity")
        roles = sorted(set(roles) & ROLE_PERMISSIONS.keys())
        if not roles:
            raise ValueError("no_application_role")
        owner = digest(json.dumps([claims["iss"], claims["sub"]]))
        name = claims.get("name") or claims.get("preferred_username") or "企业用户"
        if not isinstance(name, str):
            name = "企业用户"
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        # Reauthentication rotates and revokes the previous application session.
        self.revoke(request)
        self.db.execute(
            "INSERT INTO enterprise_logins VALUES (:hash,:owner,:name,:roles,:csrf,:exp)",
            {
                "hash": digest(token),
                "owner": owner,
                "name": name[:100],
                "roles": json.dumps(roles),
                "csrf": csrf,
                "exp": time.time() + self.settings.session_seconds,
            },
        )
        request.session.clear()
        request.session["login"] = token
        audit(self.db, "login", owner, roles=roles)

    def revoke(self, request: Request):
        self.db.execute(
            "DELETE FROM enterprise_logins WHERE token_hash=:hash",
            {"hash": digest(str(request.session.get("login", "")))},
        )


def auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/auth")

    @router.get("/me")
    def me(request: Request):
        identity = request.app.state.identity
        principal = identity.resolve(request)
        return {
            "mode": "enterprise" if identity.settings.enabled else "local",
            "authenticated": principal is not None,
            "organization": identity.settings.organization,
            "user": {"name": principal.display_name, "roles": principal.roles}
            if principal
            else None,
            "permissions": sorted(principal.permissions) if principal else [],
            "csrf_token": principal.csrf if principal else None,
        }

    @router.get("/login")
    async def login(request: Request):
        identity = request.app.state.identity
        if not identity.settings.enabled:
            raise HTTPException(404)
        try:
            client = await identity.trusted_client()
            return await client.authorize_redirect(
                request, identity.settings.public_url + "/api/auth/callback"
            )
        except Exception:
            audit(identity.db, "login_unavailable")
            return JSONResponse({"error": "identity_provider_unavailable"}, status_code=503)

    @router.get("/callback")
    async def callback(request: Request):
        identity = request.app.state.identity
        if not identity.settings.enabled:
            raise HTTPException(404)
        try:
            client = await identity.trusted_client()
            token = await client.authorize_access_token(
                request,
                claims_options={"iss": {"essential": True, "value": identity.settings.oidc_issuer}},
                leeway=30,
            )
            identity.establish(request, token["userinfo"])
            # Provider access/refresh/ID tokens are intentionally not persisted or sent to JS.
            return RedirectResponse("/", status_code=303)
        except Exception:
            identity.revoke(request)
            request.session.clear()
            audit(identity.db, "login_rejected")
            return RedirectResponse("/?login_error=1", status_code=303)

    @router.post("/logout")
    def logout(request: Request):
        identity = request.app.state.identity
        principal = identity.require(request)
        if principal:
            identity.revoke(request)
            request.session.clear()
            audit(identity.db, "logout", principal.owner_id)
        return {"ok": True}

    return router
