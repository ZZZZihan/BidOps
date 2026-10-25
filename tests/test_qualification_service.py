"""Actual Pydantic AI runs + SQLite history. Scripted outputs are not quality scores."""

import asyncio
import copy
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from bidops.company.authorization import CompanyError
from bidops.qualification.candidates import CandidateFilters
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.service import ScreeningService
from bidops.screening.schemas import ScreeningAssessment
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from test_company_access import ALICE, BOB, payload
from test_notice_versions import AT, BODY, persist
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


def scripted_response(messages, info, *, state="satisfied", mutation=None):
    prompt = next(
        json.loads(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )
    if prompt["stage"] == "extraction":
        segment = prompt["segments"][0]
        conditions = []
        for match in re.finditer(r"UNIT-\d+", segment["text"]):
            start, end = segment["start"] + match.start(), segment["start"] + match.end()
            conditions.append(
                {
                    "start": start,
                    "end": end,
                    "hard_requirement": True,
                    "expression": {
                        "operator": "atom",
                        "start": start,
                        "end": end,
                        "text": match.group(),
                    },
                    "scope_kind": "all",
                    "validity": {"kind": "not_required"},
                }
            )
        output = {
            "document_id": prompt["document"]["document_id"],
            "segments": [
                {
                    "segment_id": segment["segment_id"],
                    "disposition": "requirement" if conditions else "not_requirement",
                    "conditions": conditions,
                }
            ],
        }
    else:
        condition = prompt["condition"]
        evidence = [e for f in prompt["profile"]["facts"] for e in f["evidence"]]
        output = {
            "condition_id": condition["condition_id"],
            "judgments": [
                {
                    "node_id": condition["expression"]["node_id"],
                    "status": state,
                    "reason": "scripted_engineering_fixture",
                    "company_evidence": evidence,
                    "missing_items": ["proof_missing"]
                    if state == "information_insufficient"
                    else [],
                }
            ],
        }
    if mutation:
        mutation(prompt, output)
    return ModelResponse(
        parts=[ToolCallPart(info.output_tools[0].name, output)], model_name="offline-unit"
    )


@pytest.fixture
def service(sources):
    adapter, _, material, profile, fact, _ = sources
    notice = persist(
        adapter.notices,
        BODY.replace("P-001", "P-QUAL") + "\nUNIT-1 UNIT-2 UNIT-3 UNIT-4",
        path="qualification-only",
        at=AT + timedelta(minutes=1),
    )
    # Process recovery uses the real clock; its synthetic input must be fresh
    # before the worker fixes a manifest, without changing historical versions.
    persist(
        adapter.notices,
        BODY.replace("P-001", "P-QUAL") + "\nUNIT-1 UNIT-2 UNIT-3 UNIT-4",
        path="qualification-only",
        at=datetime.now(UTC),
    )
    runtime = ScreeningService(
        adapter.company,
        adapter.notices,
        ScreeningSettings(enabled=True, max_requests=20),
        FunctionModel(scripted_response, model_name="offline-unit"),
    )
    return runtime, notice, profile, material, fact


def run(case, **updates):
    runtime, notice, profile, _, _ = case
    return asyncio.run(
        runtime.checklist(
            ALICE,
            profile,
            notice.project_id,
            [notice.packages[0].package_id],
            as_of=datetime.now(UTC),
            **updates,
        )
    )


def test_real_agent_full_conditions_both_evidence_and_immutable_history(service):
    runtime, _, profile, _, _ = service
    result = run(service)
    assert result["state"] == "completed", result
    assert len(result["inventory"]["conditions"]) == 4
    assert result["human_review_state"] == "unreviewed"
    assert result["recommendation"] == "status_unknown"
    assert len(result["groups"]) == 1
    assessment = ScreeningAssessment.model_validate(result["groups"][0]["assessment"])
    assert len(assessment.assessments) == 4
    assert all(
        a.status == "satisfied" and a.company_evidence and a.notice_evidence
        for a in assessment.assessments
    )
    assert result["provenance"]["model_calls"] == 5
    assert result["provenance"]["actual_models"] == ["offline-unit"]
    assert result["provenance"]["estimated_cost_cny"] == "0"
    old_manifest = copy.deepcopy(result["manifest"])
    runtime.sources.company.repo.put(
        ALICE,
        payload(display_name="changed"),
        object_id=profile["object_id"],
        expected_version=profile["version_id"],
    )
    again = runtime.read(ALICE, result["run_id"])
    assert again["manifest"] == old_manifest
    assert again["groups"] == result["groups"]
    assert not again["source_state"]["current"]
    assert again["provenance"] == result["provenance"]
    assert len(runtime.repository.get(ALICE, result["run_id"])["raw_model_outputs"]) == 5
    with pytest.raises(CompanyError):
        runtime.read(BOB, result["run_id"])


def test_bad_lock_path_preserves_completed_run_with_explicit_unknown_metadata(
    service, monkeypatch, tmp_path
):
    monkeypatch.setenv("BIDOPS_LOCK_PATH", str(tmp_path))
    result = run(service)
    assert result["state"] == "completed"
    assert result["provenance"]["model_calls"] == 5
    assert result["provenance"]["lock_sha256"] is None
    assert service[0].read(ALICE, result["run_id"])["result_revision"] == result["result_revision"]


@pytest.mark.parametrize(
    "mutation", ["no_proof", "quote", "identity", "human_approval", "validity"]
)
def test_model_cannot_forge_proof_or_human_decision(service, mutation):
    runtime = service[0]

    def mutate(prompt, output):
        if prompt["stage"] == "assessment":
            atom = output["judgments"][0]
            if mutation == "no_proof":
                atom["company_evidence"] = []
            elif mutation == "quote":
                atom["company_evidence"][0]["quote"] = "FORGED123"
            elif mutation == "identity":
                atom["company_evidence"][0]["evidence_id"] = "invented"
            elif mutation == "validity":
                atom["company_evidence"][0]["validity"] = {"kind": "perpetual"}
            else:
                output["human_review_state"] = "approved"

    runtime.model_override = FunctionModel(lambda m, i: scripted_response(m, i, mutation=mutate))
    result = run(service)
    assert result["state"] == "failed"
    assert result["human_review_state"] == "unreviewed"
    assert result["recommendation"] == "not_evaluated"
    assert len(result["inventory"]["conditions"]) == 4
    assert all(
        a["status"] == "information_insufficient"
        for g in result["groups"]
        for a in g["assessment"]["assessments"]
    )
    assert result["provenance"]["model_calls"] == 3


def test_request_limit_retains_partial_inventory_and_unknowns(service):
    runtime = service[0]
    runtime.settings.max_requests = 1
    result = run(service)
    assert result["state"] == "failed" and result["error_code"] == "screening_request_limit"
    assert len(result["inventory"]["conditions"]) == 4
    assert result["provenance"]["model_calls"] == 1
    assert result["recommendation"] == "not_evaluated"


def test_timeout_before_extraction_does_not_omit_unprocessed_document(service):
    runtime = service[0]
    runtime.settings.max_seconds = 0.01

    async def slow(messages, info):
        await asyncio.sleep(0.2)
        return scripted_response(messages, info)

    runtime.model_override = FunctionModel(slow)
    result = run(service)
    assert result["state"] == "timed_out"
    assert result["inventory"]["unresolved"]
    assert result["inventory"]["documents"][0]["segments"][0]["disposition"] == "unprocessed"
    assert result["recommendation"] == "not_evaluated"
    assert result["provenance"]["usage_complete"] is False


def test_revocation_during_model_call_cannot_release_or_save_result(service):
    runtime, _, _, material, _ = service

    def revoke(messages, info):
        runtime.sources.company.db.execute(
            "DELETE FROM company_grants WHERE object_id=:id", {"id": material["object_id"]}
        )
        return scripted_response(messages, info)

    runtime.model_override = FunctionModel(revoke)
    with pytest.raises(CompanyError):
        run(service)
    assert runtime.repository.db.rows("SELECT * FROM qualification_results") == []


def test_corrections_do_not_apply_old_hard_failure_as_current_exclusion(service):
    runtime, notice, _, _, _ = service
    persist(
        runtime.sources.notices,
        BODY.replace("P-001", "P-QUAL").replace("招标公告", "更正公告") + "\nUNIT-9",
        path="qualification-correction",
        at=AT + timedelta(minutes=2),
    )
    runtime.model_override = FunctionModel(
        lambda m, i: scripted_response(m, i, state="not_satisfied")
    )
    result = run(service)
    assert len(result["groups"]) == 2
    assert any(
        u["reason"] == "revision_precedence_requires_review"
        for u in result["inventory"]["unresolved"]
    )
    assert result["recommendation"] == "not_evaluated"
    assert result["manifest"]["project_id"] == notice.project_id


def test_candidate_batch_public_changes_are_visible_without_changing_old_page(service):
    runtime, _, profile, _, _ = service
    batch = runtime.candidates(ALICE, profile, CandidateFilters())
    persist(runtime.sources.notices, BODY.replace("P-001", "P-NEW"), path="new-project")
    again = runtime.candidate_page(ALICE, batch["batch_id"])
    assert again["items"] == batch["items"]
    assert again["manifest_sha256"] == batch["manifest_sha256"]
    assert "public_catalog_changed" in again["source_state"]["stale_reasons"]


def test_default_off_and_no_live_key_or_ledger_does_not_start_agent(service):
    runtime = service[0]
    runtime.settings.enabled = False
    with pytest.raises(CompanyError, match="screening_disabled"):
        run(service)
    runtime.settings.enabled = True
    runtime.model_override = None
    with pytest.raises(CompanyError, match="screening_model_unavailable"):
        run(service)
    assert not runtime.status()["live_calls_enabled"]
    assert runtime.repository.db.rows("SELECT * FROM agent_runs") == []
