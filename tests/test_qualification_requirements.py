"""Synthetic source binding tests; no model quality or human acceptance claim."""

import hashlib

import pytest
from bidops.qualification.contracts import (
    ConditionInventory,
    ExpressionDraft,
    ExtractionDraft,
    VerifiedDocument,
)
from bidops.qualification.requirements import bind_inventory, segment_documents
from bidops.screening.schemas import EvidenceRef, VersionRef
from pydantic import ValidationError


def document(text="A证书\nB证书\nC证书\nD证书\n", name="notice", kind="notice", parent=None):
    digest = hashlib.sha256(text.encode()).hexdigest()
    version = VersionRef(object_id=name, version_id=name + "-v1", sha256=digest)
    return VerifiedDocument(
        document_id=name,
        object_kind=kind,
        version=version,
        text_sha256=digest,
        text=text,
        parent_notice_revision=parent or version,
    )


def factory(*documents):
    by_id = {item.document_id: item for item in documents}

    def make(document_id, start, end):
        item = by_id[document_id]
        return EvidenceRef(
            evidence_id=f"{document_id}:{start}:{end}",
            side="notice",
            object_kind=item.object_kind,
            version=item.version,
            text_sha256=item.text_sha256,
            quote=item.text[start:end],
            start=start,
            end=end,
        )

    return make


def condition(item, start=0, end=None, **overrides):
    end = len(item.text) if end is None else end
    return {
        "start": start,
        "end": end,
        "hard_requirement": True,
        "expression": {
            "operator": "atom",
            "text": item.text[start:end].strip(),
            "start": start,
            "end": end,
        },
        "scope_kind": "all",
        "validity": {"kind": "not_required"},
        **overrides,
    }


def draft(item, *, conditions=None, disposition="requirement", reason=None, limit=8000):
    segments = segment_documents([item], max_segment_chars=limit)
    return {
        "document_id": item.document_id,
        "segments": [
            {
                "segment_id": segments[0].segment_id,
                "disposition": disposition,
                "conditions": conditions if conditions is not None else [condition(item)],
                "reason": reason,
            }
        ],
    }


def inventory(item=None):
    item = item or document()
    return bind_inventory([item], [draft(item)], factory(item))


def test_all_conditions_and_attachment_are_preserved_beyond_three():
    notice = document()
    attachment = document("附件要求E证书", "attachment", "attachment", notice.version)
    offset, clauses = 0, []
    for line in notice.text.splitlines(keepends=True):
        clauses.append(condition(notice, offset, offset + len(line)))
        offset += len(line)
    result = bind_inventory(
        [notice, attachment],
        [draft(notice, conditions=clauses), draft(attachment)],
        factory(notice, attachment),
    )
    assert result.complete
    assert len(result.conditions) == 5
    assert len({item.condition_id for item in result.conditions}) == 5
    assert result.conditions[-1].notice_evidence[0].object_kind == "attachment"
    restored = ConditionInventory.model_validate_json(result.model_dump_json())
    assert restored == result


def test_condition_and_node_ids_are_stable_and_position_specific():
    item = document("证书\n证书\n")
    clauses = [condition(item, 0, 3), condition(item, 3, 6)]
    answer = draft(item, conditions=clauses)
    first = bind_inventory([item], [answer], factory(item))
    second = bind_inventory([item], [answer], factory(item))
    assert first == second
    assert first.conditions[0].condition_id != first.conditions[1].condition_id
    assert first.conditions[0].expression.node_id != first.conditions[1].expression.node_id
    changed = document("证书\n证书!\n")
    assert inventory(changed).conditions[0].condition_id != first.conditions[0].condition_id


def test_segments_cover_full_text_without_clipping_or_hidden_tail():
    item = document("甲甲\n乙乙\n丙丙\n")
    segments = segment_documents([item], max_segment_chars=4)
    assert "".join(part.text for part in segments) == item.text
    assert [(part.start, part.end) for part in segments] == [(0, 3), (3, 6), (6, 9)]
    first = draft(item, conditions=[condition(item, 0, 3)], limit=4)
    result = bind_inventory([item], [first], factory(item), max_segment_chars=4)
    assert not result.complete
    assert len(result.unresolved) == 2
    assert all(part.reason == "model_segment_not_returned" for part in result.unresolved)
    assert len(result.documents[0].segments) == 3


def test_overlong_line_is_wholly_unprocessed_not_truncated():
    item = document("很长条款" * 20 + "\n短\n")
    segments = segment_documents([item], max_segment_chars=10)
    assert segments[0].text == item.text[:81]
    assert not segments[0].processable
    result = bind_inventory([item], [], factory(item), max_segment_chars=10)
    assert not result.complete
    assert result.unresolved[0].reason == "segment_size_limit"
    answer = {
        "document_id": item.document_id,
        "segments": [{"segment_id": segments[0].segment_id, "disposition": "not_requirement"}],
    }
    with pytest.raises(ValueError, match="unprocessable_segment_answer"):
        bind_inventory([item], [answer], factory(item), max_segment_chars=10)


def test_missing_attachment_and_unparsed_clause_block_completeness():
    item = document()
    result = bind_inventory(
        [item],
        [
            draft(
                item, conditions=[], disposition="unparsed", reason="cross_version_logic_unresolved"
            )
        ],
        factory(item),
        missing_attachment_ids=["missing-pdf", "missing-pdf"],
    )
    assert not result.complete
    assert result.missing_attachment_ids == ["missing-pdf"]
    assert result.unresolved[0].reason == "cross_version_logic_unresolved"


@pytest.mark.parametrize("text", ["", "  \n\t"])
def test_empty_document_is_never_complete(text):
    item = document(text)
    result = bind_inventory([item], [], factory(item))
    assert not result.complete
    assert result.unresolved[-1].reason == "empty_document"


@pytest.mark.parametrize("kind,count", [("all", 2), ("any", 2), ("not", 1), ("unless", 2)])
def test_logic_tree_scope_and_validity_are_bound_to_real_source(kind, count):
    item = document("A证B证")
    tree = {
        "operator": kind,
        "text": item.text,
        "start": 0,
        "end": 4,
        "operands": [
            {"operator": "atom", "text": item.text[i : i + 1], "start": i, "end": i + 1}
            for i in range(count)
        ],
    }
    proposed = condition(
        item,
        expression=tree,
        scope_kind="specific",
        package_ids=["package-1"],
        scope_spans=[{"start": 0, "end": 2}],
        validity={
            "kind": "at",
            "basis": "as_of",
            "required_at": "2026-10-23T00:00:00Z",
            "evidence_ids": ["invented-draft-placeholder"],
        },
        validity_spans=[{"start": 2, "end": 4}],
    )
    result = bind_inventory(
        [item], [draft(item, conditions=[proposed])], factory(item), package_ids=["package-1"]
    )
    bound = result.conditions[0]
    assert bound.expression.operator == kind
    assert len(bound.expression.operands) == count
    assert bound.package_scope.evidence_ids == ["notice:0:2"]
    assert bound.validity.evidence_ids == ["notice:2:4"]
    assert all(ref.evidence_id != "invented-draft-placeholder" for ref in bound.notice_evidence)


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("start", -1, "greater_than_equal"),
        ("end", True, "int_type"),
        ("end", 999, "condition_span_outside_segment"),
        ("package_ids", ["invented-package"], "specific_draft_scope"),
    ],
)
def test_invalid_condition_draft_is_not_silently_dropped(field, value, error):
    item = document()
    proposed = condition(item)
    proposed[field] = value
    with pytest.raises((ValidationError, ValueError), match=error):
        bind_inventory([item], [draft(item, conditions=[proposed])], factory(item))


def test_unknown_package_and_invented_source_anchor_reject():
    item = document()
    proposed = condition(item, scope_kind="specific", package_ids=["unknown"])
    with pytest.raises(ValueError, match="draft_package_not_in_inputs"):
        bind_inventory(
            [item], [draft(item, conditions=[proposed])], factory(item), package_ids=["p1"]
        )
    proposed = condition(item)
    proposed["expression"]["text"] = "伪造正文"
    with pytest.raises(ValueError, match="expression_text_anchor_mismatch"):
        bind_inventory([item], [draft(item, conditions=[proposed])], factory(item))


@pytest.mark.parametrize("mode", ["document", "segment", "duplicate", "duplicate_condition"])
def test_unknown_and_duplicate_outputs_reject(mode):
    item = document()
    answer = draft(item)
    answers = [answer]
    if mode == "document":
        answer["document_id"] = "unknown"
    elif mode == "segment":
        answer["segments"][0]["segment_id"] = "unknown"
    elif mode == "duplicate":
        answers.append(answer)
    else:
        answer["segments"][0]["conditions"] *= 2
    with pytest.raises(ValueError):
        bind_inventory([item], answers, factory(item))


@pytest.mark.parametrize(
    "field,value",
    [
        ("object_kind", "attachment"),
        ("text_sha256", "0" * 64),
        ("evidence_id", "same"),
    ],
)
def test_factory_cannot_redefine_bound_source(field, value):
    item = document("A证B证")
    original = factory(item)

    def invalid(doc_id, start, end):
        ref = original(doc_id, start, end)
        payload = ref.model_dump()
        payload[field] = value
        return payload

    proposed = condition(item)
    proposed["scope_kind"] = "specific"
    proposed["package_ids"] = ["p1"]
    proposed["scope_spans"] = [{"start": 0, "end": 1}]
    with pytest.raises(ValueError, match="bound_evidence"):
        bind_inventory([item], [draft(item, conditions=[proposed])], invalid)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_segment_limit_reject(limit):
    with pytest.raises(ValueError, match="invalid_segment_limit"):
        segment_documents([document()], max_segment_chars=limit)


def test_mutated_document_and_inventory_are_revalidated():
    item = document()
    item.text += "篡改"
    with pytest.raises(ValueError, match="document_text_hash_mismatch"):
        segment_documents([item])
    result = inventory()
    result.documents[0].segments[0].start = 1
    with pytest.raises(ValidationError, match="inventory_coverage_gap_or_overlap"):
        ConditionInventory.model_validate(result)


def test_contract_does_not_accept_human_actor_or_invalid_expression_arity():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExtractionDraft.model_validate({"document_id": "n", "segments": [], "human_actor": "agent"})
    with pytest.raises(ValidationError, match="invalid_draft_operator_arity"):
        ExpressionDraft(operator="not", text="A", start=0, end=1)


@pytest.mark.parametrize("mode", ["text_hash", "end", "owner", "parent"])
def test_inventory_revalidates_document_evidence_binding(mode):
    first, second = document("A证书", name="first"), document("B证书", name="second")
    result = bind_inventory([first, second], [draft(first), draft(second)], factory(first, second))
    if mode == "text_hash":
        result.conditions[0].notice_evidence[0].text_sha256 = "0" * 64
    elif mode == "end":
        result.documents[0].text_length = 1
        result.documents[0].segments[0].end = 1
    elif mode == "owner":
        one = result.documents[0].segments[0]
        two = result.documents[1].segments[0]
        one.condition_ids, two.condition_ids = two.condition_ids, one.condition_ids
    else:
        result.documents[0].parent_notice_revision = second.version
    with pytest.raises(ValidationError):
        ConditionInventory.model_validate(result)


@pytest.mark.parametrize("mode", ["document", "span", "one_bound"])
def test_unresolved_segment_cannot_be_moved_to_other_document_or_span(mode):
    first, second = document("A证书", name="first"), document("B证书", name="second")
    result = bind_inventory([first, second], [], factory(first, second))
    if mode == "document":
        result.unresolved[0].document_id = second.document_id
    elif mode == "span":
        result.unresolved[0].end = 2
    else:
        result.unresolved[0].start = None
    with pytest.raises(ValidationError):
        ConditionInventory.model_validate(result)


def test_child_expression_must_fit_its_immediate_parent_span():
    item = document("A证B证")
    proposed = condition(item)
    proposed["expression"] = {
        "operator": "not",
        "start": 0,
        "end": 1,
        "text": "A",
        "operands": [{"operator": "atom", "start": 2, "end": 3, "text": "B"}],
    }
    with pytest.raises(ValueError, match="expression_span_outside_condition"):
        bind_inventory([item], [draft(item, conditions=[proposed])], factory(item))


def test_zero_length_document_cannot_claim_complete_by_removing_unresolved():
    result = bind_inventory([document("")], [], factory(document("")))
    result.unresolved.clear()
    assert not ConditionInventory.model_validate(result).complete
