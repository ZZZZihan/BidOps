"""Versioned screening contracts, separate from legacy demo and storage shapes.

These models validate structure and internally consistent references. They do not
authenticate a reviewer, establish access rights, or prove that a quotation supports
a claim. Those checks belong to the authorized services and independent evaluation.
Evaluation must retain the original prediction even when validation rejects it.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints, model_validator

from bidops.models import StrictModel

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
QualificationStatus = Literal[
    "satisfied",
    "not_satisfied",
    "information_insufficient",
    "evidence_conflict",
    "not_applicable",
]
ExecutionStatus = Literal[
    "queued",
    "running",
    "waiting_review",
    "cancel_requested",
    "cancelled",
    "failed",
    "completed",
    "timed_out",
    "interrupted",
]
ReviewDecision = Literal["confirm", "reject", "request_materials"]


class ScreeningModel(StrictModel):
    # Models may be passed back after mutable lists/children were edited in memory.
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class VersionRef(ScreeningModel):
    object_id: NonBlank
    version_id: NonBlank
    sha256: Sha256

    def key(self) -> tuple[str, str, str]:
        return self.object_id, self.version_id, self.sha256


class EvidenceValidity(ScreeningModel):
    """Explicitly recorded material validity; absent dates never imply permanence."""

    kind: Literal["not_required", "interval", "perpetual", "unknown"] = "not_required"
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None
    start_inclusive: bool = True
    end_inclusive: bool = True
    reason: NonBlank | None = None

    @model_validator(mode="after")
    def interval_shape(self):
        if self.kind == "interval":
            if self.valid_from is None or self.valid_until is None:
                raise ValueError("material_validity_requires_both_bounds")
            if self.valid_until < self.valid_from:
                raise ValueError("inverted_material_validity")
        elif self.valid_from is not None or self.valid_until is not None:
            raise ValueError("validity_dates_require_interval")
        if self.kind in {"unknown", "perpetual"} and self.reason is None:
            raise ValueError("material_validity_requires_reason")
        return self

    def covers(self, start: datetime, end: datetime) -> bool:
        if self.kind == "perpetual":
            return True
        if self.kind != "interval":
            return False
        return (
            self.valid_from < start or (self.start_inclusive and self.valid_from == start)
        ) and (self.valid_until > end or (self.end_inclusive and self.valid_until == end))


class EvidenceRef(ScreeningModel):
    evidence_id: NonBlank
    side: Literal["notice", "company"]
    object_kind: Literal["notice", "attachment", "company_fact", "company_material"]
    version: VersionRef
    text_sha256: Sha256
    quote: Annotated[str, Field(min_length=1)]
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    locator: Literal["normalized_text_v2", "page_text_v1"] = "normalized_text_v2"
    page: int | None = Field(default=None, ge=1)
    validity: EvidenceValidity = Field(default_factory=EvidenceValidity)

    @model_validator(mode="after")
    def evidence_shape(self):
        if self.end <= self.start or not self.quote.strip():
            raise ValueError("invalid_evidence_span")
        if self.end - self.start != len(self.quote):
            raise ValueError("evidence_span_length_mismatch")
        if (self.locator == "page_text_v1") != (self.page is not None):
            raise ValueError("page_locator_requires_page_only")
        expected = "notice" if self.object_kind in {"notice", "attachment"} else "company"
        if self.side != expected:
            raise ValueError("evidence_side_kind_mismatch")
        return self


class AssessmentInputs(ScreeningModel):
    project_family_id: NonBlank
    project_id: NonBlank
    notice_revision: VersionRef
    profile_revision: VersionRef
    material_revisions: list[VersionRef] = Field(default_factory=list)
    attachment_revisions: list[VersionRef] = Field(default_factory=list)
    as_of: AwareDatetime

    @model_validator(mode="after")
    def unique_versions(self):
        refs = [self.notice_revision, self.profile_revision]
        refs += self.material_revisions + self.attachment_revisions
        if len(refs) != len({ref.key() for ref in refs}):
            raise ValueError("duplicate_input_version")
        identity_hashes = {}
        for ref in refs:
            key = (ref.object_id, ref.version_id)
            if key in identity_hashes and identity_hashes[key] != ref.sha256:
                raise ValueError("input_version_hash_conflict")
            identity_hashes[key] = ref.sha256
        return self


class PackageScope(ScreeningModel):
    kind: Literal["all", "specific", "unknown"]
    package_ids: list[NonBlank] = Field(default_factory=list)
    evidence_ids: list[NonBlank] = Field(default_factory=list)
    reason: NonBlank | None = None

    @model_validator(mode="after")
    def scope_shape(self):
        if len(self.package_ids) != len(set(self.package_ids)):
            raise ValueError("duplicate_package_id")
        if (self.kind == "specific") != bool(self.package_ids):
            raise ValueError("specific_scope_requires_packages_only")
        if self.kind == "specific" and not self.evidence_ids:
            raise ValueError("package_scope_requires_evidence")
        if self.kind == "unknown" and self.reason is None:
            raise ValueError("unknown_scope_requires_reason")
        return self


class ConditionExpression(ScreeningModel):
    """Ordered syntax tree; 'unless' operands are [base, exception].

    Unknown is not false: NOT unknown remains unknown, and an unknown exception
    cannot waive a base requirement. ANY needs proof for one sufficient branch,
    whereas ALL needs every applicable branch. Runtime reasoning is a B04 concern.
    """

    node_id: NonBlank
    operator: Literal["atom", "all", "any", "not", "unless"]
    text: NonBlank
    evidence_ids: list[NonBlank] = Field(min_length=1)
    operands: list["ConditionExpression"] = Field(default_factory=list)

    @model_validator(mode="after")
    def operator_arity(self):
        count = len(self.operands)
        valid = {
            "atom": count == 0,
            "all": count >= 2,
            "any": count >= 2,
            "not": count == 1,
            "unless": count == 2,
        }
        if not valid[self.operator]:
            raise ValueError("invalid_condition_operator_arity")
        return self

    def nodes(self) -> list["ConditionExpression"]:
        return [self] + [node for child in self.operands for node in child.nodes()]


class ValidityRequirement(ScreeningModel):
    kind: Literal["not_required", "at", "throughout", "unknown"]
    basis: Literal["as_of", "bid_deadline", "document_deadline", "explicit"] | None = None
    required_at: AwareDatetime | None = None
    required_until: AwareDatetime | None = None
    evidence_ids: list[NonBlank] = Field(default_factory=list)
    reason: NonBlank | None = None

    @model_validator(mode="after")
    def requirement_shape(self):
        if self.kind in {"at", "throughout"}:
            if self.required_at is None or self.basis is None or not self.evidence_ids:
                raise ValueError("temporal_requirement_requires_time_basis_and_evidence")
            if (self.kind == "throughout") != (self.required_until is not None):
                raise ValueError("throughout_requires_end_only")
            if self.required_until is not None and self.required_until < self.required_at:
                raise ValueError("inverted_requirement_period")
        elif any(
            value is not None for value in (self.basis, self.required_at, self.required_until)
        ):
            raise ValueError("unresolved_requirement_cannot_claim_time")
        if self.kind == "unknown" and self.reason is None:
            raise ValueError("unknown_validity_requires_reason")
        return self


class Condition(ScreeningModel):
    condition_id: NonBlank
    original_text: NonBlank
    hard_requirement: bool = Field(strict=True)
    package_scope: PackageScope
    expression: ConditionExpression
    validity: ValidityRequirement
    notice_evidence: list[EvidenceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def condition_references(self):
        if any(e.side != "notice" for e in self.notice_evidence):
            raise ValueError("condition_requires_notice_evidence")
        ids = [e.evidence_id for e in self.notice_evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_condition_evidence_id")
        nodes = self.expression.nodes()
        if len(nodes) != len({node.node_id for node in nodes}):
            raise ValueError("duplicate_logic_node_id")
        referenced = self.package_scope.evidence_ids + self.validity.evidence_ids
        referenced += [value for node in nodes for value in node.evidence_ids]
        if not set(referenced) <= set(ids):
            raise ValueError("unresolved_condition_evidence")
        return self


class ConditionAssessment(ScreeningModel):
    condition_id: NonBlank
    status: QualificationStatus
    reason: NonBlank
    notice_evidence: list[EvidenceRef] = Field(min_length=1)
    company_evidence: list[EvidenceRef] = Field(default_factory=list)
    # Only result-critical omissions belong here, not unused alternatives of ANY.
    missing_items: list[NonBlank] = Field(default_factory=list)
    conflicting_evidence_ids: list[NonBlank] = Field(default_factory=list)
    scope_evidence_ids: list[NonBlank] = Field(default_factory=list)
    validity_evidence_ids: list[NonBlank] = Field(default_factory=list)

    @model_validator(mode="after")
    def assessment_shape(self):
        if any(e.side != "notice" for e in self.notice_evidence) or any(
            e.side != "company" for e in self.company_evidence
        ):
            raise ValueError("assessment_evidence_side_mismatch")
        refs = self.notice_evidence + self.company_evidence
        ids = [ref.evidence_id for ref in refs]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_assessment_evidence_id")
        used = self.conflicting_evidence_ids + self.scope_evidence_ids + self.validity_evidence_ids
        if not set(used) <= set(ids):
            raise ValueError("unresolved_assessment_evidence")
        if self.status in {"satisfied", "not_satisfied", "not_applicable"}:
            if not self.company_evidence:
                raise ValueError("definite_judgment_requires_company_evidence")
            if self.conflicting_evidence_ids:
                raise ValueError("definite_judgment_cannot_have_unresolved_conflict")
        if self.status == "satisfied" and self.missing_items:
            raise ValueError("satisfied_cannot_have_missing_proof")
        if self.status == "information_insufficient" and not self.missing_items:
            raise ValueError("insufficient_requires_missing_items")
        if self.status == "evidence_conflict":
            if len(set(self.conflicting_evidence_ids)) < 2:
                raise ValueError("conflict_requires_distinct_evidence")
        elif self.conflicting_evidence_ids:
            raise ValueError("conflict_references_require_conflict_status")
        if self.status == "not_applicable" and not self.scope_evidence_ids:
            raise ValueError("not_applicable_requires_scope_evidence")
        return self


class ScreeningAssessment(ScreeningModel):
    assessment_id: NonBlank
    result_revision: NonBlank
    inputs: AssessmentInputs
    target_package_id: NonBlank | None = None
    conditions: list[Condition] = Field(min_length=1)
    assessments: list[ConditionAssessment] = Field(min_length=1)

    @model_validator(mode="after")
    def assessment_integrity(self):
        conditions = {item.condition_id: item for item in self.conditions}
        conclusions = {item.condition_id: item for item in self.assessments}
        if len(conditions) != len(self.conditions) or len(conclusions) != len(self.assessments):
            raise ValueError("duplicate_condition_or_assessment")
        if conditions.keys() != conclusions.keys():
            raise ValueError("assessment_must_cover_every_declared_condition")
        versions = {
            "notice": {self.inputs.notice_revision.key()},
            "attachment": {ref.key() for ref in self.inputs.attachment_revisions},
            "company_fact": {self.inputs.profile_revision.key()},
            "company_material": {ref.key() for ref in self.inputs.material_revisions},
        }
        seen = {}
        for condition in self.conditions:
            conclusion = conclusions[condition.condition_id]
            refs = (
                condition.notice_evidence + conclusion.notice_evidence + conclusion.company_evidence
            )
            for ref in refs:
                if ref.version.key() not in versions[ref.object_kind]:
                    raise ValueError("evidence_version_not_in_inputs")
                if ref.evidence_id in seen and seen[ref.evidence_id] != ref:
                    raise ValueError("evidence_id_redefined")
                seen[ref.evidence_id] = ref
            scope = condition.package_scope
            if scope.kind == "unknown" and conclusion.status not in {
                "information_insufficient",
                "evidence_conflict",
            }:
                raise ValueError("unknown_scope_cannot_be_definite")
            if scope.kind == "specific" and self.target_package_id not in scope.package_ids:
                if self.target_package_id is None and conclusion.status not in {
                    "information_insufficient",
                    "evidence_conflict",
                }:
                    raise ValueError("missing_target_package_cannot_be_definite")
                if conclusion.status not in {
                    "not_applicable",
                    "information_insufficient",
                    "evidence_conflict",
                }:
                    raise ValueError("condition_not_applicable_to_target_package")
            requirement = condition.validity
            if requirement.basis == "as_of" and requirement.required_at != self.inputs.as_of:
                raise ValueError("as_of_requirement_must_match_inputs")
            if conclusion.status == "satisfied":
                if requirement.kind == "unknown":
                    raise ValueError("unknown_validity_cannot_be_satisfied")
                if requirement.kind in {"at", "throughout"}:
                    proofs = [
                        ref
                        for ref in conclusion.company_evidence
                        if ref.evidence_id in conclusion.validity_evidence_ids
                    ]
                    if not proofs or not all(
                        ref.validity.covers(
                            requirement.required_at,
                            requirement.required_until or requirement.required_at,
                        )
                        for ref in proofs
                    ):
                        raise ValueError("satisfied_requires_valid_proof_at_required_time")
        return self


class ReviewCommand(ScreeningModel):
    """User input has no actor fields; authorized services must inject identity."""

    decision: ReviewDecision
    expected_result_revision: NonBlank
    reason: NonBlank
    idempotency_key: NonBlank


class HumanActor(ScreeningModel):
    actor_kind: Literal["human"]
    principal_id: NonBlank
    authorization_context_id: NonBlank


class ReviewEvent(ScreeningModel):
    """Record shape only, never proof of authentication or authority by itself.

    B05 fixtures must use test_fixture. B06 must construct server_authenticated
    events using a live authorized principal and enforce revision/idempotency.
    """

    event_id: NonBlank
    run_id: NonBlank
    assessment_id: NonBlank
    result_revision: NonBlank
    inputs_manifest_sha256: Sha256
    command: ReviewCommand
    actor: HumanActor
    occurred_at: AwareDatetime
    origin: Literal["test_fixture", "server_authenticated"]
    supersedes_event_id: NonBlank | None = None

    @model_validator(mode="after")
    def review_revision(self):
        if self.command.expected_result_revision != self.result_revision:
            raise ValueError("review_revision_mismatch")
        if self.supersedes_event_id == self.event_id:
            raise ValueError("review_event_cannot_supersede_itself")
        return self


class TaskExecution(ScreeningModel):
    """Execution completion confers neither qualification nor human approval."""

    run_id: NonBlank
    status: ExecutionStatus
    result_revision: NonBlank | None = None
    failure_reason: NonBlank | None = None

    @model_validator(mode="after")
    def execution_shape(self):
        if self.status in {"completed", "waiting_review"} and self.result_revision is None:
            raise ValueError("execution_state_requires_result_revision")
        failed = self.status in {"failed", "timed_out", "interrupted"}
        if failed != (self.failure_reason is not None):
            raise ValueError("failure_reason_requires_failure_state")
        return self
