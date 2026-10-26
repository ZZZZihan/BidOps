"""Signed, one-use OIDC protocol for one fixed fictional local demonstration user."""

import hashlib
import secrets
import time
from base64 import b64encode, urlsafe_b64encode
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
from joserfc import jwt
from joserfc.jwk import RSAKey

# Stable across process restarts and listening ports: existing object ownership survives.
ISSUER = "http://127.0.0.1/bidops-local-demo"
CLIENT_ID = "bidops-local-workflow"
CLIENT_SECRET = "LOCAL_DEMONSTRATION_CLIENT_NOT_AN_EXTERNAL_CREDENTIAL"


class LocalIdentityProvider:
    def __init__(self, origin):
        self.origin = origin
        self.key = RSAKey.generate_key(2048, parameters={"kid": secrets.token_hex(8)})
        self.pending = {}

    def callback_location(self, authorization_location):
        parsed = urlsplit(authorization_location)
        if parsed._replace(query="").geturl() != ISSUER + "/authorize":
            raise ValueError("unexpected local authorization endpoint")
        fields = parse_qs(parsed.query, strict_parsing=True)
        expected = {
            "client_id": CLIENT_ID,
            "redirect_uri": self.origin + "/api/auth/callback",
            "response_type": "code",
            "code_challenge_method": "S256",
        }
        if any(fields.get(key) != [value] for key, value in expected.items()):
            raise ValueError("invalid local authorization request")
        values = {}
        for key in ("state", "nonce", "code_challenge"):
            if len(fields.get(key, [])) != 1 or not fields[key][0]:
                raise ValueError("missing local authorization binding")
            values[key] = fields[key][0]
        now = time.monotonic()
        self.pending = {key: value for key, value in self.pending.items() if value["until"] > now}
        if len(self.pending) >= 100:
            raise ValueError("too many local authorizations")
        code = secrets.token_urlsafe(32)
        self.pending[code] = {**values, "until": now + 120}
        return (
            self.origin
            + "/api/auth/callback?"
            + urlencode({"code": code, "state": values["state"]})
        )

    def handle(self, request):
        url = str(request.url)
        if url == ISSUER + "/.well-known/openid-configuration":
            return httpx2.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": ISSUER + "/authorize",
                    "token_endpoint": ISSUER + "/token",
                    "jwks_uri": ISSUER + "/keys",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        if url == ISSUER + "/keys":
            return httpx2.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if url != ISSUER + "/token" or request.method != "POST":
            return httpx2.Response(400, json={"error": "local_identity_endpoint_rejected"})
        try:
            credentials = b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if not secrets.compare_digest(
                request.headers.get("authorization", ""), "Basic " + credentials
            ):
                raise ValueError("client authentication")
            fields = parse_qs(request.content.decode(), strict_parsing=True)
            if fields.get("grant_type") != ["authorization_code"]:
                raise ValueError("grant")
            if fields.get("redirect_uri") != [self.origin + "/api/auth/callback"]:
                raise ValueError("redirect")
            if len(fields.get("code", [])) != 1 or len(fields.get("code_verifier", [])) != 1:
                raise ValueError("code")
            pending = self.pending.pop(fields["code"][0], None)
            challenge = (
                urlsafe_b64encode(hashlib.sha256(fields["code_verifier"][0].encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if (
                pending is None
                or pending["until"] <= time.monotonic()
                or not secrets.compare_digest(challenge, pending["code_challenge"])
            ):
                raise ValueError("authorization binding")
        except (ValueError, UnicodeError):
            return httpx2.Response(400, json={"error": "invalid_grant"})
        now = int(time.time())
        token = jwt.encode(
            {"alg": "RS256", "kid": self.key.kid},
            {
                "iss": ISSUER,
                "sub": "fictional-local-workflow-user-v1",
                "aud": CLIENT_ID,
                "iat": now,
                "exp": now + 240,
                "nonce": pending["nonce"],
                "sid": secrets.token_hex(16),
                "name": "本机演示审核人",
                "bidops_roles": ["analyst", "reviewer"],
            },
            self.key,
        )
        return httpx2.Response(
            200,
            json={
                "access_token": secrets.token_urlsafe(24),
                "token_type": "Bearer",
                "id_token": token,
            },
        )
