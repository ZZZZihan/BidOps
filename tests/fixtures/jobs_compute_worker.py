"""Network-denied, real DBOS two-computation recovery worker.

Only the parent creates business fixtures. Recovery opens existing tasks and
never resubmits them. Token usage and ledger amounts are synthetic; actual
provider requests and actual fees are always zero.
"""

import argparse
import asyncio
import hashlib
import json
import os
import signal
import socket
import sqlite3
import time
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock

from bidops.company import CompanyService
from bidops.company.authorization import ToolContext
from bidops.db import Database
from bidops.jobs.config import JobsSettings
from bidops.jobs.model import ACTIVE_MODEL
from bidops.jobs.runtime import CALCULATION_QUEUE, JobsService
from bidops.notice_data import NoticeRepository
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.service import ScreeningService
from dbos import DBOS
from pydantic_ai import models
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy.engine import make_url
from test_qualification_service import scripted_response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("mode", choices=["crash", "recover", "read-saved", "budget"])
    args = parser.parse_args()
    os.umask(0o077)
    models.ALLOW_MODEL_REQUESTS = False

    def deny_network(*_args, **_kwargs):
        raise RuntimeError("offline compute recovery forbids socket I/O")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.socket.sendto = deny_network
    socket.create_connection = deny_network
    directory = args.directory.resolve()
    seed = json.loads((directory / "bootstrap.json").read_text())
    if not Path(make_url(seed["database_url"]).database).is_file():
        raise RuntimeError("parent-created business fixture is missing")

    def event(kind, **fields):
        value = {"event": kind, "pid": os.getpid(), "mode": args.mode, **fields}
        with (directory / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    release, lock = Event(), Lock()
    calls, active, peaks = Counter(), set(), []

    async def answer(messages, info):
        guard = ACTIVE_MODEL.get()
        run_id = guard.run_id
        assert DBOS.workflow_id == run_id + "-calculation"
        with lock:
            calls[run_id] += 1
            first = calls[run_id] == 1
            active.add(run_id)
            peaks.append(len(active))
            event("local_model_call", run_id=run_id, active=len(active))
        try:
            if first:
                while not release.is_set():
                    await asyncio.sleep(0.01)
            # The actual await above lets other native calculations change
            # context. Every continuation must retain its own guard/workflow.
            assert ACTIVE_MODEL.get() is guard
            assert DBOS.workflow_id == run_id + "-calculation"
            if args.mode == "read-saved":
                raise AssertionError("saved reads must not invoke a local model")
            if args.mode == "budget":
                raise RuntimeError("synthetic provider returned no response")

            def mark(prompt, output):
                if prompt["stage"] == "assessment":
                    for judgment in output["judgments"]:
                        judgment["reason"] = "isolated:" + run_id

            response = scripted_response(messages, info, mutation=mark)
            response.model_name = "deepseek-flash"
            response.usage = RequestUsage(input_tokens=100, output_tokens=10)
            return response
        finally:
            with lock:
                active.remove(run_id)
                event("local_model_return", run_id=run_id, active=len(active))

    provider = FunctionModel(answer, model_name="deepseek-flash")
    db = Database(seed["database_url"])
    company = CompanyService(db, seed["data_dir"])
    config = ScreeningSettings(
        _env_file=None,
        enabled=True,
        max_requests=20,
        max_seconds=120,
        live_calls_enabled=True,
        ledger_verified=True,
    )
    reservation = (
        (2 * config.max_request_bytes + 32768) * config.input_cny_per_million
        + config.max_output_tokens * config.output_cny_per_million
    ) / 1_000_000
    if args.mode == "budget":
        config.budget_cny = reservation
    screening = ScreeningService(
        company,
        NoticeRepository(db, Path(seed["data_dir"]) / "raw"),
        config,
        provider,
    )
    # Test-only instance injection exercises the real reservation path without
    # enabling a provider, credentials, HTTP endpoint, or environment override.
    screening._model = lambda: (provider, False)
    runtime = JobsService(
        screening,
        seed["data_dir"],
        JobsSettings(_env_file=None, enabled=True, compute_concurrency=2),
    )
    event(
        "starting",
        application_version=runtime.versions["application_version"],
        real_model_calls=0,
        actual_cost_cny="0",
        accounting_kind="synthetic_shared_ledger",
    )

    def native_rows():
        with closing(sqlite3.connect(runtime.path)) as conn:
            return conn.execute(
                "SELECT workflow_uuid,status,queue_name,parent_workflow_id "
                "FROM workflow_status ORDER BY workflow_uuid"
            ).fetchall()

    try:
        runtime.start()
        actor = ToolContext("alice")
        if args.mode in {"crash", "budget"}:
            count = 2 if args.mode == "budget" else 3
            ids = [
                runtime.submit(actor, seed["request"], f"compute-{index}")["run_id"]
                for index in range(count)
            ]
            (directory / "run-ids.json").write_text(json.dumps(ids))
        else:
            ids = json.loads((directory / "run-ids.json").read_text())
            assert {r["run_id"] for r in db.rows("SELECT run_id FROM durable_jobs")} == set(ids)

        deadline = time.monotonic() + 45
        observed_queued = False
        retried_unknown = False
        while time.monotonic() < deadline:
            rows = [runtime.repository.get_system(run_id) for run_id in ids]
            with lock:
                entered = set(calls)
            if args.mode == "budget":
                if len(entered) == 1 and any(row["status"] == "failed" for row in rows):
                    # The losing native calculation has exhausted the budget
                    # while the winning reservation is still in-flight.
                    event("competing_reservation_rejected", active_calls=len(entered))
                    release.set()
                if all(row["status"] == "failed" for row in rows):
                    if not retried_unknown:
                        # The first provider has now failed without a receipt.
                        # A later task must still count that unknown reserve.
                        ids.append(
                            runtime.submit(actor, seed["request"], "after-unknown")["run_id"]
                        )
                        retried_unknown = True
                        continue
                    summary = {
                        "ids": ids,
                        "statuses": [row["status"] for row in rows],
                        "error_codes": [row["error_code"] for row in rows],
                        "calls": dict(calls),
                        "reservation_cny": str(reservation),
                        "peak_active": max(peaks, default=0),
                        "native": native_rows(),
                    }
                    (directory / "budget.json").write_text(json.dumps(summary, indent=2))
                    return
            elif args.mode != "read-saved" and not release.is_set() and len(entered) == 2:
                native = native_rows()
                children = [r for r in native if r[0].endswith("-calculation")]
                if len(children) == 3 and Counter(r[1] for r in children) == {
                    "PENDING": 2,
                    "ENQUEUED": 1,
                }:
                    assert all(row[2] == CALCULATION_QUEUE for row in children)
                    assert all(row[3] == row[0].removesuffix("-calculation") for row in children)
                    observed_queued = True
                    event("bounded_queue_observed", children=children)
                    if args.mode == "crash":
                        event("sigkill", started_run_ids=sorted(entered), native=native)
                        os.kill(os.getpid(), signal.SIGKILL)
                        raise AssertionError("SIGKILL unexpectedly returned")
                    release.set()

            if all(row["status"] == "waiting_review" for row in rows):
                saved = [runtime.read(actor, run_id) for run_id in ids]
                result_hashes = {
                    r["result_id"]: r["body_sha256"]
                    for r in db.rows("SELECT result_id,body_sha256 FROM qualification_results")
                }
                summary = {
                    "application_version": runtime.versions["application_version"],
                    "jobs": [
                        {
                            "run_id": row["run_id"],
                            "status": row["status"],
                            "result_id": row["result_id"],
                            "result_revision": row["result_revision"],
                            "result_body_sha256": result_hashes[row["result_id"]],
                            "inputs_manifest_sha256": row["inputs_manifest_sha256"],
                            "usage": row["usage"],
                        }
                        for row in saved
                    ],
                    "calls": dict(calls),
                    "peak_active": max(peaks, default=0),
                    "queued_child_observed": observed_queued,
                    "native": native_rows(),
                    "ledger_total_cny": str(
                        sum(
                            (
                                Decimal(str(row["estimated_cny"]))
                                if row["state"] == "completed"
                                else Decimal(str(row["reserved_cny"]))
                                for row in db.rows("SELECT * FROM agent_runs")
                            ),
                            Decimal(0),
                        )
                    ),
                }
                encoded = json.dumps(summary, indent=2)
                (directory / f"{args.mode}.json").write_text(encoded)
                event("saved_results", summary_sha256=hashlib.sha256(encoded.encode()).hexdigest())
                return
            if args.mode != "budget" and any(
                row["status"] in {"failed", "interrupted", "timed_out", "cancelled"} for row in rows
            ):
                raise RuntimeError(
                    "native recovery stopped: "
                    + repr([(row["status"], row["error_code"]) for row in rows])
                )
            time.sleep(0.02)
        raise TimeoutError("native compute worker did not reach the expected state")
    finally:
        release.set()
        runtime.close()
        db.engine.dispose()


if __name__ == "__main__":
    main()
