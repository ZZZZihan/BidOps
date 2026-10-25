"""Exact notice locations with real fixed-source ACLs and offline Pydantic AI tools.

These tests verify source binding and offsets, not a real model's interpretation.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bidops.company.authorization import CompanyError
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.requirements import bind_inventory, segment_documents
from bidops.qualification.runtime import Dependencies, extraction_agent
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
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
