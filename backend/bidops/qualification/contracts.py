"""B04 drafts and coverage contracts, separate from the frozen B01 contracts.

Drafts are untrusted model proposals. Only the binding service creates stable
condition IDs and source references. Structural completeness is not semantic recall.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints, model_validator

from bidops.models import StrictModel
from bidops.screening.schemas import (
    Condition,
    EvidenceRef,
    QualificationStatus,
    ScreeningAssessment,
    ValidityRequirement,
    VersionRef,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Offset = Annotated[int, Field(strict=True, ge=0)]


class QualificationModel(StrictModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always", allow_inf_nan=False)


class VerifiedDocument(QualificationModel):
    document_id: Name
    object_kind: Literal["notice", "attachment"]
    version: VersionRef
    text_sha256: Digest
    text: str
    parent_notice_revision: VersionRef
    available_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def parent(self):
        if self.object_kind == "notice" and self.version != self.parent_notice_revision:
            raise ValueError("notice_document_parent_mismatch")
        return self


class SpanDraft(QualificationModel):
    start: Offset
    end: Annotated[int, Field(strict=True, gt=0)]

    @model_validator(mode="after")
    def bounds(self):
        if self.end <= self.start:
            raise ValueError("invalid_draft_span")
        return self


class ExpressionDraft(SpanDraft):
    operator: Literal["atom", "all", "any", "not", "unless"]
    text: Name
    operands: list["ExpressionDraft"] = Field(default_factory=list)

    @model_validator(mode="after")
    def arity(self):
        count = len(self.operands)
        if not {
            "atom": count == 0,
            "all": count >= 2,
            "any": count >= 2,
            "not": count == 1,
            "unless": count == 2,
        }[self.operator]:
            raise ValueError("invalid_draft_operator_arity")
        return self


class DraftValidityRequirement(QualificationModel):
    """Model-proposed time requirements before the server binds evidence spans.

    Temporary IDs are accepted for compatibility but are never trusted: the
    binder replaces them with its own references and validates ValidityRequirement.
    """

    kind: Literal["not_required", "at", "throughout", "unknown"]
    basis: Literal["as_of", "bid_deadline", "document_deadline", "explicit"] | None = None
    required_at: AwareDatetime | None = None
    required_until: AwareDatetime | None = None
    evidence_ids: list[Name] = Field(default_factory=list)
    reason: Name | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_bound_requirement(cls, value):
        if isinstance(value, ValidityRequirement):
            return value.model_dump()
        return value

    @model_validator(mode="after")
    def requirement_shape(self):
        if self.kind in {"at", "throughout"}:
            if self.required_at is None or self.basis is None:
                raise ValueError("temporal_draft_requires_time_and_basis")
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


class ConditionDraft(SpanDraft):
    hard_requirement: bool = Field(strict=True)
    expression: ExpressionDraft
    scope_kind: Literal["all", "specific", "unknown"]
    package_ids: list[Name] = Field(default_factory=list)
    scope_spans: list[SpanDraft] = Field(default_factory=list)
    scope_reason: Name | None = None
    validity: DraftValidityRequirement
    validity_spans: list[SpanDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def scope(self):
        if (self.scope_kind == "specific") != bool(self.package_ids):
            raise ValueError("specific_draft_scope_requires_packages_only")
        if len(self.package_ids) != len(set(self.package_ids)):
            raise ValueError("duplicate_draft_package")
        if self.scope_kind == "unknown" and self.scope_reason is None:
            raise ValueError("unknown_draft_scope_requires_reason")
        return self


class TextSegment(SpanDraft):
    segment_id: Name
    document_id: Name
    text: str
    processable: bool = True
    reason: Name | None = None


class SegmentDraft(QualificationModel):
    segment_id: Name
    disposition: Literal["requirement", "not_requirement", "unparsed"]
    conditions: list[ConditionDraft] = Field(default_factory=list)
    reason: Name | None = None

    @model_validator(mode="after")
    def disposition_shape(self):
        if (self.disposition == "requirement") != bool(self.conditions):
            raise ValueError("requirement_segment_requires_conditions_only")
        if self.disposition == "unparsed" and self.reason is None:
            raise ValueError("unparsed_segment_requires_reason")
        return self


class ExtractionDraft(QualificationModel):
    document_id: Name
    segments: list[SegmentDraft]


class SegmentCoverage(SpanDraft):
    segment_id: Name
    disposition: Literal["requirement", "not_requirement", "unparsed", "unprocessed"]
    condition_ids: list[Name] = Field(default_factory=list)
    reason: Name | None = None


class DocumentCoverage(QualificationModel):
    document_id: Name
    object_kind: Literal["notice", "attachment"]
    version: VersionRef
    parent_notice_revision: VersionRef
    text_sha256: Digest
    text_length: Offset
    segments: list[SegmentCoverage]

    @model_validator(mode="after")
    def parent(self):
        if self.object_kind == "notice" and self.version != self.parent_notice_revision:
            raise ValueError("notice_document_parent_mismatch")
        return self


class UnresolvedClause(QualificationModel):
    document_id: Name
    segment_id: Name | None = None
    start: Offset | None = None
    end: Annotated[int, Field(strict=True, gt=0)] | None = None
    reason: Name

    @model_validator(mode="after")
    def bounds(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("unresolved_span_requires_both_bounds")
        if self.end is not None and self.end <= self.start:
            raise ValueError("invalid_unresolved_span")
        return self


class ConditionInventory(QualificationModel):
    documents: list[DocumentCoverage]
    conditions: list[Condition] = Field(default_factory=list)
    unresolved: list[UnresolvedClause] = Field(default_factory=list)
    missing_attachment_ids: list[Name] = Field(default_factory=list)

    @model_validator(mode="after")
    def coverage_shape(self):
        documents = {document.document_id: document for document in self.documents}
        conditions = {condition.condition_id: condition for condition in self.conditions}
        if len(documents) != len(self.documents) or len(conditions) != len(self.conditions):
            raise ValueError("duplicate_inventory_document_or_condition")
        ids, segments, owners = [], {}, {}
        versions = {
            (document.object_kind, document.version.key()): document for document in self.documents
        }
        if len(versions) != len(self.documents):
            raise ValueError("duplicate_inventory_document_version")
        for document in self.documents:
            offset = 0
            for segment in document.segments:
                if segment.start != offset or segment.end > document.text_length:
                    raise ValueError("inventory_coverage_gap_or_overlap")
                if segment.segment_id in segments:
                    raise ValueError("duplicate_inventory_segment")
                segments[segment.segment_id] = (document, segment)
                if (segment.disposition == "requirement") != bool(segment.condition_ids):
                    raise ValueError("inventory_segment_condition_mismatch")
                if segment.disposition in {"unparsed", "unprocessed"} and not segment.reason:
                    raise ValueError("unresolved_inventory_segment_requires_reason")
                ids.extend(segment.condition_ids)
                owners.update({key: (document, segment) for key in segment.condition_ids})
                offset = segment.end
            if offset != document.text_length:
                raise ValueError("inventory_coverage_incomplete")
        if len(ids) != len(set(ids)) or set(ids) != set(conditions):
            raise ValueError("inventory_conditions_not_exactly_covered")
        for condition in self.conditions:
            owner, segment = owners[condition.condition_id]
            anchored = False
            for ref in condition.notice_evidence:
                source = versions.get((ref.object_kind, ref.version.key()))
                if source is None:
                    raise ValueError("inventory_evidence_not_in_documents")
                if ref.text_sha256 != source.text_sha256 or ref.end > source.text_length:
                    raise ValueError("inventory_evidence_document_mismatch")
                if source.document_id == owner.document_id:
                    anchored |= segment.start <= ref.start < ref.end <= segment.end
            if not anchored:
                raise ValueError("inventory_condition_not_anchored_in_segment")
        for unresolved in self.unresolved:
            if unresolved.document_id not in documents:
                raise ValueError("unresolved_document_not_in_inventory")
            if unresolved.segment_id is not None:
                pair = segments.get(unresolved.segment_id)
                if pair is None or pair[0].document_id != unresolved.document_id:
                    raise ValueError("unresolved_segment_not_in_inventory")
                if (unresolved.start, unresolved.end) != (pair[1].start, pair[1].end):
                    raise ValueError("unresolved_segment_span_mismatch")
            if (
                unresolved.end is not None
                and unresolved.end > documents[unresolved.document_id].text_length
            ):
                raise ValueError("unresolved_span_outside_document")
        return self

    @property
    def complete(self) -> bool:
        return (
            bool(self.documents)
            and all(document.text_length > 0 for document in self.documents)
            and not self.unresolved
            and not self.missing_attachment_ids
            and all(
                segment.disposition in {"requirement", "not_requirement"}
                for document in self.documents
                for segment in document.segments
            )
        )


class ApplicabilityRecord(QualificationModel):
    condition_id: Name
    target_package_id: Name | None
    disposition: Literal["applicable", "out_of_scope", "unknown"]
    notice_evidence: list[EvidenceRef]
    reason: Name

    @model_validator(mode="after")
    def notice_only(self):
        if not self.notice_evidence or any(ref.side != "notice" for ref in self.notice_evidence):
            raise ValueError("applicability_requires_notice_evidence")
        if self.disposition == "out_of_scope" and self.target_package_id is None:
            raise ValueError("missing_target_cannot_be_out_of_scope")
        return self


class NodeJudgment(QualificationModel):
    node_id: Name
    status: QualificationStatus
    reason: Name
    company_evidence: list[EvidenceRef] = Field(default_factory=list)
    missing_items: list[Name] = Field(default_factory=list)
    conflicting_evidence_ids: list[Name] = Field(default_factory=list)
    scope_evidence_ids: list[Name] = Field(default_factory=list)
    validity_evidence_ids: list[Name] = Field(default_factory=list)


class AssessmentGroup(QualificationModel):
    primary_notice_revision: VersionRef
    assessment: ScreeningAssessment

    @model_validator(mode="after")
    def primary(self):
        if self.assessment.inputs.notice_revision != self.primary_notice_revision:
            raise ValueError("assessment_group_primary_mismatch")
        return self


class ScreeningSummary(QualificationModel):
    recommendation: Literal[
        "review_recommended",
        "needs_materials",
        "resolve_conflict",
        "do_not_pursue",
        "status_unknown",
        "not_evaluated",
    ]
    reasons: list[Name]
    missing_items: list[Name] = Field(default_factory=list)
    human_review_state: Literal["unreviewed"] = "unreviewed"
