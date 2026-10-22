"""Offline enterprise protocol tests. No real users or fabricated procurement records."""

import asyncio
import hashlib
import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from bidops.agent_demo.runtime import AgentService, DemoFailure, DemoSettings
from bidops.agent_demo.schemas import ChatRequest
from bidops.api import create_app
from bidops.enterprise.auth import Principal, audit
from bidops.enterprise.config import EnterpriseSettings
from bidops.enterprise.operations import SingleWorker
from bidops.enterprise.sessions import PersistentConversations, prune_private_history
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy.exc import IntegrityError

ISSUER = "https://identity.test/realms/unit"
ORIGIN = "https://bidops.test"
ALICE = Principal("unit-alice", "Alice", ("analyst",), "unit-csrf")
BOB = Principal("unit-bob", "Bob", ("analyst",), "unit-csrf-bob")


def enterprise(**kwargs):
    return EnterpriseSettings(
        enabled=True,
        public_url=ORIGIN,
        oidc_issuer=ISSUER,
        oidc_client_id="unit-client",
        oidc_client_secret="UNIT_NOT_A_REAL_CLIENT_SECRET",
        cookie_secret="unit-" * 10,
        **kwargs,
    )


class ProtocolProvider:
    """Real signed OIDC protocol over HTTPX2 MockTransport, not a mocked auth decision."""

    def __init__(self):
        self.key = RSAKey.generate_key(2048, parameters={"kid": "unit-key"})
        self.other_key = RSAKey.generate_key(2048, parameters={"kid": "unit-key"})
        self.claims = {}
        self.roles = ["analyst"]
        self.nonce = ""
        self.challenge = ""
        self.subject = "alice"
        self.token_calls = 0
        self.bad_signature = False
        self.metadata_issuer = ISSUER

    def handle(self, request):
        if request.url.path.endswith("openid-configuration"):
            return httpx2.Response(
                200,
                json={
                    "issuer": self.metadata_issuer,
                    "authorization_endpoint": ISSUER + "/auth",
                    "token_endpoint": ISSUER + "/token",
                    "jwks_uri": ISSUER + "/keys",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        if request.url.path.endswith("/keys"):
            return httpx2.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        assert request.url.path.endswith("/token")
        self.token_calls += 1
        from base64 import urlsafe_b64encode

        fields = parse_qs(request.content.decode())
        assert fields["redirect_uri"] == [ORIGIN + "/api/auth/callback"]
        challenge = (
            urlsafe_b64encode(hashlib.sha256(fields["code_verifier"][0].encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == self.challenge
        claims = {
            "iss": ISSUER,
            "sub": self.subject,
            "aud": "unit-client",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": self.nonce,
            "name": "Unit User",
            "bidops_roles": self.roles,
            **self.claims,
        }
        token = jwt.encode(
            {"alg": "RS256", "kid": "unit-key"},
            claims,
            self.other_key if self.bad_signature else self.key,
        )
        return httpx2.Response(
            200,
            json={"access_token": "UNIT_ACCESS_TOKEN", "token_type": "Bearer", "id_token": token},
        )

    def start(self, client):
        response = client.get("/api/auth/login", follow_redirects=False)
        assert response.status_code == 302
        fields = parse_qs(urlsplit(response.headers["location"]).query)
        assert fields["code_challenge_method"] == ["S256"]
        assert fields["response_type"] == ["code"]
        self.nonce, self.challenge = fields["nonce"][0], fields["code_challenge"][0]
        return fields["state"][0]

    def login(self, client):
        state = self.start(client)
        response = client.get(
            "/api/auth/callback",
            params={"code": "unit-code", "state": state},
            follow_redirects=False,
        )
        return response


@pytest.fixture
def secured(settings):
    app = create_app(settings, DemoSettings(_env_file=None), enterprise())
    provider = ProtocolProvider()
    with TestClient(app, base_url=ORIGIN) as client:
        app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
            provider.handle
        )
        yield app, client, provider


def auth_headers(client):
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": client.get("/api/auth/me").json()["csrf_token"],
        "Idempotency-Key": uuid.uuid4().hex,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"enabled": True},
        {"enabled": True, "cookie_secret": "short"},
        {
            "enabled": True,
            "cookie_secret": "a" * 32,
            "oidc_client_secret": "unit",
            "oidc_issuer": "http://remote.test",
        },
        {
            "enabled": True,
            "cookie_secret": "a" * 32,
            "oidc_client_secret": "unit",
            "oidc_issuer": ISSUER,
            "public_url": "https://user:secret@bidops.test",
        },
    ],
)
def test_enterprise_requires_complete_secure_configuration(kwargs):
    with pytest.raises(ValidationError):
        EnterpriseSettings(**kwargs)


@pytest.mark.parametrize(
    "path",
    [
        "/sources",
        "/notices",
        "/notices/unknown",
        "/agent/status",
        "/api/sources",
        "/api/notices",
        "/api/notices/unknown",
        "/api/agent/status",
        "/api/sessions",
        "/api/sessions/unknown",
        "/api/audit",
        "/api/metrics",
    ],
)
def test_all_private_routes_and_aliases_require_identity(secured, path):
    _, client, _ = secured
    assert client.get(path).status_code == 401


def test_login_cookie_csrf_logout_and_no_tokens_in_browser(secured):
    app, client, provider = secured
    response = provider.login(client)
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert (
        "httponly" in cookie.lower()
        and "secure" in cookie.lower()
        and "samesite=lax" in cookie.lower()
    )
    identity = client.get("/api/auth/me").json()
    assert identity["authenticated"] and "agent:use" in identity["permissions"]
    assert not any(
        token in json.dumps(identity) + cookie
        for token in ["UNIT_ACCESS_TOKEN", "id_token", "refresh_token"]
    )
    assert client.get("/api/notices").status_code == 200
    assert client.post("/api/auth/logout").status_code == 403
    old_cookie = client.cookies.get("bidops_session")
    assert client.post("/api/auth/logout", headers=auth_headers(client)).status_code == 200
    assert not client.get("/api/auth/me").json()["authenticated"]
    client.cookies.set("bidops_session", old_cookie, domain="bidops.test", path="/")
    assert (
        client.get("/api/notices").status_code == 401
    )  # revoked server-side, even with old cookie
    assert app.state.store.db.rows("SELECT COUNT(*) n FROM enterprise_logins")[0]["n"] == 0


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.test"},
        {"aud": "another-client"},
        {"nonce": "wrong"},
        {"exp": 1},
        {"iat": int(time.time()) + 3600},
        {"sub": ""},
        {"bidops_roles": "admin"},
        {"bidops_roles": []},
        {"bidops_roles": ["superuser"]},
    ],
)
def test_invalid_oidc_claims_fail_closed(secured, claims):
    _, client, provider = secured
    provider.claims = claims
    response = provider.login(client)
    assert response.headers["location"] == "/?login_error=1"
    assert client.get("/api/notices").status_code == 401


def test_invalid_signature_and_discovery_issuer_rejected(secured):
    app, client, provider = secured
    provider.bad_signature = True
    assert "login_error" in provider.login(client).headers["location"]
    app.state.identity.oauth.enterprise.server_metadata["issuer"] = "https://wrong.test"
    assert client.get("/api/auth/login").status_code == 503


def test_state_is_bound_to_browser_single_use_and_expires(secured):
    app, client, provider = secured
    state = provider.start(client)
    state_cookie = client.cookies.get("bidops_session")
    client.cookies.clear()
    assert (
        "login_error"
        in client.get(
            "/api/auth/callback", params={"code": "unit", "state": state}, follow_redirects=False
        ).headers["location"]
    )
    assert provider.token_calls == 0
    # Authlib invalidates the attempted state, including a callback without the browser marker.
    state = provider.start(client)
    state_cookie = client.cookies.get("bidops_session")
    client.cookies.set("bidops_session", state_cookie, domain="bidops.test", path="/")
    response = client.get(
        "/api/auth/callback", params={"code": "unit", "state": state}, follow_redirects=False
    )
    assert response.headers["location"] == "/"
    client.cookies.set("bidops_session", state_cookie, domain="bidops.test", path="/")
    assert (
        "login_error"
        in client.get(
            "/api/auth/callback", params={"code": "unit", "state": state}, follow_redirects=False
        ).headers["location"]
    )
    assert provider.token_calls == 1
    state = provider.start(client)
    app.state.store.db.execute("UPDATE enterprise_oauth_state SET expires_at=0")
    assert (
        "login_error"
        in client.get(
            "/api/auth/callback", params={"code": "unit", "state": state}, follow_redirects=False
        ).headers["location"]
    )
    assert provider.token_calls == 1


@pytest.mark.parametrize(
    "role,chat,audit_access",
    [("viewer", 403, 403), ("analyst", 503, 403), ("auditor", 403, 200), ("admin", 503, 200)],
)
def test_role_matrix(secured, role, chat, audit_access):
    _, client, provider = secured
    provider.roles = [role]
    provider.login(client)
    assert client.get("/api/notices").status_code == 200
    assert client.get("/api/audit").status_code == audit_access
    assert client.get("/api/metrics").status_code == audit_access
    assert (
        client.post(
            "/api/agent/chat", json={"message": "unit"}, headers=auth_headers(client)
        ).status_code
        == chat
    )


def test_csrf_origin_host_expiry_and_size_guards(secured):
    app, client, provider = secured
    provider.login(client)
    good = auth_headers(client)
    for headers in [{}, {**good, "Origin": "https://evil.test"}, {**good, "X-CSRF-Token": "wrong"}]:
        assert (
            client.post("/api/agent/chat", json={"message": "unit"}, headers=headers).status_code
            == 403
        )
    assert client.get("/api/health", headers={"Host": "evil.test"}).status_code == 400
    assert client.post("/api/agent/chat", content="x" * 20000, headers=good).status_code == 413
    app.state.store.db.execute("UPDATE enterprise_logins SET expires_at=0")
    assert client.get("/api/notices").status_code == 401
    assert client.get("/api/health").status_code == 200


@pytest.fixture
def conversations(store, ingest):
    ingest("项目编号：UNIT-SESSION\n项目名称：会话测试语法片段\n预算金额：1元\n")
    calls = []

    def respond(messages, info):
        calls.append(messages)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"response_mode": "out_of_scope", "reason": "off_topic"},
                )
            ],
            model_name="offline-unit",
        )

    service = AgentService(store, DemoSettings(_env_file=None), FunctionModel(respond))
    return PersistentConversations(service, enterprise()), calls


def turn(manager, principal=ALICE, message="单元测试问题", sid=None, key=None):
    return asyncio.run(
        manager.chat(
            principal, ChatRequest(message=message, session_id=sid), key or uuid.uuid4().hex
        )
    )


def test_restart_restores_framework_history_and_response(conversations):
    manager, calls = conversations
    first = turn(manager)
    restarted = PersistentConversations(
        AgentService(
            manager.service.store, DemoSettings(_env_file=None), manager.service.model_override
        ),
        enterprise(),
    )
    second = turn(restarted, sid=first.session_id, message="下一轮单元测试")
    assert second.session_id == first.session_id
    assert len(calls[1]) > len(calls[0])
    assert restarted.history(ALICE, first.session_id)[0]["answer"]["run_id"] == first.run_id
    assert len(restarted.list(ALICE)) == 1
    assert not restarted.list(BOB)


def test_idempotency_replays_without_spending_and_rejects_conflicts(conversations):
    manager, calls = conversations
    key = uuid.uuid4().hex
    first = turn(manager, key=key)
    spend = manager.service.spent()
    assert turn(manager, key=key).run_id == first.run_id
    assert len(calls) == 1 and manager.service.spent() == spend
    with pytest.raises(DemoFailure, match="idempotency_conflict"):
        turn(manager, key=key, message="changed")
    assert len(calls) == 1
    assert turn(manager, principal=BOB, key=key).session_id != first.session_id


def test_cross_user_session_read_write_denied(conversations):
    manager, calls = conversations
    first = turn(manager)
    for action in [
        lambda: manager.history(BOB, first.session_id),
        lambda: turn(manager, principal=BOB, sid=first.session_id),
    ]:
        with pytest.raises(DemoFailure, match="session_not_found"):
            action()
    assert len(calls) == 1


def test_busy_interrupted_and_rejected_requests_do_not_repeat_provider(conversations):
    manager, calls = conversations
    key = uuid.uuid4().hex
    request = ChatRequest(message="单元测试问题")
    sid, _ = manager._claim(ALICE, request, key)
    with pytest.raises(DemoFailure, match="request_in_progress"):
        turn(manager, key=key)
    with pytest.raises(DemoFailure, match="session_busy"):
        turn(manager, sid=sid)
    manager.db.execute("UPDATE enterprise_turns SET created_at=0")
    with pytest.raises(DemoFailure, match="request_interrupted"):
        turn(manager, key=key)
    with pytest.raises(DemoFailure, match="request_interrupted"):
        turn(manager, key=key)
    assert not calls


def test_rate_cap_turn_cap_and_secret_exclusion(conversations):
    manager, calls = conversations
    manager.settings.requests_per_minute = 1
    from limits import RateLimitItemPerMinute

    manager.rate = RateLimitItemPerMinute(1)
    first = turn(manager)
    with pytest.raises(DemoFailure, match="user_rate_limit"):
        turn(manager)
    manager.rate = RateLimitItemPerMinute(100)
    manager.service.settings.max_turns = 1
    with pytest.raises(DemoFailure, match="conversation_limit_reached"):
        turn(manager, sid=first.session_id)
    with pytest.raises(DemoFailure, match="credential_in_message"):
        turn(manager, message="sk-" + "x" * 32)
    assert len(calls) == 1
    assert "sk-" not in json.dumps(manager.db.rows("SELECT * FROM enterprise_turns"))


def test_audit_append_only_redacted_retention_cascades(conversations):
    manager, _ = conversations
    first = turn(manager, message="PRIVATE_UNIT_QUESTION")
    events = manager.db.rows("SELECT * FROM enterprise_audit")
    assert "PRIVATE_UNIT_QUESTION" not in json.dumps(events)
    with pytest.raises(IntegrityError):
        manager.db.execute("DELETE FROM enterprise_audit")
    with pytest.raises(IntegrityError):
        manager.db.execute("UPDATE enterprise_audit SET event='changed'")
    manager.db.execute("UPDATE enterprise_sessions SET updated_at=0")
    assert prune_private_history(manager.db, 30)["deleted_sessions"] == 1
    assert not manager.db.rows("SELECT * FROM enterprise_turns")
    with pytest.raises(DemoFailure):
        manager.history(ALICE, first.session_id)
    assert len(manager.db.rows("SELECT * FROM enterprise_audit")) > len(events)


def test_single_worker_lock_fails_second_worker(settings):
    first = SingleWorker(settings.database_url)
    try:
        with pytest.raises(RuntimeError, match="exactly one worker"):
            SingleWorker(settings.database_url)
    finally:
        first.close()
    SingleWorker(settings.database_url).close()


def test_real_api_owner_isolation_and_completed_replay(secured):
    app, client, provider = secured
    store = app.state.store
    store.ingest(
        store.sources[0].allowed_urls[0],
        "项目编号：UNIT-API\n项目名称：接口测试语法片段\n".encode(),
        manual_attestation=True,
    )
    calls = []

    def respond(messages, info):
        calls.append(1)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "response_mode": "out_of_scope",
                        "reason": "off_topic",
                    },
                )
            ],
            model_name="offline-unit",
        )

    app.state.agent_service.model_override = FunctionModel(respond)
    provider.login(client)
    headers = auth_headers(client)
    first = client.post("/api/agent/chat", json={"message": "UNIT_API_QUESTION"}, headers=headers)
    assert first.status_code == 200
    again = client.post("/api/agent/chat", json={"message": "UNIT_API_QUESTION"}, headers=headers)
    assert again.json()["run_id"] == first.json()["run_id"] and len(calls) == 1
    sid = first.json()["session_id"]
    assert client.get(f"/api/sessions/{sid}").json()["turns"][0]["question"] == "UNIT_API_QUESTION"
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    provider.subject = "bob"
    provider.login(client)
    assert client.get("/api/sessions").json()["items"] == []
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert (
        client.post(
            "/api/agent/chat",
            json={"message": "unit", "session_id": sid},
            headers=auth_headers(client),
        ).status_code
        == 404
    )
    assert len(calls) == 1


def test_concurrent_duplicate_and_cancelled_run_are_not_replayed(conversations):
    manager, _ = conversations

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(messages, info):
            entered.set()
            await release.wait()
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "response_mode": "out_of_scope",
                            "reason": "off_topic",
                        },
                    )
                ],
                model_name="offline-unit",
            )

        manager.service.model_override = FunctionModel(respond)
        request, key = ChatRequest(message="CANCEL_UNIT"), uuid.uuid4().hex
        task = asyncio.create_task(manager.chat(ALICE, request, key))
        await asyncio.wait_for(entered.wait(), timeout=2)
        with pytest.raises(DemoFailure, match="request_in_progress"):
            await manager.chat(ALICE, request, key)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(DemoFailure, match="request_interrupted"):
            await manager.chat(ALICE, request, key)
        assert manager.db.rows("SELECT state FROM enterprise_turns")[0]["state"] == "failed"
        assert manager.service.spent() == 0.25
        assert not manager.service.slots.locked()

    asyncio.run(exercise())


def test_metrics_and_audit_have_bounded_labels_and_no_question_body(secured, caplog):
    app, client, provider = secured
    provider.roles = ["auditor"]
    provider.login(client)
    with caplog.at_level("INFO", logger="bidops.requests"):
        response = client.get("/api/notices/PRIVATE_UNIT_ID?token=PRIVATE_QUERY")
    assert response.status_code == 404 and len(response.headers["x-request-id"]) == 32
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    metrics = client.get("/api/metrics").text
    assert 'route="/notices/{notice_id}"' in metrics
    assert "PRIVATE_UNIT_ID" not in metrics and "PRIVATE_QUERY" not in caplog.text
    audit(app.state.store.db, "unit_audit_event")
    assert client.get("/api/audit?limit=1").json()["items"][0]["event"] == "unit_audit_event"
