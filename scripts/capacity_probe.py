#!/usr/bin/env python3
"""Execute the frozen local-engineering HTTP capacity probe, with zero external calls.

The full mode never scales time/data down. --unit-smoke uses an explicitly
versioned UNIT profile and cannot produce a formal capacity trace. All outputs
are private, include no cookies, and preserve every scheduled request/error.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from bidops.operations_acceptance.capacity import (  # noqa: E402
    CapacityManifest,
    build_run_plan,
    canonical_sha256,
    evaluate_capacity,
    load_manifest,
    scheduled_requests,
)
from bidops.operations_acceptance.load import (  # noqa: E402
    append_json,
    fixed_notice_ids,
    inventory,
    seed_company,
    verify_notice_page,
)


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def isolated_environment(environment):
    """Do not import host app configuration, provider credentials or proxy settings."""
    return {
        key: value
        for key, value in environment.items()
        if not key.upper().startswith("BIDOPS")
        and not any(
            part in key.upper()
            for part in (
                "API_KEY",
                "TOKEN",
                "SECRET",
                "PASSWORD",
                "PROXY",
            )
        )
    }


def deny_external_network():
    original = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError("Capacity fixture forbids non-loopback connections")
        return original(sock, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("External connect_ex forbidden")
    )
    socket.socket.sendto = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("External datagrams forbidden")
    )


def hardware(path):
    if platform.system() == "Darwin":
        memory = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True))
    else:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    return {
        "logical_cpus": os.cpu_count(),
        "memory_bytes": memory,
        "disk_free_bytes": shutil.disk_usage(path).free,
        "storage": "local_disk",
    }


async def login(client, account, output):
    begin = time.monotonic_ns()
    response = await client.get("/api/auth/login", follow_redirects=False)
    if response.status_code != 302:
        raise ValueError("Capacity login start failed")
    fields = parse_qs(urlsplit(response.headers["location"]).query)
    response = await client.get(
        "/api/auth/callback",
        params={
            "state": fields["state"][0],
            "code": json.dumps(
                {
                    "sub": account,
                    "nonce": fields["nonce"][0],
                    "challenge": fields["code_challenge"][0],
                }
            ),
        },
        follow_redirects=False,
    )
    if response.status_code != 303:
        raise ValueError("Capacity signed callback failed")
    response = await client.get("/api/auth/me")
    identity = response.json()
    if not identity.get("authenticated"):
        raise ValueError("Capacity signed identity absent")
    client.headers["X-CSRF-Token"] = identity["csrf_token"]
    append_json(
        output / "auth-control.jsonl",
        {
            "account_id": account,
            "completed_at": datetime.now(UTC).isoformat(),
            "elapsed_ms": (time.monotonic_ns() - begin) // 1_000_000,
            "status": "PASS",
            "signed_protocol": "RS256_PKCE",
        },
    )
    return identity


async def require(client, method, path, **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise ValueError(f"Capacity setup HTTP failed: {response.status_code}:{path.split('?')[0]}")
    return response.json()


async def setup_candidate_batch(client, output, payload):
    """Full-corpus freeze is setup, with a separate bounded, disclosed deadline.

    This does not change the timed workload's 10 second HTTP timeout, and is
    never counted as a passing API latency sample in the capacity denominator.
    """
    begin = time.monotonic_ns()
    outcome = "FAIL"
    try:
        value = await require(
            client,
            "POST",
            "/api/screening/candidates",
            json=payload,
            timeout=300,
        )
        outcome = "PASS"
        return value
    finally:
        append_json(
            output / "setup-operations.jsonl",
            {
                "operation": "freeze_complete_candidate_batch",
                "path": "/api/screening/candidates",
                "deadline_ms": 300000,
                "elapsed_ms": (time.monotonic_ns() - begin) // 1_000_000,
                "outcome": outcome,
                "included_in_timed_capacity_metrics": False,
            },
        )


async def wait_job(client, run_id, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        saved = await require(client, "GET", f"/api/jobs/runs/{run_id}")
        if saved["status"] == "waiting_review":
            return saved
        if saved["status"] in {"failed", "timed_out", "cancelled", "interrupted"}:
            raise ValueError("Capacity dependency job failed: " + saved["status"])
        await asyncio.sleep(0.1)
    raise TimeoutError("Capacity dependency job timed out")


def job_receipts(data, output, requests, origin_ns, requested_delay, started_at):
    events = read_jsonl(output / "worker-events.jsonl")
    db = sqlite3.connect(data / "business.sqlite3")
    db.row_factory = sqlite3.Row
    rows = {
        r["run_id"]: dict(r)
        for r in db.execute(
            "SELECT s.*,j.created_at FROM durable_job_state s "
            "JOIN durable_jobs j ON j.run_id=s.run_id"
        )
    }
    records = []
    for receipt in requests:
        run_id = receipt.get("run_id")
        if receipt["operation"] != "submit_task" or not run_id:
            continue
        own = [e for e in events if e.get("run_id") == run_id]
        start = next((e for e in own if e["event"] == "compute_started"), None)
        finish = next((e for e in own if e["event"] == "compute_finished"), None)
        waited = [e for e in own if e["event"] == "model_wait_part_finished"]
        row = rows.get(run_id)
        if start is None or finish is None or not waited or row is None:
            # Missing execution is left absent so the scorer keeps the planned
            # accepted request in its missing-job denominator, never fabricates 0ms.
            append_json(
                output / "incomplete-jobs.jsonl",
                {"run_id": run_id, "status": row["status"] if row else "missing"},
            )
            continue
        records.append(
            {
                "run_id": run_id,
                "submission_event_id": receipt["event_id"],
                "accepted_offset_ms": int((row["created_at"] - started_at.timestamp()) * 1000),
                "compute_started_offset_ms": max(
                    0, (start["monotonic_ns"] - origin_ns) // 1_000_000
                ),
                "compute_finished_offset_ms": max(
                    0, (finish["monotonic_ns"] - origin_ns) // 1_000_000
                ),
                "status": row["status"],
                "requested_model_delay_ms": requested_delay,
                "observed_model_delay_ms": sum(e["observed_delay_ms"] for e in waited),
                "real_model_calls": 0,
                "real_source_calls": 0,
                "result_ids": [row["result_id"]] if row["result_id"] else [],
            }
        )
    db.close()
    return records


async def execute(args, manifest):
    import httpx

    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    frozen = output / "manifest.json"
    frozen.write_text(manifest.model_dump_json(indent=2) + "\n")
    port_socket = socket.socket()
    port_socket.bind(("127.0.0.1", 0))
    port = port_socket.getsockname()[1]
    port_socket.close()
    origin = f"http://127.0.0.1:{port}"
    log = (output / "worker.log").open("wb")
    environment = isolated_environment(os.environ)
    environment.update(
        PYTHONPATH=str(ROOT / "backend"), BIDOPS_NETWORK_ENABLED="false", OTEL_SDK_DISABLED="true"
    )
    worker = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
            "--output",
            str(output),
            "--manifest",
            str(frozen),
            "--manifest-sha256",
            hashlib.sha256(frozen.read_bytes()).hexdigest(),
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    clients, identities = {}, {}

    async def observe_http(response):
        clock_path = output / "clock.json"
        base = json.loads(clock_path.read_text())["monotonic_ns"] if clock_path.exists() else None
        append_json(
            output / "http-responses.jsonl",
            {
                "method": response.request.method,
                "path": response.request.url.path,
                "status_code": response.status_code,
                "offset_ms": (time.monotonic_ns() - base) // 1_000_000 if base else None,
            },
        )

    try:
        async with httpx.AsyncClient(
            base_url=origin,
            timeout=10,
            trust_env=False,
            event_hooks={"response": [observe_http]},
        ) as health:
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                if worker.poll() is not None:
                    raise RuntimeError("Capacity HTTP worker exited during setup")
                try:
                    if (await health.get("/api/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
            else:
                raise TimeoutError("Capacity data seed/startup did not finish")
        for account in manifest.account_ids:
            client = httpx.AsyncClient(
                base_url=origin,
                timeout=manifest.workload.http_timeout_ms / 1000,
                headers={"Origin": origin},
                trust_env=False,
                event_hooks={"response": [observe_http]},
            )
            clients[account] = client
            identities[account] = await login(client, account, output)
        profile = await asyncio.to_thread(
            seed_company,
            output / "data",
            manifest,
            {account: value["user"]["subject_id"] for account, value in identities.items()},
        )
        notice = json.loads((output / "notice-seed.json").read_text())
        payload = {
            "profile_revision": profile,
            "project_id": notice["project_id"],
            "target_package_ids": [notice["package_id"]],
            "additional_material_revisions": [],
        }
        owner = clients[manifest.account_ids[0]]
        batch = await setup_candidate_batch(
            owner,
            output,
            {
                "profile_revision": profile,
                "filters": {
                    "keyword": f"SYNTHETIC_CAPACITY_{manifest.data.notice_versions - 1:05d}"
                },
            },
        )
        status_jobs = {}
        for account in manifest.client_accounts.values():
            submitted = await require(
                clients[account],
                "POST",
                "/api/jobs/runs",
                json=payload,
                headers={"Idempotency-Key": "capacity-preseed"},
            )
            status_jobs[account] = submitted["run_id"]
        saved = await wait_job(owner, status_jobs[manifest.account_ids[0]])
        for account in manifest.client_accounts.values():
            await wait_job(clients[account], status_jobs[account])
        case = await require(
            owner,
            "POST",
            "/api/reviews/cases",
            json={
                "run_id": saved["run_id"],
                "candidate_batch_id": batch["batch_id"],
                "reviewer_ids": [v["user"]["subject_id"] for v in identities.values()],
                "idempotency_key": "capacity-case",
            },
        )
        before = await asyncio.to_thread(inventory, output / "data", manifest)
        expected_notice_ids = await asyncio.to_thread(fixed_notice_ids, output / "data")
        if len(expected_notice_ids) != manifest.data.notice_versions:
            raise ValueError("Capacity fixed notice page inventory mismatch")
        write_json(output / "page-inventory.json", expected_notice_ids)
        for account in manifest.account_ids:
            identities[account] = await login(clients[account], account, output)
        started = datetime.now(UTC) + timedelta(seconds=3)
        origin_ns = time.monotonic_ns() + 3_000_000_000
        code = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        plan = build_run_plan(
            manifest,
            run_id=("UNIT-smoke-" if args.unit_smoke else "local-capacity-")
            + uuid.uuid4().hex[:12],
            started_at=started,
            code_sha=code,
            lock_sha256=hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
            durable_lock_sha256=hashlib.sha256(
                (ROOT / "requirements/durable.lock").read_bytes()
            ).hexdigest(),
        )
        schedule = scheduled_requests(manifest, plan.run_id)
        write_json(output / "plan.json", plan.model_dump(mode="json"))
        write_json(output / "schedule.json", [r.model_dump(mode="json") for r in schedule])
        write_json(
            output / "input-pin.json",
            {
                "manifest_bytes_sha256": hashlib.sha256(frozen.read_bytes()).hexdigest(),
                "manifest_sha256": canonical_sha256(manifest),
                "plan_sha256": canonical_sha256(plan),
                "dataset_sha256": before["inventory_sha256"],
                "page_inventory_sha256": canonical_sha256(expected_notice_ids),
                "planned_business_operations": len(schedule),
                "planned_business_operations_per_second": manifest.workload.requests_per_second,
                "http_accounting": "Business operations plus separate control/dependent HTTP log",
                "code_sha": code,
                "source_tree_dirty": bool(
                    subprocess.check_output(
                        ["git", "status", "--porcelain"], cwd=ROOT, text=True
                    ).strip()
                ),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "load_sha256": hashlib.sha256(
                    (ROOT / "backend/bidops/operations_acceptance/load.py").read_bytes()
                ).hexdigest(),
                "measurement_kind": "local_engineering",
                "pid": worker.pid,
                "platform": platform.platform(),
                "hardware": hardware(output),
            },
        )
        write_json(output / "clock.json", {"monotonic_ns": origin_ns})
        receipts = []
        client_locks = {c: asyncio.Lock() for c in manifest.client_accounts}
        refresh_at = {a: time.monotonic() + 180 for a in clients}
        review_lock = asyncio.Lock()

        async def arrival(fixed):
            await asyncio.sleep(
                max(
                    0,
                    (origin_ns + fixed.scheduled_offset_ms * 1_000_000 - time.monotonic_ns())
                    / 1_000_000_000,
                )
            )
            account = fixed.account_id
            client = clients[account]
            async with client_locks[fixed.client_id]:
                start = max(0, (time.monotonic_ns() - origin_ns) // 1_000_000)
                row = {
                    **fixed.model_dump(mode="json"),
                    "started_offset_ms": start,
                    "finished_offset_ms": None,
                    "outcome": "missing_finish",
                    "status_code": None,
                    "content_verified": False,
                    "expected_items": None,
                    "returned_items": None,
                    "expected_total": None,
                    "reported_total": None,
                    "truncated": False,
                    "run_id": None,
                }
                detail = {"method": "GET", "path": "", "response_sha256": None}
                try:
                    if time.monotonic() >= refresh_at[account]:
                        identities[account] = await login(client, account, output)
                        refresh_at[account] = time.monotonic() + 180
                    if fixed.operation == "read_search":
                        offset = (
                            fixed.sequence
                            % math.ceil(manifest.data.notice_versions / manifest.limits.page_size)
                        ) * manifest.limits.page_size
                        detail["path"] = "/api/notices"
                        response = await client.get(
                            detail["path"],
                            params={"limit": manifest.limits.page_size, "offset": offset},
                        )
                        value = response.json()
                        count = min(
                            manifest.limits.page_size, manifest.data.notice_versions - offset
                        )
                        row.update(
                            expected_items=count,
                            returned_items=len(value.get("items", [])),
                            expected_total=manifest.data.notice_versions,
                            reported_total=value.get("total"),
                        )
                        row["content_verified"] = verify_notice_page(
                            value, expected_notice_ids, offset, manifest.limits.page_size
                        )
                        row["truncated"] = (
                            len(value.get("items", [])) != count
                            or value.get("total") != manifest.data.notice_versions
                        )
                    elif fixed.operation == "task_status":
                        run_id = status_jobs[account]
                        detail["path"] = (
                            f"/api/jobs/runs/{run_id}" if run_id else "/api/jobs/status"
                        )
                        response = await client.get(detail["path"])
                        value = response.json()
                        row["content_verified"] = (
                            value.get("run_id") == run_id
                            if run_id
                            else value.get("backend") == "dbos"
                        )
                    elif fixed.operation == "submit_task":
                        detail.update(method="POST", path="/api/jobs/runs")
                        response = await client.post(
                            detail["path"],
                            json=payload,
                            headers={"Idempotency-Key": fixed.event_id},
                        )
                        value = response.json()
                        row["run_id"] = value.get("run_id")
                        row["content_verified"] = response.status_code == 202 and bool(
                            row["run_id"]
                        )
                    else:
                        async with review_lock:
                            current = await require(
                                client, "GET", f"/api/reviews/cases/{case['case_id']}"
                            )
                            item = current["items"][0]
                            revision = next(
                                r
                                for r in current["revisions"]
                                if r["revision_id"] == item["revision_id"]
                            )
                            detail.update(
                                method="POST",
                                path=f"/api/reviews/cases/{case['case_id']}/decisions",
                            )
                            response = await client.post(
                                detail["path"],
                                json={
                                    "revision_id": item["revision_id"],
                                    "assessment_id": item["assessment_id"],
                                    "condition_id": item["condition_id"],
                                    "expected_version": current["review_version"],
                                    "supersedes_event_id": item["latest_event"]["event_id"]
                                    if item["latest_event"]
                                    else None,
                                    "command": {
                                        "decision": "request_materials",
                                        "expected_result_revision": revision["result_revision"],
                                        "reason": "SYNTHETIC load event; no approval",
                                        "idempotency_key": fixed.event_id,
                                    },
                                },
                            )
                            value = response.json()
                            row["content_verified"] = (
                                response.status_code == 200
                                and value.get("review_version") == current["review_version"] + 1
                            )
                    row["outcome"] = "response"
                    row["status_code"] = response.status_code
                    detail["response_sha256"] = hashlib.sha256(response.content).hexdigest()
                except httpx.TimeoutException:
                    row["outcome"] = "timeout"
                    detail["error_code"] = "http_timeout"
                except Exception as exc:
                    row["outcome"] = "exception"
                    detail["error_code"] = type(exc).__name__
                finally:
                    row["finished_offset_ms"] = max(
                        start, (time.monotonic_ns() - origin_ns) // 1_000_000
                    )
                    receipts.append(row)
                    append_json(output / "requests.jsonl", {"receipt": row, "http": detail})

        await asyncio.gather(*(arrival(fixed) for fixed in schedule))
        stop_ms = (manifest.workload.warmup_seconds + manifest.workload.steady_seconds) * 1000
        await asyncio.sleep(
            max(0, (origin_ns + stop_ms * 1_000_000 - time.monotonic_ns()) / 1_000_000_000)
        )
        # Identity freshness stays in force during queued-job drain as well.
        deadline = time.monotonic() + manifest.workload.drain_seconds
        while time.monotonic() < deadline:
            connection = sqlite3.connect(output / "data/business.sqlite3")
            outstanding = connection.execute(
                "SELECT COUNT(*) FROM durable_job_state "
                "WHERE status IN ('queued','running','cancel_requested')"
            ).fetchone()[0]
            connection.close()
            if not outstanding:
                break
            for account in manifest.client_accounts.values():
                if time.monotonic() >= refresh_at[account]:
                    identities[account] = await login(clients[account], account, output)
                    refresh_at[account] = time.monotonic() + 180
            await asyncio.sleep(0.5)
        end = math.ceil((time.monotonic_ns() - origin_ns) / 1_000_000_000) * 1000
        await asyncio.sleep(
            max(
                0,
                (
                    origin_ns
                    + (end + manifest.workload.resource_interval_ms) * 1_000_000
                    - time.monotonic_ns()
                )
                / 1_000_000_000,
            )
        )
        after = await asyncio.to_thread(inventory, output / "data", manifest)
        resources = [
            r
            for r in read_jsonl(output / "resources.jsonl")
            if r["offset_ms"] <= end + manifest.workload.resource_interval_ms
        ]
        trace = {
            "run_id": plan.run_id,
            "plan_sha256": canonical_sha256(plan),
            "measurement_kind": "local_engineering",
            "ended_offset_ms": end,
            "clients": [
                {
                    "client_id": c,
                    "account_id": a,
                    "session_sha256": hashlib.sha256(
                        clients[a].cookies.get("bidops_session").encode()
                    ).hexdigest(),
                }
                for c, a in manifest.client_accounts.items()
            ],
            "hardware": hardware(output),
            "dataset_before": before,
            "dataset_after": after,
            "requests": receipts,
            "jobs": job_receipts(
                output / "data",
                output,
                receipts,
                origin_ns,
                manifest.simulation.requested_delay_ms,
                plan.started_at,
            ),
            "resources": resources,
        }
        write_json(output / "trace.json", trace)
        http_rows = read_jsonl(output / "http-responses.jsonl")
        measured_http = [
            r for r in http_rows if r["offset_ms"] is not None and 0 <= r["offset_ms"] <= end
        ]
        write_json(
            output / "http-accounting.json",
            {
                "planned_business_operations": len(schedule),
                "observed_business_operations": len(receipts),
                "http_responses_including_control_and_dependents": len(measured_http),
                "all_http_responses_including_setup": len(http_rows),
                "failed_http_responses": sum(r["status_code"] >= 400 for r in measured_http),
                "path_counts": {
                    path: sum(r["path"] == path for r in measured_http)
                    for path in sorted({r["path"] for r in measured_http})
                },
            },
        )
        report = evaluate_capacity(manifest, plan, trace)
        write_json(output / "report.json", report.model_dump(mode="json"))
        print(
            json.dumps(
                {
                    "output": str(output),
                    "capacity_status": report.capacity_status,
                    "recovery_status": report.recovery_status,
                    "request_count": len(receipts),
                    "measurement_kind": "local_engineering",
                    "real_model_calls": 0,
                    "real_source_calls": 0,
                }
            )
        )
        return 0 if args.unit_smoke else 1 if report.capacity_status == "FAIL" else 2
    except Exception as exc:
        write_json(
            output / "failure.json",
            {
                "status": "FAILED_DURING_MEASUREMENT"
                if (output / "clock.json").exists()
                else "FAILED_BEFORE_WARMUP",
                "error_code": type(exc).__name__,
                "measurement_claim": "none",
            },
        )
        raise
    finally:
        for client in clients.values():
            await client.aclose()
        worker.terminate()
        try:
            await asyncio.to_thread(worker.wait, 15)
        except subprocess.TimeoutExpired:
            worker.kill()
            await asyncio.to_thread(worker.wait)
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "evals/capacity-v1/manifest.json")
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--unit-smoke", action="store_true")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    deny_external_network()
    manifest = load_manifest(args.manifest, args.manifest_sha256)
    if args.serve:
        import uvicorn
        from bidops.operations_acceptance.load import create_load_app

        uvicorn.run(
            create_load_app(args.output, manifest, f"http://127.0.0.1:{args.port}"),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )
        return 0
    if args.unit_smoke:
        value = manifest.model_dump()
        value["profile_id"] = "UNIT-smoke-not-capacity"
        value["data"].update(
            notice_versions=10, procurement_packages=50, materials=2, attachments=1
        )
        value["workload"].update(
            warmup_seconds=50,
            steady_seconds=50,
            rate_bucket_seconds=50,
            min_requests_per_client=10,
            drain_seconds=90,
        )
        value["simulation"]["requested_delay_ms"] = 100
        manifest = CapacityManifest.model_validate(value)
    return asyncio.run(execute(args, manifest))


if __name__ == "__main__":
    raise SystemExit(main())
