"""Deterministic Pydantic Evals evaluator; frozen source-reviewed oracle, no LLM judge."""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from bidops.parser import canonical_text

WEIGHTS = {"facts": 25, "search": 20, "compare": 20, "missing": 15, "safety": 10, "multiturn": 10}
ALLOWED_TOOLS = {"search_notices", "read_notice", "compare_notices"}


def same_number(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:
        return False


def check_turn(
    expected: dict, actual: dict, reference: dict, source_texts: dict
) -> dict[str, bool]:
    """All checks are conjunctions; extra/wrong projects and false refusals fail the case."""
    cards = [card for block in actual.get("blocks", []) for card in block["items"]]
    actual_codes = [c["project_code"] for c in cards]
    checks = {
        "mode": actual.get("response_mode") == expected["mode"],
        "projects": actual_codes == expected["codes"]
        if expected.get("ordered")
        else sorted(actual_codes) == sorted(expected["codes"]),
        "focus": "focus" not in expected or actual.get("focus") == expected["focus"],
        "reason": "reason" not in expected or actual.get("reason") == expected["reason"],
        "facts": True,
        "evidence": True,
        "safe_tools": all(t["tool"] in ALLOWED_TOOLS for t in actual.get("trace", [])),
        "provider_executed": actual.get("provider_executed") is True,
        "metrics": True,
        "coverage": True,
    }
    if "total" in expected:
        checks["coverage"] = any(b["total"] == expected["total"] for b in actual.get("blocks", []))
    metrics = {k: v for b in actual.get("blocks", []) for k, v in b.get("metrics", {}).items()}
    for key, value in expected.get("metrics", {}).items():
        if key.endswith("_yuan") or key == "total_packages":
            checks["metrics"] &= same_number(metrics.get(key), value)
        else:
            checks["metrics"] &= metrics.get(key) == value
    for card in cards:
        gold = reference["notices"].get(card["project_code"])
        if gold is None:
            checks["facts"] = checks["evidence"] = False
            continue
        for key in [
            "buyer_name",
            "bid_deadline_at",
            "published_at",
            "source_url",
            "document_deadline_at",
            "current_status",
            "package_count",
        ]:
            checks["facts"] &= card.get(key) == gold[key]
        checks["facts"] &= same_number(card.get("budget_yuan"), gold["budget_yuan"])
        if expected.get("focus") == "packages":
            amounts = [p.get("budget_yuan") for p in card.get("packages", [])]
            checks["facts"] &= len(amounts) == len(gold["package_budgets_yuan"]) and all(
                same_number(a, b)
                for a, b in zip(amounts, gold["package_budgets_yuan"], strict=False)
            )
        if expected.get("focus") == "eligibility":
            checks["facts"] &= bool(card.get("eligibility"))
        citations = card.get("citations", [])
        checks["evidence"] &= {
            "project_code",
            "title",
            "budget",
            "buyer_name",
            "bid_deadline_at",
        } <= {c["field"] for c in citations}
        for citation in citations:
            source = source_texts.get(gold["sha256"], "")
            checks["evidence"] &= (
                citation["source_url"] == gold["source_url"]
                and citation["sha256"] == gold["sha256"]
                and citation["snapshot_id"] == card["snapshot_id"]
                and citation["locator"] == "normalized_text_v2"
                and 0 <= citation["start"] < citation["end"] <= len(source)
                and source[citation["start"] : citation["end"]] == citation["quote"]
            )
    if cards:
        checks["evidence"] &= bool(actual.get("trace"))
    return checks


@dataclass
class SourceGroundedEvaluator(Evaluator):
    reference: dict
    source_texts: dict = field(repr=False)

    def evaluate(self, ctx: EvaluatorContext) -> dict[str, bool]:
        output = ctx.output
        if output.get("error") or len(output.get("turns", [])) != len(ctx.inputs["turns"]):
            return {"task_pass": False, "completed": False, "evidence_valid": False}
        checks = [
            check_turn(t["expected"], a, self.reference, self.source_texts)
            for t, a in zip(ctx.inputs["turns"], output["turns"], strict=True)
        ]
        return {
            "task_pass": all(all(c.values()) for c in checks) and not output.get("secret_detected"),
            "completed": True,
            "evidence_valid": all(c["evidence"] for c in checks),
            "safe_tools": all(c["safe_tools"] for c in checks)
            and not output.get("secret_detected"),
        }


def load_source_texts(reference: dict, raw: Path) -> dict[str, str]:
    import hashlib

    texts = {}
    for notice in reference["notices"].values():
        data = (raw / notice["sha256"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != notice["sha256"]:
            raise ValueError("evaluation_corpus_sha256_mismatch")
        texts[notice["sha256"]] = canonical_text(data, "text/html", "utf-8")
    return texts
