"""Durable per-attempt accounting around the same B04 admission rules.

DBOS owns checkpoints. A response received before its checkpoint is reused from
the private business receipt. An attempt with no receipt remains charged at its
reservation; recovery may make another bounded, separately reserved request.
"""

import asyncio
import hashlib
import time
import uuid
from contextvars import ContextVar
from decimal import Decimal

from dbos import DBOS
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel

from bidops.company.authorization import CompanyError
from bidops.qualification.model_budget import GuardedModel
from bidops.qualification.sources import digest

ACTIVE_MODEL = ContextVar("bidops_durable_model")


def summarize_attempts(jobs, run_id, limits, requested_model, offline) -> dict:
    """Read persisted usage without constructing a currently enabled model client."""
    attempts = jobs.list_attempts_system(run_id)
    ledgers = jobs.db.rows(
        "SELECT * FROM agent_runs WHERE question_sha256=:sha",
        {"sha": hashlib.sha256(run_id.encode()).hexdigest()},
    )
    linked = {a["ledger_id"] for a in attempts if a["ledger_id"]}
    orphans = [a for a in ledgers if a["run_id"] not in linked]
    known = [a for a in attempts if a["status"] == "response_received"]
    return {
        "requested_model": requested_model,
        "actual_models": sorted(
            {a["usage"].get("model_name") for a in known if a["usage"].get("model_name")}
        ),
        "model_calls": len(attempts),
        "confirmed_calls": len(known),
        "unknown_calls": len(attempts) - len(known),
        "orphan_reservations": len(orphans),
        "input_tokens": sum(a["usage"].get("input_tokens", 0) for a in known),
        "output_tokens": sum(a["usage"].get("output_tokens", 0) for a in known),
        "estimated_cost_cny": str(
            sum(
                (
                    Decimal(str(a["estimated_cny"] or 0))
                    for a in ledgers
                    if a["state"] == "completed"
                ),
                Decimal(0),
            )
        ),
        "reserved_cost_cny": str(
            sum(
                (Decimal(str(a["reserved_cny"])) for a in ledgers if a["state"] != "completed"),
                Decimal(0),
            )
        ),
        "usage_complete": not orphans
        and len(known) == len(attempts)
        and all(a["state"] == "completed" for a in ledgers)
        and all(
            0 < a["usage"].get("input_tokens", 0) <= 2 * limits.max_request_bytes + 32768
            and 0 < a["usage"].get("output_tokens", 0) <= limits.max_output_tokens
            for a in known
        ),
        "model_identity_complete": bool(known)
        and len(known) == len(attempts)
        and all(
            bool(a["usage"].get("model_name"))
            if offline
            else str(a["usage"].get("model_name", "")).casefold()
            in {"deepseek-flash", "deepseek-v4.1-flash-1017"}
            for a in known
        ),
        "offline": offline,
    }


class JobModelProxy(WrapperModel):
    """One pre-registered Pydantic AI model, with task-local admission state."""

    async def request(self, messages, model_settings, model_request_parameters):
        return await ACTIVE_MODEL.get().request(messages, model_settings, model_request_parameters)


class DurableModel(GuardedModel):
    def __init__(self, *args, jobs, execution_check, fault=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.jobs, self.execution_check, self.fault = jobs, execution_check, fault

    def checkpoint_fault(self, window):
        # Injectable only by the in-process engineering harness, never HTTP/env.
        if self.fault:
            self.fault(window)

    async def request(self, messages, model_settings, model_request_parameters):
        if DBOS.workflow_id != self.run_id + "-calculation" or DBOS.step_id is None:
            raise CompanyError("job_model_requires_framework_step", 503)
        self.execution_check()
        settings, reserve, input_bound = self.prepare_request(
            messages, model_settings, model_request_parameters
        )
        encoded = ModelMessagesTypeAdapter.dump_json(messages)
        encoded += TypeAdapter(ModelRequestParameters).dump_json(model_request_parameters)
        # Framework timestamps are bookkeeping, not fields sent to the provider.
        # They are recreated during workflow replay; preserve user content (even
        # a user-supplied nested timestamp) while excluding only typed metadata.
        transport = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
        for message in transport:
            message.pop("timestamp", None)
            for part in message.get("parts", []):
                part.pop("timestamp", None)
        fingerprint = digest(
            [
                DBOS.workflow_id,
                DBOS.step_id,
                transport,
                TypeAdapter(ModelRequestParameters).dump_python(
                    model_request_parameters, mode="json"
                ),
                settings,
                self.wrapped.model_name,
            ]
        )
        attempts = self.jobs.list_attempts_system(self.run_id)
        recovered = next(
            (
                a
                for a in reversed(attempts)
                if a["request_sha256"] == fingerprint and a["status"] == "response_received"
            ),
            None,
        )
        if recovered is not None:
            if recovered["response"] is None:
                raise CompanyError("job_response_missing", 409)
            response = TypeAdapter(ModelResponse).validate_python(recovered["response"])
            # Reconcile the receipt before the framework records this missing
            # checkpoint. This never admits a second external request.
            self.account_response(response, recovered["ledger_id"], reserve, input_bound)
            self.execution_check()
            return response
        if getattr(self, "deadline", float("inf")) <= time.time():
            raise CompanyError("screening_timeout", 504)
        if hasattr(self, "deadline"):
            settings["request_timeout"] = min(
                settings.get("request_timeout", 30), max(0.001, self.deadline - time.time())
            )
        if len(attempts) >= self.limits.max_requests:
            raise CompanyError("screening_request_limit", 429)
        for attempt in attempts:
            if attempt["status"] == "started":
                self.jobs.finish_attempt(self.run_id, attempt["seq"], unknown=True)
        ledger_id = None
        if not self.offline:
            if not self.limits.live_calls_enabled or not self.limits.ledger_verified:
                raise CompanyError("screening_live_calls_disabled", 503)
            ledger_id = f"qualification-{self.run_id}-{uuid.uuid4().hex}"
            self._reserve(ledger_id, reserve)
        attempt = self.jobs.record_attempt_started(
            self.run_id, request_sha256=fingerprint, ledger_id=ledger_id
        )
        self.checkpoint_fault("before_model")
        try:
            # Only an in-flight provider request consumes this wall-clock
            # deadline. Native checkpoint replay must remain possible after it.
            async with asyncio.timeout(settings.get("request_timeout", 30)):
                response = await self.wrapped.request(messages, settings, model_request_parameters)
        except BaseException as exc:
            self.jobs.finish_attempt(self.run_id, attempt["seq"], unknown=True)
            if ledger_id:
                self.db.execute(
                    "UPDATE agent_runs SET state='unconfirmed',error_code='response_unconfirmed',"
                    "model_requests=1 WHERE run_id=:id",
                    {"id": ledger_id},
                )
            # Never persist provider exception text that can contain private input.
            if isinstance(exc, Exception):
                if isinstance(exc, TimeoutError):
                    raise CompanyError("screening_timeout", 504) from None
                raise CompanyError("screening_model_failed", 503) from None
            raise
        self.checkpoint_fault("response_before_receipt")
        self.jobs.finish_attempt(
            self.run_id,
            attempt["seq"],
            response=TypeAdapter(ModelResponse).dump_python(response, mode="json"),
            usage={
                "model_name": response.model_name,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "offline": self.offline,
            },
        )
        self.checkpoint_fault("receipt_before_accounting")
        self.account_response(response, ledger_id, reserve, input_bound)
        self.checkpoint_fault("accounting_before_checkpoint")
        self.execution_check()
        return response

    def provenance(self):
        attempts = self.jobs.list_attempts_system(self.run_id)
        self.outputs[:] = [a["response"] for a in attempts if a["response"] is not None]
        return summarize_attempts(
            self.jobs, self.run_id, self.limits, self.wrapped.model_name, self.offline
        )
