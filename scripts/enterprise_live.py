"""Opt-in real DeepSeek integration on an isolated copy of the existing real corpus."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import time
import uuid
from pathlib import Path

from bidops.agent_demo.runtime import INSTRUCTIONS, AgentService, DemoSettings
from bidops.agent_demo.schemas import ChatRequest
from bidops.config import Settings
from bidops.enterprise.auth import Principal
from bidops.enterprise.backup import backup, restore
from bidops.enterprise.config import EnterpriseSettings
from bidops.enterprise.sessions import PersistentConversations
from bidops.storage import Store


def record_new_costs(source_db, probe_db, baseline_ids: set[str]) -> int:
    """Keep the shared development budget ledger, including failures, across isolated probes."""
    recorded = 0
    for row in probe_db.rows("SELECT * FROM agent_runs"):
        if row["run_id"] in baseline_ids:
            continue
        source_db.execute(
            "INSERT INTO agent_runs(run_id,created_at,state,question_sha256,reserved_cny,"
            "estimated_cny,input_tokens,output_tokens,model_requests,error_code) "
            "VALUES (:run_id,:created_at,:state,:question_sha256,:reserved_cny,"
            ":estimated_cny,:input_tokens,:output_tokens,:model_requests,:error_code) "
            "ON CONFLICT(run_id) DO NOTHING",
            row,
        )
        recorded += 1
    return recorded


async def exercise(run_id: str):
    output = Path("data/artifacts") / run_id
    report_path = Path("reports") / f"{run_id}.json"
    if report_path.exists():
        raise ValueError("Report exists; do not overwrite past evidence")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    source = Store(Settings())
    baseline_ids = {r["run_id"] for r in source.db.rows("SELECT run_id FROM agent_runs")}
    before = source.db.rows("SELECT COUNT(*) n FROM notices")[0]["n"]
    backup(source.settings, output / "backup")
    source.db.engine.dispose()
    restore(output / "backup", output / "restored")
    settings = Settings(
        data_dir=output / "restored", database_url=f"sqlite:///{output}/restored/bidops.sqlite3"
    )
    store = Store(settings)
    # OIDC is separately verified with signed protocol tests; this is a provider/persistence probe.
    principal = Principal("integration-probe", "Integration probe", ("analyst",), "unused")
    service = AgentService(store, DemoSettings())
    manager = PersistentConversations(service, EnterpriseSettings())
    request = ChatRequest(message="TGPC-2026-A-0384 的预算是多少？")
    key = uuid.uuid4().hex
    started = time.monotonic()
    checks, answers = [], []
    failure = None
    try:
        first = await manager.chat(principal, request, key)
        answers.append(first)
        checks.append(
            {
                "name": "real_notice_budget",
                "passed": first.provider_executed
                and any(
                    c.project_code == "TGPC-2026-A-0384" and c.budget_yuan == "1120000.00"
                    for b in first.blocks
                    for c in b.items
                ),
            }
        )
        await service.close()
        store.db.engine.dispose()
        store = Store(settings)
        service = AgentService(store, DemoSettings())
        manager = PersistentConversations(service, EnterpriseSettings())
        count = store.db.rows("SELECT COUNT(*) n FROM agent_runs")[0]["n"]
        cached = await manager.chat(principal, request, key)
        checks.append(
            {
                "name": "restart_idempotency_no_provider_rerun",
                "passed": cached.run_id == first.run_id
                and store.db.rows("SELECT COUNT(*) n FROM agent_runs")[0]["n"] == count,
            }
        )
        second = await manager.chat(
            principal,
            ChatRequest(message="它的采购单位是谁？", session_id=first.session_id),
            uuid.uuid4().hex,
        )
        answers.append(second)
        checks.append(
            {
                "name": "followup_after_restart",
                "passed": second.provider_executed
                and any(
                    c.project_code == "TGPC-2026-A-0384"
                    and c.buyer_name == "天津市生态环境监测中心"
                    for b in second.blocks
                    for c in b.items
                ),
            }
        )
        third = await manager.chat(
            principal,
            ChatRequest(
                message="比较 TGPC-2026-A-0384 和 TGPC-2026-A-0343 的预算差额",
                session_id=first.session_id,
            ),
            uuid.uuid4().hex,
        )
        answers.append(third)
        checks.append(
            {
                "name": "exact_comparison",
                "passed": third.provider_executed
                and any(
                    b.metrics.get("budget_difference_yuan") == "950000.00" for b in third.blocks
                ),
            }
        )
        checks.append(
            {
                "name": "saved_history",
                "passed": len(manager.history(principal, first.session_id)) == 3,
            }
        )
    except Exception as exc:
        failure = getattr(exc, "code", type(exc).__name__)
    finally:
        await service.close()
        # Execute live probes sequentially; copying a database is not a distributed budget service.
        record_new_costs(source.db, store.db, baseline_ids)
        store.db.engine.dispose()
        source.db.engine.dispose()
    (output / "responses.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in answers], ensure_ascii=False, indent=2)
    )
    report = {
        "run_id": run_id,
        "date": "2026-10-22",
        "kind": "real_provider_integration_not_fresh_holdout",
        "notice_count": before,
        "new_collection": 0,
        "checks": checks,
        "passed": sum(c["passed"] for c in checks),
        "planned_checks": 5,
        "executed_checks": len(checks),
        "failure": failure,
        "real_model_turns": len(answers),
        "model": sorted({a.model for a in answers}),
        "input_tokens": sum(a.usage["input_tokens"] for a in answers),
        "output_tokens": sum(a.usage["output_tokens"] for a in answers),
        "model_requests": sum(a.usage["model_requests"] for a in answers),
        "estimated_cny_peak_completed": round(
            sum(a.usage["estimated_cost_cny_peak"] for a in answers), 6
        ),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "prompt_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
        "pydantic_ai": importlib.metadata.version("pydantic-ai-slim"),
        "authlib": importlib.metadata.version("authlib"),
        "oidc_live_test": False,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return not failure and len(checks) == 5 and all(c["passed"] for c in checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Real provider credits require --execute")
    if not args.run_id or any(
        c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.run_id
    ):
        parser.error("Use a lowercase safe run ID")
    raise SystemExit(0 if asyncio.run(exercise(args.run_id)) else 1)
