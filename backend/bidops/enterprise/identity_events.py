"""Keycloak's OIDC Back-Channel Logout profile; cryptography stays in Authlib's JOSE stack."""

import hashlib
import time
from dataclasses import dataclass

from joserfc import jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry
from sqlalchemy import text

LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
MAX_LOGOUT_AGE_SECONDS = 300
LOGOUT_CLOCK_SKEW_SECONDS = 30


class IdentityProviderUnavailable(Exception):
    pass


async def _fetch_jwks(client, *, force=False):
    try:
        return await client.fetch_jwk_set(force=force)
    except Exception as exc:
        raise IdentityProviderUnavailable from exc


@dataclass(frozen=True)
class LogoutEvent:
    issuer: str
    subject: str | None
    sid: str | None
    jti_hash: str
    issued_at: int


async def verify_logout_token(client, settings, raw_token: str) -> LogoutEvent:
    if not isinstance(raw_token, str) or not raw_token or len(raw_token) > 16000:
        raise ValueError("invalid_logout_token")
    # Reuse Authlib's discovery/JWKS transport, including its timeout and rotation refresh.
    jwks = await _fetch_jwks(client)
    registry = JWSRegistry(algorithms=["RS256"], strict_check_header=False)
    try:
        token = jwt.decode(raw_token, KeySet.import_key_set(jwks), registry=registry)
    except InvalidKeyIdError:
        jwks = await _fetch_jwks(client, force=True)
        token = jwt.decode(raw_token, KeySet.import_key_set(jwks), registry=registry)
    claims, header = token.claims, token.header
    if (
        header.get("alg") != "RS256"
        or header.get("typ") not in {None, "JWT", "logout+jwt"}
        or any(key in header for key in ("jku", "jwk", "x5u"))
        or not isinstance(header.get("kid"), str)
        or not header["kid"]
    ):
        raise ValueError("invalid_logout_header")
    now = time.time()
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    audience = claims.get("aud")
    if isinstance(audience, str):
        audience = [audience]
    if (
        claims.get("iss") != settings.oidc_issuer
        or not isinstance(audience, list)
        or not audience
        or not all(isinstance(item, str) for item in audience)
        or settings.oidc_client_id not in audience
        or type(issued_at) is not int
        or not now - MAX_LOGOUT_AGE_SECONDS <= issued_at <= now + LOGOUT_CLOCK_SKEW_SECONDS
        or (
            "exp" in claims
            and (type(expires_at) is not int or expires_at <= now or expires_at <= issued_at)
        )
        or "nonce" in claims
        or not isinstance(claims.get("events"), dict)
        or not isinstance(claims["events"].get(LOGOUT_EVENT), dict)
        or not isinstance(claims.get("jti"), str)
        or not 1 <= len(claims["jti"]) <= 256
    ):
        raise ValueError("invalid_logout_claims")
    subject, sid = claims.get("sub"), claims.get("sid")
    for field in ("sub", "sid"):
        if field in claims and (
            not isinstance(claims[field], str) or not 1 <= len(claims[field]) <= 1024
        ):
            raise ValueError("invalid_logout_target")
    if not subject and not sid:
        raise ValueError("missing_logout_target")
    return LogoutEvent(
        claims["iss"], subject, sid, hashlib.sha256(claims["jti"].encode()).hexdigest(), issued_at
    )


def apply_logout(db, event: LogoutEvent) -> bool:
    """Atomically deduplicate and revoke. An out-of-order event can never grant a role."""
    now = time.time()
    with db.engine.begin() as conn:
        receipt = conn.execute(
            text(
                "INSERT INTO enterprise_logout_receipts(issuer,jti_hash,issued_at,received_at) "
                "VALUES (:issuer,:jti,:iat,:now) ON CONFLICT DO NOTHING RETURNING jti_hash"
            ),
            {"issuer": event.issuer, "jti": event.jti_hash, "iat": event.issued_at, "now": now},
        ).first()
        if not receipt:
            return False
        target_type, target_value = ("sid", event.sid) if event.sid else ("sub", event.subject)
        conn.execute(
            text(
                "INSERT INTO enterprise_identity_revocations "
                "(issuer,target_type,target_value,target_subject,revoked_at,received_at) "
                "VALUES (:issuer,:kind,:target,:subject,:iat,:now) "
                "ON CONFLICT(issuer,target_type,target_value,target_subject) DO UPDATE SET "
                "revoked_at=MAX(revoked_at,excluded.revoked_at), "
                "received_at=MAX(received_at,excluded.received_at)"
            ),
            {
                "issuer": event.issuer,
                "kind": target_type,
                "target": target_value,
                "subject": event.subject if event.sid and event.subject else "",
                "iat": event.issued_at,
                "now": now,
            },
        )
        # A subject-wide event also reaches subjects whose last browser session has expired.
        if not event.sid:
            owners = (
                conn.execute(
                    text(
                        "SELECT owner_id FROM enterprise_subjects "
                        "WHERE issuer=:iss AND subject=:sub"
                    ),
                    {"iss": event.issuer, "sub": event.subject},
                )
                .scalars()
                .all()
            )
        else:
            owners = (
                conn.execute(
                    text(
                        "SELECT DISTINCT l.owner_id FROM enterprise_logins l "
                        "JOIN enterprise_login_identities i ON i.token_hash=l.token_hash "
                        "WHERE i.issuer=:iss AND i.sid=:sid AND (:sub IS NULL OR i.subject=:sub)"
                    ),
                    {"iss": event.issuer, "sub": event.subject, "sid": event.sid},
                )
                .scalars()
                .all()
            )
        for owner in owners:
            # Conservative: terminate every local browser for this owner, including async contexts.
            conn.execute(
                text(
                    "UPDATE enterprise_subjects SET fresh_until=0,updated_at=:now WHERE owner_id=:o"
                ),
                {"o": owner, "now": now},
            )
            conn.execute(text("DELETE FROM enterprise_logins WHERE owner_id=:o"), {"o": owner})
    return True
