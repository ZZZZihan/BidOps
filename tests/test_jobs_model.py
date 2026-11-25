"""Real SQLite receipts/ledger and local FunctionModel fault windows; no provider I/O.

Most tests use DBOS's actual context object to identify a logical step without
running a worker. One checkpoint test runs DBOS itself. SIGKILL and application
recovery are covered by the separate worker integration harness.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from bidops.company.authorization import CompanyError
from bidops.enterprise.budget_review import install_budget_review_barrier
from bidops.jobs.model import ACTIVE_MODEL, DurableModel, JobModelProxy, summarize_attempts
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.sources import ScreeningInputManifest
from dbos import DBOS, DBOSContextEnsure, SetWorkflowID
from pydantic import TypeAdapter
from pydantic_ai import models
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.native_tools import WebSearchTool
from test_company_access import ALICE
from test_jobs_repository import domain as domain
from test_jobs_repository import jobs as jobs
from test_jobs_repository import running
from test_jobs_repository import sources as sources
from test_qualification_budget import LocalProvider


class ForcedExit(BaseException):
    """Simulate loss of in-process state, never handled as a provider exception."""


@pytest.fixture(autouse=True)
def no_model_network(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


def limits(**updates):
    return ScreeningSettings(
        _env_file=None,
        **{
            "enabled": True,
            "live_calls_enabled": True,
            "ledger_verified": True,
            "max_requests": 4,
            **updates,
        },
    )


@pytest.fixture
def model_case(jobs):
    job = running(jobs)
    repo = jobs[0]
    manifest = job["manifest"]
    bundle = repo.sources.resolve(
        ALICE,
        manifest["profile_revision"],
        manifest["project_id"],
        ScreeningInputManifest.model_validate(manifest).as_of,
        manifest["material_revisions"],
    )
    return repo, job, bundle


def guard(case, provider=None, *, config=None, fault=None, offline=False):
    repo, job, bundle = case
    return DurableModel(
        provider or LocalProvider(),
        db=repo.db,
        settings=config or limits(),
        adapter=repo.sources,
        context=ALICE,
        bundle=bundle,
        run_id=job["run_id"],
        offline=offline,
        jobs=repo,
        execution_check=lambda: repo.require_execution(job["run_id"]),
        fault=fault,
    )


def invoke(model, *, step=1, settings=None, messages=None, parameters=None):
    async def request():
        with DBOSContextEnsure() as context:
            context.workflow_id = model.run_id + "-calculation"
            context.function_id = step
            context.curr_step_function_id = step
            assert DBOS.workflow_id == model.run_id + "-calculation" and DBOS.step_id == step
            return await model.request(
                messages
                or [
                    ModelRequest(
                        parts=[
                            UserPromptPart(
                                "SYNTHETIC DURABLE REQUEST",
                                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                            )
                        ],
                        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                ],
                settings,
                parameters or ModelRequestParameters(),
            )

    return asyncio.run(request())


def attempts(case):
    return case[0].list_attempts_system(case[1]["run_id"])


def ledger(case):
    return case[0].db.rows("SELECT * FROM agent_runs ORDER BY rowid")


def reservation(config=None):
    config = config or limits()
    return (
        (2 * config.max_request_bytes + 32768) * config.input_cny_per_million
        + config.max_output_tokens * config.output_cny_per_million
    ) / 1_000_000


def crash_at(window):
    def fault(observed):
        if observed == window:
            raise ForcedExit(window)

    return fault


@pytest.mark.parametrize("invalid_context", ["none", "root_workflow", "child_without_step"])
def test_only_native_calculation_step_can_admit_a_model_request(model_case, invalid_context):
    provider = LocalProvider()
    model = guard(model_case, provider)

    async def request():
        args = (
            [ModelRequest(parts=[UserPromptPart("SYNTHETIC OUTSIDE MODEL STEP")])],
            None,
            ModelRequestParameters(),
        )
        if invalid_context == "none":
            return await model.request(*args)
        with DBOSContextEnsure() as context:
            context.workflow_id = model.run_id + (
                "-calculation" if invalid_context == "child_without_step" else ""
            )
            context.function_id = 1
            context.curr_step_function_id = -1 if invalid_context == "child_without_step" else 1
            return await model.request(*args)

    with pytest.raises(CompanyError, match="job_model_requires_framework_step"):
        asyncio.run(request())
    assert not provider.seen_settings and attempts(model_case) == ledger(model_case) == []


def test_confirmed_response_and_restart_provenance_come_from_persistent_receipt(model_case):
    provider = LocalProvider(returned_model="deepseek-v4.1-flash-1017")
    model = guard(model_case, provider)
    response = invoke(model)
    assert response.model_name == "deepseek-v4.1-flash-1017"
    (attempt,) = attempts(model_case)
    assert attempt["seq"] == 1 and attempt["status"] == "response_received"
    (cost,) = ledger(model_case)
    assert attempt["ledger_id"] == cost["run_id"] and cost["state"] == "completed"
    assert Decimal(str(cost["estimated_cny"])) == Decimal("0.00028")
    restarted_provider = LocalProvider(
        effect=lambda: pytest.fail("provenance made a provider request")
    )
    restarted = guard(model_case, restarted_provider)
    report = restarted.provenance()
    assert report["model_calls"] == report["confirmed_calls"] == 1
    assert report["unknown_calls"] == 0 and report["usage_complete"] is True
    assert report["model_identity_complete"] is True
    assert report["actual_models"] == ["deepseek-v4.1-flash-1017"]
    assert Decimal(report["estimated_cost_cny"]) == Decimal("0.00028")
    assert Decimal(report["reserved_cost_cny"]) == 0 and not restarted_provider.seen_settings
    assert restarted.outputs == [attempt["response"]]


def test_saved_attempt_summary_needs_no_enabled_model_or_credentials(model_case):
    invoke(guard(model_case))
    disabled = ScreeningSettings(_env_file=None, enabled=False, live_calls_enabled=False)
    report = summarize_attempts(
        model_case[0], model_case[1]["run_id"], disabled, "deepseek-flash", False
    )
    assert report["model_calls"] == report["confirmed_calls"] == 1
    assert report["requested_model"] == "deepseek-flash"
    assert report["usage_complete"] and report["model_identity_complete"]
    assert Decimal(report["estimated_cost_cny"]) == Decimal("0.00028")
    assert len(attempts(model_case)) == len(ledger(model_case)) == 1


def test_restart_cannot_reset_persistent_request_limit(model_case):
    config = limits(max_requests=2)
    first, second, blocked = (LocalProvider() for _ in range(3))
    invoke(guard(model_case, first, config=config), step=1)
    invoke(guard(model_case, second, config=config), step=2)
    with pytest.raises(CompanyError, match="screening_request_limit"):
        invoke(guard(model_case, blocked, config=config), step=3)
    assert len(attempts(model_case)) == 2 and len(ledger(model_case)) == 2
    assert len(first.seen_settings) == len(second.seen_settings) == 1 and not blocked.seen_settings


@pytest.mark.parametrize("window", ["before_model", "response_before_receipt"])
def test_unconfirmed_windows_keep_old_reservation_and_charge_new_attempt(model_case, window):
    original_provider = LocalProvider()
    with pytest.raises(ForcedExit):
        invoke(guard(model_case, original_provider, fault=crash_at(window)))
    assert attempts(model_case)[0]["status"] == "started"
    assert ledger(model_case)[0]["state"] == "started"
    assert len(original_provider.seen_settings) == (window == "response_before_receipt")
    recovered_provider = LocalProvider()
    recovered = guard(model_case, recovered_provider)
    invoke(recovered)
    old, new = attempts(model_case)
    assert old["status"] == "unknown" and old["response"] is None
    assert new["seq"] == 2 and new["status"] == "response_received"
    costs = ledger(model_case)
    assert Decimal(str(costs[0]["reserved_cny"])) == reservation()
    assert costs[0]["state"] != "completed" and costs[1]["state"] == "completed"
    assert recovered.provenance()["unknown_calls"] == 1
    assert recovered.provenance()["usage_complete"] is False
    assert recovered.provenance()["model_identity_complete"] is False


@pytest.mark.parametrize("window", ["receipt_before_accounting", "accounting_before_checkpoint"])
def test_receipt_reuse_reconciles_without_second_model_or_second_charge(model_case, window):
    original_provider = LocalProvider()
    with pytest.raises(ForcedExit):
        invoke(guard(model_case, original_provider, fault=crash_at(window)))
    (saved,) = attempts(model_case)
    assert saved["status"] == "response_received" and saved["response"] is not None
    assert ledger(model_case)[0]["state"] == (
        "started" if window == "receipt_before_accounting" else "completed"
    )
    restarted_provider = LocalProvider(
        effect=lambda: pytest.fail("receipt recovery repeated provider request")
    )
    restarted = guard(model_case, restarted_provider)
    recovered = invoke(restarted)
    assert TypeAdapter(ModelResponse).dump_python(recovered, mode="json") == saved["response"]
    assert (
        len(attempts(model_case))
        == len(ledger(model_case))
        == len(original_provider.seen_settings)
        == 1
    )
    assert not restarted_provider.seen_settings
    assert ledger(model_case)[0]["state"] == "completed"
    assert Decimal(restarted.provenance()["estimated_cost_cny"]) == Decimal("0.00028")


def test_pre_attempt_ledger_orphan_stays_reserved_and_counts_shared_ceiling(
    model_case, monkeypatch
):
    repo = model_case[0]
    config = limits(budget_cny=reservation())
    original = repo.record_attempt_started

    def die_before_attempt(*args, **kwargs):
        raise ForcedExit("reserve persisted before attempt record")

    monkeypatch.setattr(repo, "record_attempt_started", die_before_attempt)
    first = LocalProvider()
    with pytest.raises(ForcedExit):
        invoke(guard(model_case, first, config=config))
    assert attempts(model_case) == [] and not first.seen_settings
    assert ledger(model_case)[0]["state"] == "started"
    orphan_report = guard(model_case, config=config).provenance()
    assert Decimal(orphan_report["reserved_cost_cny"]) == reservation(config)
    assert orphan_report["usage_complete"] is False
    monkeypatch.setattr(repo, "record_attempt_started", original)
    restarted = LocalProvider()
    with pytest.raises(CompanyError, match="screening_budget_exhausted"):
        invoke(guard(model_case, restarted, config=config))
    assert len(ledger(model_case)) == 1 and not restarted.seen_settings


@pytest.mark.parametrize("state", ["completed", "started", "unconfirmed", "failed"])
def test_existing_demo_and_screening_rows_share_the_same_admission_budget(model_case, state):
    model_case[0].db.execute(
        "INSERT INTO agent_runs(run_id,created_at,state,question_sha256,"
        "reserved_cny,estimated_cny) VALUES ('old-demo','unit',:state,:sha,9.8,9.8)",
        {"state": state, "sha": "a" * 64},
    )
    provider = LocalProvider()
    with pytest.raises(CompanyError, match="screening_budget_exhausted"):
        invoke(guard(model_case, provider))
    assert (
        not provider.seen_settings and attempts(model_case) == [] and len(ledger(model_case)) == 1
    )


@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError, asyncio.CancelledError])
def test_provider_failure_unknown_reservation_no_private_exception_text(model_case, failure):
    def fail():
        raise failure("PRIVATE_PROVIDER_EXCEPTION_MUST_NOT_PERSIST")

    provider = LocalProvider(effect=fail)
    expected = CompanyError if issubclass(failure, Exception) else failure
    with pytest.raises(expected):
        invoke(guard(model_case, provider))
    assert attempts(model_case)[0]["status"] == "unknown"
    assert attempts(model_case)[0]["response"] is None
    assert ledger(model_case)[0]["state"] == "unconfirmed"
    assert Decimal(str(ledger(model_case)[0]["reserved_cny"])) == reservation()
    assert "PRIVATE_PROVIDER_EXCEPTION" not in str(attempts(model_case) + ledger(model_case))


@pytest.mark.parametrize("returned_model", [None, "", "deepseek-pro", "invented-flash"])
def test_unverified_model_identity_remains_incomplete_across_receipt_recovery(
    model_case, returned_model
):
    provider = LocalProvider(returned_model=returned_model)
    with pytest.raises(CompanyError, match="screening_model_identity_unconfirmed"):
        invoke(guard(model_case, provider))
    (cost,) = ledger(model_case)
    assert cost["state"] == "unconfirmed" and Decimal(str(cost["reserved_cny"])) == 10
    recovered = guard(
        model_case, LocalProvider(effect=lambda: pytest.fail("unverified receipt reissued"))
    )
    with pytest.raises(CompanyError, match="screening_model_identity_unconfirmed"):
        invoke(recovered)
    report = recovered.provenance()
    assert report["usage_complete"] is False and report["model_identity_complete"] is False
    assert len(attempts(model_case)) == len(ledger(model_case)) == 1


@pytest.mark.parametrize("tokens", [(0, 10), (100, 0), (100, 6001), (300000, 100)])
def test_bad_usage_receipt_keeps_conservative_charge_and_remains_incomplete(model_case, tokens):
    provider = LocalProvider(tokens=tokens)
    model = guard(model_case, provider)
    with pytest.raises(CompanyError, match="screening_usage_unconfirmed"):
        invoke(model)
    (cost,) = ledger(model_case)
    actual = (Decimal(tokens[0]) * 2 + Decimal(tokens[1]) * 8) / 1_000_000
    assert cost["state"] == "unconfirmed"
    assert Decimal(str(cost["reserved_cny"])) == max(reservation(), actual)
    assert model.provenance()["usage_complete"] is False
    recovered = guard(
        model_case, LocalProvider(effect=lambda: pytest.fail("bad receipt repeated model"))
    )
    with pytest.raises(CompanyError, match="screening_usage_unconfirmed"):
        invoke(recovered)
    assert recovered.provenance()["usage_complete"] is False and len(ledger(model_case)) == 1


def test_live_disabled_creates_neither_attempt_nor_ledger(model_case):
    provider = LocalProvider()
    with pytest.raises(CompanyError, match="screening_live_calls_disabled"):
        invoke(guard(model_case, provider, config=limits(live_calls_enabled=False)))
    assert not provider.seen_settings and attempts(model_case) == ledger(model_case) == []


@pytest.mark.parametrize("point", ["before", "during"])
def test_cancel_blocks_release_and_next_call_but_retains_inflight_cost(model_case, point):
    repo, job, _ = model_case

    def cancel():
        current = repo.get_system(job["run_id"])
        repo.request_cancel(ALICE, job["run_id"], current["state_version"])

    if point == "before":
        cancel()
    provider = LocalProvider(effect=cancel if point == "during" else None)
    with pytest.raises(CompanyError, match="job_not_executable"):
        invoke(guard(model_case, provider))
    assert len(provider.seen_settings) == (point == "during")
    assert len(attempts(model_case)) == len(ledger(model_case)) == (point == "during")
    if point == "during":
        assert ledger(model_case)[0]["state"] == "completed"
    with pytest.raises(CompanyError, match="job_not_executable"):
        invoke(guard(model_case, LocalProvider()), step=2)
    assert not repo.db.rows("SELECT * FROM qualification_results")


def test_revoke_during_response_denies_release_and_recovery_without_zeroing_usage(model_case):
    repo, job, _ = model_case
    material = job["manifest"]["material_revisions"][0]["object_id"]
    provider = LocalProvider(effect=lambda: repo.auth.revoke(ALICE, material, ALICE.owner_id))
    with pytest.raises(CompanyError, match="company_object_not_found"):
        invoke(guard(model_case, provider))
    assert ledger(model_case)[0]["state"] == "completed"
    assert attempts(model_case)[0]["status"] == "response_received"
    with pytest.raises(CompanyError):
        repo.list_attempts(ALICE, job["run_id"])
    blocked = LocalProvider()
    with pytest.raises(CompanyError):
        invoke(guard(model_case, blocked))
    assert not blocked.seen_settings and len(ledger(model_case)) == 1


@pytest.mark.parametrize("change", ["settings", "schema", "message", "step"])
def test_fingerprint_distinguishes_full_effective_request(model_case, change):
    first, second = LocalProvider(), LocalProvider()
    invoke(guard(model_case, first), settings={"top_p": 0.1})
    options = {"settings": {"top_p": 0.1}}
    if change == "settings":
        options["settings"] = {"top_p": 0.9}
    elif change == "schema":
        options["parameters"] = ModelRequestParameters(allow_text_output=False)
    elif change == "message":
        options["messages"] = [ModelRequest(parts=[UserPromptPart("DIFFERENT SYNTHETIC REQUEST")])]
    else:
        options["step"] = 2
    invoke(guard(model_case, second), **options)
    assert len(first.seen_settings) == len(second.seen_settings) == 1
    assert len({a["request_sha256"] for a in attempts(model_case)}) == 2


def test_request_size_and_native_tools_fail_before_budget_or_model(model_case):
    provider = LocalProvider()
    with pytest.raises(CompanyError, match="screening_request_size_limit"):
        invoke(
            guard(model_case, provider, config=limits(max_request_bytes=10000)),
            messages=[ModelRequest(parts=[UserPromptPart("中文" * 3000)])],
        )
    with pytest.raises(CompanyError, match="screening_native_tools_disabled"):
        invoke(
            guard(model_case, provider),
            parameters=ModelRequestParameters(native_tools=[WebSearchTool()]),
        )
    assert not provider.seen_settings and attempts(model_case) == ledger(model_case) == []


def test_framework_message_and_typed_part_timestamps_do_not_duplicate_same_logical_request(
    model_case,
):
    at = datetime(2026, 1, 1, tzinfo=UTC)

    def messages(timestamp):
        return [
            ModelRequest(
                parts=[UserPromptPart("IDENTICAL USER CONTENT", timestamp=timestamp)],
                timestamp=timestamp,
            )
        ]

    first = LocalProvider()
    invoke(guard(model_case, first), messages=messages(at))
    second = LocalProvider(
        effect=lambda: pytest.fail("typed timestamp alone caused duplicate call")
    )
    invoke(guard(model_case, second), messages=messages(at + timedelta(days=1)))
    assert len(first.seen_settings) == 1 and not second.seen_settings
    assert len(attempts(model_case)) == len(ledger(model_case)) == 1


def test_nested_user_tool_content_timestamp_remains_part_of_request_fingerprint(model_case):
    at = datetime(2026, 1, 1, tzinfo=UTC)

    def messages(user_timestamp):
        return [
            ModelRequest(
                parts=[
                    UserPromptPart("USER PROVIDED TIME IS MATERIAL", timestamp=at),
                    ToolReturnPart(
                        "read_company_material",
                        {"timestamp": user_timestamp, "text": "SYNTHETIC proof"},
                        "unit-tool",
                        timestamp=at,
                    ),
                ],
                timestamp=at,
            )
        ]

    first, second = LocalProvider(), LocalProvider()
    invoke(guard(model_case, first), messages=messages("2026-01-01"))
    invoke(guard(model_case, second), messages=messages("2026-12-31"))
    assert len(first.seen_settings) == len(second.seen_settings) == 1
    assert len({a["request_sha256"] for a in attempts(model_case)}) == 2


def test_restore_barrier_is_checked_before_any_new_attempt_and_correct_ack_uses_same_ledger(
    model_case,
):
    repo = model_case[0]
    token = install_budget_review_barrier(repo.db.engine.url.database)
    provider = LocalProvider()
    with pytest.raises(CompanyError, match="screening_budget_review_required"):
        invoke(guard(model_case, provider))
    assert attempts(model_case) == ledger(model_case) == [] and not provider.seen_settings
    invoke(guard(model_case, provider, config=limits(ledger_restore_token=token)))
    assert len(ledger(model_case)) == 1 and ledger(model_case)[0]["state"] == "completed"


@pytest.mark.parametrize("tokens", [(100, 10), (0, 0)])
def test_fixture_receipts_are_free_but_usage_completeness_is_still_truthful(model_case, tokens):
    provider = LocalProvider(returned_model="offline-fixture", tokens=tokens)
    model = guard(model_case, provider, offline=True)
    invoke(model)
    assert ledger(model_case) == []
    report = guard(model_case, offline=True).provenance()
    assert report["model_calls"] == 1 and report["offline"] is True
    assert Decimal(report["estimated_cost_cny"]) == Decimal(report["reserved_cost_cny"]) == 0
    assert report["usage_complete"] is (tokens == (100, 10))


def test_actual_dbos_checkpoint_skips_proxy_and_new_guard_reads_persistent_provenance(
    model_case, tmp_path
):
    repo, job, _ = model_case
    provider = LocalProvider()
    holder = {"model": guard(model_case, provider)}
    proxy = JobModelProxy(provider)
    config = {
        "name": "bidops-unit-model-checkpoint",
        "application_version": "unit-model-v1",
        "system_database_url": f"sqlite:///{tmp_path / 'model-dbos.sqlite3'}",
        "enable_otlp": False,
        "log_level": "WARNING",
    }
    DBOS(config=config)

    @DBOS.step(name="unit_durable_model_receipt_step")
    async def model_step():
        token = ACTIVE_MODEL.set(holder["model"])
        try:
            response = await proxy.request(
                [ModelRequest(parts=[UserPromptPart("UNIT DBOS STEP")])],
                None,
                ModelRequestParameters(),
            )
            return TypeAdapter(ModelResponse).dump_python(response, mode="json")
        finally:
            ACTIVE_MODEL.reset(token)

    @DBOS.workflow(name="unit_durable_model_checkpoint_workflow")
    async def workflow():
        return await model_step()

    DBOS.launch()
    try:
        with SetWorkflowID(job["run_id"] + "-calculation"):
            result = DBOS.start_workflow(workflow).get_result()
        assert result["model_name"] == "deepseek-flash"
        assert len(provider.seen_settings) == 1
    finally:
        DBOS.destroy()
    blocked = LocalProvider(effect=lambda: pytest.fail("completed DBOS step called model again"))
    holder["model"] = guard(model_case, blocked)
    DBOS(config=config)
    DBOS.launch()
    try:
        assert DBOS.retrieve_workflow(job["run_id"] + "-calculation").get_result() == result
        report = holder["model"].provenance()
        assert report["model_calls"] == report["confirmed_calls"] == 1
        assert Decimal(report["estimated_cost_cny"]) == Decimal("0.00028")
        assert (
            not blocked.seen_settings and len(attempts(model_case)) == len(ledger(model_case)) == 1
        )
    finally:
        DBOS.destroy()
