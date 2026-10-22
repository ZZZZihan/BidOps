"""Synthetic declarations exercise audits; no real gold, people or permission.

The 'independent_acceptance' fixtures model well-formed declarations only. A local
PASS must still leave business acceptance BLOCKED for external authentication.
"""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bidops.acceptance.evaluation import (
    BUSINESS_ACS,
    audit_evaluation,
    canonical_sha256,
    gold_binding,
)
from bidops.screening.eval_protocol import RISKS, records_digest, select_gold_record_ids
from bidops.screening.evaluation import P1_FIELDS

FIXTURE = Path(__file__).parents[1] / "evals/enterprise-v1/engineering-fixture.json"
NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def iso(seconds=0):
    return (NOW + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def fixture_bundle():
    value = json.loads(FIXTURE.read_text())
    value.pop("fixture_label")
    value.update(
        sampling={
            "purpose": "engineering_fixture",
            "seed": 127,
            "candidate_pool_sha256": records_digest([]),
            "selected_record_ids": [],
        },
        annotations=[],
        contributors=[],
    )
    return value


def codes(report, section=None):
    issues = report["sections"][section].get("issues", []) if section else report["issues"]
    return {issue["code"] for issue in issues}


def _clone(value, prefix, target, index):
    if isinstance(value, list):
        return [_clone(v, prefix, target, index) for v in value]
    if isinstance(value, dict):
        return {
            k: digest(f"version:{index}:{v}") if k == "sha256" else _clone(v, prefix, target, index)
            for k, v in value.items()
        }
    return value.replace(prefix, target) if isinstance(value, str) else value


def acceptance(offset=0):
    """Thirty generated records, twenty bound tasks, ten pairs: declarations only."""
    original = json.loads(FIXTURE.read_text())
    records, tasks, outputs, evidence = [], {}, {}, []
    for index in range(30):
        number, template = offset + index, index % 5
        prefix, target = f"UNIT-{template}", f"SYN-{number}"
        task = _clone(original["gold_tasks"][template], prefix, target, number)
        observed = _clone(original["observed_tasks"][template], prefix, target, number)
        oracle = _clone(
            [
                r
                for r in original["evidence"]
                if r["reference"]["evidence_id"].startswith(prefix + "-")
            ],
            prefix,
            target,
            number,
        )
        refs = {}
        for item in oracle:
            ref = item["reference"]
            item["source_text"] += f"\nSYNTHETIC RECORD {number}; not real evidence."
            ref["text_sha256"] = digest(item["source_text"])
            ref["start"] = item["source_text"].index(ref["quote"])
            ref["end"] = ref["start"] + len(ref["quote"])
            refs[ref["evidence_id"]] = ref

        def replace_refs(value, references=refs):
            if isinstance(value, list):
                return [replace_refs(v) for v in value]
            if isinstance(value, dict):
                if "evidence_id" in value:
                    return copy.deepcopy(references[value["evidence_id"]])
                return {key: replace_refs(v) for key, v in value.items()}
            return value

        task, observed = replace_refs(task), replace_refs(observed)
        deadline = iso(-100 if index < 15 else 100)
        for field_map in (task["fields"], observed["fields"]):
            field_map.update(
                source_url=f"https://synthetic.example/{number}",
                project_code=f"SYN-PROJECT-{number}",
                bid_deadline_at=deadline,
                notice_kind=["tender", "correction", "award"][index % 3],
            )
        record = {
            "record_id": f"r{number}",
            "split": "acceptance",
            "family_id": task["inputs"]["project_family_id"],
            "notice_id": task["inputs"]["notice_revision"]["object_id"],
            "project_code": task["fields"]["project_code"],
            "source_url": task["fields"]["source_url"],
            "raw_sha256": task["inputs"]["notice_revision"]["sha256"],
            "text_sha256": task["conditions"][0]["condition"]["notice_evidence"][0]["text_sha256"],
            "package_ids": [task["package_id"]],
            "provenance_kind": "real",
            "source_permission": "allowed",
            "notice_category": task["fields"]["notice_kind"],
            "layout": f"layout-{index % 2}",
            "captured_at": iso(),
            "deadline_at": deadline,
            "risk_types": list(RISKS),
        }
        records.append(record)
        tasks[record["record_id"]], outputs[record["record_id"]] = task, observed
        evidence.extend(oracle)
    selected = select_gold_record_ids(records, 127)
    annotations = [
        {
            "record_id": r["record_id"],
            "family_id": r["family_id"],
            **gold_binding([tasks[r["record_id"]]]),
            "source_sha256": r["raw_sha256"],
            "purpose": "real_independent",
            "source_available": True,
            "source_refs": [f"synthetic:{r['record_id']}"],
            "annotator_id": "annotator",
            "reviewer_id": "reviewer",
            "annotated_at": iso(-200),
            "reviewed_at": iso(-100),
        }
        for r in records
        if r["record_id"] in selected
    ]
    people = [
        {
            "person_id": name,
            "actual_human": True,
            "independent": True,
            "attestation_ref": "SYNTHETIC declaration only",
        }
        for name in ("annotator", "reviewer", "operator-a", "operator-b")
    ]
    assignments, observations = [], []
    for index, gold in enumerate(annotations[:10]):
        for mode in ("manual", "assisted"):
            task_id = f"trial-{index}-{mode}"
            operator = "operator-a" if (index % 2 == 0) == (mode == "manual") else "operator-b"
            assignments.append(
                {
                    "task_id": task_id,
                    "pair_id": f"pair-{index}",
                    "family_id": gold["family_id"],
                    "mode": mode,
                    "operator_id": operator,
                    "input_sha256": gold["input_sha256"],
                    "gold_record_id": gold["record_id"],
                    "gold_sha256": gold["gold_sha256"],
                    "deliverable_sha256": digest(f"deliverable:{index}"),
                    "quality_unit_ids": gold["quality_unit_ids"],
                    "time_limit_seconds": 200,
                    "order": index + 1,
                    "blind_to_gold": True,
                    "previously_performed": False,
                }
            )
            elapsed = 100 if mode == "manual" else 70
            observations.append(
                {
                    "task_id": task_id,
                    "status": "completed",
                    "started_at": iso(1000 + 500 * index),
                    "stopped_at": iso(1000 + 500 * index + elapsed),
                    "active_seconds": elapsed,
                    "waiting_seconds": 0,
                    "correct_unit_ids": gold["quality_unit_ids"],
                }
            )
    payload = {
        "purpose": "independent_acceptance",
        "dataset_records": records,
        "sampling": {
            "purpose": "independent_acceptance",
            "seed": 127,
            "candidate_pool_sha256": records_digest(records),
            "selected_record_ids": selected,
        },
        "annotations": annotations,
        "contributors": people,
        "gold_tasks": [tasks[key] for key in selected],
        "observed_tasks": [outputs[key] for key in selected],
        "evidence": evidence,
        "task_bindings": [{"task_id": tasks[key]["task_id"], "record_id": key} for key in selected],
        "trial_plan": {
            "purpose": "independent_acceptance",
            "frozen_at": iso(),
            "randomization_seed": 127,
            "allocation_record_ref": "SYNTHETIC allocations",
            "start_rule": "open fixed source",
            "stop_rule": "save final answers",
            "assignments": assignments,
        },
        "trial_observations": observations,
    }
    payload["blind_history"] = {
        "current_run_id": f"run-{offset}",
        "runs": [history_run(payload, f"run-{offset}")],
    }
    return payload


def history_run(payload, name, *, role="blind", at=0, tuning=False, outcome="PASS"):
    return {
        "run_id": name,
        "role": role,
        "dataset_records": copy.deepcopy(payload["dataset_records"]),
        "dataset_sha256": records_digest(payload["dataset_records"]),
        "result_sha256": canonical_sha256(payload["observed_tasks"]),
        "frozen_at": iso(at - 50),
        "started_at": iso(at),
        "finished_at": iso(at + 100),
        "answer_accessed_at": iso(at + 101),
        "answers_used_for_tuning": tuning,
        "outcome": outcome,
    }


def test_fixture_quality_pass_is_not_any_business_gate(fixture_bundle):
    report = audit_evaluation(fixture_bundle)
    assert report["status"] == "PASS"
    assert report["sections"]["screening"]["status"] == "PASS"
    assert all(report["ac_status"][name] == "BLOCKED" for name in BUSINESS_ACS)
    assert report["ac_status"]["B08-03"] == report["ac_status"]["B08-07"] == "PASS"
    assert report["business_acceptance"] == "BLOCKED" and report["agent_score"] is None
    assert report["sections"]["pydantic_evals"]["cases"] == 5
    assert set(report["sections"]["screening"]["p1_fields"]) == set(P1_FIELDS)
    assert "source_text" not in json.dumps(report)


def test_complete_supplied_declarations_still_need_external_acceptance():
    report = audit_evaluation(acceptance())
    assert report["status"] == "PASS", report["issues"]
    assert set(report["ac_status"].values()) == {"PASS"}
    assert report["business_acceptance"] == "BLOCKED"
    assert report["sections"]["bindings"]["counts"] == {
        "records": 20,
        "families": 20,
        "packages": 20,
        "company_project_pairs": 20,
    }
    assert report["sections"]["human_trials"]["quality"]["manual"]["denominator"] == 10


@pytest.mark.parametrize(
    "state", ["failed", "timed_out", "cancelled", "interrupted", "missing", "unknown"]
)
def test_every_registered_task_retains_all_denominators(fixture_bundle, state):
    if state == "missing":
        fixture_bundle["observed_tasks"].pop(0)
    else:
        fixture_bundle["observed_tasks"][0]["state"] = state
    report = audit_evaluation(fixture_bundle)
    scored = report["sections"]["screening"]
    assert report["status"] == "FAIL"
    assert scored["metrics"]["quality"]["denominator"] == 5
    assert scored["metrics"]["quality"]["numerator"] == 4
    assert report["sections"]["registration"]["registered"] == 5
    assert report["sections"]["pydantic_evals"]["cases"] == 5
    for value in scored["p1_fields"].values():
        assert value["counts"]["registered"] == 5
    assert report["sections"]["registration"]["tasks"][0]["state"] == state


def test_duplicate_attempt_never_replaces_failed_first(fixture_bundle):
    second = copy.deepcopy(fixture_bundle["observed_tasks"][0])
    fixture_bundle["observed_tasks"][0]["state"] = "failed"
    fixture_bundle["observed_tasks"].append(second)
    report = audit_evaluation(fixture_bundle)
    assert report["status"] == "FAIL" and "duplicate_output" in codes(report)
    assert report["sections"]["screening"]["metrics"]["quality"]["numerator"] == 4


def test_dangerous_rejected_retry_is_still_audited(fixture_bundle):
    second = copy.deepcopy(fixture_bundle["observed_tasks"][1])
    second["assessment"]["assessments"][0].update(status="satisfied", company_evidence=[])
    fixture_bundle["observed_tasks"].append(second)
    report = audit_evaluation(fixture_bundle)
    assert report["sections"]["screening"]["serious_errors"] == ["UNIT-1:license"]
    assert "serious_errors" in codes(report)


@pytest.mark.parametrize("output", [None, [], {"task_id": []}, {"task_id": "not-registered"}])
def test_unregistered_malformed_output_does_not_crash_or_drop_tasks(fixture_bundle, output):
    fixture_bundle["observed_tasks"].append(output)
    report = audit_evaluation(fixture_bundle)
    assert report["status"] == "FAIL"
    assert "unregistered_output" in codes(report)
    assert report["sections"]["screening"]["metrics"]["quality"]["denominator"] == 5


@pytest.mark.parametrize("field", ["budget_unit", "bid_deadline_at", "budget"])
def test_p1_uses_frozen_exact_numeric_and_timezone_rules(fixture_bundle, field):
    observed = fixture_bundle["observed_tasks"][0]["fields"]
    observed[field] = (
        {"amount": "1e9999", "currency": "CNY", "unit": "元"}
        if field == "budget"
        else (
            "元"
            if field == "budget_unit" and observed[field] == "万元"
            else "万元"
            if field == "budget_unit"
            else "2026-10-23T08:00:00"
        )
    )
    scored = audit_evaluation(fixture_bundle)["sections"]["screening"]["p1_fields"][field]
    assert scored["counts"]["incorrect"] >= 1
    assert scored["precision"]["denominator"] == 5


def test_empty_and_all_abstained_are_not_green(fixture_bundle):
    empty = copy.deepcopy(fixture_bundle)
    empty.update(gold_tasks=[], observed_tasks=[], evidence=[])
    report = audit_evaluation(empty)
    assert report["status"] == "BLOCKED" and report["ac_status"]["B08-03"] == "BLOCKED"
    for row in fixture_bundle["observed_tasks"]:
        row["fields"] = {}
        row["assessment"]["conditions"] = []
        row["assessment"]["assessments"] = []
    report = audit_evaluation(fixture_bundle)
    assert report["status"] == "FAIL"
    assert report["sections"]["screening"]["p1_fields"]["budget_unit"]["precision"]["value"] is None


def test_zero_required_p1_denominator_is_blocked_not_a_perfect_score(fixture_bundle):
    for task in fixture_bundle["gold_tasks"]:
        task["fields"]["budget_unit"] = None
    for observed in fixture_bundle["observed_tasks"]:
        observed["fields"]["budget_unit"] = None
    report = audit_evaluation(fixture_bundle)
    p1 = report["sections"]["screening"]["p1_fields"]["budget_unit"]
    assert p1["precision"]["denominator"] == p1["coverage"]["denominator"] == 0
    assert p1["precision"]["value"] is None and p1["coverage"]["status"] == "BLOCKED"
    assert report["status"] == "BLOCKED"


@pytest.mark.parametrize("mutation", ["company_side", "stale", "unreadable"])
def test_double_sided_support_and_version_errors_survive_aggregation(fixture_bundle, mutation):
    if mutation == "company_side":
        fixture_bundle["observed_tasks"][0]["assessment"]["assessments"][0]["company_evidence"] = []
    else:
        reference = next(
            r
            for r in fixture_bundle["evidence"]
            if r["reference"]["evidence_id"] == "UNIT-0-company-ref"
        )
        reference["current" if mutation == "stale" else "readable"] = False
    scored = audit_evaluation(fixture_bundle)["sections"]["screening"]
    assert scored["status"] == "FAIL"
    assert scored["metrics"]["evidence_support"]["numerator"] == 4
    assert scored["metrics"]["quality"]["denominator"] == 5
    if mutation == "stale":
        assert scored["version_errors"]


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("gold", "annotation_scoring_binding_mismatch"),
        ("input", "annotation_scoring_binding_mismatch"),
        ("unit", "annotation_scoring_binding_mismatch"),
        ("source", "task_source_binding_mismatch"),
        ("family", "task_family_binding_mismatch"),
        ("text", "task_text_binding_mismatch"),
        ("deadline", "task_deadline_binding_mismatch"),
        ("category", "task_category_binding_mismatch"),
        ("package", "incomplete_record_package_tasks"),
        ("drop_task", "selected_gold_not_scored"),
        ("duplicate_binding", "duplicate_task_binding"),
    ],
)
def test_gold_and_all_package_inputs_are_bound(mutation, expected):
    payload = acceptance()
    record_id = payload["task_bindings"][0]["record_id"]
    record = next(r for r in payload["dataset_records"] if r["record_id"] == record_id)
    annotation = next(a for a in payload["annotations"] if a["record_id"] == record_id)
    if mutation in {"gold", "input"}:
        annotation[f"{mutation}_sha256"] = "a" * 64
    elif mutation == "unit":
        annotation["quality_unit_ids"] = ["invented"]
    elif mutation == "source":
        record["raw_sha256"] = "b" * 64
    elif mutation == "family":
        record["family_id"] = "unrelated-family"
    elif mutation == "text":
        record["text_sha256"] = "c" * 64
    elif mutation == "deadline":
        record["deadline_at"] = iso(9999)
    elif mutation == "category":
        record["notice_category"] = "invented-for-coverage"
    elif mutation == "package":
        record["package_ids"].append("omitted-package")
    elif mutation == "drop_task":
        payload["gold_tasks"].pop(0)
        payload["task_bindings"].pop(0)
    else:
        payload["task_bindings"].append(copy.deepcopy(payload["task_bindings"][0]))
    report = audit_evaluation(payload)
    assert expected in codes(report, "bindings")
    assert report["ac_status"]["B08-02"] != "PASS"
    assert report["ac_status"]["B08-04"] != "PASS"


@pytest.mark.parametrize(
    "field,value", [("model_preannotated", True), ("reviewer_id", None), ("reviewed_at", None)]
)
def test_annotations_cannot_self_certify_independence(field, value):
    payload = acceptance()
    payload["annotations"][0][field] = value
    report = audit_evaluation(payload)
    assert report["ac_status"]["B08-02"] != "PASS"
    assert report["sections"]["annotations"]["status"] == "FAIL"


@pytest.mark.parametrize("state", ["failed", "timed_out", "cancelled", "interrupted", "missing"])
def test_trial_failure_stays_in_common_denominator_and_fails_time(state):
    payload = acceptance()
    if state == "missing":
        payload["trial_observations"].pop(0)
    else:
        payload["trial_observations"][0].update(
            status=state, failure_reason="synthetic stopped task"
        )
    report = audit_evaluation(payload)
    trial = report["sections"]["human_trials"]
    assert trial["time_status"] == "FAIL"
    assert trial["quality"]["manual"] == {"numerator": 9, "denominator": 10, "value": 0.9}
    assert len(trial["tasks"]) == 20 and trial["median_saving"] is None
    assert report["ac_status"]["B08-05"] == "FAIL"


def test_trial_outside_bound_dataset_cannot_borrow_unrelated_valid_gold():
    payload = acceptance()
    payload["task_bindings"] = payload["task_bindings"][1:]
    report = audit_evaluation(payload)
    assert "trial_not_bound_to_evaluation" in codes(report, "human_trials")


def test_no_plan_cannot_hide_observations():
    payload = acceptance()
    payload["trial_plan"] = None
    assert "observations_without_registered_plan" in codes(
        audit_evaluation(payload), "human_trials"
    )


def retest():
    prior, current = acceptance(), acceptance(100)
    history = history_run(prior, "old-blind", tuning=True, outcome="FAIL")
    regression = history_run(prior, "old-regression", role="regression", at=300, outcome="FAIL")
    new = history_run(current, "new-blind", at=600)
    current["blind_history"] = {"current_run_id": "new-blind", "runs": [new, history, regression]}
    return current


@pytest.mark.parametrize("relabel_all", [True, False])
def test_historical_split_relabel_cannot_hide_exposed_corpus(relabel_all):
    payload = retest()
    current, prior, regression = payload["blind_history"]["runs"]
    prior["dataset_records"][0]["family_id"] = current["dataset_records"][0]["family_id"]
    for record in prior["dataset_records"] if relabel_all else prior["dataset_records"][:1]:
        record["split"] = "dev"
    prior["dataset_sha256"] = records_digest(prior["dataset_records"])
    regression["dataset_records"] = copy.deepcopy(prior["dataset_records"])
    regression["dataset_sha256"] = prior["dataset_sha256"]
    report = audit_evaluation(payload)
    assert report["ac_status"]["B08-06"] == "FAIL"
    assert "reused_blind_corpus" in codes(report, "blind_history")
    if relabel_all:
        assert "blind_history_has_no_acceptance_records" in codes(report, "blind_history")


def test_regression_may_have_seen_old_answers_but_new_blind_cannot_overlap():
    payload = retest()
    current, prior, regression = payload["blind_history"]["runs"]
    regression["answer_accessed_at"] = prior["answer_accessed_at"]
    assert audit_evaluation(payload)["ac_status"]["B08-06"] == "PASS"
    current["started_at"] = prior["started_at"]
    report = audit_evaluation(payload)
    assert "overlapping_blind_runs" in codes(report, "blind_history")
    assert report["ac_status"]["B08-06"] == "FAIL"


def test_first_failure_is_retained_and_fresh_retest_can_pass():
    report = audit_evaluation(retest())
    assert report["ac_status"]["B08-06"] == "PASS", report["issues"]
    history = report["sections"]["blind_history"]
    assert [r["outcome"] for r in history["runs"]] == ["FAIL", "FAIL", "PASS"]
    assert history["path"] == "new_independent_retest"


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("same_family", "reused_blind_corpus"),
        ("same_raw", "reused_blind_corpus"),
        ("same_text", "reused_blind_corpus"),
        ("same_attachment", "reused_blind_corpus"),
        ("no_regression", "tuned_blind_not_retained_as_regression"),
        ("wrong_dataset_hash", "current_dataset_hash_mismatch"),
        ("wrong_result_hash", "current_result_hash_mismatch"),
        ("wrong_outcome", "current_outcome_mismatch"),
        ("early_answers", "answers_seen_before_blind_finish"),
        ("used_current_answers", "fresh_blind_required_after_tuning"),
        ("missing_tuning_time", "missing_tuning_answer_access_time"),
        ("choose_old", "current_run_not_latest_blind"),
    ],
)
def test_blind_history_rejects_reuse_cherry_pick_and_unbound_results(mutation, code):
    payload = retest()
    current, prior, regression = payload["blind_history"]["runs"]
    if mutation.startswith("same_"):
        key = {
            "same_family": "family_id",
            "same_raw": "raw_sha256",
            "same_text": "text_sha256",
            "same_attachment": "attachment_ids",
        }[mutation]
        if key == "attachment_ids":
            prior["dataset_records"][0][key] = ["shared-global-attachment"]
        current["dataset_records"][0][key] = copy.deepcopy(prior["dataset_records"][0][key])
    elif mutation == "no_regression":
        payload["blind_history"]["runs"].remove(regression)
    elif mutation == "wrong_dataset_hash":
        current["dataset_sha256"] = "a" * 64
    elif mutation == "wrong_result_hash":
        current["result_sha256"] = "a" * 64
    elif mutation == "wrong_outcome":
        current["outcome"] = "FAIL"
    elif mutation == "early_answers":
        current["answer_accessed_at"] = current["started_at"]
    elif mutation == "used_current_answers":
        current["answers_used_for_tuning"] = True
    elif mutation == "missing_tuning_time":
        prior["answer_accessed_at"] = None
    else:
        payload["blind_history"]["current_run_id"] = "old-blind"
    report = audit_evaluation(payload)
    assert code in codes(report, "blind_history")
    assert report["ac_status"]["B08-06"] != "PASS"


@pytest.mark.parametrize("payload", [None, {}, [], {"purpose": "independent_acceptance"}])
def test_invalid_or_absent_bundle_is_not_green(payload):
    report = audit_evaluation(payload)
    assert report["status"] == "FAIL" and report["business_acceptance"] == "BLOCKED"
    assert report["ac_status"]["B08-03"] == "FAIL"


@pytest.mark.parametrize("mutation", ["nan", "extra", "invalid_gold", "broken_oracle"])
def test_bad_oracle_is_explicit_not_silently_dropped(fixture_bundle, mutation):
    if mutation == "nan":
        fixture_bundle["observed_tasks"][0]["unknown"] = float("nan")
    elif mutation == "extra":
        fixture_bundle["threshold_override"] = 0
    elif mutation == "invalid_gold":
        fixture_bundle["gold_tasks"][0]["conditions"] = []
    else:
        fixture_bundle["gold_tasks"][0]["conditions"][0]["support_bundles"] = [["invented"]]
    report = audit_evaluation(fixture_bundle)
    assert report["status"] == "FAIL"
    assert report["sections"]["registration"]["registered"] == 5


def test_audit_does_not_mutate_inputs_and_hashes_are_order_stable():
    payload = acceptance()
    original = copy.deepcopy(payload)
    audit_evaluation(payload)
    assert payload == original
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
