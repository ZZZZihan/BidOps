"""Local FunctionModel calls against the real shared SQLite ledger; no provider I/O."""

import asyncio
import hashlib
import json
import sqlite3
from decimal import Decimal

import pytest
from bidops.agent_demo.runtime import AgentService, DemoFailure, DemoSettings
from bidops.company.authorization import CompanyError
from bidops.db import Database
from bidops.enterprise.backup import backup, restore
from bidops.enterprise.budget_review import (
    BudgetReviewRequired,
    install_budget_review_barrier,
    require_budget_review,
)
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.model_budget import GuardedModel
from bidops.storage import Store
from pydantic import ValidationError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage
from test_company_access import ALICE, BOB
from test_company_access import domain as _domain_fixture
from test_qualification_sources import sources as _sources_fixture

domain = _domain_fixture
sources = _sources_fixture


class LocalProvider(FunctionModel):
    """An actual framework FunctionModel with independently controlled wire metadata."""

    def __init__(self, *, returned_model="deepseek-flash", tokens=(100, 10), effect=None):
        self.seen_settings = []
        self.returned_model, self.tokens, self.effect = returned_model, tokens, effect
        super().__init__(self.answer, model_name="deepseek-flash")

    def answer(self, messages, info):
        self.seen_settings.append(dict(info.model_settings or {}))
        if self.effect:
            self.effect()
        return ModelResponse(parts=[TextPart("SYNTHETIC LOCAL RESPONSE")])

    async def request(self, messages, model_settings, model_request_parameters):
        response = await super().request(messages, model_settings, model_request_parameters)
        # FunctionModel normally fills model/usage itself. These simulate provider-returned
        # metadata after that framework step, including missing and implausible usage.
        response.model_name = self.returned_model
        response.usage = RequestUsage(input_tokens=self.tokens[0], output_tokens=self.tokens[1])
        return response


class OpenAdapter:
    def recheck(self, context, bundle):
        pass


def limits(**updates):
    return ScreeningSettings(
        _env_file=None,
        enabled=True,
        live_calls_enabled=True,
        ledger_verified=True,
        **updates,
    )


def guard(
    store,
    provider=None,
    *,
    config=None,
    run_id="local",
    adapter=None,
    context=None,
    bundle=None,
    offline=False,
):
    return GuardedModel(
        provider or LocalProvider(),
        db=store.db,
        settings=config or limits(),
        adapter=adapter or OpenAdapter(),
        context=context,
        bundle=bundle,
        run_id=run_id,
        offline=offline,
    )


def invoke(model, *, messages=None, parameters=None, settings=None):
    return asyncio.run(
        model.request(
            messages or [ModelRequest(parts=[UserPromptPart("SYNTHETIC")])],
            settings,
            parameters or ModelRequestParameters(),
        )
    )


def reserve_for(config):
    return (
        (2 * config.max_request_bytes + 32768) * config.input_cny_per_million
        + config.max_output_tokens * config.output_cny_per_million
    ) / 1_000_000


def ledger(store):
    return store.db.rows("SELECT * FROM agent_runs ORDER BY run_id")


def add_legacy(store, run_id, *, state, reserved, estimated):
    store.db.execute(
        "INSERT INTO agent_runs(run_id,created_at,state,question_sha256,"
        "reserved_cny,estimated_cny) "
        "VALUES (:id,'2026-01-01T00:00:00Z',:state,:sha,:reserved,:estimated)",
        {
            "id": run_id,
            "state": state,
            "sha": "a" * 64,
            "reserved": reserved,
            "estimated": estimated,
        },
    )


def test_success_records_actual_usage_and_forces_bounded_provider_settings(store):
    provider = LocalProvider(returned_model="deepseek-v4.1-flash-1017")
    model = guard(store, provider)
    response = invoke(
        model,
        settings={
            "max_tokens": 999999,
            "max_retries": 8,
            "temperature": 2,
            "parallel_tool_calls": True,
        },
    )
    assert response.model_name == "deepseek-v4.1-flash-1017"
    (row,) = ledger(store)
    assert row["state"] == "completed"
    assert (row["input_tokens"], row["output_tokens"], row["model_requests"]) == (100, 10, 1)
    assert Decimal(str(row["estimated_cny"])) == Decimal("0.00028")
    assert model.provenance()["actual_models"] == ["deepseek-v4.1-flash-1017"]
    assert model.provenance()["usage_complete"] is True
    assert model.provenance()["model_identity_complete"] is True
    assert Decimal(model.provenance()["reserved_cost_cny"]) == 0
    (sent,) = provider.seen_settings
    assert sent["max_tokens"] == 6000 and sent["max_retries"] == 0
    assert sent["temperature"] == 0 and sent["parallel_tool_calls"] is False


@pytest.mark.parametrize("state", ["started", "unconfirmed", "failed", "completed"])
def test_legacy_demo_ledger_is_the_same_cumulative_budget(store, state):
    # Completed requests consume their estimate; every other state retains its reserve.
    add_legacy(
        store, "old-demo", state=state, reserved=9.8, estimated=9.8 if state == "completed" else 0
    )
    provider = LocalProvider()
    with pytest.raises(CompanyError, match="screening_budget_exhausted"):
        invoke(guard(store, provider))
    assert not provider.seen_settings
    assert len(ledger(store)) == 1


def test_completed_legacy_rows_use_actual_cost_not_old_reservation(store):
    add_legacy(store, "old-demo", state="completed", reserved=10, estimated=0.01)
    invoke(guard(store))
    assert len(ledger(store)) == 2


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError, asyncio.CancelledError])
def test_failed_or_cancelled_call_retains_reservation_and_blocks_next_budget(store, failure):
    def fail():
        raise failure("SYNTHETIC provider failure")

    config = limits(budget_cny=reserve_for(limits()))
    model = guard(store, LocalProvider(effect=fail), config=config)
    with pytest.raises(failure):
        invoke(model)
    (row,) = ledger(store)
    assert row["state"] == "unconfirmed" and row["error_code"] == "response_unconfirmed"
    assert Decimal(str(row["reserved_cny"])) == reserve_for(config)
    assert model.provenance()["usage_complete"] is False
    next_provider = LocalProvider()
    with pytest.raises(CompanyError, match="screening_budget_exhausted"):
        invoke(guard(store, next_provider, config=config, run_id="second"))
    assert not next_provider.seen_settings


@pytest.mark.parametrize(
    "returned_model", [None, "", "deepseek-pro", "deepseek-chat", "invented-flash"]
)
def test_unconfirmed_actual_model_retains_entire_budget_and_stops(store, returned_model):
    model = guard(store, LocalProvider(returned_model=returned_model))
    with pytest.raises(CompanyError, match="screening_model_identity_unconfirmed"):
        invoke(model)
    (row,) = ledger(store)
    assert row["state"] == "unconfirmed"
    assert Decimal(str(row["reserved_cny"])) == Decimal("10")
    assert row["error_code"] == "screening_model_identity_unconfirmed"
    assert model.provenance()["usage_complete"] is False
    assert model.provenance()["model_identity_complete"] is False
    with pytest.raises(CompanyError, match="screening_budget_exhausted"):
        invoke(guard(store, run_id="after-unknown"))
    assert len(ledger(store)) == 1


@pytest.mark.parametrize("tokens", [(0, 0), (100, 0), (0, 10), (500000, 6000), (100, 6001)])
def test_missing_or_above_bound_usage_stops_and_never_releases_reserve(store, tokens):
    config = limits()
    model = guard(store, LocalProvider(tokens=tokens), config=config)
    with pytest.raises(CompanyError, match="screening_usage_unconfirmed"):
        invoke(model)
    (row,) = ledger(store)
    expected = max(
        reserve_for(config), (tokens[0] * Decimal(2) + tokens[1] * Decimal(8)) / 1_000_000
    )
    assert row["state"] == "unconfirmed"
    assert Decimal(str(row["reserved_cny"])) == expected
    assert (row["input_tokens"], row["output_tokens"]) == tokens
    assert row["error_code"] == "screening_usage_unconfirmed"
    assert model.provenance()["usage_complete"] is False


@pytest.mark.parametrize("oversized", ["prompt", "tool_schema", "instruction"])
def test_complete_framework_request_bytes_are_bounded_before_admission(store, oversized):
    messages = [ModelRequest(parts=[UserPromptPart("SYNTHETIC")])]
    parameters = ModelRequestParameters()
    if oversized == "prompt":
        messages = [ModelRequest(parts=[UserPromptPart("中" * 4000)])]
    elif oversized == "tool_schema":
        parameters.function_tools = [
            ToolDefinition(name="synthetic", parameters_json_schema={"description": "x" * 11000})
        ]
    else:
        parameters.prompted_output_template = "x" * 11000
    provider = LocalProvider()
    with pytest.raises(CompanyError, match="screening_request_size_limit"):
        invoke(
            guard(store, provider, config=limits(max_request_bytes=10000)),
            messages=messages,
            parameters=parameters,
        )
    assert not ledger(store) and not provider.seen_settings


def test_native_tools_rejected_before_spending_or_provider_call(store):
    provider = LocalProvider()
    with pytest.raises(CompanyError, match="screening_native_tools_disabled"):
        invoke(
            guard(store, provider),
            parameters=ModelRequestParameters(native_tools=[WebSearchTool()]),
        )
    assert not ledger(store) and not provider.seen_settings


def test_per_run_request_limit_and_live_configuration_are_fail_closed(store):
    provider = LocalProvider()
    model = guard(store, provider, config=limits(max_requests=1))
    invoke(model)
    with pytest.raises(CompanyError, match="screening_request_limit"):
        invoke(model)
    assert len(provider.seen_settings) == 1 and len(ledger(store)) == 1
    disabled = limits().model_copy(update={"ledger_verified": False})
    with pytest.raises(CompanyError, match="screening_live_calls_disabled"):
        invoke(guard(store, provider, config=disabled, run_id="unverified"))
    assert len(provider.seen_settings) == 1 and len(ledger(store)) == 1


def test_offline_function_fixture_never_writes_shared_paid_ledger(store):
    model = guard(store, offline=True)
    invoke(model)
    assert not ledger(store)
    assert model.provenance()["offline"] is True
    assert Decimal(model.provenance()["estimated_cost_cny"]) == 0


def test_live_db_revocation_before_call_forbids_provider_and_reservation(sources):
    adapter, bundle, doc, profile, _, _ = sources
    for oid in (doc["object_id"], profile["object_id"]):
        adapter.company.auth.grant(ALICE, oid, BOB.owner_id, ["read"])
    adapter.company.auth.revoke(ALICE, doc["object_id"], BOB.owner_id)
    provider = LocalProvider()
    model = guard(adapter.company, provider, adapter=adapter, context=BOB, bundle=bundle)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        invoke(model)
    assert not provider.seen_settings and not ledger(adapter.company)


def test_live_db_revocation_during_call_hides_response_and_forbids_next(sources):
    adapter, bundle, doc, profile, _, _ = sources
    for oid in (doc["object_id"], profile["object_id"]):
        adapter.company.auth.grant(ALICE, oid, BOB.owner_id, ["read"])
    provider = LocalProvider(
        effect=lambda: adapter.company.auth.revoke(ALICE, doc["object_id"], BOB.owner_id)
    )
    model = guard(adapter.company, provider, adapter=adapter, context=BOB, bundle=bundle)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        invoke(model)
    assert len(provider.seen_settings) == 1
    assert ledger(adapter.company)[0]["state"] == "completed"  # Paid usage survives revoked result.
    with pytest.raises(CompanyError, match="company_object_not_found"):
        invoke(model)
    assert len(provider.seen_settings) == 1 and len(ledger(adapter.company)) == 1


def test_two_concurrent_runs_cannot_spend_the_same_unfinished_reservation(store):
    config = limits(budget_cny=reserve_for(limits()))

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def waiting_response(messages, info):
            entered.set()
            await release.wait()
            return ModelResponse(
                parts=[TextPart("SYNTHETIC")],
                usage=RequestUsage(input_tokens=100, output_tokens=10),
            )

        first = guard(
            store,
            FunctionModel(waiting_response, model_name="deepseek-flash"),
            config=config,
            run_id="first",
        )
        task = asyncio.create_task(
            first.request(
                [ModelRequest(parts=[UserPromptPart("SYNTHETIC")])],
                None,
                ModelRequestParameters(),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        try:
            with pytest.raises(CompanyError, match="screening_budget_exhausted"):
                await guard(store, config=config, run_id="second").request(
                    [ModelRequest(parts=[UserPromptPart("SYNTHETIC")])],
                    None,
                    ModelRequestParameters(),
                )
            (row,) = ledger(store)
            assert row["state"] == "started"
        finally:
            release.set()
            await task
        assert ledger(store)[0]["state"] == "completed"

    asyncio.run(scenario())


def test_streaming_cannot_bypass_nonstream_budget_guard(store):
    provider = LocalProvider()
    model = guard(store, provider)

    async def attempt():
        with pytest.raises(CompanyError, match="screening_streaming_disabled"):
            async with model.request_stream([], None, ModelRequestParameters()):
                pytest.fail("Streaming unexpectedly started")

    asyncio.run(attempt())
    assert not ledger(store) and not provider.seen_settings


def test_fresh_database_has_no_restore_ack_requirement(store):
    with store.db.engine.begin() as conn:
        require_budget_review(conn, None)
    assert not store.db.rows("SELECT name FROM sqlite_master WHERE name='budget_review_barrier'")


@pytest.mark.parametrize("ack", [None, "", "0" * 32, "not-the-token", False, "中文"])
def test_restored_ledger_requires_exact_ack_without_changing_usage(store, ack):
    add_legacy(store, "confirmed", state="completed", reserved=0.25, estimated=0.031)
    add_legacy(store, "uncertain", state="unconfirmed", reserved=0.5, estimated=0)
    before = ledger(store)
    token = install_budget_review_barrier(store.db.engine.url.database)
    assert token and len(token) == 32
    with store.db.engine.begin() as conn:
        with pytest.raises(BudgetReviewRequired, match="^budget_review_required$"):
            require_budget_review(conn, ack)
        require_budget_review(conn, token)
    assert ledger(store) == before


def test_restore_ack_is_not_a_bypass_for_budget_cost(store):
    add_legacy(store, "old-expensive-run", state="completed", reserved=10, estimated=9.8)
    token = install_budget_review_barrier(store.db.engine.url.database)
    config = limits().model_copy(update={"ledger_restore_token": token})
    provider = LocalProvider()
    with pytest.raises(CompanyError, match="screening_budget_exhausted"):
        invoke(guard(store, provider, config=config))
    assert not provider.seen_settings


@pytest.mark.parametrize("damage", ["missing_row", "invalid_marker"])
def test_existing_but_incomplete_restore_barrier_fails_closed(store, damage):
    token = install_budget_review_barrier(store.db.engine.url.database)
    if damage == "missing_row":
        store.db.execute("DELETE FROM budget_review_barrier")
    else:
        store.db.execute("UPDATE budget_review_barrier SET restore_token='invalid'")
    with store.db.engine.begin() as conn:
        with pytest.raises(BudgetReviewRequired):
            require_budget_review(conn, token)


def legacy_bundle(path, *, include_ledger=True):
    """A genuine v1 SQLite backup, predating company/identity/migration tables."""
    path.mkdir()
    database = path / "bidops.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE snapshots(sha256 TEXT NOT NULL)")
        if include_ledger:
            conn.execute(
                "CREATE TABLE agent_runs(run_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,"
                "state TEXT NOT NULL,question_sha256 TEXT NOT NULL,reserved_cny REAL NOT NULL,"
                "estimated_cny REAL NOT NULL DEFAULT 0,input_tokens INTEGER NOT NULL DEFAULT 0,"
                "output_tokens INTEGER NOT NULL DEFAULT 0,"
                "model_requests INTEGER NOT NULL DEFAULT 0,"
                "error_code TEXT)"
            )
            conn.execute(
                "INSERT INTO agent_runs VALUES ('completed','2026-01-01','completed',?,"
                "0.25,0.031,100,10,1,NULL)",
                ("a" * 64,),
            )
            conn.execute(
                "INSERT INTO agent_runs VALUES ('unknown','2026-01-01','unconfirmed',?,"
                "0.5,0,0,0,1,'response_unconfirmed')",
                ("b" * 64,),
            )
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "files": {"bidops.sqlite3": hashlib.sha256(database.read_bytes()).hexdigest()},
            }
        )
    )
    return path


def test_actual_legacy_v1_restore_creates_new_barrier_each_time_and_preserves_ledger(tmp_path):
    bundle = legacy_bundle(tmp_path / "old-backup")
    with sqlite3.connect(bundle / "bidops.sqlite3") as conn:
        before = conn.execute("SELECT * FROM agent_runs ORDER BY run_id").fetchall()
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='company_objects'"
        ).fetchone()
    tokens = []
    for name in ("first-restore", "second-restore"):
        destination = tmp_path / name
        report = restore(bundle, destination)
        assert report["budget_review_required"] is True
        tokens.append(report["budget_restore_token"])
        recovered = Database(f"sqlite:///{destination}/bidops.sqlite3")
        try:
            with recovered.engine.begin() as conn:
                after = [
                    tuple(row)
                    for row in conn.exec_driver_sql("SELECT * FROM agent_runs ORDER BY run_id")
                ]
                assert after == before
                with pytest.raises(BudgetReviewRequired):
                    require_budget_review(conn, tokens[0] if len(tokens) > 1 else None)
                require_budget_review(conn, tokens[-1])
        finally:
            recovered.engine.dispose()
    assert len(set(tokens)) == 2


def test_actual_modern_restore_rotates_previously_acknowledged_marker(store, settings, tmp_path):
    add_legacy(store, "paid-before-restore", state="unconfirmed", reserved=0.5, estimated=0)
    previous = install_budget_review_barrier(store.db.engine.url.database)
    bundle, destination = tmp_path / "current-backup", tmp_path / "current-restored"
    backup(settings, bundle)
    report = restore(bundle, destination)
    assert report["budget_review_required"] and report["budget_restore_token"] != previous
    recovered = Database(f"sqlite:///{destination}/bidops.sqlite3")
    try:
        with recovered.engine.begin() as conn:
            with pytest.raises(BudgetReviewRequired):
                require_budget_review(conn, previous)
            require_budget_review(conn, report["budget_restore_token"])
            assert conn.exec_driver_sql("SELECT reserved_cny FROM agent_runs").scalar_one() == 0.5
    finally:
        recovered.engine.dispose()


def test_legacy_backup_without_ledger_still_requires_reconciliation_before_migration(tmp_path):
    report = restore(
        legacy_bundle(tmp_path / "no-ledger", include_ledger=False), tmp_path / "restored"
    )
    assert report["budget_review_required"] is True
    recovered = Database(f"sqlite:///{tmp_path}/restored/bidops.sqlite3")
    try:
        with recovered.engine.begin() as conn:
            assert not conn.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE name='agent_runs'"
            ).first()
            with pytest.raises(BudgetReviewRequired):
                require_budget_review(conn, None)
            require_budget_review(conn, report["budget_restore_token"])
    finally:
        recovered.engine.dispose()


@pytest.fixture
def recovered_budget(store, settings, tmp_path):
    add_legacy(store, "prior-cost", state="completed", reserved=0.25, estimated=0.031)
    add_legacy(store, "prior-uncertainty", state="unconfirmed", reserved=0.5, estimated=0)
    previous = install_budget_review_barrier(store.db.engine.url.database)
    before = ledger(store)
    bundle, destination = tmp_path / "paid-backup", tmp_path / "paid-restored"
    backup(settings, bundle)
    current = restore(bundle, destination)["budget_restore_token"]
    recovered = Store(
        settings.model_copy(
            update={
                "database_url": f"sqlite:///{destination}/bidops.sqlite3",
                "data_dir": destination,
            }
        )
    )
    try:
        assert ledger(recovered) == before
        assert current != previous
        yield recovered, current, previous, before
    finally:
        recovered.db.engine.dispose()


@pytest.mark.parametrize("entry", ["qualification", "demo"])
@pytest.mark.parametrize("ack_kind", ["missing", "wrong", "previous_restore"])
def test_actual_paid_admission_rejects_unacknowledged_restore(recovered_budget, entry, ack_kind):
    recovered, current, previous, before = recovered_budget
    ack = {"missing": None, "wrong": "0" * 32, "previous_restore": previous}[ack_kind]
    if entry == "qualification":
        provider = LocalProvider()
        with pytest.raises(CompanyError, match="screening_budget_review_required") as error:
            invoke(guard(recovered, provider, config=limits(ledger_restore_token=ack)))
        assert error.value.status_code == 503
        assert not provider.seen_settings
    else:
        service = AgentService(recovered, DemoSettings(_env_file=None, budget_restore_token=ack))
        with pytest.raises(DemoFailure, match="budget_review_required") as error:
            service.reserve("attempt-after-restore", "SYNTHETIC")
        assert error.value.status == 503
    assert ledger(recovered) == before


@pytest.mark.parametrize("entry", ["qualification", "demo"])
def test_current_restore_ack_allows_only_normal_paid_admission(recovered_budget, entry):
    recovered, token, _, before = recovered_budget
    if entry == "qualification":
        provider = LocalProvider()
        invoke(guard(recovered, provider, config=limits(ledger_restore_token=token)))
        assert len(provider.seen_settings) == 1
    else:
        service = AgentService(recovered, DemoSettings(_env_file=None, budget_restore_token=token))
        service.reserve("admitted-after-restore", "SYNTHETIC")
    rows = ledger(recovered)
    prior = next(row for row in rows if row["run_id"] == "prior-uncertainty")
    assert prior["state"] == "unconfirmed" and prior["reserved_cny"] == 0.5
    assert all(row in rows for row in before)
    assert len(rows) == len(before) + 1


def test_legacy_demo_cannot_configure_a_second_or_larger_budget():
    assert DemoSettings(_env_file=None, max_cost_cny=10).max_cost_cny == 10
    with pytest.raises(ValidationError):
        DemoSettings(_env_file=None, max_cost_cny="10.01")
