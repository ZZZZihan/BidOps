"""Deterministic five-state logic and conservative, never-approved summaries."""

from collections.abc import Mapping, Sequence
from datetime import datetime

from bidops.notice_data.schemas import ProjectStatus
from bidops.qualification.contracts import (
    ApplicabilityRecord,
    AssessmentGroup,
    ConditionInventory,
    NodeJudgment,
    ScreeningSummary,
)
from bidops.screening.schemas import (
    AssessmentInputs,
    Condition,
    ConditionAssessment,
    ConditionExpression,
    EvidenceRef,
    QualificationStatus,
    ScreeningAssessment,
    VersionRef,
)


def _evaluate(node, statuses):
    """Return status and the atom IDs actually needed to establish that status."""
    if node.operator == "atom":
        return statuses[node.node_id], [node.node_id]
    children = [_evaluate(child, statuses) for child in node.operands]
    if node.operator == "not":
        status, witnesses = children[0]
        return {"satisfied": "not_satisfied", "not_satisfied": "satisfied"}.get(
            status, status
        ), witnesses
    if node.operator == "unless":
        (base, base_ids), (exception, exception_ids) = children
        if exception == "satisfied":
            return "not_applicable", exception_ids
        if base in {"satisfied", "not_applicable"}:
            return base, base_ids
        if exception in {"not_satisfied", "not_applicable"}:
            return base, base_ids + exception_ids
        state = (
            "evidence_conflict"
            if "evidence_conflict" in {base, exception}
            else "information_insufficient"
        )
        return state, base_ids + exception_ids
    decisive = "not_satisfied" if node.operator == "all" else "satisfied"
    for status, ids in children:
        if status == decisive:
            return status, ids
    active = [(status, ids) for status, ids in children if status != "not_applicable"]
    if not active:
        return "not_applicable", [key for _, ids in children for key in ids]
    status = (
        "evidence_conflict"
        if any(status == "evidence_conflict" for status, _ in active)
        else "information_insufficient"
        if any(status == "information_insufficient" for status, _ in active)
        else "satisfied"
        if node.operator == "all"
        else "not_satisfied"
    )
    return status, [key for _, ids in children for key in ids]


def reduce_expression(
    expression: ConditionExpression,
    leaf_statuses: Mapping[str, QualificationStatus | NodeJudgment],
) -> QualificationStatus:
    """Unknown is not false; NOT cannot turn absence of proof into proof.

    Status reduction alone does not validate evidence. Use assess_condition for
    checked leaf proofs, and the source adapter for authorization and raw bytes.
    """
    expression = ConditionExpression.model_validate(expression)
    nodes = expression.nodes()
    if len({node.node_id for node in nodes}) != len(nodes):
        raise ValueError("duplicate_logic_node_id")
    atoms = {node.node_id for node in nodes if node.operator == "atom"}
    if set(leaf_statuses) != atoms:
        raise ValueError("leaf_judgments_must_exactly_cover_atoms")
    statuses = {}
    allowed = {
        "satisfied",
        "not_satisfied",
        "information_insufficient",
        "evidence_conflict",
        "not_applicable",
    }
    for key, value in leaf_statuses.items():
        status = value.status if isinstance(value, NodeJudgment) else value
        if not isinstance(status, str) or status not in allowed:
            raise ValueError("invalid_leaf_status")
        if isinstance(value, NodeJudgment) and value.node_id != key:
            raise ValueError("leaf_judgment_id_mismatch")
        statuses[key] = status
    return _evaluate(expression, statuses)[0]


def applicability(condition: Condition, package_id: str | None) -> ApplicabilityRecord:
    condition = Condition.model_validate(condition)
    scope = condition.package_scope
    if scope.kind == "unknown" or (scope.kind == "specific" and package_id is None):
        disposition, reason = "unknown", "package_scope_unresolved"
    elif scope.kind == "all" or package_id in scope.package_ids:
        disposition, reason = "applicable", "package_scope_matches"
    else:
        disposition, reason = "out_of_scope", "different_package"
    evidence = [ref for ref in condition.notice_evidence if ref.evidence_id in scope.evidence_ids]
    return ApplicabilityRecord(
        condition_id=condition.condition_id,
        target_package_id=package_id,
        disposition=disposition,
        notice_evidence=evidence or condition.notice_evidence,
        reason=reason,
    )


def _unique_evidence(references: Sequence[EvidenceRef]) -> list[EvidenceRef]:
    evidence = {}
    for reference in references:
        previous = evidence.get(reference.evidence_id)
        if previous is not None and previous != reference:
            raise ValueError("evidence_id_redefined")
        evidence[reference.evidence_id] = reference
    return list(evidence.values())


def assess_condition(
    condition: Condition,
    judgments: Sequence[NodeJudgment],
    *,
    target_package_id: str | None = None,
    as_of: datetime | None = None,
) -> ConditionAssessment:
    """Validate every atom, reduce only decisive witnesses, bind the B01 result.

    Nondecisive missing/conflicting branches remain in the caller's saved drafts;
    they cannot contaminate a proven OR or conceal an invalid atom proposal.
    """
    condition = Condition.model_validate(condition)
    judgments = [NodeJudgment.model_validate(judgment) for judgment in judgments]
    by_id = {judgment.node_id: judgment for judgment in judgments}
    if len(by_id) != len(judgments):
        raise ValueError("duplicate_leaf_judgment")
    status = reduce_expression(condition.expression, by_id)
    for judgment in judgments:
        ConditionAssessment(
            condition_id=judgment.node_id,
            status=judgment.status,
            reason=judgment.reason,
            notice_evidence=condition.notice_evidence,
            company_evidence=judgment.company_evidence,
            missing_items=judgment.missing_items,
            conflicting_evidence_ids=judgment.conflicting_evidence_ids,
            scope_evidence_ids=judgment.scope_evidence_ids,
            validity_evidence_ids=judgment.validity_evidence_ids,
        )
    _unique_evidence(
        condition.notice_evidence
        + [ref for judgment in judgments for ref in judgment.company_evidence]
    )
    _, witnesses = _evaluate(
        condition.expression, {key: value.status for key, value in by_id.items()}
    )
    selected = [by_id[key] for key in dict.fromkeys(witnesses)]
    company = _unique_evidence([ref for judgment in selected for ref in judgment.company_evidence])
    missing = list(dict.fromkeys(item for judgment in selected for item in judgment.missing_items))
    conflicts = list(
        dict.fromkeys(item for judgment in selected for item in judgment.conflicting_evidence_ids)
    )
    scope = list(
        dict.fromkeys(item for judgment in selected for item in judgment.scope_evidence_ids)
    )
    validity = list(
        dict.fromkeys(item for judgment in selected for item in judgment.validity_evidence_ids)
    )
    requirement = condition.validity
    if requirement.basis == "as_of" and (as_of is None or requirement.required_at != as_of):
        raise ValueError("as_of_requirement_must_match_inputs")
    application = applicability(condition, target_package_id)
    if application.disposition == "out_of_scope":
        raise ValueError("out_of_scope_condition_use_applicability_record")
    if application.disposition == "unknown":
        status = "information_insufficient" if status != "evidence_conflict" else status
        missing.append("package_scope_unresolved")
    if status == "satisfied" and requirement.kind == "unknown":
        status, missing = "information_insufficient", ["validity_requirement_unknown"]
    if status == "satisfied" and requirement.kind in {"at", "throughout"}:
        # Every decisive leaf needs its own dated proof. A valid certificate in
        # one conjunct cannot lend its validity to an unverified second one.
        def dated_proof(judgment):
            proofs = [
                ref
                for ref in judgment.company_evidence
                if ref.evidence_id in judgment.validity_evidence_ids
            ]
            return bool(proofs) and all(
                ref.validity.covers(
                    requirement.required_at, requirement.required_until or requirement.required_at
                )
                for ref in proofs
            )

        if not all(dated_proof(judgment) for judgment in selected):
            status, missing = "information_insufficient", ["valid_proof_at_required_time"]
    # A verified exception is an applicability decision with both sides of proof.
    if status == "not_applicable":
        scope = list(
            dict.fromkeys(
                scope + condition.expression.evidence_ids + [ref.evidence_id for ref in company]
            )
        )
    if status in {"satisfied", "not_satisfied", "not_applicable"}:
        conflicts = []
        missing = []
    return ConditionAssessment(
        condition_id=condition.condition_id,
        status=status,
        reason="server_reduced_unreviewed",
        notice_evidence=condition.notice_evidence,
        company_evidence=company,
        missing_items=list(dict.fromkeys(missing)),
        conflicting_evidence_ids=conflicts,
        scope_evidence_ids=scope,
        validity_evidence_ids=validity,
    )


def conditions_for_group(
    inventory: ConditionInventory, notice_revision: VersionRef, target_package_id: str | None
) -> list[Condition]:
    inventory = ConditionInventory.model_validate(inventory)
    notice_revision = VersionRef.model_validate(notice_revision)
    parents = {
        (document.object_kind, document.version.key()): document.parent_notice_revision
        for document in inventory.documents
    }
    selected = []
    for condition in inventory.conditions:
        condition_parents = {
            parents[(ref.object_kind, ref.version.key())].key() for ref in condition.notice_evidence
        }
        if len(condition_parents) != 1:
            raise ValueError("condition_crosses_primary_notice_versions")
        if (
            notice_revision.key() in condition_parents
            and applicability(condition, target_package_id).disposition != "out_of_scope"
        ):
            selected.append(condition)
    return selected


def build_assessment_group(
    inputs: AssessmentInputs,
    conditions: Sequence[Condition],
    assessments: Sequence[ConditionAssessment],
    *,
    target_package_id: str | None,
    assessment_id: str,
    result_revision: str,
) -> AssessmentGroup | None:
    if not conditions and not assessments:
        return None
    assessment = ScreeningAssessment(
        assessment_id=assessment_id,
        result_revision=result_revision,
        inputs=inputs,
        target_package_id=target_package_id,
        conditions=list(conditions),
        assessments=list(assessments),
    )
    return AssessmentGroup(
        primary_notice_revision=assessment.inputs.notice_revision, assessment=assessment
    )


def aggregate_recommendation(
    inventory: ConditionInventory,
    assessments: Sequence[ConditionAssessment],
    *,
    project_status: ProjectStatus | str,
    target_package_id: str | None = None,
    execution_complete: bool = True,
) -> ScreeningSummary:
    inventory = ConditionInventory.model_validate(inventory)
    assessments = [ConditionAssessment.model_validate(value) for value in assessments]
    by_id = {value.condition_id: value for value in assessments}
    if len(by_id) != len(assessments):
        raise ValueError("duplicate_summary_condition")
    known = {condition.condition_id for condition in inventory.conditions}
    if not set(by_id) <= known:
        raise ValueError("summary_condition_not_in_inventory")
    state = project_status.status if isinstance(project_status, ProjectStatus) else project_status
    if state not in {"unknown", "open", "closed", "terminated"}:
        raise ValueError("invalid_project_status")
    if state in {"closed", "terminated"}:
        return ScreeningSummary(recommendation="do_not_pursue", reasons=["project_" + state])
    applicable = [
        condition
        for condition in inventory.conditions
        if applicability(condition, target_package_id).disposition != "out_of_scope"
    ]
    hard = [
        by_id[condition.condition_id]
        for condition in applicable
        if condition.hard_requirement and condition.condition_id in by_id
    ]
    missing = list(
        dict.fromkeys(item for assessment in assessments for item in assessment.missing_items)
    )
    if not execution_complete or not inventory.complete:
        return ScreeningSummary(
            recommendation="not_evaluated",
            reasons=["incomplete_condition_coverage"],
            missing_items=missing,
        )
    if any(value.status == "not_satisfied" for value in hard):
        return ScreeningSummary(
            recommendation="do_not_pursue",
            reasons=["hard_requirement_not_satisfied"],
            missing_items=missing,
        )
    if not applicable:
        return ScreeningSummary(
            recommendation="not_evaluated", reasons=["no_applicable_conditions"]
        )
    if any(condition.condition_id not in by_id for condition in applicable):
        return ScreeningSummary(
            recommendation="not_evaluated",
            reasons=["condition_assessment_missing"],
            missing_items=missing,
        )
    if any(
        applicability(condition, target_package_id).disposition == "unknown"
        for condition in applicable
    ):
        return ScreeningSummary(
            recommendation="needs_materials",
            reasons=["package_scope_unresolved"],
            missing_items=missing,
        )
    if any(value.status == "evidence_conflict" for value in hard):
        return ScreeningSummary(
            recommendation="resolve_conflict",
            reasons=["hard_requirement_conflict"],
            missing_items=missing,
        )
    if any(value.status == "information_insufficient" for value in hard):
        return ScreeningSummary(
            recommendation="needs_materials",
            reasons=["hard_requirement_proof_missing"],
            missing_items=missing,
        )
    if state != "open" or (
        isinstance(project_status, ProjectStatus)
        and (
            not project_status.is_fresh
            or not project_status.source_complete
            or project_status.missing_attachment_ids
            or project_status.integrity_issues
        )
    ):
        return ScreeningSummary(
            recommendation="status_unknown",
            reasons=["project_status_unconfirmed"],
            missing_items=missing,
        )
    return ScreeningSummary(
        recommendation="review_recommended",
        reasons=["unreviewed_checklist_ready"],
        missing_items=missing,
    )
