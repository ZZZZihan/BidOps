"""Saved SQLite/service collection; scripted model responses are not gold or quality."""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from bidops.acceptance import collection
from bidops.acceptance.collection import TaskRegistration, collect_observed
from bidops.company.authorization import CompanyError, ToolContext
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import JobsService
from bidops.qualification.candidates import CandidateFilters
from bidops.qualification.sources import digest
from bidops.screening.evaluation import P1_FIELDS, same_field
from bidops.screening.schemas import VersionRef
from dbos import DBOS
from pydantic_ai.models.function import FunctionModel
from test_company_access import ADMIN, ALICE, BOB
from test_jobs_runtime import wait_for
from test_notice_versions import AT, BODY, persist
from test_qualification_service import run, scripted_response
from test_qualification_service import service as service
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources


def batch_for(case, *, filters=None):
    screening, _, profile, _, _ = case
    return screening.candidates(ALICE, profile, filters or CandidateFilters())


def register(case, batch, *, result=None, unit=None, task_id="registered-first", **updates):
    _, notice, _, _, _ = case
    unit = unit or next(
        item
        for item in batch["items"]
        if item["project_id"] == notice.project_id
        and item["package_id"] == notice.packages[0].package_id
    )
    return {
        "task_id": task_id,
        "batch_id": batch["batch_id"],
        "unit_id": unit["unit_id"],
        "notice_revision": notice.version_ref.model_dump(mode="json"),
        "package_id": unit["package_id"],
        "execution_kind": "screening" if result else "not_submitted",
        "execution_id": result["run_id"] if result else None,
        **updates,
    }


def reader_job(case, settings, *, state="queued"):
    """Persist genuine business job states without starting a worker or provider."""
    screening, notice, profile, _, _ = case
    runtime = JobsService(screening, settings.data_dir, JobsSettings(enabled=True))
    bundle = screening.sources.resolve(ALICE, profile, notice.project_id, datetime.now(UTC))
    request = {"profile_revision": profile, "project_id": notice.project_id}
    created = runtime.repository.get_or_create(
        ALICE,
        idempotency_key="collection-offline-reader",
        request=request,
        prepare=lambda: {
            "manifest": bundle.manifest.model_dump(mode="json"),
            "settings": runtime.fixed_settings(),
            "versions": {
                **runtime.versions,
                "offline": True,
                "requested_model": "offline-unit",
                "result_id": "not-created-result",
                "result_revision": "not-created-revision",
            },
        },
    )
    if state != "queued":
        if state in {"failed", "timed_out", "interrupted"}:
            created = runtime.repository.transition(
                created["run_id"], created["state_version"], state, error="offline_failure"
            )
        elif state == "running":
            created = runtime.repository.transition(
                created["run_id"], created["state_version"], state
            )
        else:
            runtime.repository.request_cancel(ALICE, created["run_id"], created["state_version"])
            created = runtime.repository.get(ALICE, created["run_id"])
            if state == "cancelled":
                created = runtime.repository.transition(
                    created["run_id"], created["state_version"], "cancelled"
                )
    return runtime, created


def test_real_saved_agent_result_provenance_and_raw_hashes_without_new_calls(service, monkeypatch):
    screening = service[0]
    result = run(service)
    batch = batch_for(service)
    registration = register(service, batch, result=result)
    before = screening.repository.get(ALICE, result["run_id"])
    ledger = screening.repository.db.rows("SELECT * FROM agent_runs ORDER BY run_id")
    monkeypatch.setattr(screening, "_model", lambda: pytest.fail("collector constructed model"))
    gathered = collect_observed(screening, ALICE, [registration])
    prediction, record = gathered["observed_tasks"][0], gathered["records"][0]
    assert prediction["state"] == "completed"
    assert prediction["assessment"] == result["groups"][0]["assessment"]
    assert set(prediction["fields"]) == set(P1_FIELDS)
    assert record["agent_result"] == result
    assert record["provenance"]["actual_models"] == ["offline-unit"]
    assert record["usage"]["model_calls"] == 5
    assert record["usage"]["estimated_cost_cny"] == "0"
    assert record["raw_model_outputs_sha256"] == digest(before["raw_model_outputs"])
    assert record["raw_model_output_hashes"] == [digest(v) for v in before["raw_model_outputs"]]
    assert "raw_model_outputs" not in record
    assert record["agent_result"]["human_review_state"] == "unreviewed"
    assert gathered["human_review_projections"] == []
    assert gathered["metadata"]["business_acceptance"] == "BLOCKED"
    assert screening.repository.get(ALICE, result["run_id"]) == before
    assert screening.repository.db.rows("SELECT * FROM agent_runs ORDER BY run_id") == ledger


def test_entire_fixed_batch_beyond_page_one_preserves_every_registered_exclusion(service):
    screening, _, profile, _, _ = service
    for index in range(55):
        persist(
            screening.sources.notices,
            BODY.replace("P-001", f"COLLECT-{index}") + f"\n项目名称：工程样例{index}",
            path=f"collection-{index}",
        )
    first = screening.candidates(ALICE, profile, CandidateFilters(keyword="不存在的标题"))
    saved = screening.repository.get(ALICE, first["batch_id"])["result"]
    assert first["has_more"] and saved["counts"]["total_units"] > 100
    registrations = [
        {
            "task_id": f"full-{index}",
            "batch_id": first["batch_id"],
            "unit_id": unit["unit_id"],
            "notice_revision": unit["notice_revisions"][0],
            "package_id": unit["package_id"],
            "execution_kind": "not_submitted",
        }
        for index, unit in enumerate(saved["items"])
    ]
    collected = collect_observed(screening, ALICE, registrations)
    assert len(collected["records"]) == len(registrations) == len(saved["items"])
    assert [r["task_id"] for r in collected["observed_tasks"]] == [
        r["task_id"] for r in registrations
    ]
    assert all(row["state"] == "missing" for row in collected["observed_tasks"])
    excluded = [r for r in collected["records"] if r["candidate"]["disposition"] == "excluded"]
    assert len(excluded) >= 110
    assert collected["metadata"]["registered"] == saved["counts"]["total_units"]
    assert collected["records"][-1]["batch"]["total"] == len(registrations)
    assert collected["metadata"]["executions"] == []


def test_p1_all_fields_from_fixed_notice_and_exact_yuan_amount_not_gold(service):
    screening, _, profile, _, _ = service
    notice = persist(
        screening.sources.notices,
        "项目编号：COLLECT-MONEY\n项目名称：原固定标题\n采购人名称：原采购单位\n"
        "公告类型：招标公告\n预算金额：1.234567万元\n"
        "投标截止时间：2026年11月30日10:00\n专门面向中小企业\n第1包",
        path="collection-money",
    )
    batch = screening.candidates(ALICE, profile, CandidateFilters())
    unit = next(u for u in batch["items"] if u["project_id"] == notice.project_id)
    registration = register(
        service, batch, unit=unit, notice_revision=notice.version_ref.model_dump(mode="json")
    )
    persist(
        screening.sources.notices,
        "项目编号：COLLECT-MONEY\n项目名称：后来的新标题\n采购人名称：原采购单位\n"
        "公告类型：招标公告\n预算金额：99元\n第1包",
        path="collection-money",
        at=AT + timedelta(hours=2),
    )
    gathered = collect_observed(screening, ALICE, [registration])
    fields = gathered["records"][0]["fields"]
    assert set(fields) == set(P1_FIELDS)
    assert fields == {
        "project_code": "COLLECT-MONEY",
        "source_url": "https://procurement.example/collection-money",
        "title": "原固定标题",
        "buyer_name": "原采购单位",
        "budget": {"amount": notice.notice.budget.amount, "currency": "CNY", "unit": "元"},
        "budget_unit": "万元",
        "bid_deadline_at": "2026-11-30T10:00:00+08:00",
        "notice_kind": "tender_announcement",
        "eligibility_excerpts": ["专门面向中小企业"],
    }
    assert same_field(
        "budget", {"amount": "1.234567", "currency": "CNY", "unit": "万元"}, fields["budget"]
    )
    assert gathered["observed_tasks"][0]["state"] == "missing"
    assert gathered["observed_tasks"][0]["stale_disclosed"] is True


def test_unknown_p1_fields_stay_none_not_zero_or_current_date(service):
    screening = service[0]
    batch = batch_for(service)
    collected = collect_observed(screening, ALICE, [register(service, batch)])
    fields = collected["records"][0]["fields"]
    assert fields["budget"] is fields["budget_unit"] is fields["title"] is None
    assert fields["eligibility_excerpts"] is None
    assert collected["observed_tasks"][0]["assessment"] == {}


def test_saved_partial_failure_keeps_every_condition_and_original_state(service):
    screening = service[0]
    screening.settings.max_requests = 1
    result = run(service)
    assert result["state"] == "failed"
    collected = collect_observed(
        screening, ALICE, [register(service, batch_for(service), result=result)]
    )
    row, record = collected["observed_tasks"][0], collected["records"][0]
    assert row["state"] == "failed"
    assert len(record["agent_result"]["inventory"]["conditions"]) == 4
    assert len(row["assessment"]["assessments"]) == 4
    assert all(a["status"] == "information_insufficient" for a in row["assessment"]["assessments"])
    assert record["agent_result"]["error_code"] == "screening_request_limit"
    assert record["usage"]["model_calls"] == 1


def test_timeout_does_not_become_missing_or_disappear(service):
    screening = service[0]
    screening.settings.max_seconds = 0.01

    async def slow(messages, info):
        await asyncio.sleep(0.2)
        return scripted_response(messages, info)

    screening.model_override = FunctionModel(slow)
    result = run(service)
    assert result["state"] == "timed_out"
    collected = collect_observed(
        screening, ALICE, [register(service, batch_for(service), result=result)]
    )
    assert collected["metadata"]["states"] == {"timed_out": 1}
    assert collected["records"][0]["agent_result"]["inventory"]["unresolved"]
    assert not collected["records"][0]["usage"]["usage_complete"]


def test_registered_package_cannot_choose_another_successful_group(service):
    result = run(service)
    batch = batch_for(service)
    unit = next(
        u
        for u in batch["items"]
        if u["project_id"] == service[1].project_id
        and u["package_id"] == service[1].packages[1].package_id
    )
    collected = collect_observed(
        service[0], ALICE, [register(service, batch, result=result, unit=unit)]
    )
    assert collected["observed_tasks"][0]["state"] == "failed"
    assert collected["observed_tasks"][0]["assessment"] == {}
    assert collected["records"][0]["agent_result"] == result
    assert collected["records"][0]["assessment_selection_error"] == "registered_assessment_missing"


@pytest.mark.parametrize(
    "state",
    ["queued", "running", "failed", "timed_out", "interrupted", "cancel_requested", "cancelled"],
)
def test_actual_jobs_reader_keeps_noncompleted_and_failure_states(
    service, settings, state, monkeypatch
):
    runtime, job = reader_job(service, settings, state=state)
    registration = register(
        service, batch_for(service), execution_kind="job", execution_id=job["run_id"]
    )
    monkeypatch.setattr(runtime, "start", lambda: pytest.fail("collector started worker"))
    monkeypatch.setattr(runtime, "resume", lambda *_: pytest.fail("collector resumed task"))
    collected = collect_observed(service[0], ALICE, [registration], jobs=runtime)
    assert collected["observed_tasks"][0]["state"] == state
    assert collected["records"][0]["execution"]["status"] == state
    assert collected["records"][0]["usage"]["model_calls"] == 0
    assert collected["records"][0]["usage"]["actual_models"] == []
    assert not runtime.started


def test_actual_dbos_saved_wait_is_computation_completed_and_never_human_approved(
    service, settings
):
    screening, notice, profile, _, _ = service
    runtime = JobsService(screening, settings.data_dir, JobsSettings(enabled=True))
    try:
        runtime.start()
        job = runtime.submit(
            ALICE, {"profile_revision": profile, "project_id": notice.project_id}, "collect-durable"
        )
        waiting = wait_for(runtime, job["run_id"], "waiting_review")
        batch = batch_for(service)
        units = [u for u in batch["items"] if u["project_id"] == notice.project_id]
        registered = [
            register(
                service,
                batch,
                unit=u,
                task_id=f"pkg-{index}",
                execution_kind="job",
                execution_id=job["run_id"],
            )
            for index, u in enumerate(units)
        ]
        calls = waiting["usage"]["model_calls"]
        screening.model_override = None
        screening.settings.api_key = None
        screening.settings.live_calls_enabled = False
        runtime.close()
        collected = collect_observed(screening, ALICE, registered, jobs=runtime)
        assert len(collected["observed_tasks"]) == 2
        assert all(row["state"] == "completed" for row in collected["observed_tasks"])
        assert all(r["execution"]["status"] == "waiting_review" for r in collected["records"])
        assert all(
            r["agent_result"]["human_review_state"] == "unreviewed" for r in collected["records"]
        )
        assert calls == 9
        assert collected["metadata"]["executions"][0]["usage"]["model_calls"] == calls
        assert len(collected["metadata"]["executions"]) == 1
        assert runtime.read(ALICE, job["run_id"])["usage"]["model_calls"] == calls
        assert collected["human_review_projections"] == []
    finally:
        runtime.close()
        DBOS.destroy(destroy_registry=True)


@pytest.mark.parametrize("actor", [BOB, ADMIN, ToolContext("not-authenticated")])
def test_owner_and_current_subject_boundaries_cannot_be_bypassed(service, actor):
    batch = batch_for(service)
    with pytest.raises(CompanyError):
        collect_observed(service[0], actor, [register(service, batch)])


def test_missing_execution_id_raises_same_private_not_found_instead_of_guessing_existence(service):
    registration = register(
        service, batch_for(service), execution_kind="screening", execution_id="unknown-result"
    )
    with pytest.raises(CompanyError, match="screening_result_not_found") as error:
        collect_observed(service[0], ALICE, [registration])
    assert error.value.status_code == 404


def test_revocation_while_later_task_is_collected_rejects_all_previously_assembled_bodies(
    service, monkeypatch
):
    screening, notice, _, material, _ = service
    result = run(service)
    batch = batch_for(service)
    other = next(
        u
        for u in batch["items"]
        if u["project_id"] == notice.project_id and u["package_id"] != notice.packages[0].package_id
    )
    tasks = [
        register(service, batch, result=result),
        register(service, batch, unit=other, task_id="later"),
    ]
    original, seen = collection._fields, []

    def revoke(version):
        seen.append(version.version_id)
        value = original(version)
        if len(seen) == 2:
            screening.sources.company.auth.revoke(ALICE, material["object_id"], ALICE.owner_id)
        return value

    monkeypatch.setattr(collection, "_fields", revoke)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        collect_observed(screening, ALICE, tasks)
    assert len(seen) == 2


@pytest.mark.parametrize(
    "extra",
    [
        {"gold": {}},
        {"candidate": True},
        {"actor_id": "alice"},
        {"fields": {"budget": "pretend"}},
        {"assessment": {}},
    ],
)
def test_registration_rejects_gold_predictions_and_actor_from_payload(service, extra):
    registration = {**register(service, batch_for(service)), **extra}
    with pytest.raises(CompanyError, match="acceptance_invalid_registration"):
        collect_observed(service[0], ALICE, [registration])


def test_duplicate_registration_and_wrong_fixed_bindings_are_rejected(service):
    screening = service[0]
    result = run(service)
    registration = register(service, batch_for(service), result=result)
    with pytest.raises(CompanyError, match="duplicate_registered_task"):
        collect_observed(screening, ALICE, [registration, registration])
    wrong = copy.deepcopy(registration)
    wrong["notice_revision"]["sha256"] = "f" * 64
    with pytest.raises(CompanyError, match="registration_target_mismatch"):
        collect_observed(screening, ALICE, [wrong])
    with pytest.raises(CompanyError, match="result_revision_mismatch"):
        collect_observed(screening, ALICE, [{**registration, "expected_result_revision": "wrong"}])
    # Reusing an execution cache must not skip a later registration's revision pin.
    with pytest.raises(CompanyError, match="result_revision_mismatch"):
        collect_observed(
            screening,
            ALICE,
            [
                registration,
                {**registration, "task_id": "second", "expected_result_revision": "wrong"},
            ],
        )


def test_validation_is_strict_and_collection_result_does_not_mutate_registration(service):
    registration = register(service, batch_for(service))
    model = TaskRegistration.model_validate(registration)
    before = model.model_dump(mode="json")
    gathered = collect_observed(service[0], ALICE, [model])
    assert model.model_dump(mode="json") == before
    assert gathered["metadata"]["registration_sha256"] == digest([before])
    assert gathered["metadata"]["observed_tasks_sha256"] == digest(gathered["observed_tasks"])
    assert gathered["metadata"]["records_sha256"] == digest(gathered["records"])
    with pytest.raises(CompanyError, match="authenticated_context_required"):
        collect_observed(service[0], {"owner_id": ALICE.owner_id}, [registration])
    assert VersionRef.model_validate(before["notice_revision"]) == service[1].version_ref
    assert "gold" not in json.dumps(gathered["records"], ensure_ascii=False)
