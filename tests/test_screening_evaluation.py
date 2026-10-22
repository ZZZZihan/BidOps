"""Error injection against synthetic UNIT records; never Agent acceptance results."""

import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest
from bidops.screening.evaluation import (
    P1_FIELDS,
    GoldTask,
    ScreeningEvaluator,
    same_field,
    score_p1_fields,
    score_screening,
)
from pydantic import ValidationError
from pydantic_evals import Case, Dataset

FIXTURE = Path(__file__).parents[1] / "evals/enterprise-v1/engineering-fixture.json"


@pytest.fixture
def corpus():
    return json.loads(FIXTURE.read_text())


def score(corpus):
    return score_screening(corpus["gold_tasks"], corpus["observed_tasks"], corpus["evidence"])


def test_oracle_agreement_is_only_engineering_score(corpus):
    report = score(corpus)
    assert report["status"] == "PASS"
    assert report["business_acceptance"] == "NOT_ASSESSED"
    assert report["metrics"]["quality"]["numerator"] == 5
    assert report["metrics"]["quality"]["denominator"] == 5
    assert set(report["p1_fields"]) == set(P1_FIELDS)


@pytest.mark.parametrize("index", [1, 2, 3])
def test_hard_false_satisfied_is_serious_even_with_invalid_schema(corpus, index):
    pred = corpus["observed_tasks"][index]
    pred["positive_recommendation"] = False  # 'Pending review' does not excuse the claim.
    row = pred["assessment"]["assessments"][0]
    row.update(status="satisfied", company_evidence=[])
    report = score(corpus)
    assert report["status"] == "FAIL"
    assert f"UNIT-{index}:license" in report["serious_errors"]
    assert report["metrics"]["quality"]["denominator"] == 5
    assert report["metrics"]["completion"]["numerator"] == 4


def test_duplicate_raw_claim_cannot_hide_serious_error(corpus):
    pred = corpus["observed_tasks"][2]
    safe = pred["assessment"]["assessments"][0]
    unsafe = copy.deepcopy(safe)
    unsafe["status"] = "satisfied"
    pred["assessment"]["assessments"] = [unsafe, safe]
    report = score(corpus)
    assert "UNIT-2:license" in report["serious_errors"]
    assert report["status"] == "FAIL"


def test_missed_veto_and_terminated_recommendation(corpus):
    pred = corpus["observed_tasks"][1]
    pred["assessment"]["conditions"] = []
    pred["assessment"]["assessments"] = []
    pred["positive_recommendation"] = True
    report = score(corpus)
    assert report["metrics"]["hard_condition_recall"]["value"] == 0.75
    assert report["failure_ids"]["hard_veto_bypassed"] == ["UNIT-1:license"]
    assert report["failure_ids"]["terminated_recommended"] == ["UNIT-1"]
    assert report["status"] == "FAIL"


def test_logic_or_package_mutation_counts_as_missed_condition(corpus):
    condition = corpus["observed_tasks"][0]["assessment"]["conditions"][0]
    condition["package_scope"] = {
        "kind": "specific",
        "package_ids": ["other-package"],
        "evidence_ids": ["UNIT-0-notice-ref"],
    }
    report = score(corpus)
    assert report["metrics"]["hard_condition_recall"]["value"] == 0.75
    assert report["status"] == "FAIL"


@pytest.mark.parametrize("mutation", ["id", "quote", "version", "span", "hash", "unreadable"])
def test_invalid_citation_is_detected(corpus, mutation):
    ref = corpus["observed_tasks"][0]["assessment"]["assessments"][0]["company_evidence"][0]
    if mutation == "id":
        ref["evidence_id"] = "invented"
    elif mutation == "quote":
        ref["quote"] = "X" * len(ref["quote"])
    elif mutation == "version":
        ref["version"]["version_id"] = "invented"
    elif mutation == "span":
        ref["start"] += 1
        ref["end"] += 1
    elif mutation == "hash":
        ref["text_sha256"] = "a" * 64
    else:
        next(r for r in corpus["evidence"] if r["reference"]["evidence_id"] == ref["evidence_id"])[
            "readable"
        ] = False
    report = score(corpus)
    assert report["metrics"]["citation_resolution"]["value"] < 1
    assert report["status"] == "FAIL"


def test_resolvable_but_irrelevant_evidence_is_not_semantic_support(corpus):
    # An unrelated, authentic span in the same material version is still unsupported.
    pred = corpus["observed_tasks"][0]
    old = pred["assessment"]["assessments"][0]["company_evidence"][0]
    irrelevant = copy.deepcopy(old)
    text = "UNIT synthetic billing address; this proves no license."
    irrelevant.update(
        evidence_id="unit-irrelevant-address",
        quote=text,
        end=len(text),
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    corpus["evidence"].append(
        {"reference": irrelevant, "source_text": text, "readable": True, "current": True}
    )
    pred["assessment"]["assessments"][0]["company_evidence"] = [irrelevant]
    report = score(corpus)
    assert report["metrics"]["citation_resolution"]["value"] == 1
    assert report["metrics"]["evidence_support"]["value"] == 4 / 5
    assert report["metrics"]["definite_error_rate_conditions"]["value"] == 1 / 3
    assert report["status"] == "FAIL"


def test_right_evidence_for_opposite_claim_is_unsupported(corpus):
    corpus["observed_tasks"][0]["assessment"]["assessments"][0]["status"] = "not_satisfied"
    report = score(corpus)
    assert report["metrics"]["citation_resolution"]["value"] == 1
    assert report["metrics"]["evidence_support"]["value"] == 4 / 5


@pytest.mark.parametrize("disclosed", [False, True, "false"])
def test_current_stale_evidence_cannot_support_current_claim(corpus, disclosed):
    next(r for r in corpus["evidence"] if r["reference"]["evidence_id"] == "UNIT-0-company-ref")[
        "current"
    ] = False
    corpus["observed_tasks"][0]["stale_disclosed"] = disclosed
    report = score(corpus)
    assert report["metrics"]["evidence_support"]["status"] == "FAIL"
    assert bool(report["version_errors"]) == (disclosed is not True)
    assert report["status"] == "FAIL"


def test_historical_scope_explicitly_retains_old_versions(corpus):
    next(r for r in corpus["evidence"] if r["reference"]["evidence_id"] == "UNIT-0-company-ref")[
        "current"
    ] = False
    corpus["gold_tasks"][0]["scope"] = "historical"
    assert score(corpus)["status"] == "PASS"


@pytest.mark.parametrize("state", ["failed", "timed_out", "cancelled", "interrupted", "missing"])
def test_unfinished_tasks_stay_in_all_registered_denominators(corpus, state):
    if state == "missing":
        corpus["observed_tasks"].pop(0)
    else:
        corpus["observed_tasks"][0]["state"] = state
    report = score(corpus)
    assert report["metrics"]["quality"]["denominator"] == 5
    assert report["metrics"]["quality"]["numerator"] == 4
    assert report["metrics"]["hard_condition_recall"]["denominator"] == 4
    assert report["metrics"]["candidate_recall_packages"]["denominator"] == 4
    assert report["p1_fields"]["project_code"]["recall"]["value"] == 0.8
    assert report["status"] == "FAIL"


def test_all_abstain_cannot_pass(corpus):
    for pred in corpus["observed_tasks"]:
        row = pred["assessment"]["assessments"][0]
        row.update(
            status="information_insufficient",
            company_evidence=[],
            conflicting_evidence_ids=[],
            missing_items=["UNIT all abstain"],
        )
        pred["fields"] = dict.fromkeys(P1_FIELDS)
    report = score(corpus)
    assert report["metrics"]["decidable_coverage"]["value"] == 0
    assert report["metrics"]["definite_error_rate_conditions"]["value"] is None
    assert report["metrics"]["definite_error_rate_conditions"]["status"] == "BLOCKED"
    assert report["status"] == "FAIL"


def test_zero_denominator_or_missing_critical_stratum_never_passes(corpus):
    empty = score_screening([], [], [])
    assert empty["status"] == "BLOCKED"
    assert all(m["value"] is None for m in empty["metrics"].values())
    corpus["gold_tasks"] = corpus["gold_tasks"][:1]
    corpus["observed_tasks"] = corpus["observed_tasks"][:1]
    partial = score(corpus)
    assert partial["status"] == "BLOCKED"
    assert partial["metrics"]["serious_false_satisfied:not_satisfied"]["denominator"] == 0


@pytest.mark.parametrize("field", P1_FIELDS)
def test_every_p1_field_is_individually_scored(corpus, field):
    corpus["observed_tasks"][0]["fields"][field] = "UNIT wrong value"
    result = score(corpus)["p1_fields"][field]
    assert result["counts"]["incorrect"] == 1
    assert result["precision"]["value"] == 0.8
    assert result["precision"]["failure_ids"] == ["UNIT-0"]


def test_unknown_is_not_zero_or_correct_known_value(corpus):
    for task in corpus["gold_tasks"]:
        task["fields"]["bid_deadline_at"] = None
    for pred in corpus["observed_tasks"]:
        pred["fields"]["bid_deadline_at"] = None
    report = score(corpus)
    field = report["p1_fields"]["bid_deadline_at"]
    assert field["precision"]["value"] is None
    assert field["coverage"]["status"] == "BLOCKED"
    assert field["unknown_accuracy"]["value"] == 1
    assert report["status"] == "BLOCKED"
    corpus["observed_tasks"][0]["fields"]["bid_deadline_at"] = "2026-11-01T00:00:00Z"
    assert score(corpus)["p1_fields"]["bid_deadline_at"]["counts"]["incorrect"] == 1


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-1", "abc", ""])
def test_money_rejects_nonfinite_negative_and_non_decimal(value):
    money = {"amount": value, "currency": "CNY", "unit": "元"}
    assert not same_field("budget", money, money)


def test_money_exact_units_and_timezone_instant_equality():
    yuan = {"amount": "123456.78", "currency": "CNY", "unit": "元"}
    wan = {"amount": "12.345678", "currency": "CNY", "unit": "万元"}
    assert same_field("budget", yuan, wan)
    assert not same_field("budget", yuan, dict(yuan, amount=123456.78))
    assert not same_field("budget", yuan, dict(yuan, currency="USD"))
    assert not same_field("budget_unit", "元", "万元")
    assert same_field("bid_deadline_at", "2026-11-06T08:30:00+08:00", "2026-11-06T00:30:00Z")
    assert not same_field("bid_deadline_at", "2026-11-06T08:30:00+08:00", "2026-11-06T00:30:00")


@pytest.mark.parametrize("bad", [[], {}, 1, None])
@pytest.mark.parametrize("key", ["condition_id", "status"])
def test_malformed_raw_json_never_drops_entire_batch(corpus, bad, key):
    corpus["observed_tasks"][0]["assessment"]["assessments"][0][key] = bad
    report = score(corpus)
    assert report["counts"]["registered_tasks"] == 5
    assert report["status"] == "FAIL"


def test_duplicate_and_unregistered_outputs_cannot_cherry_pick(corpus):
    corpus["observed_tasks"].append(copy.deepcopy(corpus["observed_tasks"][0]))
    corpus["observed_tasks"].append({"task_id": []})
    report = score(corpus)
    assert report["counts"]["registered_tasks"] == 5
    assert len(report["protocol_errors"]) == 2
    assert report["status"] == "FAIL"


def test_gold_must_be_complete_and_evidence_bound(corpus):
    del corpus["gold_tasks"][0]["fields"]["title"]
    with pytest.raises(ValidationError, match="all_p1_fields"):
        score(corpus)


def test_gold_support_cannot_import_another_company(corpus):
    corpus["gold_tasks"][0]["conditions"][0]["support_bundles"] = [
        ["UNIT-0-notice-ref", "UNIT-1-company-ref"]
    ]
    with pytest.raises(ValueError, match="version_not_in_inputs"):
        score(corpus)


def test_conflict_requires_actual_reviewed_conflicting_evidence(corpus):
    row = corpus["observed_tasks"][3]["assessment"]["assessments"][0]
    unrelated = copy.deepcopy(row["company_evidence"][1])
    unrelated["evidence_id"] = "unit-unrelated"
    corpus["evidence"].append(
        {
            "reference": unrelated,
            "source_text": unrelated["quote"],
            "readable": True,
            "current": True,
        }
    )
    row["company_evidence"][1] = unrelated
    row["conflicting_evidence_ids"][1] = unrelated["evidence_id"]
    report = score(corpus)
    assert report["metrics"]["citation_resolution"]["value"] == 1
    assert report["rows"][3]["correct"] is False
    assert report["metrics"]["evidence_support"]["value"] == 0.8
    assert report["status"] == "FAIL"


def test_missing_items_must_match_reviewed_oracle(corpus):
    corpus["observed_tasks"][2]["assessment"]["assessments"][0]["missing_items"] = [
        "UNIT an unrelated address is missing"
    ]
    report = score(corpus)
    assert report["rows"][2]["correct"] is False
    assert report["status"] == "FAIL"


def test_rejected_duplicate_attempt_remains_in_safety_audit(corpus):
    retry = copy.deepcopy(corpus["observed_tasks"][2])
    retry["assessment"]["assessments"][0]["status"] = "satisfied"
    corpus["observed_tasks"].append(retry)
    report = score(corpus)
    assert report["metrics"]["quality"]["numerator"] == 5  # Fixed first attempt.
    assert report["metrics"]["quality"]["denominator"] == 5
    assert report["serious_errors"] == ["UNIT-2:license"]
    assert report["rejected_attempts"][0]["serious_errors"] == ["UNIT-2:license"]
    assert report["status"] == "FAIL"


def test_mutated_oracle_instance_revalidates(corpus):
    task = GoldTask.model_validate(corpus["gold_tasks"][0])
    task.fields["budget"] = {"amount": "Infinity", "currency": "CNY", "unit": "元"}
    with pytest.raises(ValidationError, match="invalid_gold_field"):
        score_screening([task], [], corpus["evidence"])


def test_money_comparison_does_not_round_to_decimal_context_precision():
    left = {"amount": "123456789012345678901234567890.1234", "currency": "CNY", "unit": "元"}
    right = dict(left, amount="12345678901234567890123456.78901234", unit="万元")
    different = dict(left, amount="123456789012345678901234567890.1235")
    assert same_field("budget", left, right)
    assert not same_field("budget", left, different)


def test_invalid_raw_values_are_not_echoed_into_public_report(corpus):
    private = "UNIT-private-body-do-not-publish"
    pred = corpus["observed_tasks"][0]
    pred["state"] = private
    pred["assessment"]["assessments"][0]["status"] = private
    pred["assessment"]["assessments"][0]["condition_id"] = private
    report = score(corpus)
    assert report["status"] == "FAIL"
    assert private not in json.dumps(report)


@pytest.mark.parametrize("field", ["bid_deadline_at", "budget_unit"])
@pytest.mark.parametrize(
    "missing,wrong,expected", [(3, 0, "PASS"), (4, 0, "FAIL"), (0, 1, "PASS"), (0, 2, "FAIL")]
)
def test_inherited_p1_threshold_boundaries(corpus, field, missing, wrong, expected):
    gold, outputs = [], {}
    for i in range(20):
        task = GoldTask.model_validate(corpus["gold_tasks"][0])
        task.task_id = f"UNIT-threshold-{i}"
        gold.append(task)
        pred = copy.deepcopy(corpus["observed_tasks"][0])
        if i < missing:
            pred["fields"][field] = None
        elif i < missing + wrong:
            pred["fields"][field] = "UNIT wrong"
        outputs[task.task_id] = pred
    result = score_p1_fields(gold, outputs)[field]
    assert result["coverage" if missing else "precision"]["status"] == expected


def test_pydantic_evals_uses_same_scorer_and_does_not_call_model(corpus):
    by_id = {p["task_id"]: p for p in corpus["observed_tasks"]}
    dataset = Dataset(
        name="B01 engineering scorer fixture",
        cases=[
            Case(name=t["task_id"], inputs=GoldTask.model_validate(t)) for t in corpus["gold_tasks"]
        ],
        evaluators=[ScreeningEvaluator(evidence=corpus["evidence"])],
    )
    report = asyncio.run(dataset.evaluate(lambda task: by_id[task.task_id], progress=False))
    assert len(report.cases) == 5
    assert not report.failures
    assert all(
        all(assertion.value for assertion in case.assertions.values()) for case in report.cases
    )
