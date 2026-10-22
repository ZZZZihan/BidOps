import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from joserfc.errors import JoseError
from sqlalchemy import text

from bidops.enterprise.config import EnterpriseSettings
from bidops.enterprise.identity_events import (
    IdentityProviderUnavailable,
    apply_logout,
    verify_logout_token,
)

ROLE_PERMISSIONS = {
    "viewer": {"catalog:read"},
    "analyst": {"catalog:read", "agent:use"},
    "reviewer": {"catalog:read", "review:decide"},
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
            "SELECT l.*,s.roles_json AS current_roles FROM enterprise_logins l "
            "JOIN enterprise_login_identities i ON i.token_hash=l.token_hash "
            "JOIN enterprise_subjects s ON s.owner_id=l.owner_id "
            "AND s.issuer=i.issuer AND s.subject=i.subject "
            "WHERE l.token_hash=:hash AND l.expires_at>:now "
            "AND s.status='active' AND s.fresh_until>:now AND i.fresh_until>:now",
            {"hash": digest(token), "now": time.time()},
        )
        if not rows:
            return None
        row = rows[0]
        roles = tuple(
            sorted(set(json.loads(row["roles_json"])) & set(json.loads(row["current_roles"])))
        )
        if not roles:
            return None
        return Principal(row["owner_id"], row["display_name"], roles, row["csrf"])

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
        now, issued_at, sid = time.time(), claims.get("iat"), claims.get("sid")
        if (
            claims.get("iss") != self.settings.oidc_issuer
            or not isinstance(claims.get("sub"), str)
            or not claims["sub"]
            or len(claims["sub"]) > 1024
            or type(issued_at) is not int
            or issued_at > now + 30
            or issued_at + self.settings.identity_snapshot_seconds <= now
            or (sid is not None and (not isinstance(sid, str) or not 1 <= len(sid) <= 1024))
            or not isinstance(roles, list)
            or not all(isinstance(role, str) for role in roles)
        ):
            raise ValueError("invalid_identity")
        roles = sorted(set(roles) & ROLE_PERMISSIONS.keys())
        owner = digest(json.dumps([claims["iss"], claims["sub"]]))
        name = claims.get("name") or claims.get("preferred_username") or "企业用户"
        if not isinstance(name, str):
            name = "企业用户"
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        fresh_until = min(now, issued_at) + self.settings.identity_snapshot_seconds
        rejected = False
        with self.db.engine.begin() as conn:
            # The first write serializes this snapshot against logout and other SSO callbacks.
            conn.execute(
                text(
                    "INSERT INTO enterprise_subjects "
                    "(owner_id,issuer,subject,status,roles_json,roles_checked_at,"
                    "fresh_until,updated_at) "
                    "VALUES (:owner,:iss,:sub,'active',:roles,:iat,:fresh,:now) "
                    "ON CONFLICT(owner_id) DO NOTHING"
                ),
                {
                    "owner": owner,
                    "iss": claims["iss"],
                    "sub": claims["sub"],
                    "roles": json.dumps(roles),
                    "iat": issued_at,
                    "now": now,
                    "fresh": fresh_until,
                },
            )
            subject = conn.execute(
                text(
                    "SELECT status,roles_json,roles_checked_at FROM enterprise_subjects "
                    "WHERE owner_id=:o"
                ),
                {"o": owner},
            ).first()
            revoked = conn.execute(
                text(
                    "SELECT 1 FROM enterprise_identity_revocations WHERE issuer=:iss AND "
                    "((target_type='sub' AND target_value=:sub AND revoked_at>=:iat) OR "
                    "(target_type='sid' AND target_value=:sid "
                    "AND (target_subject='' OR target_subject=:sub)))"
                ),
                {"iss": claims["iss"], "sub": claims["sub"], "sid": sid, "iat": issued_at},
            ).first()
            if not subject or subject[0] != "active" or revoked:
                raise ValueError("identity_disabled_or_revoked")
            if issued_at < subject[2]:
                raise ValueError("stale_identity_snapshot")
            # OIDC iat has second precision. A role conflict within that same second has
            # no trusted ordering, so invalidate every session until a newer issuance.
            if not roles or (issued_at == subject[2] and json.loads(subject[1]) != roles):
                conn.execute(
                    text(
                        "UPDATE enterprise_subjects SET roles_json='[]',fresh_until=0,"
                        "roles_checked_at=:iat,updated_at=:now WHERE owner_id=:owner"
                    ),
                    {"owner": owner, "iat": issued_at, "now": now},
                )
                conn.execute(text("DELETE FROM enterprise_logins WHERE owner_id=:o"), {"o": owner})
                rejected = True
            else:
                conn.execute(
                    text(
                        "UPDATE enterprise_subjects SET roles_json=:roles,roles_checked_at=:iat,"
                        "fresh_until=:fresh,updated_at=:now WHERE owner_id=:owner"
                    ),
                    {
                        "owner": owner,
                        "roles": json.dumps(roles),
                        "iat": issued_at,
                        "fresh": fresh_until,
                        "now": now,
                    },
                )
                # Reauthentication rotates only this browser's application session.
                conn.execute(
                    text("DELETE FROM enterprise_logins WHERE token_hash=:hash"),
                    {"hash": digest(str(request.session.get("login", "")))},
                )
                conn.execute(
                    text(
                        "INSERT INTO enterprise_logins "
                        "VALUES (:hash,:owner,:name,:roles,:csrf,:exp)"
                    ),
                    {
                        "hash": digest(token),
                        "owner": owner,
                        "name": name[:100],
                        "roles": json.dumps(roles),
                        "csrf": csrf,
                        "exp": now + self.settings.session_seconds,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO enterprise_login_identities "
                        "(token_hash,issuer,subject,sid,authenticated_at,fresh_until) "
                        "VALUES (:hash,:iss,:sub,:sid,:iat,:fresh)"
                    ),
                    {
                        "hash": digest(token),
                        "iss": claims["iss"],
                        "sub": claims["sub"],
                        "sid": sid,
                        "iat": issued_at,
                        "fresh": fresh_until,
                    },
                )
        if rejected:
            audit(self.db, "identity_snapshot_rejected", owner)
            raise ValueError("conflicting_or_empty_identity_roles")
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
            "user": {
                "name": principal.display_name,
                "roles": principal.roles,
                "subject_id": principal.owner_id,
            }
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
                request,
                identity.settings.public_url + "/api/auth/callback",
                prompt="login",
                max_age=0,
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

    @router.post("/backchannel-logout")
    async def backchannel_logout(request: Request):
        identity = request.app.state.identity
        if not identity.settings.enabled:
            raise HTTPException(404)
        # This OP-to-RP endpoint intentionally uses no browser cookie/Origin/CSRF credential.
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
            "application/x-www-form-urlencoded"
        ):
            return JSONResponse({"error": "invalid_logout_request"}, status_code=400)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 16384:
                return JSONResponse({"error": "request_too_large"}, status_code=413)
        try:
            fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=8)
            values = fields.get("logout_token", [])
            if len(values) != 1 or not values[0]:
                raise ValueError("missing_logout_token")
        except (UnicodeDecodeError, ValueError):
            return JSONResponse({"error": "invalid_logout_request"}, status_code=400)
        try:
            client = await identity.trusted_client()
        except Exception:
            return JSONResponse({"error": "identity_provider_unavailable"}, status_code=503)
        try:
            event = await verify_logout_token(client, identity.settings, values[0])
        except IdentityProviderUnavailable:
            return JSONResponse({"error": "identity_provider_unavailable"}, status_code=503)
        except (ValueError, JoseError, KeyError, TypeError):
            audit(identity.db, "backchannel_logout_rejected")
            return JSONResponse({"error": "invalid_logout_token"}, status_code=400)
        applied = apply_logout(identity.db, event)
        if applied:
            audit(identity.db, "backchannel_logout")
        # OIDC treats an already logged out subject as success; retries are idempotent.
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

    return router
