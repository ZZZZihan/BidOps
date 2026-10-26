"""Explicit opt-in live DeepSeek evaluation. Run from repository root."""

import argparse
import asyncio
import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from bidops.agent_demo.evaluation import (
    WEIGHTS,
    SourceGroundedEvaluator,
    check_turn,
    load_source_texts,
)
from bidops.agent_demo.prompts import prompt_identity
from bidops.agent_demo.runtime import INSTRUCTIONS, AgentService, DemoFailure, DemoSettings
from bidops.agent_demo.schemas import ChatRequest
from bidops.config import Settings
from bidops.storage import Store
from pydantic import TypeAdapter
from pydantic_evals import Case, Dataset
from pydantic_evals.reporting import EvaluationReport


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def corpus_digest(store):
    rows = store.db.rows("SELECT notice_id,payload_json FROM notices ORDER BY notice_id")
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


async def main(args):
    if not args.execute:
        raise SystemExit("Live evaluation requires --execute; default makes no model requests.")
    dataset_path, oracle_path = Path("evals/cases-v1.json"), Path("evals/reference-v1.json")
    corpus = json.loads(oracle_path.read_text())
    cases = [c for c in json.loads(dataset_path.read_text())["cases"] if c["split"] == args.split]
    output_dir = Path("data/artifacts/demo-eval") / args.run_id
    if output_dir.exists():
        raise SystemExit("Run ID already exists. Preserve the original run; choose a new ID.")
    output_dir.mkdir(parents=True)
    service = AgentService(Store(Settings()), DemoSettings())
    if not service.status()["configured"]:
        raise SystemExit("DeepSeek credential is not configured.")
    texts = load_source_texts(corpus, service.store.raw)
    before = corpus_digest(service.store)
    started = datetime.now(UTC).isoformat()
    key = service.settings.api_key.get_secret_value()
    attempts = []

    async def task(case):
        session_id = None
        result = {"turns": [], "error": None, "secret_detected": False}
        try:
            for turn in case["turns"]:
                answer = await service.chat(
                    ChatRequest(message=turn["message"], session_id=session_id),
                    now=datetime.fromisoformat(corpus["as_of"]),
                )
                session_id = answer.session_id
                payload = answer.model_dump(mode="json")
                if key in json.dumps(payload):
                    result["secret_detected"] = True
                    result["error"] = "credential_disclosure_detected"
                    break
                result["turns"].append(payload)
        except DemoFailure as exc:
            result["error"] = exc.code
        finally:
            if session_id:
                service.sessions.pop(session_id, None)
        (output_dir / f"{case['id']}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        attempts.append(case["id"])
        print(
            f"{len(attempts)}/{len(cases)} {case['id']} {result['error'] or 'completed'}",
            flush=True,
        )
        return result

    dataset = Dataset(
        name=f"BidOps Demo v1 {args.split}",
        cases=[Case(name=c["id"], inputs=c, metadata={"category": c["category"]}) for c in cases],
        evaluators=[SourceGroundedEvaluator(corpus, texts)],
    )
    try:
        report = await dataset.evaluate(task, max_concurrency=2, progress=False)
        (output_dir / "pydantic-evals.json").write_bytes(
            TypeAdapter(EvaluationReport).dump_json(report, indent=2)
        )
        rows, usages, latencies = [], [], []
        for case in report.cases:
            assertions = {k: v.value for k, v in case.assertions.items()}
            checks = []
            for turn, actual in zip(case.inputs["turns"], case.output["turns"], strict=False):
                checks.append(check_turn(turn["expected"], actual, corpus, texts))
                usages.append(actual["usage"])
                latencies.append(actual["elapsed_ms"])
            rows.append(
                {
                    "id": case.name,
                    "category": case.metadata["category"],
                    "passed": assertions.get("task_pass", False),
                    "assertions": assertions,
                    "error": case.output["error"],
                    "failed_checks": [
                        {k: v for k, v in check.items() if not v} for check in checks
                    ],
                    "questions": [t["message"] for t in case.inputs["turns"]],
                    "observed": [
                        {
                            "mode": a["response_mode"],
                            "focus": a["focus"],
                            "reason": a["reason"],
                            "codes": [c["project_code"] for b in a["blocks"] for c in b["items"]],
                            "tools": [t["tool"] for t in a["trace"]],
                        }
                        for a in case.output["turns"]
                    ],
                }
            )
        for failure in report.failures:
            source = next(c for c in cases if c["id"] == failure.name)
            rows.append(
                {
                    "id": failure.name,
                    "category": source["category"],
                    "passed": False,
                    "error": "evaluation_task_failure",
                    "assertions": {},
                }
            )
        scores = {}
        for category, weight in WEIGHTS.items():
            selected = [r for r in rows if r["category"] == category]
            total = sum(c["category"] == category for c in cases)
            passed = sum(r["passed"] for r in selected)
            scores[category] = {
                "passed": passed,
                "total": total,
                "weight": weight,
                "weighted_points": round(weight * passed / total, 4),
            }
        notice_unchanged = before == corpus_digest(service.store)
        integrity_gate = notice_unchanged and all(
            row["assertions"].get("evidence_valid", False)
            and row["assertions"].get("safe_tools", False)
            for row in rows
        )
        score = round(sum(c["weighted_points"] for c in scores.values()), 2)
        total_usage = {
            k: round(sum(u[k] for u in usages), 6)
            for k in (
                "input_tokens",
                "output_tokens",
                "model_requests",
                "tool_calls",
                "estimated_cost_cny_peak",
            )
        }
        summary = {
            "run_id": args.run_id,
            "split": args.split,
            "started_at": started,
            "completed_at": datetime.now(UTC).isoformat(),
            "as_of": corpus["as_of"],
            "dataset_sha256": sha(dataset_path),
            "oracle_sha256": sha(oracle_path),
            "corpus_manifest_sha256": corpus["manifest_sha256"],
            "prompt_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
            "prompt": prompt_identity(),
            "implementation_sha256": {
                str(p): sha(p) for p in sorted(Path("backend/bidops/agent_demo").glob("*.py"))
            },
            "versions": {p: version(p) for p in ["pydantic-ai-slim", "pydantic-evals", "openai"]},
            "model_configured": service.settings.model_name,
            "models_returned": dict(
                Counter(a["model"] for c in report.cases for a in c.output["turns"])
            ),
            "cases_total": len(cases),
            "cases_executed": len(attempts),
            "cases_passed": sum(r["passed"] for r in rows),
            "cases_failed": len(cases) - sum(r["passed"] for r in rows),
            "turns_completed": len(usages),
            "evaluation_framework_failures": len(report.failures),
            "category_scores": scores,
            "score_100": score,
            "citation_and_readonly_gate": integrity_gate,
            "notice_data_unchanged": notice_unchanged,
            "goal_met": score >= 85 and integrity_gate,
            "usage_completed_turns": total_usage,
            "ledger_estimated_spend_plus_failed_reservations_cny": service.spent(),
            "latency_ms": {
                "median": statistics.median(latencies) if latencies else None,
                "p95": sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]
                if latencies
                else None,
                "maximum": max(latencies) if latencies else None,
            },
            "limitations": [
                "Six public notice snapshots; not independent P1 gold data.",
                "Deterministic checks, no LLM judge and no blinded human review.",
                "Model aliases and provider responses may change; exact replay not guaranteed.",
                "Cost uses peak uncached token estimates, not provider invoices.",
            ],
            "cases": sorted(rows, key=lambda r: r["id"]),
        }
        public = Path("reports") / f"{args.run_id}.json"
        public.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    k: summary[k]
                    for k in [
                        "cases_total",
                        "cases_passed",
                        "score_100",
                        "goal_met",
                        "usage_completed_turns",
                        "latency_ms",
                    ]
                },
                ensure_ascii=False,
            )
        )
    finally:
        await service.close()
        service.store.db.engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "heldout"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.run_id.replace("-", "").replace("_", "").isalnum():
        parser.error("run-id must contain letters, digits, hyphens or underscores")
    asyncio.run(main(args))
