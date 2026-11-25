"""B01-01 contract fixtures, not real notices, people, or business measurements."""

import copy
import hashlib

import pytest
from bidops.screening.schemas import (
    Condition,
    ConditionExpression,
    EvidenceRef,
    EvidenceValidity,
    ReviewCommand,
    ReviewEvent,
    ScreeningAssessment,
    TaskExecution,
    ValidityRequirement,
)
from pydantic import ValidationError

NOW = "2026-10-23T08:00:00+08:00"
DEADLINE = "2026-11-06T08:30:00+08:00"


def version(object_id, text, revision="v1"):
    return {
        "object_id": object_id,
        "version_id": revision,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def evidence(evidence_id, side, text, version_ref, object_kind=None):
    return {
        "evidence_id": evidence_id,
        "side": side,
        "object_kind": object_kind or ("notice" if side == "notice" else "company_material"),
        "version": copy.deepcopy(version_ref),
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "quote": text,
        "start": 0,
        "end": len(text),
        "locator": "normalized_text_v2",
    }


@pytest.fixture
def result_data():
    notice_text = "UNIT合同夹具：供应商应持有有效许可证。"
    company_text = "UNIT企业材料夹具：许可证已签发。"
    notice = version("unit-notice", notice_text)
    material = version("unit-material", company_text)
    notice_evidence = evidence("notice-clause", "notice", notice_text, notice)
    company_evidence = evidence("company-proof", "company", company_text, material)
    return {
        "assessment_id": "unit-assessment",
        "result_revision": "r1",
        "inputs": {
            "project_family_id": "unit-family",
            "project_id": "unit-project",
            "notice_revision": notice,
            "profile_revision": version("unit-profile", "UNIT企业画像夹具"),
            "material_revisions": [material],
            "attachment_revisions": [],
            "as_of": NOW,
        },
        "target_package_id": "unit-package-1",
        "conditions": [
            {
                "condition_id": "license",
                "original_text": notice_text,
                "hard_requirement": True,
                "package_scope": {"kind": "all"},
                "expression": {
                    "node_id": "license-atom",
                    "operator": "atom",
                    "text": notice_text,
                    "evidence_ids": ["notice-clause"],
                },
                "validity": {"kind": "not_required"},
                "notice_evidence": [notice_evidence],
            }
        ],
        "assessments": [
            {
                "condition_id": "license",
                "status": "satisfied",
                "reason": "UNIT契约正例；双方材料支持，尚未经真实人审。",
                "notice_evidence": [notice_evidence],
                "company_evidence": [company_evidence],
            }
        ],
    }


def test_versioned_contract_roundtrip_preserves_inputs(result_data):
    result = ScreeningAssessment.model_validate(result_data)
    restored = ScreeningAssessment.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.inputs.notice_revision.version_id == "v1"
    assert restored.inputs.profile_revision.object_id == "unit-profile"
    assert restored.target_package_id == "unit-package-1"
    assert "actor" not in result.model_dump()


@pytest.mark.parametrize(
    "status",
    [
        "satisfied",
        "not_satisfied",
        "information_insufficient",
        "evidence_conflict",
        "not_applicable",
    ],
)
def test_five_qualification_outcomes_are_distinct(result_data, status):
    item = result_data["assessments"][0]
    item["status"] = status
    if status == "information_insufficient":
        item["company_evidence"] = []
        item["missing_items"] = ["UNIT：尚未提供许可证材料"]
    elif status == "evidence_conflict":
        other = version("unit-revocation", "UNIT材料夹具：许可证撤销")
        result_data["inputs"]["material_revisions"].append(other)
        item["company_evidence"].append(
            evidence("company-conflict", "company", "UNIT材料夹具：许可证撤销", other)
        )
        item["conflicting_evidence_ids"] = ["company-proof", "company-conflict"]
    elif status == "not_applicable":
        item["scope_evidence_ids"] = ["notice-clause", "company-proof"]
    result = ScreeningAssessment.model_validate(result_data)
    assert result.assessments[0].status == status


@pytest.mark.parametrize("status", ["satisfied", "not_satisfied", "not_applicable"])
def test_missing_company_proof_never_becomes_definite(result_data, status):
    item = result_data["assessments"][0]
    item.update(status=status, company_evidence=[])
    if status == "not_applicable":
        item["scope_evidence_ids"] = ["notice-clause"]
    with pytest.raises(ValidationError, match="requires_company_evidence"):
        ScreeningAssessment.model_validate(result_data)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_version",
        "bad_hash",
        "empty_version",
        "backward_span",
        "wrong_span_length",
        "blank_quote",
        "wrong_side",
        "page_missing",
        "page_unexpected",
    ],
)
def test_malformed_evidence_is_rejected(result_data, mutation):
    ref = copy.deepcopy(result_data["assessments"][0]["company_evidence"][0])
    if mutation == "missing_version":
        del ref["version"]
    elif mutation == "bad_hash":
        ref["version"]["sha256"] = "not-a-digest"
    elif mutation == "empty_version":
        ref["version"]["version_id"] = "  "
    elif mutation == "backward_span":
        ref["start"] = ref["end"]
    elif mutation == "wrong_span_length":
        ref["end"] += 1
    elif mutation == "blank_quote":
        ref.update(quote=" ", start=0, end=1)
    elif mutation == "wrong_side":
        ref["side"] = "notice"
    elif mutation == "page_missing":
        ref["locator"] = "page_text_v1"
    elif mutation == "page_unexpected":
        ref["page"] = 1
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate(ref)


def test_page_and_profile_fact_evidence_are_versioned(result_data):
    ref = result_data["assessments"][0]["company_evidence"][0]
    ref.update(
        locator="page_text_v1",
        page=2,
        object_kind="company_fact",
        version=result_data["inputs"]["profile_revision"],
    )
    assert (
        ScreeningAssessment.model_validate(result_data).assessments[0].company_evidence[0].page == 2
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "undeclared_version",
        "duplicate_input",
        "version_hash_conflict",
        "redefined_evidence",
        "missing_condition_result",
        "duplicate_condition",
        "duplicate_result",
        "undeclared_condition",
        "foreign_evidence_side",
        "naive_as_of",
    ],
)
def test_aggregate_rejects_version_and_condition_leaks(result_data, mutation):
    item = result_data["assessments"][0]
    if mutation == "undeclared_version":
        item["company_evidence"][0]["version"]["version_id"] = "unprovided-v2"
    elif mutation == "duplicate_input":
        refs = result_data["inputs"]["material_revisions"]
        refs.append(copy.deepcopy(refs[0]))
    elif mutation == "version_hash_conflict":
        other = copy.deepcopy(result_data["inputs"]["material_revisions"][0])
        other["sha256"] = "f" * 64
        result_data["inputs"]["material_revisions"].append(other)
    elif mutation == "redefined_evidence":
        item["notice_evidence"] = copy.deepcopy(item["notice_evidence"])
        item["notice_evidence"][0]["quote"] = "X" * len(item["notice_evidence"][0]["quote"])
    elif mutation == "missing_condition_result":
        result_data["assessments"] = []
    elif mutation == "duplicate_condition":
        result_data["conditions"].append(copy.deepcopy(result_data["conditions"][0]))
    elif mutation == "duplicate_result":
        result_data["assessments"].append(copy.deepcopy(item))
    elif mutation == "undeclared_condition":
        item["condition_id"] = "unrecognized-condition"
    elif mutation == "foreign_evidence_side":
        item["company_evidence"] = [copy.deepcopy(item["notice_evidence"][0])]
    elif mutation == "naive_as_of":
        result_data["inputs"]["as_of"] = "2026-10-23T08:00:00"
    with pytest.raises(ValidationError):
        ScreeningAssessment.model_validate(result_data)


@pytest.mark.parametrize(
    "patch",
    [
        {"status": "satisfied", "missing_items": ["关键证明缺失"]},
        {"status": "information_insufficient"},
        {"status": "evidence_conflict", "conflicting_evidence_ids": ["company-proof"]},
        {"status": "evidence_conflict", "conflicting_evidence_ids": ["company-proof"] * 2},
        {"status": "not_applicable"},
        {"status": "not_applicable", "scope_evidence_ids": ["invented"]},
        {"status": "satisfied", "conflicting_evidence_ids": ["notice-clause", "company-proof"]},
        {"status": "completed"},
        {"status": "approved"},
    ],
)
def test_invalid_conclusions_do_not_silently_become_unknown_or_approved(result_data, patch):
    result_data["assessments"][0].update(patch)
    with pytest.raises(ValidationError):
        ScreeningAssessment.model_validate(result_data)


def atom(node_id, text):
    return {"node_id": node_id, "operator": "atom", "text": text, "evidence_ids": ["notice-clause"]}


def test_and_or_negation_and_exception_survive_roundtrip(result_data):
    tree = {
        **atom("root", "A且(B或非C)，D除外"),
        "operator": "unless",
        "operands": [
            {
                **atom("and", "A且(B或非C)"),
                "operator": "all",
                "operands": [
                    atom("A", "A"),
                    {
                        **atom("or", "B或非C"),
                        "operator": "any",
                        "operands": [
                            atom("B", "B"),
                            {**atom("not", "非C"), "operator": "not", "operands": [atom("C", "C")]},
                        ],
                    },
                ],
            },
            atom("D", "D豁免"),
        ],
    }
    result_data["conditions"][0]["expression"] = tree
    result = ScreeningAssessment.model_validate(result_data)
    restored = ScreeningAssessment.model_validate_json(result.model_dump_json())
    assert restored.conditions[0].expression == result.conditions[0].expression
    assert [node.operator for node in restored.conditions[0].expression.nodes()] == [
        "unless",
        "all",
        "atom",
        "any",
        "atom",
        "not",
        "atom",
        "atom",
    ]


@pytest.mark.parametrize(
    "operator,count",
    [("atom", 1), ("all", 0), ("all", 1), ("any", 0), ("not", 0), ("not", 2), ("unless", 1)],
)
def test_logic_operators_reject_ambiguous_arity(operator, count):
    tree = {**atom("root", "UNIT逻辑"), "operator": operator}
    tree["operands"] = [atom(f"n{i}", "子条件") for i in range(count)]
    with pytest.raises(ValidationError, match="operator_arity"):
        ConditionExpression.model_validate(tree)


@pytest.mark.parametrize("mutation", ["duplicate_node", "unknown_evidence", "unknown_operator"])
def test_condition_rejects_dangling_logic_references(result_data, mutation):
    condition = result_data["conditions"][0]
    if mutation == "duplicate_node":
        condition["expression"].update(
            operator="all", operands=[atom("same", "甲"), atom("same", "乙")]
        )
    elif mutation == "unknown_evidence":
        condition["expression"]["evidence_ids"] = ["unprovided-attachment"]
    else:
        condition["expression"]["operator"] = "xor"
    with pytest.raises(ValidationError):
        Condition.model_validate(condition)


def test_specific_package_scope_does_not_leak_to_another_package(result_data):
    condition = result_data["conditions"][0]
    condition["package_scope"] = {
        "kind": "specific",
        "package_ids": ["unit-package-1"],
        "evidence_ids": ["notice-clause"],
    }
    ScreeningAssessment.model_validate(result_data)
    result_data["target_package_id"] = "unit-package-2"
    with pytest.raises(ValidationError, match="not_applicable_to_target"):
        ScreeningAssessment.model_validate(result_data)
    item = result_data["assessments"][0]
    item.update(status="not_applicable", scope_evidence_ids=["notice-clause", "company-proof"])
    assert ScreeningAssessment.model_validate(result_data).assessments[0].status == "not_applicable"


def test_unknown_package_scope_requires_abstention(result_data):
    result_data["conditions"][0]["package_scope"] = {"kind": "unknown", "reason": "包范围尚待明确"}
    with pytest.raises(ValidationError, match="unknown_scope"):
        ScreeningAssessment.model_validate(result_data)
    result_data["assessments"][0].update(
        status="information_insufficient", company_evidence=[], missing_items=["采购包适用范围"]
    )
    ScreeningAssessment.model_validate(result_data)


def test_missing_package_is_not_proof_of_nonapplicability(result_data):
    result_data["conditions"][0]["package_scope"] = {
        "kind": "specific",
        "package_ids": ["unit-package-1"],
        "evidence_ids": ["notice-clause"],
    }
    result_data["target_package_id"] = None
    result_data["assessments"][0].update(
        status="not_applicable", scope_evidence_ids=["notice-clause", "company-proof"]
    )
    with pytest.raises(ValidationError, match="missing_target_package"):
        ScreeningAssessment.model_validate(result_data)
    result_data["assessments"][0].update(
        status="information_insufficient", missing_items=["待确认目标采购包"]
    )
    ScreeningAssessment.model_validate(result_data)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_notice_evidence",
        "missing_scope_kind",
        "missing_validity_kind",
        "missing_operator",
        "coerced_hard_requirement",
    ],
)
def test_omitted_contract_semantics_are_not_invented(result_data, mutation):
    condition = result_data["conditions"][0]
    if mutation == "missing_notice_evidence":
        condition["notice_evidence"] = []
    elif mutation == "missing_scope_kind":
        condition["package_scope"] = {}
    elif mutation == "missing_validity_kind":
        condition["validity"] = {}
    elif mutation == "missing_operator":
        del condition["expression"]["operator"]
    else:
        condition["hard_requirement"] = "yes"
    with pytest.raises(ValidationError):
        ScreeningAssessment.model_validate(result_data)


def temporal_result(result_data):
    result_data["conditions"][0]["validity"] = {
        "kind": "at",
        "basis": "bid_deadline",
        "required_at": DEADLINE,
        "evidence_ids": ["notice-clause"],
    }
    item = result_data["assessments"][0]
    item["validity_evidence_ids"] = ["company-proof"]
    item["company_evidence"][0]["validity"] = {
        "kind": "interval",
        "valid_from": "2026-01-01T00:00:00+08:00",
        "valid_until": DEADLINE,
        "end_inclusive": True,
    }
    return result_data


def test_certificate_valid_at_deadline_and_timezone_equivalence(result_data):
    data = temporal_result(result_data)
    # Same instant represented in UTC must not be mistaken for expiry.
    data["assessments"][0]["company_evidence"][0]["validity"]["valid_until"] = (
        "2026-11-06T00:30:00Z"
    )
    assert ScreeningAssessment.model_validate(data).assessments[0].status == "satisfied"


def test_as_of_validity_cannot_move_to_an_earlier_date_with_valid_certificate(result_data):
    data = temporal_result(result_data)
    data["conditions"][0]["validity"].update(basis="as_of", required_at="2026-01-02T00:00:00+08:00")
    data["assessments"][0]["company_evidence"][0]["validity"]["valid_until"] = (
        "2026-01-03T00:00:00+08:00"
    )
    with pytest.raises(ValidationError, match="as_of_requirement_must_match_inputs"):
        ScreeningAssessment.model_validate(data)


def test_changed_model_instances_are_revalidated_recursively(result_data):
    result = ScreeningAssessment.model_validate(result_data)
    result.assessments[0].company_evidence.clear()
    with pytest.raises(ValidationError, match="requires_company_evidence"):
        ScreeningAssessment.model_validate(result)
    result = ScreeningAssessment.model_validate(result_data)
    result.conditions[0].notice_evidence[0].version.version_id = "unprovided-version"
    with pytest.raises(ValidationError):
        ScreeningAssessment.model_validate(result)


@pytest.mark.parametrize(
    "mutation",
    ["expired", "exclusive_end", "missing_dates", "missing_binding", "unknown_requirement"],
)
def test_date_sensitive_satisfaction_requires_current_proof(result_data, mutation):
    data = temporal_result(result_data)
    item = data["assessments"][0]
    if mutation == "expired":
        item["company_evidence"][0]["validity"]["valid_until"] = "2026-11-05T23:59:59+08:00"
    elif mutation == "exclusive_end":
        item["company_evidence"][0]["validity"]["end_inclusive"] = False
    elif mutation == "missing_dates":
        item["company_evidence"][0].pop("validity")
    elif mutation == "missing_binding":
        item["validity_evidence_ids"] = []
    elif mutation == "unknown_requirement":
        data["conditions"][0]["validity"] = {"kind": "unknown", "reason": "所需日期未确定"}
    with pytest.raises(ValidationError):
        ScreeningAssessment.model_validate(data)


def test_validity_interval_shape_and_required_period(result_data):
    data = temporal_result(result_data)
    validity = data["conditions"][0]["validity"]
    validity.update(kind="throughout", required_until="2026-11-07T08:30:00+08:00")
    with pytest.raises(ValidationError, match="valid_proof"):
        ScreeningAssessment.model_validate(data)
    material_validity = data["assessments"][0]["company_evidence"][0]["validity"]
    material_validity["valid_until"] = validity["required_until"]
    ScreeningAssessment.model_validate(data)
    material_validity["valid_from"] = "2026-12-01T00:00:00+08:00"
    with pytest.raises(ValidationError, match="inverted_material"):
        EvidenceValidity.model_validate(material_validity)
    validity["required_at"] = "2026-11-06T08:30:00"
    with pytest.raises(ValidationError):
        ValidityRequirement.model_validate(validity)


@pytest.fixture
def review_event():
    # Clearly marked fictional identity and fixture event; never an approval record.
    return {
        "event_id": "unit-fixture-event",
        "run_id": "unit-run",
        "assessment_id": "unit-assessment",
        "result_revision": "r1",
        "inputs_manifest_sha256": "a" * 64,
        "command": {
            "decision": "request_materials",
            "expected_result_revision": "r1",
            "reason": "UNIT契约夹具：补充材料",
            "idempotency_key": "unit-key",
        },
        "actor": {
            "actor_kind": "human",
            "principal_id": "UNIT_FICTIONAL_REVIEWER",
            "authorization_context_id": "UNIT_NOT_A_REAL_AUTHORIZATION",
        },
        "occurred_at": NOW,
        "origin": "test_fixture",
    }


def test_review_event_is_separate_and_explicitly_fixture(review_event):
    value = ReviewEvent.model_validate(review_event)
    assert value.origin == "test_fixture"
    assert value.command.decision == "request_materials"


@pytest.mark.parametrize("field", ["actor", "reviewer", "human_decision", "approved"])
def test_model_assessment_cannot_claim_human_approval(result_data, field):
    result_data[field] = "invented"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ScreeningAssessment.model_validate(result_data)


@pytest.mark.parametrize(
    "mutation",
    [
        "agent",
        "no_identity",
        "actor_in_command",
        "revision",
        "self_supersede",
        "naive_time",
        "missing_origin",
    ],
)
def test_review_contract_rejects_invalid_actor_and_revision(review_event, mutation):
    if mutation == "agent":
        review_event["actor"]["actor_kind"] = "agent"
    elif mutation == "no_identity":
        review_event["actor"]["principal_id"] = " "
    elif mutation == "actor_in_command":
        review_event["command"]["actor"] = review_event["actor"]
    elif mutation == "revision":
        review_event["command"]["expected_result_revision"] = "stale-r0"
    elif mutation == "self_supersede":
        review_event["supersedes_event_id"] = review_event["event_id"]
    elif mutation == "naive_time":
        review_event["occurred_at"] = "2026-10-23T08:00:00"
    elif mutation == "missing_origin":
        del review_event["origin"]
    with pytest.raises(ValidationError):
        ReviewEvent.model_validate(review_event)


@pytest.mark.parametrize("decision", ["satisfied", "completed", "approved"])
def test_qualification_or_execution_state_is_not_a_review_decision(review_event, decision):
    review_event["command"]["decision"] = decision
    with pytest.raises(ValidationError):
        ReviewCommand.model_validate(review_event["command"])


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "running",
        "waiting_review",
        "cancel_requested",
        "cancelled",
        "failed",
        "completed",
        "timed_out",
        "interrupted",
    ],
)
def test_execution_states_do_not_express_qualification(status):
    data = {"run_id": "unit-run", "status": status}
    if status in {"waiting_review", "completed"}:
        data["result_revision"] = "r1"
    if status in {"failed", "timed_out", "interrupted"}:
        data["failure_reason"] = "UNIT失败原因"
    assert TaskExecution.model_validate(data).status == status


@pytest.mark.parametrize(
    "patch",
    [
        {"status": "satisfied"},
        {"status": "confirm"},
        {"status": "completed"},
        {"status": "failed"},
        {"status": "queued", "failure_reason": "矛盾状态"},
        {"status": "running", "approved": True},
    ],
)
def test_execution_contract_rejects_approval_and_incomplete_terminal_state(patch):
    with pytest.raises(ValidationError):
        TaskExecution.model_validate({"run_id": "unit-run", **patch})


def test_rejected_prediction_can_still_be_retained_for_scoring(result_data):
    raw = copy.deepcopy(result_data)
    raw["assessments"][0]["company_evidence"] = []
    with pytest.raises(ValidationError):
        ScreeningAssessment.model_validate(raw)
    # No sanitization or downgrade hides the original severe false-positive claim.
    assert raw["assessments"][0]["status"] == "satisfied"
    assert raw["assessments"][0]["company_evidence"] == []
