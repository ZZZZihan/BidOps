import asyncio
import json
import re
import time
import uuid
from datetime import datetime

from limits import RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from pydantic_ai.messages import ModelMessagesTypeAdapter
from sqlalchemy import text

from bidops.agent_demo.runtime import CST, AgentService, DemoFailure, Session
from bidops.agent_demo.schemas import ChatRequest, ChatResponse
from bidops.enterprise.auth import Principal, audit, digest
from bidops.enterprise.config import EnterpriseSettings


class PersistentConversations:
    """Persistence/idempotency adapter around the existing framework-managed agent run.

    A lost in-flight run is never replayed automatically: provider billing may have happened.
    This is bounded synchronous execution, not a homegrown durable workflow engine.
    """

    def __init__(self, service: AgentService, settings: EnterpriseSettings):
        self.service, self.db, self.settings = service, service.store.db, settings
        self.limiter = FixedWindowRateLimiter(MemoryStorage())
        self.rate = RateLimitItemPerMinute(settings.requests_per_minute)

    def list(self, principal: Principal):
        return self.db.rows(
            "SELECT s.session_id,s.created_at,s.updated_at,COUNT(t.request_id) turns "
            "FROM enterprise_sessions s LEFT JOIN enterprise_turns t ON t.session_id=s.session_id "
            "WHERE s.owner_id=:owner GROUP BY s.session_id ORDER BY s.updated_at DESC LIMIT 50",
            {"owner": principal.owner_id},
        )

    def history(self, principal: Principal, session_id: str):
        self._owned(principal, session_id)
        rows = self.db.rows(
            "SELECT question,state,error_code,response_json FROM enterprise_turns "
            "WHERE owner_id=:owner AND session_id=:sid ORDER BY created_at,request_id",
            {"owner": principal.owner_id, "sid": session_id},
        )
        return [
            {
                "question": row["question"],
                "state": row["state"],
                "error": row["error_code"],
                "answer": json.loads(row["response_json"]) if row["response_json"] else None,
            }
            for row in rows
        ]

    def _owned(self, principal: Principal, session_id: str):
        if not self.db.rows(
            "SELECT 1 FROM enterprise_sessions WHERE session_id=:sid AND owner_id=:owner",
            {"sid": session_id, "owner": principal.owner_id},
        ):
            raise DemoFailure("session_not_found", 404)

    def _claim(self, principal: Principal, request: ChatRequest, key: str):
        now = time.time()
        fingerprint = digest(request.model_dump_json())
        with self.db.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            params = {"owner": principal.owner_id, "key": key}
            previous = (
                conn.execute(
                    text(
                        "SELECT * FROM enterprise_turns WHERE owner_id=:owner AND request_id=:key"
                    ),
                    params,
                )
                .mappings()
                .first()
            )
            if previous:
                if previous["request_sha256"] != fingerprint:
                    raise DemoFailure("idempotency_conflict", 409)
                if previous["state"] == "completed":
                    return previous["session_id"], ChatResponse.model_validate_json(
                        previous["response_json"]
                    )
                if previous["state"] == "failed":
                    raise DemoFailure(previous["error_code"], previous["error_status"])
                if previous["created_at"] + self.service.settings.max_run_seconds + 30 > now:
                    raise DemoFailure("request_in_progress", 409)
                conn.execute(
                    text(
                        "UPDATE enterprise_turns SET state='failed',"
                        "error_code='request_interrupted',"
                        "error_status=503 WHERE owner_id=:owner AND request_id=:key"
                    ),
                    params,
                )
                conn.commit()
                raise DemoFailure("request_interrupted", 503)
            sid = request.session_id or uuid.uuid4().hex
            params.update(sid=sid, now=now, until=now + self.service.settings.max_run_seconds + 30)
            if request.session_id:
                row = (
                    conn.execute(
                        text(
                            "SELECT * FROM enterprise_sessions "
                            "WHERE session_id=:sid AND owner_id=:owner"
                        ),
                        params,
                    )
                    .mappings()
                    .first()
                )
                if not row:
                    raise DemoFailure("session_not_found", 404)
                if row["lock_until"] > now:
                    raise DemoFailure("session_busy", 409)
            else:
                count = conn.execute(
                    text("SELECT COUNT(*) FROM enterprise_sessions WHERE owner_id=:owner"), params
                ).scalar_one()
                if count >= self.service.settings.max_sessions:
                    raise DemoFailure("session_capacity_reached", 429)
                conn.execute(
                    text(
                        "INSERT INTO enterprise_sessions"
                        "(session_id,owner_id,created_at,updated_at) "
                        "VALUES (:sid,:owner,:now,:now)"
                    ),
                    params,
                )
            count = conn.execute(
                text("SELECT COUNT(*) FROM enterprise_turns WHERE session_id=:sid"), params
            ).scalar_one()
            if count >= self.service.settings.max_turns:
                raise DemoFailure("conversation_limit_reached", 429)
            # Earlier interrupted turns become explicit failed history, never fabricated success.
            conn.execute(
                text(
                    "UPDATE enterprise_turns SET state='failed',error_code='request_interrupted',"
                    "error_status=503 WHERE session_id=:sid AND state='started'"
                ),
                params,
            )
            conn.execute(
                text(
                    "UPDATE enterprise_sessions SET lock_until=:until,updated_at=:now "
                    "WHERE session_id=:sid"
                ),
                params,
            )
            conn.execute(
                text(
                    "INSERT INTO enterprise_turns(owner_id,request_id,session_id,request_sha256,"
                    "question,created_at,state) "
                    "VALUES (:owner,:key,:sid,:sha,:question,:now,'started')"
                ),
                dict(params, sha=fingerprint, question=request.message),
            )
            conn.commit()
            return sid, None

    async def chat(
        self, principal: Principal, request: ChatRequest, key: str | None
    ) -> ChatResponse:
        if not key or not re.fullmatch(r"[a-f0-9]{32}", key):
            raise DemoFailure("idempotency_key_required", 422)
        self.service.validate_request(request)
        if not self.limiter.hit(self.rate, principal.owner_id):
            raise DemoFailure("user_rate_limit", 429)
        if self.service.slots.locked():
            raise DemoFailure("agent_busy", 429)
        sid, cached = self._claim(principal, request, key)
        if cached:
            audit(
                self.db,
                "request_replayed",
                principal.owner_id,
                request_id=key,
                run_id=cached.run_id,
            )
            return cached
        audit(self.db, "request_started", principal.owner_id, request_id=key, session_id=sid)
        try:
            episodes = self.db.rows(
                "SELECT messages_json FROM enterprise_turns "
                "WHERE session_id=:sid AND state='completed' "
                "ORDER BY created_at DESC,request_id DESC LIMIT 2",
                {"sid": sid},
            )
            session = Session(
                episodes=[
                    ModelMessagesTypeAdapter.validate_json(r["messages_json"])
                    for r in reversed(episodes)
                ]
            )
            async with self.service.slots:
                answer = await self.service._run(request.message, sid, session, datetime.now(CST))
            with self.db.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE enterprise_turns SET state='completed',response_json=:response,"
                        "messages_json=:messages WHERE owner_id=:owner AND request_id=:key"
                    ),
                    {
                        "response": answer.model_dump_json(),
                        "messages": ModelMessagesTypeAdapter.dump_json(
                            session.episodes[-1]
                        ).decode(),
                        "owner": principal.owner_id,
                        "key": key,
                    },
                )
                conn.execute(
                    text(
                        "UPDATE enterprise_sessions SET lock_until=0,updated_at=:now "
                        "WHERE session_id=:sid"
                    ),
                    {"now": time.time(), "sid": sid},
                )
            audit(
                self.db,
                "request_completed",
                principal.owner_id,
                request_id=key,
                run_id=answer.run_id,
                model=answer.model,
                **answer.usage,
            )
            return answer
        except BaseException as exc:
            code = exc.code if isinstance(exc, DemoFailure) else "request_interrupted"
            status = exc.status if isinstance(exc, DemoFailure) else 503
            with self.db.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE enterprise_turns SET state='failed',"
                        "error_code=:code,error_status=:status "
                        "WHERE owner_id=:owner AND request_id=:key AND state='started'"
                    ),
                    {"code": code, "status": status, "owner": principal.owner_id, "key": key},
                )
                conn.execute(
                    text("UPDATE enterprise_sessions SET lock_until=0 WHERE session_id=:sid"),
                    {"sid": sid},
                )
            audit(self.db, "request_failed", principal.owner_id, request_id=key, code=code)
            if isinstance(
                exc, (DemoFailure, asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                raise
            raise DemoFailure(code, status) from None


def prune_private_history(db, days: int) -> dict:
    now = time.time()
    with db.engine.begin() as conn:
        sessions = conn.execute(
            text("DELETE FROM enterprise_sessions WHERE updated_at<:cutoff AND lock_until<:now"),
            {"cutoff": now - days * 86400, "now": now},
        ).rowcount
        conn.execute(text("DELETE FROM enterprise_logins WHERE expires_at<:now"), {"now": now})
        conn.execute(text("DELETE FROM enterprise_oauth_state WHERE expires_at<:now"), {"now": now})
    audit(db, "retention_pruned", sessions=sessions, retention_days=days)
    return {"deleted_sessions": sessions, "retention_days": days}
