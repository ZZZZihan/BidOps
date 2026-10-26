import asyncio
import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import (
    AgentRunError,
    ModelAPIError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelTimeoutError,
    UsageLimitExceeded,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.usage import RunUsage, UsageLimits
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text

from bidops.agent_demo.catalog import Catalog, model_view
from bidops.agent_demo.prompts import INSTRUCTIONS as INSTRUCTIONS
from bidops.agent_demo.prompts import prompt_identity
from bidops.agent_demo.schemas import AnswerPlan, Block, ChatRequest, ChatResponse, Focus, Query
from bidops.enterprise.budget_review import BudgetReviewRequired, require_budget_review
from bidops.storage import Store

CST = ZoneInfo("Asia/Shanghai")


class DemoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BIDOPS_DEMO_", env_file="private/deepseek.env", extra="ignore"
    )
    api_key: SecretStr | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY", repr=False)
    model_name: Literal["deepseek-flash"] = "deepseek-flash"
    enabled: bool = True
    max_run_seconds: float = Field(default=60, gt=0, le=120)
    max_cost_cny: float = Field(default=10, gt=0, le=10)
    budget_restore_token: str | None = None
    max_sessions: int = Field(default=50, ge=1, le=100)
    max_turns: int = Field(default=8, ge=1, le=20)


class DemoFailure(Exception):
    def __init__(self, code: str, status: int = 503):
        self.code, self.status = code, status
        super().__init__(code)


def demo_model_settings() -> OpenAIChatModelSettings:
    return OpenAIChatModelSettings(
        thinking=False,
        extra_body={"thinking": {"type": "disabled"}},
        temperature=0,
        max_tokens=1600,
        max_retries=0,
        parallel_tool_calls=False,
        request_timeout=25,
    )


def demo_usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=6,
        tool_calls_limit=8,
        input_tokens_limit=40000,
        output_tokens_limit=8000,
    )


@dataclass
class Session:
    episodes: list[list] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class Dependencies:
    catalog: Catalog
    now: datetime
    results: dict[str, Block] = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)

    def record(self, name: str, args: dict, block: Block) -> dict:
        block.id = f"r{len(self.results) + 1}"
        self.results[block.id] = block
        self.trace.append(
            {
                "tool": name,
                "arguments": args,
                "result_id": block.id,
                "matches": block.total,
                "state": "completed",
            }
        )
        return model_view(block)


def build_agent(model: Model) -> Agent:
    agent = Agent(
        model,
        deps_type=Dependencies,
        output_type=AnswerPlan,
        instructions=INSTRUCTIONS,
        retries=1,
        tool_timeout=5,
        model_settings=demo_model_settings(),
    )

    @agent.instructions
    def date_context(ctx: RunContext[Dependencies]) -> str:
        return f"本轮参考时间：{ctx.deps.now.isoformat()}。数据是历史抓取快照，不是实时网站状态。"

    @agent.tool
    def search_notices(ctx: RunContext[Dependencies], query: Query) -> dict:
        """按关键词、元预算范围、投标截止时间搜索本地公告；排序/limit 可用于最高最低或列表。"""
        try:
            block = ctx.deps.catalog.search(query, ctx.deps.now)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return ctx.deps.record("search_notices", query.model_dump(mode="json"), block)

    @agent.tool
    def read_notice(
        ctx: RunContext[Dependencies], project_code: str, focus: Focus = "overview", offset: int = 0
    ) -> dict:
        """用完整项目编号读取公告。focus 可选预算、截止、采购包、采购单位、资格原文或文件截止。"""
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", project_code):
            raise ModelRetry("先搜索得到真实项目编号，不接受 URL 或代码。")
        return ctx.deps.record(
            "read_notice",
            {"project_code": project_code, "focus": focus, "offset": offset},
            ctx.deps.catalog.read(project_code, focus, offset=offset),
        )

    @agent.tool
    def compare_notices(
        ctx: RunContext[Dependencies],
        project_codes: list[str],
        metric: Literal["budget", "deadline", "packages"] = "budget",
    ) -> dict:
        """比较 2–6 个不同项目的预算/截止时间/包数；工具返回精确金额差额和合计。"""
        try:
            block = ctx.deps.catalog.compare(project_codes, metric)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return ctx.deps.record(
            "compare_notices", {"project_codes": project_codes, "metric": metric}, block
        )

    @agent.output_validator
    def validate_answer(ctx: RunContext[Dependencies], answer: AnswerPlan) -> AnswerPlan:
        if len(set(answer.result_ids)) != len(answer.result_ids):
            raise ModelRetry("结果 ID 不得重复。")
        if any(key not in ctx.deps.results for key in answer.result_ids):
            raise ModelRetry("引用必须来自本轮已完成的工具结果。")
        if answer.response_mode in {"results", "review_required"} and not answer.result_ids:
            raise ModelRetry("数据回答必须先调用工具并引用结果。")
        blocks = [ctx.deps.results[key] for key in answer.result_ids]
        if answer.response_mode == "results":
            if not any(b.items for b in blocks) or any(b.missing_project_codes for b in blocks):
                raise ModelRetry("没有完整匹配结果，应说明不可确定或没有匹配。")
            if answer.focus in {"eligibility", "status"}:
                raise ModelRetry("资格与最新状态必须标记 review_required。")
            if any(
                (b.operation == "read" and b.total > 1)
                or len({c.project_code for c in b.items}) < len(b.items)
                for b in blocks
            ):
                raise ModelRetry("同一项目有多个历史版本，必须 review_required。")
            if answer.focus == "documents" and all(
                c.document_deadline_at is None for b in blocks for c in b.items
            ):
                raise ModelRetry("文件截止时间未确定，必须 unavailable。")
        if answer.response_mode in {"out_of_scope", "clarify"} and answer.result_ids:
            raise ModelRetry("拒绝或澄清无需附带不相关的数据结果。")
        return answer

    return agent


MESSAGES = {
    "results": "已根据已导入公告找到以下结果。字段和引用均来自保存的原文。",
    "clarify": "请补充项目编号或名称，并说明你想查询的字段。",
    "unavailable": "现有数据无法确认这项信息。请查看缺失状态和原文，补充材料后再核对。",
    "review_required": "已列出相关原文。资格与当前状态需要人工核对，不能据此直接认定可以投标。",
    "out_of_scope": "当前助手仅查询已导入的采购公告，不能执行投标、登录、抓取或读取凭证。",
}


class AgentService:
    def __init__(
        self, store: Store, settings: DemoSettings | None = None, model: Model | None = None
    ):
        self.store, self.settings = store, settings or DemoSettings()
        self.sessions: dict[str, Session] = {}
        self.slots = asyncio.Semaphore(2)
        self.model_override = model
        self.agent = None

    def status(self) -> dict:
        return {
            "configured": bool(self.settings.api_key or self.model_override),
            "enabled": self.settings.enabled,
            "model": self.settings.model_name,
            "framework": "Pydantic AI",
            "read_only": True,
            "notice_count": self.store.db.rows("SELECT COUNT(*) n FROM notices")[0]["n"],
            "budget_limit_cny": self.settings.max_cost_cny,
            "estimated_spend_cny": self.spent(),
            "context_turns_retained": 2,
        }

    def spent(self) -> float:
        return self.store.db.rows(
            "SELECT COALESCE(SUM(CASE WHEN state='completed' THEN estimated_cny "
            "ELSE reserved_cny END),0) total FROM agent_runs"
        )[0]["total"]

    def reserve(self, run_id: str, message: str):
        # BEGIN IMMEDIATE prevents concurrent workers from spending the same reservation.
        with self.store.db.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                require_budget_review(conn, self.settings.budget_restore_token)
            except BudgetReviewRequired as exc:
                conn.rollback()
                raise DemoFailure("budget_review_required", 503) from exc
            spent = conn.execute(
                text(
                    "SELECT COALESCE(SUM(CASE WHEN state='completed' THEN estimated_cny "
                    "ELSE reserved_cny END),0) FROM agent_runs"
                )
            ).scalar_one()
            if spent + 0.25 > self.settings.max_cost_cny:
                conn.rollback()
                raise DemoFailure("budget_limit_reached", 429)
            conn.execute(
                text(
                    "INSERT INTO agent_runs(run_id,created_at,state,question_sha256,reserved_cny) "
                    "VALUES (:id,:at,'started',:sha,0.25)"
                ),
                {
                    "id": run_id,
                    "at": datetime.now(UTC).isoformat(),
                    "sha": hashlib.sha256(message.encode()).hexdigest(),
                },
            )
            conn.commit()

    def validate_request(self, request: ChatRequest):
        if not self.settings.enabled:
            raise DemoFailure("agent_disabled")
        if not self.settings.api_key and self.model_override is None:
            raise DemoFailure("deepseek_key_missing")
        if re.search(r"sk-[A-Za-z0-9]{16,}", request.message):
            raise DemoFailure("credential_in_message", 422)

    async def chat(self, request: ChatRequest, *, now: datetime | None = None) -> ChatResponse:
        self.validate_request(request)
        for key, value in list(self.sessions.items()):
            if not value.lock.locked() and time.monotonic() - value.touched > 3600:
                del self.sessions[key]
        session_id = request.session_id or uuid.uuid4().hex
        if request.session_id and session_id not in self.sessions:
            raise DemoFailure("session_expired", 404)
        if session_id not in self.sessions:
            if len(self.sessions) >= self.settings.max_sessions:
                raise DemoFailure("session_capacity_reached", 429)
            self.sessions[session_id] = Session()
        session = self.sessions[session_id]
        if session.lock.locked():
            raise DemoFailure("session_busy", 409)
        if len(session.episodes) >= self.settings.max_turns:
            raise DemoFailure("conversation_limit_reached", 429)
        if self.slots.locked():
            raise DemoFailure("agent_busy", 429)
        async with session.lock, self.slots:
            session.touched = time.monotonic()
            return await self._run(request.message, session_id, session, now or datetime.now(CST))

    async def _run(
        self, message: str, session_id: str, session: Session, now: datetime
    ) -> ChatResponse:
        run_id, started = uuid.uuid4().hex, time.monotonic()
        try:
            catalog = Catalog(self.store)
        except (ValueError, OSError):
            raise DemoFailure("source_integrity_failed") from None
        if not catalog.count:
            raise DemoFailure("empty_catalog", 409)
        self.reserve(run_id, message)
        usage = RunUsage()
        deps = Dependencies(catalog, now)
        try:
            if self.agent is None:
                model = self.model_override or OpenAIChatModel(
                    self.settings.model_name,
                    provider=DeepSeekProvider(api_key=self.settings.api_key.get_secret_value()),
                )
                self.agent = build_agent(model)
            history = [m for episode in session.episodes[-2:] for m in episode]
            result = await self.agent.run(
                message,
                deps=deps,
                message_history=history,
                usage=usage,
                timeout=self.settings.max_run_seconds,
                usage_limits=demo_usage_limits(),
            )
            session.episodes.append(result.new_messages())
            plan = result.output
            blocks = [deps.results[key] for key in plan.result_ids]
            responses = [m for m in result.new_messages() if getattr(m, "kind", None) == "response"]
            returned_model = next(
                (m.model_name for m in reversed(responses) if m.model_name),
                None,
            )
            actual_model = returned_model or self.settings.model_name
            estimated = round((usage.input_tokens * 2 + usage.output_tokens * 8) / 1_000_000, 6)
            self.store.db.execute(
                "UPDATE agent_runs SET state='completed', estimated_cny=:cost,input_tokens=:inp,"
                "output_tokens=:out,model_requests=:requests WHERE run_id=:id",
                {
                    "cost": estimated,
                    "inp": usage.input_tokens,
                    "out": usage.output_tokens,
                    "requests": usage.requests,
                    "id": run_id,
                },
            )
            response = ChatResponse(
                run_id=run_id,
                session_id=session_id,
                response_mode=plan.response_mode,
                focus=plan.focus,
                reason=plan.reason,
                message=MESSAGES[plan.response_mode],
                blocks=blocks,
                trace=deps.trace,
                model=actual_model,
                prompt=prompt_identity(),
                provider_executed=self.model_override is None,
                usage={
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "model_requests": usage.requests,
                    "tool_calls": usage.tool_calls,
                    "estimated_cost_cny_peak": estimated,
                },
                elapsed_ms=round((time.monotonic() - started) * 1000),
                as_of=now.isoformat(),
                warnings=[
                    "仅查询本地快照；未联网核查更正或终止。",
                    "附件与实际投标资格需要人工复核。",
                ]
                + (
                    ["本次结果未展示全部匹配记录；请按分页信息继续读取并核对其他版本。"]
                    if any(deps.results[key].truncated for key in result.output.result_ids)
                    else []
                ),
            )
            self.store.db.audit(
                "agent_run",
                "completed",
                run_id=run_id,
                model=actual_model,
                model_configured=self.settings.model_name,
                model_returned=returned_model,
                prompt=prompt_identity(),
                elapsed_ms=response.elapsed_ms,
                cost_state="estimated_completed",
                tool_count=len(deps.trace),
                **response.usage,
            )
            return response
        except asyncio.CancelledError:
            self._failed(run_id, "cancelled", usage, started=started)
            raise
        except Exception as exc:
            if isinstance(exc, UsageLimitExceeded):
                code = "run_usage_limit"
            elif isinstance(exc, (TimeoutError, ModelTimeoutError)):
                code = "provider_timeout"
            elif isinstance(exc, ModelQuotaExceededError):
                code = "provider_quota_exhausted"
            elif isinstance(exc, ModelRateLimitError):
                code = "provider_rate_limited"
            elif isinstance(exc, ModelAPIError):
                status = getattr(exc, "status_code", None)
                code = {
                    401: "provider_auth_failed",
                    402: "provider_quota_exhausted",
                    429: "provider_rate_limited",
                }.get(status, "provider_unavailable")
            elif isinstance(exc, AgentRunError):
                code = "agent_output_invalid"
            else:
                code = "agent_internal_error"
            self._failed(run_id, code, usage, started=started)
            raise DemoFailure(code) from None

    def _failed(self, run_id: str, code: str, usage: RunUsage, *, started: float):
        self.store.db.execute(
            "UPDATE agent_runs SET state='failed',error_code=:code,input_tokens=:inp,"
            "output_tokens=:out,model_requests=:requests WHERE run_id=:id",
            {
                "code": code,
                "inp": usage.input_tokens,
                "out": usage.output_tokens,
                "requests": usage.requests,
                "id": run_id,
            },
        )
        self.store.db.audit(
            "agent_run",
            code,
            run_id=run_id,
            prompt=prompt_identity(),
            model_configured=self.settings.model_name,
            model_returned=None,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model_requests=usage.requests,
            tool_calls=usage.tool_calls,
            cost_state="reservation_retained",
            reserved_cost_cny=0.25,
        )

    async def close(self):
        if self.agent and self.model_override is None:
            client = getattr(self.agent.model, "client", None)
            if client:
                await client.close()
