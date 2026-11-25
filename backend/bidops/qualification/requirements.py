"""Deterministic source coverage and binding; semantic extraction is external."""

import hashlib
import json
from collections.abc import Callable, Sequence

from bidops.qualification.contracts import (
    ConditionDraft,
    ConditionInventory,
    DocumentCoverage,
    ExtractionDraft,
    SegmentCoverage,
    TextSegment,
    UnresolvedClause,
    VerifiedDocument,
)
from bidops.screening.schemas import Condition, ConditionExpression, EvidenceRef, PackageScope

EvidenceFactory = Callable[[str, int, int], EvidenceRef]
DEFAULT_SEGMENT_CHARS = 8000


def stable_id(namespace: str, *values) -> str:
    payload = json.dumps([namespace, *values], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _documents(values: Sequence[VerifiedDocument]) -> list[VerifiedDocument]:
    documents = [VerifiedDocument.model_validate(value) for value in values]
    if len({item.document_id for item in documents}) != len(documents):
        raise ValueError("duplicate_document_id")
    if len({(item.object_kind, item.version.key()) for item in documents}) != len(documents):
        raise ValueError("duplicate_document_version")
    for item in documents:
        if hashlib.sha256(item.text.encode()).hexdigest() != item.text_sha256:
            raise ValueError("document_text_hash_mismatch")
    return documents


def segment_documents(
    documents: Sequence[VerifiedDocument], *, max_segment_chars: int = DEFAULT_SEGMENT_CHARS
) -> list[TextSegment]:
    """Cover every character, preserving complete lines rather than clipping text.

    A line exceeding the bound is recorded whole and unprocessable. This is a
    resource limit, not a claim that line boundaries are semantic boundaries.
    """
    if type(max_segment_chars) is not int or max_segment_chars < 1:
        raise ValueError("invalid_segment_limit")
    result = []
    for document in _documents(documents):
        start, buffer = 0, ""

        def append(value, document=document):
            nonlocal start
            end = start + len(value)
            result.append(
                TextSegment(
                    segment_id=stable_id(
                        "qualification_segment_v1",
                        document.document_id,
                        document.version.key(),
                        document.text_sha256,
                        start,
                        end,
                    ),
                    document_id=document.document_id,
                    start=start,
                    end=end,
                    text=value,
                    processable=len(value) <= max_segment_chars,
                    reason="segment_size_limit" if len(value) > max_segment_chars else None,
                )
            )
            start = end

        for line in document.text.splitlines(keepends=True):
            if buffer and len(buffer) + len(line) > max_segment_chars:
                append(buffer)
                buffer = ""
            if len(line) > max_segment_chars:
                append(line)
            else:
                buffer += line
        if buffer:
            append(buffer)
    return result


def _bind_condition(
    document: VerifiedDocument,
    proposed: ConditionDraft,
    cid: str,
    make_evidence: EvidenceFactory,
    package_ids: Sequence[str] | None,
) -> Condition:
    evidence = {}

    def citation(start, end):
        if not 0 <= start < end <= len(document.text):
            raise ValueError("draft_evidence_out_of_bounds")
        reference = EvidenceRef.model_validate(make_evidence(document.document_id, start, end))
        if (
            reference.side != "notice"
            or reference.object_kind != document.object_kind
            or reference.version != document.version
            or reference.text_sha256 != document.text_sha256
            or reference.locator != "normalized_text_v2"
            or reference.page is not None
            or reference.start != start
            or reference.end != end
            or reference.quote != document.text[start:end]
        ):
            raise ValueError("bound_evidence_source_mismatch")
        previous = evidence.get(reference.evidence_id)
        if previous is not None and previous != reference:
            raise ValueError("bound_evidence_id_redefined")
        evidence[reference.evidence_id] = reference
        return reference.evidence_id

    primary_id = citation(proposed.start, proposed.end)

    def expression(node, path=(), bounds=None):
        lower, upper = bounds or (proposed.start, proposed.end)
        if not lower <= node.start < node.end <= upper:
            raise ValueError("expression_span_outside_condition")
        if node.text != document.text[node.start : node.end].strip():
            raise ValueError("expression_text_anchor_mismatch")
        return ConditionExpression(
            node_id=stable_id("qualification_node_v1", cid, path),
            operator=node.operator,
            text=node.text,
            evidence_ids=[citation(node.start, node.end)],
            operands=[
                expression(child, (*path, i), (node.start, node.end))
                for i, child in enumerate(node.operands)
            ],
        )

    tree = expression(proposed.expression)
    if package_ids is not None and not set(proposed.package_ids) <= set(package_ids):
        raise ValueError("draft_package_not_in_inputs")
    scope_ids = [citation(span.start, span.end) for span in proposed.scope_spans]
    if proposed.scope_kind == "specific" and not scope_ids:
        scope_ids = [primary_id]
    temporal = proposed.validity.model_dump()
    temporal["evidence_ids"] = [citation(span.start, span.end) for span in proposed.validity_spans]
    if temporal["kind"] in {"at", "throughout"} and not temporal["evidence_ids"]:
        temporal["evidence_ids"] = [primary_id]
    return Condition(
        condition_id=cid,
        original_text=document.text[proposed.start : proposed.end],
        hard_requirement=proposed.hard_requirement,
        package_scope=PackageScope(
            kind=proposed.scope_kind,
            package_ids=proposed.package_ids,
            evidence_ids=scope_ids,
            reason=proposed.scope_reason,
        ),
        expression=tree,
        validity=temporal,
        notice_evidence=list(evidence.values()),
    )


def bind_inventory(
    documents: Sequence[VerifiedDocument],
    drafts: Sequence[ExtractionDraft | dict],
    make_evidence: EvidenceFactory,
    *,
    missing_attachment_ids: Sequence[str] = (),
    max_segment_chars: int = DEFAULT_SEGMENT_CHARS,
    package_ids: Sequence[str] | None = None,
) -> ConditionInventory:
    """Bind model spans to actual source references; omitted units remain visible.

    Multiple drafts per document are accepted for separate segment calls. Duplicate
    segment answers, invented units and invalid spans fail rather than being dropped.
    The callback must resolve authorized, verified source bytes; this module checks
    its returned identity and quote too, but does not replace the source service.
    """
    documents = _documents(documents)
    by_document = {document.document_id: document for document in documents}
    segments = segment_documents(documents, max_segment_chars=max_segment_chars)
    by_segment = {segment.segment_id: segment for segment in segments}
    answers = {}
    for value in drafts:
        draft = ExtractionDraft.model_validate(value)
        if draft.document_id not in by_document:
            raise ValueError("extraction_document_not_in_inputs")
        for answer in draft.segments:
            segment = by_segment.get(answer.segment_id)
            if segment is None or segment.document_id != draft.document_id:
                raise ValueError("extraction_segment_not_in_document")
            if answer.segment_id in answers:
                raise ValueError("duplicate_extraction_segment")
            if not segment.processable:
                raise ValueError("unprocessable_segment_answer")
            answers[answer.segment_id] = answer

    conditions, unresolved, coverage = [], [], {document.document_id: [] for document in documents}
    condition_ids = set()
    for segment in segments:
        document = by_document[segment.document_id]
        answer = answers.get(segment.segment_id)
        if not segment.text.strip():
            if answer is not None and answer.disposition == "requirement":
                raise ValueError("blank_segment_cannot_be_requirement")
            disposition, reason, ids = "not_requirement", None, []
        elif not segment.processable:
            disposition, reason, ids = "unprocessed", "segment_size_limit", []
        elif answer is None:
            disposition, reason, ids = "unprocessed", "model_segment_not_returned", []
        else:
            disposition, reason, ids = answer.disposition, answer.reason, []
            for proposed in answer.conditions:
                if not segment.start <= proposed.start < proposed.end <= segment.end:
                    raise ValueError("condition_span_outside_segment")
                cid = stable_id(
                    "qualification_condition_v1",
                    document.object_kind,
                    document.version.key(),
                    document.text_sha256,
                    proposed.start,
                    proposed.end,
                )
                if cid in condition_ids:
                    raise ValueError("duplicate_condition_anchor")
                condition_ids.add(cid)
                conditions.append(
                    _bind_condition(document, proposed, cid, make_evidence, package_ids)
                )
                ids.append(cid)
        coverage[document.document_id].append(
            SegmentCoverage(
                segment_id=segment.segment_id,
                start=segment.start,
                end=segment.end,
                disposition=disposition,
                condition_ids=ids,
                reason=reason,
            )
        )
        if disposition in {"unparsed", "unprocessed"}:
            unresolved.append(
                UnresolvedClause(
                    document_id=document.document_id,
                    segment_id=segment.segment_id,
                    start=segment.start,
                    end=segment.end,
                    reason=reason,
                )
            )
    for document in documents:
        if not document.text.strip():
            unresolved.append(
                UnresolvedClause(document_id=document.document_id, reason="empty_document")
            )
    return ConditionInventory(
        documents=[
            DocumentCoverage(
                document_id=document.document_id,
                object_kind=document.object_kind,
                version=document.version,
                parent_notice_revision=document.parent_notice_revision,
                text_sha256=document.text_sha256,
                text_length=len(document.text),
                segments=coverage[document.document_id],
            )
            for document in documents
        ],
        conditions=conditions,
        unresolved=unresolved,
        missing_attachment_ids=list(dict.fromkeys(missing_attachment_ids)),
    )
