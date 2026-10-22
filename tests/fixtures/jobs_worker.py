"""Actual JobsService/DBOS child for offline SIGKILL recovery acceptance.

The parent creates synthetic source data once. Every child only opens those
existing files; recovery never creates or resubmits a business task. The optional
synthetic ledger mode wraps FunctionModel in the real paid-admission path, with
authored token counts and no provider, credentials, network, or actual charges.
"""

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

from bidops.company import CompanyService
from bidops.company.authorization import ToolContext
from bidops.db import Database
from bidops.jobs.config import JobsSettings
from bidops.jobs.runtime import JobsService
from bidops.notice_data import NoticeRepository
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.service import ScreeningService
from pydantic_ai import models
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy.engine import make_url
from test_qualification_service import scripted_response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("mode", choices=["crash", "recover", "read-saved"])
    parser.add_argument("--window", default="waiting_review")
    parser.add_argument("--synthetic-ledger", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    models.ALLOW_MODEL_REQUESTS = False

    def deny_network(*_args, **_kwargs):
        raise RuntimeError("offline durable worker forbids socket I/O")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.socket.sendto = deny_network
    socket.create_connection = deny_network
    root = args.directory.resolve()
    bootstrap = json.loads((root / "bootstrap.json").read_text())
    database = Path(make_url(bootstrap["database_url"]).database)
    if not database.is_file():
        raise RuntimeError("parent-created business fixture is missing")

    def event(kind, **fields):
        payload = {"event": kind, "pid": os.getpid(), "mode": args.mode, **fields}
        with (root / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    class CountingFunctionModel(FunctionModel):
        def __init__(self):
            super().__init__(self.answer, model_name="deepseek-flash")

        def answer(self, messages, info):
            prompt = next(
                json.loads(part.content)
                for message in messages
                for part in message.parts
                if isinstance(part, UserPromptPart) and isinstance(part.content, str)
            )
            event(
                "local_model_call",
                stage=prompt["stage"],
                prompt_sha256=hashlib.sha256(
                    json.dumps(prompt, sort_keys=True).encode()
                ).hexdigest(),
            )
            return scripted_response(messages, info)

        async def request(self, messages, model_settings, model_request_parameters):
            response = await super().request(messages, model_settings, model_request_parameters)
            response.model_name = "deepseek-flash"
            response.usage = RequestUsage(input_tokens=100, output_tokens=10)
            return response

    db = Database(bootstrap["database_url"])
    company = CompanyService(db, bootstrap["data_dir"])
    provider = CountingFunctionModel()
    screening = ScreeningService(
        company,
        NoticeRepository(db, Path(bootstrap["data_dir"]) / "raw"),
        ScreeningSettings(
            _env_file=None,
            enabled=True,
            max_requests=20,
            max_seconds=120,
            live_calls_enabled=args.synthetic_ledger,
            ledger_verified=args.synthetic_ledger,
        ),
        provider,
    )
    if args.synthetic_ledger:
        # Test-only instance override. The production HTTP/environment surface
        # cannot enable this local model or replace the approved provider path.
        screening._model = lambda: (provider, False)

    def kill(window):
        if args.mode == "crash" and window == args.window:
            event("sigkill", window=window)
            os.kill(os.getpid(), signal.SIGKILL)
            raise AssertionError("SIGKILL unexpectedly returned")

    runtime = JobsService(
        screening,
        bootstrap["data_dir"],
        JobsSettings(_env_file=None, enabled=True),
        fault=kill,
    )
    event(
        "starting",
        application_version=runtime.versions["application_version"],
        dbos_version=importlib.metadata.version("dbos"),
        pydantic_ai_version=importlib.metadata.version("pydantic-ai-slim"),
        real_model_calls=0,
        actual_cost_cny="0",
        accounting_kind="synthetic_shared_ledger" if args.synthetic_ledger else "scorer_fixture",
    )
    try:
        runtime.start()
        actor = ToolContext("alice")
        if args.mode == "crash":
            job = runtime.submit(actor, bootstrap["request"], "sigkill-one-task")
            run_id = job["run_id"]
            event("submitted", run_id=run_id)
        else:
            existing = db.rows("SELECT run_id FROM durable_jobs")
            if len(existing) != 1:
                raise RuntimeError("recovery requires exactly one existing business task")
            run_id = existing[0]["run_id"]
            event("recovering_existing", run_id=run_id)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            row = runtime.repository.get_system(run_id)
            if row["status"] == "waiting_review":
                kill("waiting_review")
                saved = runtime.read(actor, run_id)
                with closing(sqlite3.connect(runtime.path)) as conn:
                    checkpoints = conn.execute(
                        "SELECT workflow_uuid,function_id,function_name,"
                        "output IS NOT NULL,error IS NOT NULL "
                        "FROM operation_outputs ORDER BY workflow_uuid,function_id"
                    ).fetchall()
                result_row = db.rows(
                    "SELECT result_id,body_sha256 FROM qualification_results WHERE result_id=:id",
                    {"id": saved["result_id"]},
                )[0]
                summary = {
                    "run_id": run_id,
                    "status": saved["status"],
                    "result_id": saved["result_id"],
                    "result_revision": saved["result_revision"],
                    "result_body_sha256": result_row["body_sha256"],
                    "inputs_manifest_sha256": saved["inputs_manifest_sha256"],
                    "usage": saved["usage"],
                    "checkpoints": checkpoints,
                }
                (root / f"{args.mode}.json").write_text(json.dumps(summary, indent=2))
                event("saved_result", **summary)
                return
            if row["status"] in {"failed", "interrupted", "timed_out", "cancelled"}:
                raise RuntimeError(f"recovery stopped: {row['status']}:{row['error_code']}")
            time.sleep(0.05)
        raise TimeoutError("native recovery did not reach waiting_review")
    finally:
        runtime.close()
        db.engine.dispose()


if __name__ == "__main__":
    main()
