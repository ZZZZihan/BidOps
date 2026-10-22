"""Deterministic engineering truth tables; no model or human acceptance claim."""

from datetime import UTC, datetime, timedelta
from itertools import product

import pytest
from bidops.notice_data.schemas import ProjectStatus
from bidops.qualification.contracts import ConditionInventory, NodeJudgment, ScreeningSummary
from bidops.qualification.logic import (
    aggregate_recommendation,
    applicability,
    assess_condition,
    build_assessment_group,
    conditions_for_group,
    reduce_expression,
)
from bidops.screening.schemas import (
    AssessmentInputs,
    Condition,
    EvidenceRef,
    VersionRef,
)
from pydantic import ValidationError

AT = datetime(2026, 10, 23, tzinfo=UTC)
STATES = (
    "satisfied",
    "not_satisfied",
    "information_insufficient",
    "evidence_conflict",
    "not_applicable",
)
PROFILE = VersionRef(object_id="profile", version_id="profile-v1", sha256="e" * 64)
TEXT = "A证书且B证书"


def notice(number="1"):
    return EvidenceRef(
        evidence_id="notice-" + number,
        side="notice",
        object_kind="notice",
        version=VersionRef(
            object_id="notice-" + number, version_id="notice-v" + number, sha256="a" * 64
        ),
        text_sha256="b" * 64,
        quote=TEXT,
        start=0,
        end=len(TEXT),
    )


def requirement(operator="atom", *, name="c", scope="all", hard=True, revision="1", validity=None):
    reference = notice(revision)
    node = lambda key: {  # noqa: E731 - concise synthetic fixture builder
        "node_id": name + ":" + key,
        "operator": "atom",
        "text": TEXT,
        "evidence_ids": [reference.evidence_id],
    }
    expression = node("a")
    if operator != "atom":
        expression = {
            "node_id": name + ":root",
            "operator": operator,
            "text": TEXT,
            "evidence_ids": [reference.evidence_id],
            "operands": [node("a")] if operator == "not" else [node("a"), node("b")],
        }
    return Condition(
        condition_id=name,
        original_text=TEXT,
        hard_requirement=hard,
        expression=expression,
        notice_evidence=[reference],
        package_scope={
            "kind": scope,
            "package_ids": ["package-1"] if scope == "specific" else [],
            "evidence_ids": [reference.evidence_id],
            "reason": "ambiguous_package" if scope == "unknown" else None,
        },
        validity=validity or {"kind": "not_required"},
    )


def proof(identity="proof", *, validity=None):
    return EvidenceRef(
        evidence_id=identity,
        side="company",
        object_kind="company_fact",
        version=PROFILE,
        text_sha256="f" * 64,
        quote="已有证明",
        start=0,
        end=4,
        validity=validity or {"kind": "not_required"},
    )


def judgment(node_id="c:a", status="satisfied", *, evidence=True, validity=None):
    company = [proof(node_id + ":proof", validity=validity)] if evidence else []
    conflicts, scope = [], []
    if status == "evidence_conflict":
        company = [proof(node_id + ":proof"), proof(node_id + ":conflicting")]
        conflicts = [item.evidence_id for item in company]
    if status == "not_applicable":
        scope = [item.evidence_id for item in company]
    return NodeJudgment(
        node_id=node_id,
        status=status,
        reason="synthetic_unreviewed",
        company_evidence=company,
        conflicting_evidence_ids=conflicts,
        scope_evidence_ids=scope,
        missing_items=[node_id + ":missing"] if status == "information_insufficient" else [],
        validity_evidence_ids=[item.evidence_id for item in company] if validity else [],
    )


def inventory(*conditions, missing=(), incomplete=False):
    conditions = list(conditions or [requirement()])
    rows = {}
    for condition in conditions:
        reference = condition.notice_evidence[0]
        key = reference.version.version_id
        row = rows.setdefault(
            key,
            {
                "document_id": key,
                "object_kind": "notice",
                "version": reference.version,
                "parent_notice_revision": reference.version,
                "text_sha256": reference.text_sha256,
                "text_length": len(TEXT),
                "segments": [
                    {
                        "segment_id": "segment-" + key,
                        "start": 0,
                        "end": len(TEXT),
                        "disposition": "requirement",
                        "condition_ids": [],
                    }
                ],
            },
        )
        row["segments"][0]["condition_ids"].append(condition.condition_id)
    unresolved = []
    if incomplete:
        unresolved.append({"document_id": next(iter(rows)), "reason": "cross_segment_logic"})
    return ConditionInventory(
        documents=list(rows.values()),
        conditions=conditions,
        unresolved=unresolved,
        missing_attachment_ids=list(missing),
    )


def assessment(condition=None, status="satisfied"):
    condition = condition or requirement()
    return assess_condition(condition, [judgment(condition.expression.node_id, status)])


def inputs(condition=None):
    return AssessmentInputs(
        project_family_id="family",
        project_id="project",
        notice_revision=(condition or requirement()).notice_evidence[0].version,
        profile_revision=PROFILE,
        as_of=AT,
    )


# Rows/columns follow STATES: satisfied, not_satisfied, insufficient, conflict, NA.
# The literal matrices are the acceptance oracle, independent of reducer branches.
TABLES = {
    "all": ("S F I C S", "F F F F F", "I F I C I", "C F C C C", "S F I C N"),
    "any": ("S S S S S", "S F I C F", "S I I C I", "S C C C C", "S F I C N"),
    "unless": ("N S S S S", "N F I C F", "N I I C I", "N C C C C", "N N N N N"),
}
DECODE = dict(zip(("S", "F", "I", "C", "N"), STATES, strict=True))


@pytest.mark.parametrize(
    "operator,left,right", list(product(("all", "any", "unless"), STATES, STATES))
)
def test_five_state_binary_truth_table(operator, left, right):
    expression = requirement(operator).expression
    actual = reduce_expression(expression, {"c:a": left, "c:b": right})
    assert actual == DECODE[TABLES[operator][STATES.index(left)].split()[STATES.index(right)]]


@pytest.mark.parametrize("state", STATES)
def test_not_preserves_unknown_conflict_and_scope(state):
    expected = {"satisfied": "not_satisfied", "not_satisfied": "satisfied"}.get(state, state)
    assert reduce_expression(requirement("not").expression, {"c:a": state}) == expected


@pytest.mark.parametrize(
    "invalid", [{}, {"c:a": "satisfied", "foreign": "satisfied"}, {"c:a": "PASS"}, {"c:a": []}]
)
def test_leaf_coverage_or_invalid_status_is_rejected(invalid):
    with pytest.raises(ValueError):
        reduce_expression(requirement().expression, invalid)


def test_mutated_expression_and_wrong_judgment_node_are_revalidated():
    expression = requirement("all").expression
    expression.operands[1].node_id = "c:a"
    with pytest.raises(ValueError, match="duplicate_logic_node_id"):
        reduce_expression(expression, {"c:a": "satisfied"})
    with pytest.raises(ValueError, match="leaf_judgment_id_mismatch"):
        reduce_expression(requirement().expression, {"c:a": judgment("wrong")})


@pytest.mark.parametrize("status", STATES)
def test_atoms_produce_all_five_states_with_actual_both_sides(status):
    condition = requirement()
    result = assess_condition(condition, [judgment(status=status)])
    assert result.status == status
    assert result.notice_evidence == condition.notice_evidence
    if status in {"satisfied", "not_satisfied", "not_applicable"}:
        assert result.company_evidence
    assert "actor" not in type(result).model_fields


@pytest.mark.parametrize("status", ["satisfied", "not_satisfied", "not_applicable"])
def test_definite_atom_without_company_proof_rejected(status):
    with pytest.raises(ValidationError, match="definite_judgment_requires_company_evidence"):
        assess_condition(requirement(), [judgment(status=status, evidence=False)])


@pytest.mark.parametrize(
    "operator,decisive,expected",
    [("any", "satisfied", "satisfied"), ("all", "not_satisfied", "not_satisfied")],
)
def test_decisive_branch_does_not_inherit_unused_missing_items(operator, decisive, expected):
    result = assess_condition(
        requirement(operator),
        [judgment(status=decisive), judgment("c:b", "information_insufficient", evidence=False)],
    )
    assert result.status == expected
    assert result.missing_items == []
    assert [item.evidence_id for item in result.company_evidence] == ["c:a:proof"]


def test_proven_or_retains_only_decisive_evidence_but_rejects_invalid_unused_atom():
    condition = requirement("any")
    result = assess_condition(condition, [judgment(), judgment("c:b", "evidence_conflict")])
    assert result.status == "satisfied"
    assert result.conflicting_evidence_ids == []
    with pytest.raises(ValidationError, match="definite_judgment_requires_company_evidence"):
        assess_condition(condition, [judgment(), judgment("c:b", "satisfied", evidence=False)])


def test_exception_needs_its_own_proof_and_unknown_cannot_waive_base():
    condition = requirement("unless")
    result = assess_condition(
        condition, [judgment(status="information_insufficient", evidence=False), judgment("c:b")]
    )
    assert result.status == "not_applicable"
    assert result.scope_evidence_ids
    assert [ref.evidence_id for ref in result.company_evidence] == ["c:b:proof"]
    unknown = assess_condition(
        condition,
        [
            judgment(status="not_satisfied"),
            judgment("c:b", "information_insufficient", evidence=False),
        ],
    )
    assert unknown.status == "information_insufficient"
    assert unknown.missing_items == ["c:b:missing"]
    with pytest.raises(ValidationError):
        assess_condition(condition, [judgment(), judgment("c:b", evidence=False)])


def test_conflicting_and_redefined_evidence_ids_rejected():
    first, second = judgment(), judgment("c:b")
    second.company_evidence[0].evidence_id = first.company_evidence[0].evidence_id
    second.company_evidence[0].quote = "不同证明"
    with pytest.raises(ValueError, match="evidence_id_redefined"):
        assess_condition(requirement("all"), [first, second])
    duplicate = judgment()
    with pytest.raises(ValueError, match="duplicate_leaf_judgment"):
        assess_condition(requirement(), [duplicate, duplicate])


@pytest.mark.parametrize(
    "scope,target,expected",
    [
        ("all", None, "applicable"),
        ("specific", "package-1", "applicable"),
        ("specific", "package-2", "out_of_scope"),
        ("specific", None, "unknown"),
        ("unknown", "package-1", "unknown"),
    ],
)
def test_package_applicability_is_separate_from_qualification(scope, target, expected):
    record = applicability(requirement(scope=scope), target)
    assert record.disposition == expected
    assert all(ref.side == "notice" for ref in record.notice_evidence)
    if expected == "unknown":
        result = assess_condition(requirement(scope=scope), [judgment()], target_package_id=target)
        assert result.status == "information_insufficient"
        assert "package_scope_unresolved" in result.missing_items
    elif expected == "out_of_scope":
        with pytest.raises(ValueError, match="use_applicability_record"):
            assess_condition(requirement(scope=scope), [judgment()], target_package_id=target)


def temporal(kind="at", basis="as_of"):
    return {
        "kind": kind,
        "basis": basis,
        "required_at": AT,
        "required_until": AT + timedelta(days=1) if kind == "throughout" else None,
        "evidence_ids": ["notice-1"],
    }


@pytest.mark.parametrize(
    "material,expected",
    [
        (
            {"kind": "interval", "valid_from": AT, "valid_until": AT + timedelta(days=1)},
            "satisfied",
        ),
        (
            {
                "kind": "interval",
                "valid_from": AT,
                "valid_until": AT + timedelta(days=1),
                "end_inclusive": False,
            },
            "information_insufficient",
        ),
        (
            {
                "kind": "interval",
                "valid_from": AT - timedelta(days=2),
                "valid_until": AT - timedelta(days=1),
            },
            "information_insufficient",
        ),
        ({"kind": "unknown", "reason": "unverified_dates"}, "information_insufficient"),
    ],
)
def test_temporal_proof_must_cover_entire_required_period(material, expected):
    result = assess_condition(
        requirement(validity=temporal("throughout")), [judgment(validity=material)], as_of=AT
    )
    assert result.status == expected


def test_unknown_time_and_absent_validity_proof_are_never_satisfied():
    result = assess_condition(
        requirement(validity={"kind": "unknown", "reason": "deadline_ambiguous"}), [judgment()]
    )
    assert result.status == "information_insufficient"
    condition = requirement(validity=temporal())
    assert assess_condition(condition, [judgment()], as_of=AT).status == "information_insufficient"
    for moment in (None, AT + timedelta(days=1)):
        with pytest.raises(ValueError, match="as_of_requirement_must_match_inputs"):
            assess_condition(condition, [judgment()], as_of=moment)


def test_all_temporal_witnesses_need_valid_proofs():
    valid = {"kind": "interval", "valid_from": AT, "valid_until": AT + timedelta(days=1)}
    result = assess_condition(
        requirement("all", validity=temporal()),
        [judgment(validity=valid), judgment("c:b")],
        as_of=AT,
    )
    assert result.status == "information_insufficient"
    positive = assess_condition(
        requirement("any", validity=temporal()),
        [judgment(validity=valid), judgment("c:b", "information_insufficient", evidence=False)],
        as_of=AT,
    )
    assert positive.status == "satisfied"


@pytest.mark.parametrize(
    "hard_status,expected",
    [
        ("satisfied", "review_recommended"),
        ("not_satisfied", "do_not_pursue"),
        ("information_insufficient", "needs_materials"),
        ("evidence_conflict", "resolve_conflict"),
        ("not_applicable", "review_recommended"),
    ],
)
def test_aggregate_hard_states_produce_suggestions_only(hard_status, expected):
    result = aggregate_recommendation(
        inventory(), [assessment(status=hard_status)], project_status="open"
    )
    assert result.recommendation == expected
    assert result.human_review_state == "unreviewed"
    assert "approved" not in result.model_dump_json()


@pytest.mark.parametrize("status", ["closed", "terminated"])
def test_closed_or_terminated_precedes_ready_checklist(status):
    result = aggregate_recommendation(inventory(), [assessment()], project_status=status)
    assert result.recommendation == "do_not_pursue"


@pytest.mark.parametrize(
    "case", ["unparsed", "missing_attachment", "execution_failed", "assessment_missing"]
)
def test_incomplete_work_never_gets_positive_suggestion(case):
    items = inventory(
        missing=["pdf"] if case == "missing_attachment" else [], incomplete=case == "unparsed"
    )
    result = aggregate_recommendation(
        items,
        [] if case == "assessment_missing" else [assessment()],
        project_status="open",
        execution_complete=case != "execution_failed",
    )
    assert result.recommendation == "not_evaluated"


@pytest.mark.parametrize(
    "flag", ["unknown", "stale", "incomplete_source", "attachment", "integrity"]
)
def test_unknown_or_unhealthy_status_is_not_a_ready_checklist(flag):
    status = ProjectStatus(
        project_id="project",
        as_of=AT,
        status="unknown" if flag == "unknown" else "open",
        reason="synthetic",
        is_fresh=flag != "stale",
        source_complete=flag != "incomplete_source",
        missing_attachment_ids=["pdf"] if flag == "attachment" else [],
        integrity_issues={"blob": "unavailable"} if flag == "integrity" else {},
    )
    assert (
        aggregate_recommendation(inventory(), [assessment()], project_status=status).recommendation
        == "status_unknown"
    )


def test_soft_unknown_remains_visible_and_unknown_scope_blocks_ready_suggestion():
    hard, soft = requirement(), requirement(name="soft", hard=False)
    result = aggregate_recommendation(
        inventory(hard, soft),
        [assessment(hard), assessment(soft, "information_insufficient")],
        project_status="open",
    )
    assert result.recommendation == "review_recommended"
    assert result.missing_items == ["soft:a:missing"]
    unknown = requirement(scope="unknown")
    assert (
        aggregate_recommendation(
            inventory(unknown), [assessment(unknown)], project_status="open"
        ).recommendation
        == "needs_materials"
    )


def test_aggregate_rejects_duplicates_foreign_conditions_and_human_approval():
    for rows in ([assessment(), assessment()], [assessment(requirement(name="foreign"))]):
        with pytest.raises(ValueError):
            aggregate_recommendation(inventory(), rows, project_status="open")
    with pytest.raises(ValidationError):
        ScreeningSummary(
            recommendation="review_recommended", reasons=["ready"], human_review_state="approved"
        )
    with pytest.raises(ValueError):
        aggregate_recommendation(inventory(), [assessment()], project_status="finished")


def test_multiple_notices_are_separate_groups_and_other_packages_use_applicability():
    first, second, excluded = (
        requirement(),
        requirement(name="c2", revision="2"),
        requirement(name="other", scope="specific"),
    )
    items = inventory(first, second, excluded)
    assert conditions_for_group(items, first.notice_evidence[0].version, "package-2") == [first]
    assert conditions_for_group(items, second.notice_evidence[0].version, "package-2") == [second]
    group = build_assessment_group(
        inputs(first),
        [first],
        [assessment(first)],
        target_package_id="package-2",
        assessment_id="test-run",
        result_revision="result-1",
    )
    assert group.primary_notice_revision == first.notice_evidence[0].version
    assert group.assessment.inputs.profile_revision == PROFILE
    assert (
        build_assessment_group(
            inputs(), [], [], target_package_id=None, assessment_id="empty", result_revision="r"
        )
        is None
    )
    with pytest.raises(ValidationError, match="evidence_version_not_in_inputs"):
        build_assessment_group(
            inputs(first),
            [first, second],
            [assessment(first), assessment(second)],
            target_package_id=None,
            assessment_id="mixed",
            result_revision="r",
        )
    with pytest.raises(ValidationError):
        build_assessment_group(
            inputs(),
            [first],
            [],
            target_package_id=None,
            assessment_id="missing",
            result_revision="r",
        )


def test_mismatched_or_mutated_versions_cannot_be_rebound_to_old_group():
    condition = requirement()
    result = assessment(condition)
    result.company_evidence[0].version.sha256 = "c" * 64
    with pytest.raises(ValidationError, match="evidence_version_not_in_inputs"):
        build_assessment_group(
            inputs(),
            [condition],
            [result],
            target_package_id=None,
            assessment_id="wrong",
            result_revision="r",
        )
    result.company_evidence.clear()
    with pytest.raises(ValidationError, match="definite_judgment_requires_company_evidence"):
        aggregate_recommendation(inventory(), [result], project_status="open")


@pytest.mark.parametrize(
    "state,expected",
    [
        ("open", "not_evaluated"),
        ("unknown", "not_evaluated"),
        ("terminated", "do_not_pursue"),
        ("closed", "do_not_pursue"),
    ],
)
def test_unresolved_revision_precedence_cannot_reject_from_obsolete_hard_condition(state, expected):
    items = inventory(incomplete=True)
    items.unresolved[0].reason = "revision_precedence_requires_review"
    result = aggregate_recommendation(
        items, [assessment(status="not_satisfied")], project_status=state
    )
    assert result.recommendation == expected
