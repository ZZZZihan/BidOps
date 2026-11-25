"""Offline prompt provenance checks over authored UNIT inputs and temporary stores."""

import asyncio
import hashlib
import json
from datetime import datetime

import pytest
from bidops.agent_demo import prompts, runtime
from bidops.agent_demo.runtime import AgentService, DemoFailure, DemoSettings
from bidops.agent_demo.schemas import ChatRequest, ChatResponse
from bidops.enterprise.auth import Principal
from bidops.enterprise.config import EnterpriseSettings
from bidops.enterprise.sessions import PersistentConversations
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

NOW = datetime.fromisoformat("2026-10-22T12:00:00+08:00")
ORIGINAL_PROMPT_SHA256 = "cd2abb4a635a9523a92fa259491feb2707bbea9e9b728d622adb9c3babad149e"
QUESTION = "UNIT-PROVENANCE 的预算；UNIT_PRIVATE_QUESTION_MARKER"
SOURCE_MARKER = "UNIT_PRIVATE_SOURCE_MARKER"
PROVIDER_BODY = "UNIT_PRIVATE_PROVIDER_BODY_DO_NOT_LOG"
UNIT = f"""项目编号：UNIT-PROVENANCE
项目名称：溯源测试语法片段 {SOURCE_MARKER}
采购人名称：单元测试采购人
预算金额：1元
提交投标文件截止时间：2026年11月6日08点30分（北京时间）
"""


@pytest.fixture
def provenance_store(store, ingest):
    ingest(UNIT)
    return store


def settings(**overrides):
    # Explicit None prevents tests from depending on an ambient provider credential.
    return DemoSettings(_env_file=None, DEEPSEEK_API_KEY=None, **overrides)


def successful_model(calls):
    def respond(messages, info):
        calls.append(info)
        if len(calls) == 1:
            part = ToolCallPart(
                "read_notice", {"project_code": "UNIT-PROVENANCE", "focus": "budget"}
            )
        else:
            part = ToolCallPart(
                info.output_tools[0].name,
                {"response_mode": "results", "result_ids": ["r1"], "focus": "budget"},
            )
        return ModelResponse(parts=[part], model_name="offline-provenance-unit")

    return FunctionModel(respond, model_name="offline-provenance-unit")


def audit_event(store):
    rows = store.db.rows("SELECT * FROM audit_events WHERE event='agent_run'")
    assert len(rows) == 1
    row = rows[0]
    return row, json.loads(row["metadata_json"])


def assert_private_content_absent(row):
    serialized = json.dumps(row, ensure_ascii=False)
    for value in (QUESTION, "UNIT_PRIVATE_QUESTION_MARKER", SOURCE_MARKER, PROVIDER_BODY, UNIT):
        assert value not in serialized
    assert prompts.INSTRUCTIONS not in serialized
    assert prompts.INSTRUCTIONS.splitlines()[0] not in serialized


def assert_failed_receipt(service, expected_code):
    store = service.store
    ledger = store.db.rows("SELECT * FROM agent_runs")
    assert len(ledger) == 1
    run = ledger[0]
    assert run["state"] == "failed"
    assert run["error_code"] == expected_code
    assert run["reserved_cny"] == 0.25
    assert service.spent() == 0.25
    row, metadata = audit_event(store)
    assert row["code"] == expected_code
    assert row["source_url"] is None
    assert metadata["run_id"] == run["run_id"]
    assert metadata["prompt"] == prompts.prompt_identity()
    assert metadata["model_configured"] == service.settings.model_name
    assert metadata["model_returned"] is None
    assert metadata["cost_state"] == "reservation_retained"
    assert metadata["reserved_cost_cny"] == 0.25
    assert isinstance(metadata["elapsed_ms"], int) and metadata["elapsed_ms"] >= 0
    for key in ("input_tokens", "output_tokens", "model_requests", "tool_calls"):
        assert isinstance(metadata[key], int) and metadata[key] >= 0
    assert_private_content_absent(row)


def test_prompt_migration_keeps_original_static_bytes_and_import_interface():
    assert runtime.INSTRUCTIONS is prompts.INSTRUCTIONS
    assert hashlib.sha256(prompts.INSTRUCTIONS.encode("utf-8")).hexdigest() == (
        ORIGINAL_PROMPT_SHA256
    )
    assert prompts.prompt_identity() == {
        "name": "bidops-notice-qa",
        "version": "notice-qa-v1",
        "owner": "repository-maintainers",
        "sha256": ORIGINAL_PROMPT_SHA256,
    }


def test_success_binds_model_instructions_receipt_and_sanitized_audit(provenance_store):
    calls = []
    service = AgentService(provenance_store, settings(), successful_model(calls))
    response = asyncio.run(service.chat(ChatRequest(message=QUESTION), now=NOW))

    assert len(calls) == 2
    for info in calls:
        assert prompts.INSTRUCTIONS.strip() in info.instructions
        assert f"本轮参考时间：{NOW.isoformat()}" in info.instructions
    assert response.prompt.model_dump() == prompts.prompt_identity()
    assert response.model == "offline-provenance-unit" and not response.provider_executed
    assert SOURCE_MARKER in response.blocks[0].items[0].title
    row, metadata = audit_event(provenance_store)
    assert row["code"] == "completed"
    assert row["source_url"] is None
    assert metadata["run_id"] == response.run_id
    assert metadata["prompt"] == response.prompt.model_dump()
    assert metadata["model_configured"] == service.settings.model_name
    assert metadata["model_returned"] == response.model
    assert metadata["elapsed_ms"] == response.elapsed_ms
    assert metadata["cost_state"] == "estimated_completed"
    assert metadata["tool_count"] == len(response.trace) == 1
    for key, value in response.usage.items():
        assert metadata[key] == value
    assert_private_content_absent(row)


@pytest.mark.parametrize("failure", ["provider_error", "timeout"])
def test_failure_keeps_reservation_and_sanitized_prompt_metadata(provenance_store, failure):
    async def respond(messages, info):
        if failure == "provider_error":
            raise ModelHTTPError(500, "offline-provenance-unit", body=PROVIDER_BODY)
        await asyncio.Event().wait()

    service = AgentService(
        provenance_store,
        settings(max_run_seconds=0.02 if failure == "timeout" else 60),
        FunctionModel(respond),
    )
    expected = "provider_timeout" if failure == "timeout" else "provider_unavailable"
    with pytest.raises(DemoFailure, match=f"^{expected}$") as error:
        asyncio.run(service.chat(ChatRequest(message=QUESTION), now=NOW))
    assert PROVIDER_BODY not in str(error.value)
    assert_failed_receipt(service, expected)


def test_cancellation_keeps_reservation_and_prompt_metadata(provenance_store):
    async def exercise():
        entered = asyncio.Event()

        async def respond(messages, info):
            entered.set()
            await asyncio.Event().wait()

        service = AgentService(provenance_store, settings(), FunctionModel(respond))
        task = asyncio.create_task(service.chat(ChatRequest(message=QUESTION), now=NOW))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return service

    assert_failed_receipt(asyncio.run(exercise()), "cancelled")


def test_persisted_legacy_receipt_replays_without_assigning_current_prompt(provenance_store):
    calls = []
    service = AgentService(provenance_store, settings(), successful_model(calls))
    manager = PersistentConversations(service, EnterpriseSettings(enabled=False))
    principal = Principal("unit-provenance-owner", "Unit", ("analyst",), "unit-csrf")
    request, key = ChatRequest(message=QUESTION), "1" * 32
    first = asyncio.run(manager.chat(principal, request, key))
    old_payload = first.model_dump(mode="json", exclude={"prompt"})
    assert ChatResponse.model_validate(old_payload).prompt is None
    serialized = json.dumps(old_payload, ensure_ascii=False)
    provenance_store.db.execute(
        "UPDATE enterprise_turns SET response_json=:response "
        "WHERE owner_id=:owner AND request_id=:key",
        {"response": serialized, "owner": principal.owner_id, "key": key},
    )
    spend_before = service.spent()
    calls_before = len(calls)

    replayed = asyncio.run(manager.chat(principal, request, key))

    assert replayed.run_id == first.run_id and replayed.prompt is None
    assert replayed.model_dump(mode="json", exclude={"prompt"}) == old_payload
    assert len(calls) == calls_before and service.spent() == spend_before
    saved = provenance_store.db.rows(
        "SELECT response_json FROM enterprise_turns WHERE request_id=:key", {"key": key}
    )[0]["response_json"]
    assert saved == serialized
    assert len(provenance_store.db.rows("SELECT run_id FROM agent_runs")) == 1
