"""Exact notice locations with real fixed-source ACLs and offline Pydantic AI tools.

These tests verify source binding and offsets, not a real model's interpretation.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bidops.company.authorization import CompanyError
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.contracts import ConditionDraft, DraftValidityRequirement, ExtractionDraft
from bidops.qualification.requirements import bind_inventory, segment_documents
from bidops.qualification.runtime import Dependencies, extraction_agent
from bidops.screening.schemas import ValidityRequirement
from pydantic import ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from test_company_access import ALICE
from test_notice_versions import BODY, persist
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources

UNICODE_QUOTE = "中🙂文资格证明"
OUTSIDE_QUOTE = "仅限前一段的原文"


def line(text):
    return text + "甲" * (99 - len(text)) + "\n"


@pytest.fixture
def locator(sources):
    adapter, _, material, profile, _, _ = sources
    current = line("【当前段🧪】 " + UNICODE_QUOTE + "；重复条款重复条款；aaaa；保留 空白。")
    notice = persist(
        adapter.notices,
        BODY.replace("P-001", "P-LOCATE")
        + "\n"
        + line(OUTSIDE_QUOTE)
        + current
        + line("仅限后一段的原文"),
        path="explicit-engineering-quote-location",
        at=datetime.now(UTC),
    )
    bundle = adapter.resolve(ALICE, profile, notice.project_id, datetime.now(UTC))
    document = bundle.documents[0]
    segment = next(
        item
        for item in segment_documents([document], max_segment_chars=100)
        if item.text.startswith("【当前段🧪】")
    )
    settings = ScreeningSettings(enabled=True, max_segment_chars=100, _env_file=None)
    deps = Dependencies(adapter, ALICE, bundle, document=document, segment=segment)
    agent = extraction_agent(FunctionModel(lambda messages, info: None), settings)
    function = agent._function_toolset.tools["locate_notice_quotes"].function
    return SimpleNamespace(
        deps=deps,
        settings=settings,
        function=function,
        material=material,
        call=lambda quotes: function(SimpleNamespace(deps=deps), quotes),
    )


def test_unique_unicode_offsets_are_absolute_characters_and_preserve_spaces(locator):
    result = locator.call([UNICODE_QUOTE, "保留 空白", " " + UNICODE_QUOTE])
    doc, segment = locator.deps.document, locator.deps.segment
    assert segment.start > 0
    assert result["document_id"] == doc.document_id
    assert result["segment_id"] == segment.segment_id
    for query in result["results"]:
        assert query["match_count"] == 1 and query["truncated"] is False
        assert len(query["matches"]) == 1
        match = query["matches"][0]
        assert match["start"] == doc.text.index(query["quote"])
        assert match["end"] == match["start"] + len(query["quote"])
        assert doc.text[match["start"] : match["end"]] == query["quote"]
        assert segment.start <= match["start"] < match["end"] <= segment.end
    unicode_start = result["results"][0]["matches"][0]["start"]
    assert unicode_start != len(doc.text[:unicode_start].encode("utf-8"))
    assert unicode_start != len(doc.text[:unicode_start].encode("utf-16-le")) // 2
    assert result["results"][0]["matches"][0]["end"] - unicode_start == len(UNICODE_QUOTE)


def test_repeated_and_overlapping_locations_are_all_reported_without_choosing(locator):
    result = locator.call(["重复条款", "aa", "重复条款"])["results"]
    first = locator.deps.document.text.index("重复条款")
    assert result[0] == result[2]
    assert result[0]["matches"] == [
        {"start": first, "end": first + 4},
        {"start": first + 4, "end": first + 8},
    ]
    overlapping = locator.deps.document.text.index("aaaa")
    assert result[1]["match_count"] == 3
    assert result[1]["matches"] == [
        {"start": overlapping + offset, "end": overlapping + offset + 2} for offset in range(3)
    ]
    assert all(query["truncated"] is False for query in result)


def test_no_fuzzy_matches_and_no_search_of_other_segments_or_private_materials(locator):
    private = locator.deps.bundle.material_texts[locator.material["version_id"]]
    queries = [
        OUTSIDE_QUOTE,
        "仅限后一段的原文",
        private,
        "完全不存在",
        "保留  空白",
        "资格證明",
        "P-001",
    ]
    assert OUTSIDE_QUOTE in locator.deps.document.text
    assert OUTSIDE_QUOTE not in locator.deps.segment.text
    result = locator.call(queries)
    assert [item["quote"] for item in result["results"]] == queries
    assert all(
        item["matches"] == [] and item["match_count"] == 0 and item["truncated"] is False
        for item in result["results"]
    )


def test_match_cap_retains_total_and_explicit_truncation(locator):
    result = locator.call(["甲"])["results"][0]
    expected = [
        locator.deps.segment.start + index
        for index, char in enumerate(locator.deps.segment.text)
        if char == "甲"
    ]
    assert len(expected) > 20
    assert result["match_count"] == len(expected)
    assert result["truncated"] is True
    assert result["matches"] == [{"start": index, "end": index + 1} for index in expected[:20]]


@pytest.mark.parametrize(
    "quotes", [[], [""], [" \n\t"], ["甲"] * 33, ["甲" * 4001], ["甲" * 2001] * 2]
)
def test_invalid_batch_count_blank_and_total_character_caps_are_rejected(locator, quotes):
    with pytest.raises(ModelRetry, match="4000"):
        locator.call(quotes)


def test_batch_and_total_character_caps_accept_the_exact_boundaries(locator):
    assert len(locator.call(["完全不存在"] * 32)["results"]) == 32
    exact = locator.call(["乙" * 2000, "丙" * 2000])
    assert len(exact["results"]) == 2
    assert all(not item["matches"] and item["match_count"] == 0 for item in exact["results"])


def test_revoked_material_acl_blocks_before_any_result_is_returned(locator):
    locator.deps.adapter.company.auth.revoke(ALICE, locator.material["object_id"], ALICE.owner_id)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        locator.call([UNICODE_QUOTE])


def test_acl_recheck_after_matching_prevents_release_on_mid_tool_revocation(locator, monkeypatch):
    adapter = locator.deps.adapter
    original = adapter.recheck
    calls = []

    def revoke_after_first_check(*args, **kwargs):
        calls.append(1)
        original(*args, **kwargs)
        if len(calls) == 1:
            adapter.company.auth.revoke(ALICE, locator.material["object_id"], ALICE.owner_id)

    monkeypatch.setattr(adapter, "recheck", revoke_after_first_check)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        locator.call([UNICODE_QUOTE])
    assert len(calls) == 2


def test_native_execution_guard_remains_required(locator):
    def unavailable():
        raise CompanyError("job_interrupted", 409)

    locator.deps.execution_check = unavailable
    with pytest.raises(CompanyError, match="job_interrupted"):
        locator.call([UNICODE_QUOTE])


@pytest.mark.parametrize("forgery", ["text", "offset"])
def test_actual_agent_tool_round_trip_binds_quotes_and_rejects_forgery(locator, forgery):
    observed = []

    def respond(messages, info):
        returns = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "locate_notice_quotes"
        ]
        if not returns:
            assert {tool.name for tool in info.function_tools} == {"locate_notice_quotes"}
            return ModelResponse(
                parts=[ToolCallPart("locate_notice_quotes", {"quotes": [UNICODE_QUOTE]})],
                model_name="offline-quote-locator",
            )
        result = returns[-1]
        observed.append(result)
        match = result["results"][0]["matches"][0]
        draft = {
            "document_id": result["document_id"],
            "segments": [
                {
                    "segment_id": result["segment_id"],
                    "disposition": "requirement",
                    "conditions": [
                        {
                            **match,
                            "hard_requirement": True,
                            "expression": {**match, "operator": "atom", "text": UNICODE_QUOTE},
                            "scope_kind": "all",
                            "validity": {"kind": "not_required"},
                        }
                    ],
                }
            ],
        }
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, draft)],
            model_name="offline-quote-locator",
        )

    agent = extraction_agent(FunctionModel(respond), locator.settings)
    draft = asyncio.run(agent.run("UNIT 明确工程夹具，仅验证原文定位。", deps=locator.deps)).output
    assert len(observed) == 1
    condition = draft.segments[0].conditions[0]
    assert locator.deps.document.text[condition.start : condition.end] == UNICODE_QUOTE
    if forgery == "text":
        condition.expression.text = "伪造的原文"
    else:
        condition.start += 1
        condition.end += 1
        condition.expression.start += 1
        condition.expression.end += 1
    with pytest.raises(ValueError, match="expression_text_anchor_mismatch"):
        bind_inventory(
            [locator.deps.document],
            [draft],
            lambda doc, start, end: locator.deps.adapter.make_evidence(
                locator.deps.bundle, doc, start, end
            ),
            max_segment_chars=100,
        )


AT = "2026-11-06T08:30:00+08:00"
UNTIL = "2026-11-07T08:30:00+08:00"


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "at", "basis": "explicit", "required_at": AT},
        {"kind": "throughout", "basis": "explicit", "required_at": AT, "required_until": UNTIL},
        {"kind": "unknown", "reason": "工程夹具缺少有效期依据"},
        {"kind": "not_required"},
    ],
)
def test_draft_validity_accepts_unbound_evidence_without_inventing_ids(payload):
    draft = DraftValidityRequirement.model_validate(payload)
    assert draft.evidence_ids == []
    if draft.kind in {"at", "throughout"}:
        # The final server-bound type deliberately retains the stronger contract.
        with pytest.raises(ValidationError, match="time_basis_and_evidence"):
            ValidityRequirement.model_validate(payload)


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"kind": "at", "basis": "explicit"}, "time_and_basis"),
        ({"kind": "at", "required_at": AT}, "time_and_basis"),
        ({"kind": "at", "basis": "explicit", "required_at": "2026-11-06T08:30:00"}, "timezone"),
        (
            {"kind": "at", "basis": "explicit", "required_at": AT, "required_until": UNTIL},
            "end_only",
        ),
        ({"kind": "throughout", "basis": "explicit", "required_at": AT}, "end_only"),
        (
            {"kind": "throughout", "basis": "explicit", "required_at": UNTIL, "required_until": AT},
            "inverted",
        ),
        ({"kind": "unknown", "reason": "未知", "required_at": AT}, "cannot_claim_time"),
        ({"kind": "unknown", "reason": "未知", "basis": "as_of"}, "cannot_claim_time"),
        ({"kind": "not_required", "required_at": AT}, "cannot_claim_time"),
        ({"kind": "unknown"}, "requires_reason"),
    ],
)
def test_draft_keeps_time_basis_timezone_period_and_unknown_constraints(payload, error):
    with pytest.raises(ValidationError, match=error):
        DraftValidityRequirement.model_validate(payload)


def simple_condition(locator, validity):
    match = locator.call([UNICODE_QUOTE])["results"][0]["matches"][0]
    return ConditionDraft.model_validate(
        {
            **match,
            "hard_requirement": True,
            "expression": {**match, "operator": "atom", "text": UNICODE_QUOTE},
            "scope_kind": "all",
            "validity": validity,
        }
    )


@pytest.mark.parametrize("temporary_ids", [[], ["UNIT_MODEL_MADE_UP_ID"]])
def test_binder_replaces_optional_temporary_validity_ids_with_verified_source(
    locator, temporary_ids
):
    condition = simple_condition(
        locator,
        {"kind": "at", "basis": "explicit", "required_at": AT, "evidence_ids": temporary_ids},
    )
    draft = ExtractionDraft(
        document_id=locator.deps.document.document_id,
        segments=[
            {
                "segment_id": locator.deps.segment.segment_id,
                "disposition": "requirement",
                "conditions": [condition],
            }
        ],
    )
    inventory = bind_inventory(
        [locator.deps.document],
        [draft],
        lambda doc, start, end: locator.deps.adapter.make_evidence(
            locator.deps.bundle, doc, start, end
        ),
        max_segment_chars=100,
    )
    bound = inventory.conditions[0]
    assert type(bound.validity) is ValidityRequirement
    assert bound.validity.evidence_ids
    assert "UNIT_MODEL_MADE_UP_ID" not in bound.validity.evidence_ids
    evidence = {item.evidence_id: item for item in bound.notice_evidence}
    for evidence_id in bound.validity.evidence_ids:
        actual = evidence[evidence_id]
        assert actual.quote == UNICODE_QUOTE
        assert locator.deps.adapter.resolve_evidence(ALICE, locator.deps.bundle, actual) == actual


def test_condition_draft_accepts_existing_bound_validity_instances(locator):
    existing = ValidityRequirement(
        kind="at", basis="explicit", required_at=AT, evidence_ids=["UNIT_OLD_INPUT_ID"]
    )
    condition = simple_condition(locator, existing)
    assert type(condition.validity) is DraftValidityRequirement
    assert condition.validity.required_at == existing.required_at
    assert condition.validity.evidence_ids == existing.evidence_ids


def test_compound_child_offset_error_gets_diagnostic_then_tool_correction(locator):
    document = locator.deps.document
    child_start = document.text.index(UNICODE_QUOTE)
    second = document.text.index("重复条款")
    parent_text = document.text[child_start : second + 4]
    messages_seen = []
    stage = 0

    def output(info, parent, children):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "document_id": document.document_id,
                        "segments": [
                            {
                                "segment_id": locator.deps.segment.segment_id,
                                "disposition": "requirement",
                                "conditions": [
                                    {
                                        **parent,
                                        "hard_requirement": True,
                                        "scope_kind": "all",
                                        "validity": {
                                            "kind": "unknown",
                                            "reason": "工程夹具未声明有效期",
                                        },
                                        "expression": {
                                            **parent,
                                            "operator": "all",
                                            "text": parent_text,
                                            "operands": children,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                )
            ],
            model_name="offline-compound-repair",
        )

    def respond(messages, info):
        nonlocal stage
        stage += 1
        if stage == 1:
            # An otherwise valid composite proposal has one child one character
            # too short, reproducing the observed nested off-by-one failure.
            return output(
                info,
                {"start": child_start, "end": second + 4},
                [
                    {
                        "start": child_start,
                        "end": child_start + len(UNICODE_QUOTE) - 1,
                        "operator": "atom",
                        "text": UNICODE_QUOTE,
                    },
                    {"start": second, "end": second + 4, "operator": "atom", "text": "重复条款"},
                ],
            )
        if stage == 2:
            retry = [
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, RetryPromptPart)
            ]
            messages_seen.extend(retry)
            assert any(
                isinstance(hint, str)
                and "expression_text_anchor_mismatch" in hint
                and "operands" in hint
                and "locate_notice_quotes" in hint
                for hint in retry
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "locate_notice_quotes", {"quotes": [parent_text, UNICODE_QUOTE, "重复条款"]}
                    )
                ],
                model_name="offline-compound-repair",
            )
        assert stage == 3
        located = next(
            part.content
            for message in reversed(messages)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "locate_notice_quotes"
        )["results"]
        parent = located[0]["matches"][0]
        repaired = []
        for query in located[1:]:
            within_parent = [
                match
                for match in query["matches"]
                if parent["start"] <= match["start"] < match["end"] <= parent["end"]
            ]
            assert len(within_parent) == 1
            repaired.append({**within_parent[0], "operator": "atom", "text": query["quote"]})
        return output(info, parent, repaired)

    agent = extraction_agent(FunctionModel(respond), locator.settings)
    result = asyncio.run(agent.run("UNIT 复合父子条件纠错闭环", deps=locator.deps))
    assert result.usage.requests == stage == 3
    assert messages_seen
    children = result.output.segments[0].conditions[0].expression.operands
    assert len(children) == 2
    assert all(document.text[child.start : child.end] == child.text for child in children)
