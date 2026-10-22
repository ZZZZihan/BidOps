"""Per-request admission around Pydantic AI; the existing cumulative ledger is shared.

No replay, retries or scheduling live here. Pydantic AI owns the model loop.
An unconfirmed request permanently retains its conservative reservation.
"""

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from sqlalchemy import text

from bidops.company.authorization import CompanyError
from bidops.enterprise.budget_review import BudgetReviewRequired, require_budget_review


class GuardedModel(WrapperModel):
    def __init__(self, wrapped, *, db, settings, adapter, context, bundle, run_id, offline):
        super().__init__(wrapped)
        self.db, self.limits, self.adapter = db, settings, adapter
        self.context, self.bundle, self.run_id, self.offline = context, bundle, run_id, offline
        self.calls = 0
        self.input_tokens = self.output_tokens = 0
        self.estimated = Decimal(0)
        self.reserved = Decimal(0)
        self.usage_complete = True
        self.actual_models = set()
        self.model_identity_complete = True
        self.outputs = []

    @asynccontextmanager
    async def request_stream(self, *args, **kwargs):
        raise CompanyError("screening_streaming_disabled", 422)
        yield  # pragma: no cover - async context manager protocol, deliberately unreachable

    def _reserve(self, request_id, reserved):
        with self.db.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                require_budget_review(conn, self.limits.ledger_restore_token)
            except BudgetReviewRequired as exc:
                conn.rollback()
                raise CompanyError("screening_budget_review_required", 503) from exc
            spent = conn.execute(
                text(
                    "SELECT COALESCE(SUM(CASE WHEN state='completed' THEN estimated_cny "
                    "ELSE reserved_cny END),0) FROM agent_runs"
                )
            ).scalar_one()
            if Decimal(str(spent)) + reserved > self.limits.budget_cny:
                conn.rollback()
                raise CompanyError("screening_budget_exhausted", 429)
            conn.execute(
                text(
                    "INSERT INTO agent_runs(run_id,created_at,state,question_sha256,reserved_cny) "
                    "VALUES (:id,:at,'started',:sha,:reserved)"
                ),
                {
                    "id": request_id,
                    "at": datetime.now(UTC).isoformat(),
                    "sha": hashlib.sha256(self.run_id.encode()).hexdigest(),
                    "reserved": float(reserved),
                },
            )
            conn.commit()

    async def request(self, messages, model_settings, model_request_parameters):
        self.adapter.recheck(self.context, self.bundle)
        if self.calls >= self.limits.max_requests:
            raise CompanyError("screening_request_limit", 429)
        if model_request_parameters.native_tools:
            raise CompanyError("screening_native_tools_disabled", 422)
        # This text-only endpoint bounds the complete framework request, including
        # instructions/tool schemas and previous tool outputs, not just user text.
        encoded = ModelMessagesTypeAdapter.dump_json(messages)
        encoded += TypeAdapter(ModelRequestParameters).dump_json(model_request_parameters)
        if len(encoded) > self.limits.max_request_bytes:
            raise CompanyError("screening_request_size_limit", 413)
        settings = dict(model_settings or {})
        settings.update(
            max_tokens=self.limits.max_output_tokens,
            max_retries=0,
            parallel_tool_calls=False,
            temperature=0,
        )
        # Two tokens per encoded byte plus a 32Ki-token transport/schema margin.
        # Rates are the official peak cache-miss CNY ceiling, never cache savings.
        input_bound = 2 * self.limits.max_request_bytes + 32768
        reserve = (
            input_bound * self.limits.input_cny_per_million
            + self.limits.max_output_tokens * self.limits.output_cny_per_million
        ) / 1_000_000
        request_id = f"qualification-{self.run_id}-{self.calls + 1}"
        if not self.offline:
            if not self.limits.live_calls_enabled or not self.limits.ledger_verified:
                raise CompanyError("screening_live_calls_disabled", 503)
            self._reserve(request_id, reserve)
            self.reserved += reserve
        self.calls += 1
        try:
            response = await self.wrapped.request(messages, settings, model_request_parameters)
        except BaseException:
            self.usage_complete = False
            if not self.offline:
                self.db.execute(
                    "UPDATE agent_runs SET state='unconfirmed',error_code='response_unconfirmed',"
                    "model_requests=1 WHERE run_id=:id",
                    {"id": request_id},
                )
            raise
        self.outputs.append(TypeAdapter(ModelResponse).dump_python(response, mode="json"))
        if response.model_name:
            self.actual_models.add(response.model_name)
        usage = response.usage
        known = usage.input_tokens > 0 and usage.output_tokens > 0
        known = (
            known
            and usage.input_tokens <= input_bound
            and usage.output_tokens <= self.limits.max_output_tokens
        )
        model_known = bool(response.model_name) and response.model_name.casefold() in {
            "deepseek-flash",
            "deepseek-v4.1-flash-1017",
        }
        self.model_identity_complete = self.model_identity_complete and (
            bool(response.model_name) if self.offline else model_known
        )
        self.usage_complete = self.usage_complete and known
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        estimate = (
            usage.input_tokens * self.limits.input_cny_per_million
            + usage.output_tokens * self.limits.output_cny_per_million
        ) / 1_000_000
        if not self.offline:
            # Missing/implausible usage cannot release a reservation as zero cost.
            self.estimated += estimate
            anomaly = None
            retained = reserve
            if not model_known:
                anomaly = "screening_model_identity_unconfirmed"
                retained = self.limits.budget_cny
            elif not known:
                anomaly = "screening_usage_unconfirmed"
                retained = max(reserve, estimate)
            if anomaly:
                self.usage_complete = False
                self.reserved += retained - reserve
            else:
                self.reserved -= reserve
            self.db.execute(
                "UPDATE agent_runs SET state=:state,estimated_cny=:cost,input_tokens=:inp,"
                "output_tokens=:out,model_requests=1,reserved_cny=:reserved,"
                "error_code=:error WHERE run_id=:id",
                {
                    "id": request_id,
                    "state": "unconfirmed" if anomaly else "completed",
                    "cost": float(estimate),
                    "inp": usage.input_tokens,
                    "out": usage.output_tokens,
                    "reserved": float(retained),
                    "error": anomaly,
                },
            )
            if anomaly:
                raise CompanyError(anomaly, 503)
        self.adapter.recheck(self.context, self.bundle)
        return response

    def provenance(self):
        return {
            "requested_model": self.wrapped.model_name,
            "actual_models": sorted(self.actual_models),
            "model_calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_cny": str(self.estimated),
            "reserved_cost_cny": str(self.reserved),
            "usage_complete": self.usage_complete,
            "offline": self.offline,
            "model_identity_complete": bool(self.actual_models) and self.model_identity_complete,
        }
