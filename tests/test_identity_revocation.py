"""Signed Keycloak-compatible protocol simulation; no real IdP or HTTPS acceptance claim."""

import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.enterprise.auth import IdentityService
from bidops.enterprise.identity_events import LOGOUT_EVENT
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey
from pydantic import ValidationError
from test_enterprise import ISSUER, ORIGIN, ProtocolProvider, enterprise


@pytest.fixture
def identity_app(settings):
    app = create_app(settings, DemoSettings(_env_file=None), enterprise())
    provider = ProtocolProvider()
    provider.claims = {"sid": "unit-session-a"}
    with TestClient(app, base_url=ORIGIN) as client:
        app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
            provider.handle
        )
        assert provider.login(client).headers["location"] == "/"
        yield app, client, provider


def logout_claims(**updates):
    return {
        "iss": ISSUER,
        "aud": "unit-client",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "jti": uuid.uuid4().hex,
        "sid": "unit-session-a",
        "sub": "alice",
        "events": {LOGOUT_EVENT: {}},
        **updates,
    }


def send_logout(client, provider, claims=None, *, key=None, header=None):
    token = jwt.encode(
        {"alg": "RS256", "kid": "unit-key", **(header or {})},
        claims if claims is not None else logout_claims(),
        key or provider.key,
    )
    return client.post("/api/auth/backchannel-logout", data={"logout_token": token})


def subject(app):
    return app.state.store.db.rows("SELECT * FROM enterprise_subjects")[0]


def test_snapshot_bound_and_fresh_reauthentication_fail_closed(identity_app):
    app, client, provider = identity_app
    row = app.state.store.db.rows("SELECT * FROM enterprise_login_identities")[0]
    assert 0 < row["fresh_until"] - time.time() <= 240
    assert row["fresh_until"] - row["authenticated_at"] == 240
    started = client.get("/api/auth/login", follow_redirects=False)
    params = parse_qs(urlsplit(started.headers["location"]).query)
    assert params["prompt"] == ["login"] and params["max_age"] == ["0"]
    app.state.store.db.execute("UPDATE enterprise_login_identities SET fresh_until=0")
    assert client.get("/api/notices").status_code == 401
    assert not client.get("/api/auth/me").json()["authenticated"]
    app.state.identity.oauth.enterprise.server_metadata["issuer"] = "https://unavailable.test"
    assert client.get("/api/auth/login").status_code == 503
    assert client.get("/api/notices").status_code == 401
    app.state.identity.oauth.enterprise.server_metadata["issuer"] = ISSUER
    provider.roles = ["viewer"]
    provider.claims["iat"] = int(row["authenticated_at"]) + 1
    assert provider.login(client).headers["location"] == "/"
    me = client.get("/api/auth/me").json()
    assert me["authenticated"] and me["user"]["roles"] == ["viewer"]
    assert "agent:use" not in me["permissions"]


def test_subject_and_login_snapshot_are_independently_required(identity_app):
    app, client, _ = identity_app
    app.state.store.db.execute("UPDATE enterprise_subjects SET fresh_until=0")
    assert client.get("/api/notices").status_code == 401


def test_snapshot_expires_at_exact_240_second_boundary(identity_app, monkeypatch):
    app, client, _ = identity_app
    fresh_until = app.state.store.db.rows("SELECT fresh_until FROM enterprise_login_identities")[0][
        "fresh_until"
    ]
    monkeypatch.setattr(time, "time", lambda: fresh_until - 0.001)
    assert client.get("/api/notices").status_code == 200
    monkeypatch.setattr(time, "time", lambda: fresh_until)
    assert client.get("/api/notices").status_code == 401
    assert not client.get("/api/auth/me").json()["authenticated"]


def test_local_disable_next_request_and_sso_cannot_reactivate(identity_app):
    app, client, provider = identity_app
    before = subject(app)
    app.state.store.db.execute("UPDATE enterprise_subjects SET status='disabled'")
    assert client.get("/api/notices").status_code == 401
    assert "login_error" in provider.login(client).headers["location"]
    after = subject(app)
    assert after["status"] == "disabled"
    assert after["roles_json"] == before["roles_json"]
    assert after["fresh_until"] == before["fresh_until"]
    assert not app.state.store.db.rows("SELECT * FROM enterprise_logins")


def test_current_roles_can_remove_but_not_elevate_old_cookie(identity_app):
    app, client, _ = identity_app
    app.state.store.db.execute('UPDATE enterprise_subjects SET roles_json=\'["admin","analyst"]\'')
    assert client.get("/api/audit").status_code == 403
    app.state.store.db.execute("UPDATE enterprise_subjects SET roles_json='[\"viewer\"]'")
    assert client.get("/api/notices").status_code == 401


def test_older_signed_admin_snapshot_cannot_replace_newer_viewer(identity_app):
    app, client, provider = identity_app
    newest = int(subject(app)["roles_checked_at"]) + 1
    provider.roles = ["viewer"]
    provider.claims["iat"] = newest
    assert provider.login(client).headers["location"] == "/"
    before = subject(app)
    provider.roles = ["admin"]
    provider.claims["iat"] = newest - 60
    assert "login_error" in provider.login(client).headers["location"]
    assert subject(app) == before
    assert subject(app)["roles_json"] == '["viewer"]'
    assert not client.get("/api/auth/me").json()["authenticated"]


@pytest.mark.parametrize("initial,incoming", [("viewer", "admin"), ("admin", "viewer")])
def test_same_second_role_conflict_revokes_until_strictly_newer_snapshot(
    identity_app, initial, incoming
):
    app, client, provider = identity_app
    issued_at = int(subject(app)["roles_checked_at"]) + 1
    provider.roles = [initial]
    provider.claims["iat"] = issued_at
    assert provider.login(client).headers["location"] == "/"
    provider.roles = [incoming]
    assert "login_error" in provider.login(client).headers["location"]
    assert subject(app)["roles_json"] == "[]"
    assert subject(app)["fresh_until"] == 0
    assert not app.state.store.db.rows("SELECT * FROM enterprise_logins")
    assert "login_error" in provider.login(client).headers["location"]
    provider.claims["iat"] = issued_at + 1
    assert provider.login(client).headers["location"] == "/"
    assert subject(app)["roles_json"] == f'["{incoming}"]'


def test_newer_empty_role_snapshot_invalidates_prior_authority(identity_app):
    app, client, provider = identity_app
    provider.roles = []
    provider.claims["iat"] = int(subject(app)["roles_checked_at"]) + 1
    assert "login_error" in provider.login(client).headers["location"]
    assert subject(app)["roles_json"] == "[]"
    assert subject(app)["fresh_until"] == 0
    assert not app.state.store.db.rows("SELECT * FROM enterprise_logins")


@pytest.mark.parametrize("target", ["both", "sub", "sid"])
def test_valid_signed_logout_revokes_next_request_and_preserves_other_subject(identity_app, target):
    app, client, provider = identity_app
    cookie = client.cookies.get("bidops_session")
    provider.subject = "bob"
    provider.claims = {"sid": "unit-session-b"}
    assert provider.login(client).headers["location"] == "/"
    client.cookies.set("bidops_session", cookie, domain="bidops.test", path="/")
    # Restore Alice in a second browser while keeping Bob's login server-side.
    provider.subject = "alice"
    provider.claims = {"sid": "unit-session-a"}
    assert provider.login(client).headers["location"] == "/"
    claims = logout_claims()
    if target == "sub":
        claims.pop("sid")
    elif target == "sid":
        claims.pop("sub")
    assert send_logout(client, provider, claims).status_code == 200
    assert client.get("/api/notices").status_code == 401
    rows = app.state.store.db.rows("SELECT * FROM enterprise_logins")
    assert len(rows) == 1 and rows[0]["owner_id"] != subject(app)["owner_id"]
    assert app.state.store.db.rows("SELECT COUNT(*) n FROM enterprise_logout_receipts")[0]["n"] == 1
    assert all(
        item["status"] == "active"
        for item in app.state.store.db.rows("SELECT status FROM enterprise_subjects")
    )


def test_replayed_event_is_durable_idempotent_and_out_of_order_never_grants(identity_app):
    app, client, provider = identity_app
    first = logout_claims(iat=int(time.time()) - 10)
    assert send_logout(client, provider, first).status_code == 200
    # Rebuild the service to demonstrate replay state is not process memory.
    app.state.identity = IdentityService(app.state.store.db, enterprise())
    app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
        provider.handle
    )
    assert send_logout(client, provider, first).status_code == 200
    older = logout_claims(iat=first["iat"] - 10)
    assert send_logout(client, provider, older).status_code == 200
    revocation = app.state.store.db.rows("SELECT * FROM enterprise_identity_revocations")[0]
    assert revocation["revoked_at"] == first["iat"]
    assert subject(app)["fresh_until"] == 0
    assert not client.get("/api/auth/me").json()["authenticated"]
    assert app.state.store.db.rows("SELECT COUNT(*) n FROM enterprise_logout_receipts")[0]["n"] == 2
    # A token from the same revoked OP sid cannot silently restore an application cookie.
    assert "login_error" in provider.login(client).headers["location"]
    assert not client.get("/api/auth/me").json()["authenticated"]
    provider.claims = {"sid": "unit-fresh-session"}
    assert provider.login(client).headers["location"] == "/"
    assert client.get("/api/notices").status_code == 200


INVALID_CLAIMS = [
    {"iss": "https://attacker.test"},
    {"aud": "wrong-client"},
    {"aud": ["unit-client", 1]},
    {"iat": None},
    {"iat": True},
    {"iat": int(time.time()) - 301},
    {"iat": int(time.time()) + 120},
    {"exp": 1},
    {"exp": "future"},
    {"nonce": None},
    {"events": {}},
    {"events": {LOGOUT_EVENT: []}},
    {"jti": ""},
    {"jti": 1},
    {"jti": "x" * 257},
    {"sid": ""},
    {"sid": 7},
    {"sub": ""},
    {"sub": None},
]


@pytest.mark.parametrize("updates", INVALID_CLAIMS)
def test_invalid_signed_logout_claims_rejected_without_state_change(identity_app, updates):
    app, client, provider = identity_app
    before = subject(app)
    response = send_logout(client, provider, logout_claims(**updates))
    assert response.status_code == 400
    assert client.get("/api/notices").status_code == 200
    assert subject(app) == before
    assert not app.state.store.db.rows("SELECT * FROM enterprise_logout_receipts")


def test_logout_requires_sub_or_sid_and_rejects_forged_signature(identity_app):
    app, client, provider = identity_app
    claims = logout_claims()
    claims.pop("sub")
    claims.pop("sid")
    assert send_logout(client, provider, claims).status_code == 400
    assert send_logout(client, provider, key=provider.other_key).status_code == 400
    assert send_logout(client, provider, header={"typ": "at+jwt"}).status_code == 400
    assert (
        send_logout(client, provider, header={"jku": "https://attacker.test/keys"}).status_code
        == 400
    )
    symmetric = OctKey.generate_key(256)
    token = jwt.encode({"alg": "HS256", "kid": "unit-key"}, logout_claims(), symmetric)
    assert (
        client.post("/api/auth/backchannel-logout", data={"logout_token": token}).status_code == 400
    )
    assert client.get("/api/notices").status_code == 200
    assert not app.state.store.db.rows("SELECT * FROM enterprise_logout_receipts")


def test_mismatched_or_unknown_target_cannot_revoke_another_subject(identity_app):
    _, client, provider = identity_app
    assert send_logout(client, provider, logout_claims(sub="bob")).status_code == 200
    assert client.get("/api/notices").status_code == 200
    assert send_logout(client, provider, logout_claims(sid="unknown-session")).status_code == 200
    assert client.get("/api/notices").status_code == 200
    assert provider.login(client).headers["location"] == "/"


def test_form_body_limits_and_no_browser_csrf_bypass_for_normal_logout(identity_app):
    _, client, provider = identity_app
    assert (
        client.post("/api/auth/backchannel-logout", json={"logout_token": "x"}).status_code == 400
    )
    assert client.post("/api/auth/backchannel-logout", data={}).status_code == 400
    assert (
        client.post(
            "/api/auth/backchannel-logout",
            content="logout_token=x&logout_token=y",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/auth/backchannel-logout",
            content="logout_token=" + "x" * 17000,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 413
    )
    assert client.post("/api/auth/logout").status_code == 403
    client.cookies.clear()
    assert send_logout(client, provider).status_code == 200


def test_jwks_rotation_refreshes_unknown_kid_and_unavailable_jwks_fail_closed(identity_app):
    app, client, provider = identity_app
    rotated = RSAKey.generate_key(2048, parameters={"kid": "rotated"})
    provider.key = rotated
    assert send_logout(client, provider, header={"kid": "rotated"}).status_code == 200
    assert client.get("/api/notices").status_code == 401
    app.state.identity.oauth.enterprise.server_metadata.pop("jwks", None)

    def unavailable(request):
        return httpx2.Response(503)

    app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
        unavailable
    )
    assert send_logout(client, provider, header={"kid": "rotated"}).status_code == 503
    assert not client.get("/api/auth/me").json()["authenticated"]


def test_subject_event_watermark_blocks_old_authentication(identity_app):
    app, client, provider = identity_app
    claims = logout_claims()
    claims.pop("sid")
    assert send_logout(client, provider, claims).status_code == 200
    assert "login_error" in provider.login(client).headers["location"]
    assert subject(app)["fresh_until"] == 0
    provider.claims = {"sid": "new-session", "iat": claims["iat"] + 1}
    assert provider.login(client).headers["location"] == "/"


def test_legacy_cookie_without_identity_snapshot_is_not_trusted(identity_app):
    app, client, _ = identity_app
    app.state.store.db.execute("DELETE FROM enterprise_login_identities")
    assert client.get("/api/notices").status_code == 401


def test_only_declared_provider_and_bounded_snapshot_configuration():
    for kwargs in ({"identity_provider": "generic"}, {"identity_snapshot_seconds": 241}):
        with pytest.raises(ValidationError):
            enterprise(**kwargs)
