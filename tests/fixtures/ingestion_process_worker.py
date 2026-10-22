"""Actual DBOS acquisition recovery child; synthetic bytes, no real I/O/provider."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import signal
import socket
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import httpx
from bidops.company import CompanyService
from bidops.company.authorization import ToolContext
from bidops.config import Settings
from bidops.ingestion.config import IngestionSettings
from bidops.ingestion.service import IngestionService
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import JobsService
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.service import ScreeningService
from bidops.storage import Store
from dbos import DBOS
from pydantic_ai import models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("mode", choices=["crash", "recover", "read-saved"])
    parser.add_argument("--window", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    models.ALLOW_MODEL_REQUESTS = False

    def deny(*_args, **_kwargs):
        raise RuntimeError("offline ingestion process forbids network")

    socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = deny
    socket.create_connection = deny
    directory = args.directory.resolve()
    bootstrap = json.loads((directory / "bootstrap.json").read_text())

    def event(kind, **fields):
        with (directory / "events.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {"event": kind, "pid": os.getpid(), "mode": args.mode, **fields}, sort_keys=True
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())

    scheduled = args.window == "after_schedule_run_commit"
    settings = Settings(
        data_dir=bootstrap["data_dir"],
        database_url=bootstrap["database_url"],
        source_registry=bootstrap["source_registry"],
        network_enabled=scheduled,
    )
    store = Store(settings)
    company = CompanyService(store.db, settings.data_dir)
    screening = ScreeningService(
        company,
        store.notice_data,
        ScreeningSettings(enabled=False, live_calls_enabled=False, _env_file=None),
    )
    jobs = JobsService(screening, settings.data_dir, JobsSettings(enabled=False, _env_file=None))

    def fault(window):
        if args.mode == "crash" and window == args.window:
            event("sigkill", window=window)
            os.kill(os.getpid(), signal.SIGKILL)
            raise AssertionError("SIGKILL unexpectedly returned")

    def response(request):
        event("mock_http_request", url_sha256=hashlib.sha256(str(request.url).encode()).hexdigest())
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            content=bootstrap["http_body"].encode(),
        )

    client = httpx.Client(transport=httpx.MockTransport(response)) if scheduled else None
    service = IngestionService(
        store,
        company,
        IngestionSettings(enabled=True, _env_file=None),
        jobs,
        fault=fault,
        client=client,
    )
    prepare = store.prepare

    def counted_prepare(*values, **kwargs):
        prepared = prepare(*values, **kwargs)
        event("prepare_notice", raw_sha256=prepared.snapshot.sha256)
        return prepared

    store.prepare = counted_prepare
    event(
        "starting",
        application_version=jobs.versions["application_version"],
        dbos_version=importlib.metadata.version("dbos"),
        real_http_requests=0,
        real_model_calls=0,
        actual_cost_cny="0",
    )
    actor = ToolContext(bootstrap["owner_id"])
    try:
        jobs.start()
        if args.mode == "crash":
            if scheduled:
                service.schedule(actor, bootstrap["schedule"])
                generation = store.db.rows("SELECT native_name FROM ingestion_schedules")[0][
                    "native_name"
                ]
                handle = DBOS.trigger_schedule(generation)
                event("triggered_native_schedule", native_id=handle.get_workflow_id())
                handle.get_result()
                raise AssertionError("schedule fault hook did not terminate child")
            service.import_manual(actor, bootstrap["request"])
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            runs = store.db.rows("SELECT run_id FROM ingestion_runs")
            if len(runs) == 1:
                record = service.repository.get_system(runs[0]["run_id"])
                if record["status"] == "completed":
                    with closing(sqlite3.connect(jobs.path)) as conn:
                        workflows = conn.execute(
                            "SELECT workflow_uuid,status,name,application_version "
                            "FROM workflow_status ORDER BY workflow_uuid"
                        ).fetchall()
                        checkpoints = conn.execute(
                            "SELECT workflow_uuid,function_id,function_name,"
                            "output IS NOT NULL,error IS NOT NULL FROM operation_outputs "
                            "ORDER BY workflow_uuid,function_id"
                        ).fetchall()
                    if all(row[1] == "SUCCESS" for row in workflows):
                        summary = {
                            "run": service.read(actor, record["run_id"]),
                            "workflows": workflows,
                            "checkpoints": checkpoints,
                        }
                        (directory / f"{args.mode}.json").write_text(json.dumps(summary, indent=2))
                        event("saved", run_id=record["run_id"], status=record["status"])
                        return
                if record["status"] in {"failed", "partial", "cancelled", "interrupted"}:
                    raise RuntimeError(
                        f"ingestion recovery failed: {record['status']}:{record['error_code']}"
                    )
            time.sleep(0.025)
        raise TimeoutError("native ingestion did not complete")
    finally:
        jobs.close()
        if client:
            client.close()
        store.db.engine.dispose()


if __name__ == "__main__":
    main()
