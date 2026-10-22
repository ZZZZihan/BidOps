"""Test that the deterministic judge rejects wrong answers, not just happy-path output."""

import copy
import json
from pathlib import Path

import pytest
from bidops.agent_demo.evaluation import check_turn


@pytest.fixture
def judge_example():
    # Authored evaluator protocol fixture; no simulated government announcement.
    source = "code title budget buyer deadline"
    gold = {
        "budget_yuan": "100.00",
        "buyer_name": "unit",
        "bid_deadline_at": "unit-deadline",
        "published_at": "unit-date",
        "source_url": "https://unit.example",
        "sha256": "a" * 64,
        "document_deadline_at": None,
        "current_status": "unknown",
        "package_count": 1,
        "package_budgets_yuan": ["100.00"],
    }
    citations = []
    for field, word in [
        ("project_code", "code"),
        ("title", "title"),
        ("budget", "budget"),
        ("buyer_name", "buyer"),
        ("bid_deadline_at", "deadline"),
    ]:
        start = source.index(word)
        citations.append(
            {
                "field": field,
                "source_url": gold["source_url"],
                "sha256": gold["sha256"],
                "snapshot_id": "unit-id",
                "locator": "normalized_text_v2",
                "quote": word,
                "start": start,
                "end": start + len(word),
            }
        )
    card = {**gold, "project_code": "UNIT", "snapshot_id": "unit-id", "citations": citations}
    actual = {
        "response_mode": "results",
        "focus": "budget",
        "provider_executed": True,
        "blocks": [{"total": 1, "items": [card]}],
        "trace": [{"tool": "read_notice"}],
    }
    return (
        {"codes": ["UNIT"], "mode": "results"},
        actual,
        {"notices": {"UNIT": gold}},
        {gold["sha256"]: source},
    )


def test_judge_valid_fixture(judge_example):
    assert all(check_turn(*judge_example).values())


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_budget",
        "wrong_deadline",
        "fake_url",
        "fake_quote",
        "offset",
        "false_refusal",
        "extra_project",
        "no_citations",
        "unsafe_tool",
        "mock_provider",
        "missing_metrics",
    ],
)
def test_judge_rejects_wrong_result(judge_example, mutation):
    expected, actual, reference, texts = copy.deepcopy(judge_example)
    card = actual["blocks"][0]["items"][0]
    if mutation == "wrong_budget":
        card["budget_yuan"] = "101"
    elif mutation == "wrong_deadline":
        card["bid_deadline_at"] = "invented"
    elif mutation == "fake_url":
        card["citations"][0]["source_url"] = "https://fake.example"
    elif mutation == "fake_quote":
        card["citations"][0]["quote"] = "invented"
    elif mutation == "offset":
        card["citations"][0]["end"] += 1
    elif mutation == "false_refusal":
        actual["response_mode"] = "unavailable"
    elif mutation == "extra_project":
        actual["blocks"][0]["items"].append(copy.deepcopy(card))
    elif mutation == "no_citations":
        card["citations"] = []
    elif mutation == "unsafe_tool":
        actual["trace"].append({"tool": "shell"})
    elif mutation == "mock_provider":
        actual["provider_executed"] = False
    else:
        expected["metrics"] = {"budget_sum_yuan": "100"}
    assert not all(check_turn(expected, actual, reference, texts).values())


def test_frozen_dataset_shape_and_distinct_ids():
    data = json.loads(Path("evals/cases-v1.json").read_text())["cases"]
    assert len(data) == len({c["id"] for c in data}) == 96
    assert sum(c["split"] == "dev" for c in data) == 24
    assert sum(c["split"] == "heldout" for c in data) == 72
    assert sum(len(c["turns"]) for c in data) == 105
    assert len({c["turns"][0]["message"] for c in data}) == 96
