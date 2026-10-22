"""Authored protocol records only: these fixtures are not real notices or human trials."""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bidops.screening.eval_protocol import (
    LEGACY_NOTICES,
    RISKS,
    DatasetRecord,
    records_digest,
    score_paired_trials,
    select_gold_record_ids,
    validate_annotations,
    validate_dataset_gate,
    validate_split,
    validate_trial_plan,
)

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def record(index, **overrides):
    return {
        "record_id": f"r{index}",
        "split": "acceptance",
        "family_id": f"f{index}",
        "notice_id": f"n{index}",
        "project_code": f"UNIT-{index}",
        "source_url": f"https://unit.example/{index}",
        "raw_sha256": digest(f"raw{index}"),
        "text_sha256": digest(f"text{index}"),
        "provenance_kind": "real",
        "source_permission": "allowed",
        "notice_category": f"type{index % 3}",
        "layout": f"layout{index % 2}",
        "captured_at": NOW,
        "deadline_at": NOW + timedelta(days=-1 if index < 15 else 1),
        "risk_types": list(RISKS),
        **overrides,
    }


def person(identifier, **overrides):
    return {
        "person_id": identifier,
        "actual_human": True,
        "independent": True,
        "attestation_ref": "unit-record-only",
        **overrides,
    }


def annotation(row, **overrides):
    return {
        "record_id": row["record_id"],
        "family_id": row["family_id"],
        "input_sha256": digest(f"review-input:{row['record_id']}"),
        "source_sha256": row["raw_sha256"],
        "gold_sha256": digest(row["record_id"]),
        "quality_unit_ids": ["condition1", "condition2"],
        "purpose": "real_independent",
        "source_available": True,
        "source_refs": ["unit-span"],
        "annotator_id": "annotator",
        "reviewer_id": "reviewer",
        "annotated_at": NOW,
        "reviewed_at": NOW + timedelta(minutes=1),
        **overrides,
    }


def issue_codes(report):
    return {issue["code"] for issue in report["issues"]}


@pytest.fixture
def dataset():
    rows = [record(i) for i in range(30)]
    selected = select_gold_record_ids(rows, seed=127)
    sampling = {
        "purpose": "independent_acceptance",
        "seed": 127,
        "candidate_pool_sha256": records_digest(rows),
        "selected_record_ids": selected,
    }
    annotations = [annotation(row) for row in rows if row["record_id"] in selected]
    return rows, sampling, annotations, [person("annotator"), person("reviewer"), person("arbiter")]


def test_independent_split_and_deterministic_manifest():
    rows = [record(1, split="dev"), record(2)]
    report = validate_split(rows)
    assert report["status"] == "PASS"
    assert report["counts"] == {"records": 2, "components": 2, "legacy_acceptance": 0}
    assert report["manifest_sha256"] == records_digest(list(reversed(rows)))
    rows[1]["layout"] = "changed"
    assert records_digest(rows) != report["manifest_sha256"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("family_id", "shared"),
        ("project_code", "SHARED"),
        ("notice_id", "shared"),
        ("version_chain_id", "shared"),
        ("package_ids", ["global:package:1"]),
        ("attachment_ids", ["global:attachment:1"]),
        ("related_notice_ids", ["original"]),
        ("raw_sha256", digest("duplicate")),
        ("text_sha256", digest("duplicate")),
        ("attachment_sha256s", [digest("duplicate attachment")]),
        ("source_url", "https://UNIT.example/same#fragment"),
    ],
)
def test_each_cross_split_identity_is_rejected(key, value):
    report = validate_split([record(1, split="dev", **{key: value}), record(2, **{key: value})])
    assert report["status"] == "FAIL"
    assert report["failure_ids"] == ["r1", "r2"]
    assert "split_leakage" in issue_codes(report)


def test_transitive_version_attachment_bridge_cannot_hide_leakage():
    rows = [
        record(1, split="dev", version_chain_id="version-A"),
        record(
            2,
            split="regression",
            version_chain_id="version-A",
            attachment_ids=["shared:attachment"],
        ),
        record(3, attachment_ids=["shared:attachment"]),
    ]
    assert validate_split(rows)["failure_ids"] == ["r1", "r2", "r3"]


def test_relabelled_families_do_not_turn_related_acceptance_records_independent():
    rows = [record(1, version_chain_id="same-project"), record(2, version_chain_id="same-project")]
    report = validate_split(rows)
    assert report["status"] == "FAIL"
    assert "connected_families_not_independent" in issue_codes(report)


def test_distinct_versions_may_share_one_declared_acceptance_family():
    rows = [
        record(1, family_id="same-family", version_chain_id="same-project"),
        record(2, family_id="same-family", version_chain_id="same-project"),
    ]
    assert validate_split(rows)["status"] == "PASS"


@pytest.mark.parametrize("identity_key", ["project_code", "source_url", "raw_sha256"])
def test_legacy_identity_excluded_even_if_other_identifiers_change(identity_key):
    value = getattr(LEGACY_NOTICES[0], identity_key)
    report = validate_split([record(1, **{identity_key: value})], legacy=[])
    assert report["status"] == "FAIL"
    assert "legacy_acceptance" in issue_codes(report)
    assert (
        validate_split([record(1, split="regression", **{identity_key: value})])["status"] == "PASS"
    )


def test_legacy_registry_matches_preserved_original_oracle():
    legacy = json.loads(Path("evals/reference-v1.json").read_text())["notices"]
    assert {row.project_code for row in LEGACY_NOTICES} == set(legacy)
    for row in LEGACY_NOTICES:
        assert (row.source_url, row.raw_sha256) == (
            legacy[row.project_code]["source_url"],
            legacy[row.project_code]["sha256"],
        )


def test_legacy_identity_propagates_through_alias_component():
    original = record(
        1,
        split="dev",
        project_code=LEGACY_NOTICES[0].project_code,
        version_chain_id="legacy-version",
    )
    newer = record(2, version_chain_id="legacy-version")
    assert {"legacy_acceptance", "split_leakage"} <= issue_codes(validate_split([original, newer]))


@pytest.mark.parametrize("mutation", ["missing_family", "duplicate_id", "naive_time", "bad_hash"])
def test_invalid_split_records_are_explicit_failures(mutation):
    rows = [record(1), record(2)]
    if mutation == "missing_family":
        del rows[0]["family_id"]
    elif mutation == "duplicate_id":
        rows[1]["record_id"] = rows[0]["record_id"]
    elif mutation == "naive_time":
        rows[0]["captured_at"] = NOW.replace(tzinfo=None)
    else:
        rows[0]["raw_sha256"] = "not-a-hash"
    assert validate_split(rows)["status"] == "FAIL"


def test_dataset_gate_preserves_full_p1_and_independent_strata(dataset):
    report = validate_dataset_gate(*dataset)
    assert report["status"] == "PASS"
    assert report["counts"]["real_notices"] == 30
    assert report["counts"]["selected_gold_families"] == 20
    assert all(count == 20 for count in report["counts"]["risk_families"].values())
    assert select_gold_record_ids(dataset[0], 127) == select_gold_record_ids(dataset[0][::-1], 127)


@pytest.mark.parametrize(
    "missing",
    [
        "real_notices",
        "notice_categories",
        "layouts",
        "historical",
        "future",
        "selected_gold_families",
        "risk",
    ],
)
def test_dataset_shortfalls_block_instead_of_passing(dataset, missing):
    rows, plan, annotations, people = copy.deepcopy(dataset)
    if missing == "real_notices":
        rows[0]["provenance_kind"] = "engineering_fixture"
    elif missing == "notice_categories":
        for row in rows:
            row["notice_category"] = "single-kind"
    elif missing == "layouts":
        for row in rows:
            row["layout"] = "single-layout"
    elif missing == "historical":
        for row in rows:
            row["deadline_at"] = NOW + timedelta(days=1)
    elif missing == "future":
        for row in rows:
            row["deadline_at"] = NOW - timedelta(days=1)
    elif missing == "selected_gold_families":
        for row in rows:
            row["family_id"] = "same-family"
    else:
        for row in rows:
            row["risk_types"] = list(RISKS[:-1])
    pool = [row for row in rows if row["provenance_kind"] == "real"]
    plan["candidate_pool_sha256"] = records_digest(pool)
    plan["selected_record_ids"] = select_gold_record_ids(rows, plan["seed"])
    annotations = [
        annotation(row) for row in rows if row["record_id"] in plan["selected_record_ids"]
    ]
    report = validate_dataset_gate(rows, plan, annotations, people)
    assert report["status"] == "BLOCKED", report


def test_missing_independent_gold_cannot_be_filled_with_fixture_labels(dataset):
    rows, plan, annotations, people = copy.deepcopy(dataset)
    for item in annotations:
        item["purpose"] = "engineering_fixture"
    report = validate_dataset_gate(rows, plan, annotations, people)
    assert report["status"] == "BLOCKED"
    assert report["counts"]["selected_gold_records"] == 0
    assert "missing_independent_gold" in issue_codes(report)


def test_sampler_and_pool_tampering_rejected(dataset):
    rows, plan, annotations, people = copy.deepcopy(dataset)
    plan["candidate_pool_sha256"] = digest("other pool")
    plan["selected_record_ids"] = [row["record_id"] for row in rows[:20]]
    report = validate_dataset_gate(rows, plan, annotations, people)
    assert report["status"] == "FAIL"
    assert {"candidate_pool_hash_mismatch", "sampling_not_reproducible"} <= issue_codes(report)


def test_gold_must_match_source_and_family(dataset):
    rows, plan, annotations, people = copy.deepcopy(dataset)
    annotations[0]["source_sha256"] = digest("wrong source")
    assert "gold_source_identity_mismatch" in issue_codes(
        validate_dataset_gate(rows, plan, annotations, people)
    )


def test_future_availability_exception_requires_auditable_count_and_evidence(dataset):
    rows, plan, _, people = copy.deepcopy(dataset)
    for row in rows:
        row["deadline_at"] = NOW - timedelta(days=1)
    plan["candidate_pool_sha256"] = records_digest(rows)
    annotations = [
        annotation(row) for row in rows if row["record_id"] in plan["selected_record_ids"]
    ]
    plan["future_availability_exception_ref"] = "unit-pool-availability-record"
    assert validate_dataset_gate(rows, plan, annotations, people)["status"] == "BLOCKED"
    plan.update(
        future_available_count=0, future_availability_evidence_sha256=digest("availability")
    )
    report = validate_dataset_gate(rows, plan, annotations, people)
    assert report["status"] == "PASS"
    assert report["counts"]["future"] == 0
    assert report["future_availability_exception_ref"] == "unit-pool-availability-record"


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("annotator", "nonindependent_annotation_person"),
        ("source", "missing_original_evidence"),
        ("reviewer", "nonindependent_annotation_person"),
        ("same_person", "annotation_roles_not_independent"),
        ("arbitration", "unresolved_disagreement"),
        ("model", "model_preannotation_not_independent"),
        ("developer", "nonindependent_annotation_person"),
        ("bad_order", "review_before_annotation"),
    ],
)
def test_invalid_gold_is_excluded(mutation, code):
    item = annotation(record(1))
    people = [person("annotator"), person("reviewer"), person("arbiter")]
    if mutation == "annotator":
        item["annotator_id"] = None
    elif mutation == "source":
        item["source_refs"] = []
    elif mutation == "reviewer":
        item["reviewer_id"] = None
    elif mutation == "same_person":
        item["reviewer_id"] = "annotator"
    elif mutation == "arbitration":
        item["disagreement"] = True
    elif mutation == "model":
        item["model_preannotated"] = True
    elif mutation == "developer":
        people[0]["developer_or_tuner"] = True
    else:
        item["reviewed_at"] = NOW - timedelta(minutes=1)
    report = validate_annotations([item], people)
    assert report["status"] == "FAIL"
    assert report["independent_gold_ids"] == []
    assert code in issue_codes(report)


def test_resolved_disagreement_requires_independent_arbitrator():
    item = annotation(
        record(1),
        disagreement=True,
        arbitrator_id="arbiter",
        arbitrated_at=NOW + timedelta(minutes=2),
        arbitration_resolution="unit-resolution",
    )
    people = [person("annotator"), person("reviewer"), person("arbiter")]
    assert validate_annotations([item], people)["status"] == "PASS"
    item["arbitrator_id"] = "reviewer"
    assert validate_annotations([item], people)["status"] == "FAIL"


def test_self_reported_noncritical_cannot_remove_independent_gold_review(dataset):
    rows, plan, annotations, people = copy.deepcopy(dataset)
    for item in annotations:
        item.update(critical=False, reviewer_id=None, reviewed_at=None)
    report = validate_dataset_gate(rows, plan, annotations, people)
    assert report["status"] == "FAIL"
    assert report["counts"]["selected_gold_records"] == 0
    assert "missing_review_time" in issue_codes(report)


def test_duplicate_gold_quality_units_are_not_eligible():
    item = annotation(record(1), quality_unit_ids=["same", "same"])
    report = validate_annotations([item], [person("annotator"), person("reviewer")])
    assert report["status"] == "FAIL"
    assert report["independent_gold_ids"] == []


@pytest.fixture
def trials():
    assignments, observations, annotations = [], [], []
    for index in range(10):
        row = record(index)
        annotations.append(annotation(row))
        for mode in ("manual", "assisted"):
            assignment = {
                "task_id": f"task{index}-{mode}",
                "pair_id": f"pair{index}",
                "family_id": row["family_id"],
                "mode": mode,
                "operator_id": f"op{(index + (mode == 'assisted')) % 2}",
                "input_sha256": digest(f"review-input:{row['record_id']}"),
                "gold_record_id": row["record_id"],
                "gold_sha256": digest(row["record_id"]),
                "deliverable_sha256": digest("deliverable"),
                "quality_unit_ids": ["condition1", "condition2"],
                "time_limit_seconds": 200,
                "order": index + 1,
                "blind_to_gold": True,
                "previously_performed": False,
            }
            assignments.append(assignment)
            start = NOW + timedelta(hours=1, seconds=index * 250)
            duration = 100 if mode == "manual" else 80
            observations.append(
                {
                    "task_id": assignment["task_id"],
                    "status": "completed",
                    "started_at": start,
                    "stopped_at": start + timedelta(seconds=duration),
                    "active_seconds": duration - 10,
                    "waiting_seconds": 10,
                    "correct_unit_ids": ["condition1", "condition2"],
                }
            )
    plan = {
        "purpose": "independent_acceptance",
        "frozen_at": NOW,
        "randomization_seed": 127,
        "allocation_record_ref": "unit-allocation",
        "start_rule": "open supplied input",
        "stop_rule": "save final review",
        "assignments": assignments,
    }
    people = [person(name) for name in ["op0", "op1", "annotator", "reviewer"]]
    return plan, observations, annotations, people


def test_paired_trials_preserve_exact_twenty_percent_boundary(trials):
    plan, observations, annotations, people = trials
    assert validate_trial_plan(plan, annotations, people)["status"] == "PASS"
    report = score_paired_trials(*trials)
    assert report["status"] == report["quality_status"] == report["time_status"] == "PASS"
    assert report["median_saving"] == 0.2
    assert (
        report["quality"]["manual"]
        == report["quality"]["assisted"]
        == {
            "numerator": 20,
            "denominator": 20,
            "value": 1.0,
        }
    )


@pytest.mark.parametrize(
    "failure",
    [
        "failed",
        "timed_out",
        "cancelled",
        "interrupted",
        "missing",
        "elapsed_timeout",
        "bad_units",
        "zero_duration",
    ],
)
def test_all_failed_or_missing_tasks_keep_quality_denominator_and_fail_time(trials, failure):
    plan, observations, annotations, people = copy.deepcopy(trials)
    selected = observations[1]
    if failure == "missing":
        observations.remove(selected)
    elif failure == "elapsed_timeout":
        selected["stopped_at"] = selected["started_at"] + timedelta(seconds=201)
    elif failure == "bad_units":
        selected["correct_unit_ids"] = ["invented"]
    elif failure == "zero_duration":
        selected["stopped_at"] = selected["started_at"]
        selected.update(active_seconds=0, waiting_seconds=0)
    else:
        selected["status"] = failure
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["status"] == report["time_status"] == "FAIL"
    assert report["quality"]["assisted"] == {"numerator": 18, "denominator": 20, "value": 0.9}
    assert report["failed_task_ids"] == ["task0-assisted"]
    assert len(report["tasks"]) == 20
    assert report["median_saving"] is None
    assert report["counts"]["registered_pairs"] == 10
    assert report["counts"]["completed_pairs"] == 9


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("same_operator", "same_operator_both_modes"),
        ("not_blind", "operator_not_blind"),
        ("previous", "operator_not_blind"),
        ("input", "unequal_trial_inputs_or_deliverables"),
        ("quality_units", "unequal_trial_inputs_or_deliverables"),
        ("developer", "nonindependent_trial_operator"),
        ("annotator", "operator_contributed_gold"),
        ("order", "unbalanced_operator_order"),
    ],
)
def test_invalid_paired_plan_never_earns_time_pass(trials, mutation, code):
    plan, observations, annotations, people = copy.deepcopy(trials)
    item = plan["assignments"][1]
    if mutation == "same_operator":
        item["operator_id"] = plan["assignments"][0]["operator_id"]
    elif mutation == "not_blind":
        item["blind_to_gold"] = False
    elif mutation == "previous":
        item["previously_performed"] = True
    elif mutation == "input":
        item["input_sha256"] = digest("different")
    elif mutation == "quality_units":
        item["quality_unit_ids"] = ["condition1"]
    elif mutation == "developer":
        people[0]["developer_or_tuner"] = True
    elif mutation == "annotator":
        annotations[0]["annotator_id"] = item["operator_id"]
    else:
        item["order"] = 2
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["status"] == report["time_status"] == "FAIL"
    assert code in issue_codes(report)


def test_zero_denominator_and_no_real_trials_cannot_pass():
    plan = {
        "frozen_at": NOW,
        "randomization_seed": 0,
        "allocation_record_ref": "unit",
        "start_rule": "start",
        "stop_rule": "stop",
        "assignments": [],
    }
    report = score_paired_trials(plan, [])
    assert report["status"] == report["time_status"] == report["quality_status"] == "BLOCKED"
    assert report["quality"]["manual"] == {"numerator": 0, "denominator": 0, "value": None}
    assert report["median_saving"] is None


def test_missing_gold_blocks_real_trial_claim_even_with_fast_complete_results(trials):
    plan, observations, _, people = trials
    report = score_paired_trials(plan, observations, contributors=people)
    assert report["status"] == report["time_status"] == "BLOCKED"
    assert "trial_missing_independent_gold" in issue_codes(report)


def test_failure_cannot_be_hidden_by_missing_coverage(trials):
    plan, observations, _, people = copy.deepcopy(trials)
    observations[1]["status"] = "failed"
    report = score_paired_trials(plan, observations, contributors=people)
    assert report["status"] == report["time_status"] == "FAIL"


def test_pre_freeze_replayed_or_out_of_order_trial_rejected(trials):
    plan, observations, annotations, people = copy.deepcopy(trials)
    observations[0]["started_at"] = NOW - timedelta(seconds=1)
    report = score_paired_trials(plan, observations, annotations, people)
    assert "invalid_or_prefreeze_trial_time" in issue_codes(report)
    observations[0]["started_at"] = observations[4]["started_at"]
    observations[0]["stopped_at"] = observations[4]["stopped_at"]
    report = score_paired_trials(plan, observations, annotations, people)
    assert "trial_order_or_overlap" in issue_codes(report)
    assert report["time_status"] == "FAIL"


def test_lower_quality_and_severe_error_override_fast_times(trials):
    plan, observations, annotations, people = copy.deepcopy(trials)
    observations[1]["correct_unit_ids"] = ["condition1"]
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["quality_status"] == report["time_status"] == "FAIL"
    observations[1]["correct_unit_ids"] = ["condition1", "condition2"]
    observations[1]["severe_error_ids"] = ["unsafe-release-1"]
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["quality_status"] == report["time_status"] == "FAIL"
    assert report["severe_error_ids"] == ["unsafe-release-1"]


def test_engineering_fixture_is_never_a_real_efficiency_result(trials):
    plan, observations, annotations, people = copy.deepcopy(trials)
    plan["purpose"] = "engineering_fixture"
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["status"] == report["time_status"] == "BLOCKED"
    assert report["purpose"] == "engineering_fixture"


def test_nonfinite_or_naive_times_rejected_without_losing_registered_tasks(trials):
    plan, observations, annotations, people = copy.deepcopy(trials)
    observations[0]["active_seconds"] = float("nan")
    observations[1]["started_at"] = NOW.replace(tzinfo=None)
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["status"] == "FAIL"
    assert len(report["tasks"]) == 20
    assert report["counts"]["failed_or_missing"] == 2
    assert report["quality"]["manual"]["denominator"] == 20
    assert report["quality"]["assisted"]["denominator"] == 20


def test_repeated_pair_does_not_reset_operator_blindness(trials):
    plan, observations, annotations, people = copy.deepcopy(trials)
    for assignment, observation in zip(
        copy.deepcopy(plan["assignments"][:2]), copy.deepcopy(observations[:2]), strict=True
    ):
        assignment.update(task_id=assignment["task_id"] + "-repeat", pair_id="repeat", order=11)
        observation.update(
            task_id=assignment["task_id"],
            started_at=observation["started_at"] + timedelta(seconds=2500),
            stopped_at=observation["stopped_at"] + timedelta(seconds=2500),
        )
        plan["assignments"].append(assignment)
        observations.append(observation)
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["status"] == report["time_status"] == "FAIL"
    assert "operator_repeats_family_or_input" in issue_codes(report)
    assert report["quality"]["assisted"]["denominator"] == 22


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_sha256", digest("unannotated input")),
        ("gold_sha256", digest("different gold")),
        ("quality_unit_ids", ["invented-without-gold"]),
    ],
)
def test_trial_inputs_gold_version_and_quality_units_bind_to_annotation(trials, field, value):
    plan, observations, annotations, people = copy.deepcopy(trials)
    for assignment in plan["assignments"]:
        assignment[field] = value
    if field == "quality_unit_ids":
        for observation in observations:
            observation["correct_unit_ids"] = value
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["status"] == report["time_status"] == "FAIL"
    assert "trial_gold_binding_mismatch" in issue_codes(report)


def test_paired_time_limits_must_be_identical(trials):
    plan, observations, annotations, people = copy.deepcopy(trials)
    plan["assignments"][1]["time_limit_seconds"] = 300
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["status"] == report["time_status"] == "FAIL"
    assert "unequal_trial_inputs_or_deliverables" in issue_codes(report)


def test_unrelated_fixture_gold_cannot_replace_real_gold_within_same_family(trials):
    plan, observations, annotations, people = copy.deepcopy(trials)
    other = copy.deepcopy(annotations[0])
    other.update(
        record_id="fixture-only-gold",
        purpose="engineering_fixture",
        input_sha256=digest("unseen-fixture-input"),
        gold_sha256=digest("fixture-gold"),
    )
    annotations.append(other)
    for assignment in plan["assignments"][:2]:
        assignment.update(
            gold_record_id=other["record_id"],
            input_sha256=other["input_sha256"],
            gold_sha256=other["gold_sha256"],
        )
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["status"] == report["time_status"] == "BLOCKED"
    assert "trial_missing_gold_record" in issue_codes(report)


def test_failed_and_timed_out_trials_keep_stop_time_and_reason(trials):
    plan, observations, annotations, people = copy.deepcopy(trials)
    observations[1].update(status="failed", failure_reason="unit-material-unavailable")
    observations[3].update(status="timed_out", failure_reason=None)
    report = score_paired_trials(plan, observations, annotations, people)
    assert report["status"] == report["time_status"] == "FAIL"
    first = next(row for row in report["tasks"] if row["task_id"] == "task0-assisted")
    second = next(row for row in report["tasks"] if row["task_id"] == "task1-assisted")
    assert first["failure_reason"] == "unit-material-unavailable"
    assert first["stopped_at"] == observations[1]["stopped_at"].isoformat()
    assert second["failure_reason"] == "reported_timed_out_without_reason"
    assert "missing_trial_failure_reason" in issue_codes(report)


def test_dataset_model_forbids_unrecognized_claim_fields():
    with pytest.raises(ValueError):
        DatasetRecord.model_validate(record(1, independently_verified=True))
