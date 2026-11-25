"""Offline, real DBOS review recovery child; never exposes an HTTP approval bypass.

The parent authenticates synthetic RS256 identities through IdentityService and
creates the source grants. Children use those actual persisted browser sessions.
Only authored FunctionModel answers and synthetic token accounting are simulated.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import signal
import socket
import sqlite3
import threading
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
from bidops.review.config import ReviewSettings
from bidops.review.service import ReviewService
from pydantic_ai import models
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from starlette.requests import Request
from test_qualification_service import scripted_response

PRIVATE_REASON = "SYNTHETIC_PRIVATE_REVIEW_REASON_PROCESS_748"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("mode", choices=["crash", "recover", "read-saved"])
    parser.add_argument("--window", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    models.ALLOW_MODEL_REQUESTS = False

    def deny_network(*_args, **_kwargs):
        raise RuntimeError("offline review recovery worker forbids socket I/O")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.socket.sendto = deny_network
    socket.create_connection = deny_network
    directory = args.directory.resolve()
    bootstrap = json.loads((directory / "bootstrap.json").read_text())

    def log(event, **fields):
        value = {"event": event, "pid": os.getpid(), "mode": args.mode, **fields}
        with (directory / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")
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
            log(
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
            live_calls_enabled=True,
            ledger_verified=True,
        ),
        provider,
    )
    # Test-only local model through real reservation/receipt/accounting paths.
    # There is no provider credential, real model request or actual charge.
    screening._model = lambda: (provider, False)

    def fault(window):
        if args.mode != "crash" or window != args.window:
            return
        if window == "review_consumed_before_checkpoint" and not db.rows(
            "SELECT 1 FROM review_outbox o JOIN durable_job_state s ON s.run_id=o.run_id "
            "WHERE o.kind='closed' AND o.consumed_at IS NOT NULL AND s.status='completed'"
        ):
            # This window deliberately kills the explicit CLOSE commit, not an
            # earlier per-item decision that correctly leaves the task waiting.
            return
        log("sigkill", window=window)
        os.kill(os.getpid(), signal.SIGKILL)
        raise AssertionError("SIGKILL unexpectedly returned")

    runtime = JobsService(
        screening, bootstrap["data_dir"], JobsSettings(_env_file=None, enabled=True)
    )
    reviews = ReviewService(
        screening,
        runtime,
        ReviewSettings(_env_file=None, enabled=True),
        fault=fault,
    )
    if args.mode == "crash" and args.window == "review_successor_committed":
        native_consume = reviews.consume

        def hold_old_decision_before_consumption(run_id, event_id):
            kind = db.rows("SELECT kind FROM review_outbox WHERE event_id=:id", {"id": event_id})
            if kind and kind[0]["kind"] == "decision":
                # A deterministic test barrier keeps the old decision pending
                # while the real successor transaction commits. SIGKILL ends
                # this process; recovery has no barrier and uses the real SDK.
                log("held_old_decision", run_id=run_id, event_id=event_id)
                threading.Event().wait(45)
                raise TimeoutError("successor fault did not kill the held consumer")
            return native_consume(run_id, event_id)

        reviews.consume = hold_old_decision_before_consumption

    def human(which):
        request = Request({"type": "http", "session": bootstrap["sessions"][which], "headers": []})
        return reviews.repository.authorization.from_request(request)

    owner, reviewer = human("owner"), human("reviewer")
    actor = ToolContext(owner.owner_id)
    log(
        "starting",
        application_version=runtime.versions["application_version"],
        dbos_version=importlib.metadata.version("dbos"),
        pydantic_ai_version=importlib.metadata.version("pydantic-ai-slim"),
        real_model_calls=0,
        actual_cost_cny="0",
        accounting_kind="synthetic_shared_ledger",
        identity_kind="RS256_verified_persisted_browser_session",
    )

    def await_job(run_id, expected):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            row = runtime.repository.get_system(run_id)
            if row["status"] == expected:
                return runtime.read(actor, run_id)
            if row["status"] in {"failed", "interrupted", "timed_out", "cancelled"}:
                raise RuntimeError(f"review recovery stopped: {row['status']}:{row['error_code']}")
            time.sleep(0.05)
        raise TimeoutError("native review recovery did not reach expected state")

    def successor_recovery(case):
        """A persisted supplement link never carries the old human decision forward."""
        original = next(row for row in case["revisions"] if row["parent_revision_id"] is None)
        if args.mode == "crash":
            item = case["items"][0]
            current = reviews.mutate(
                "decide",
                reviewer,
                case["case_id"],
                revision_id=case["head_revision_id"],
                assessment_id=item["assessment_id"],
                condition_id=item["condition_id"],
                expected_version=case["review_version"],
                command={
                    "decision": "request_materials",
                    "expected_result_revision": original["result_revision"],
                    "reason": PRIVATE_REASON + ":need-new-certificate",
                    "idempotency_key": "request-supplement-once",
                },
            )
            material = company.materials.put(
                actor,
                filename="supplement.txt",
                content_type="text/plain",
                data=b"SYNTHETIC newly supplied certificate for complete recalculation",
            )
            company.auth.grant(actor, material["object_id"], reviewer.owner_id, ["read"])
            request = {
                **bootstrap["request"],
                "additional_material_revisions": [
                    {key: material[key] for key in ("object_id", "version_id", "sha256")}
                ],
            }
            trigger = next(
                event for event in current["events"] if event["event_kind"] == "decision"
            )
            reviews.successor(
                owner,
                case["case_id"],
                request=request,
                expected_version=current["review_version"],
                revision_id=current["head_revision_id"],
                trigger_event_id=trigger["event_id"],
                idempotency_key="one-supplement-successor",
                reason=PRIVATE_REASON + ":supplement-requires-complete-recalculation",
            )
            raise AssertionError("successor commit fault hook did not terminate the child")

        links = db.rows(
            "SELECT * FROM review_successors WHERE case_id=:id", {"id": case["case_id"]}
        )
        if len(links) != 1:
            raise RuntimeError("recovery requires one existing immutable successor link")
        successor_id = links[0]["run_id"]
        await_job(successor_id, "waiting_review")
        await_job(original["run_id"], "cancelled")
        case = reviews.get(owner, case["case_id"])
        if len(case["revisions"]) == 1:
            if args.mode == "read-saved":
                raise RuntimeError("third startup must not attach an uncommitted revision")
            case = reviews.mutate(
                "attach_revision",
                owner,
                case["case_id"],
                run_id=successor_id,
                expected_version=case["review_version"],
                idempotency_key="explicitly-attach-supplement-once",
                reason=PRIVATE_REASON + ":read-new-fixed-result-before-re-review",
            )
        if len(case["revisions"]) != 2 or any(
            item["latest_event"] is not None or item["effective"] for item in case["items"]
        ):
            raise RuntimeError("new supplement revision must start wholly unreviewed")
        # DBOS may still be flushing the child's SUCCESS checkpoint after the
        # business waiting_review commit; capture only the settled native state.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with closing(sqlite3.connect(runtime.path)) as conn:
                native = conn.execute(
                    "SELECT workflow_uuid,status,name,parent_workflow_id "
                    "FROM workflow_status ORDER BY workflow_uuid"
                ).fetchall()
            status = {row[0]: row[1] for row in native}
            if (
                len(native) == 4
                and status.get(original["run_id"]) == "CANCELLED"
                and status.get(successor_id) == "PENDING"
                and status.get(successor_id + "-calculation") == "SUCCESS"
            ):
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("supplement workflows did not settle at the review boundary")
        jobs = [runtime.read(actor, row["run_id"]) for row in case["revisions"]]
        summary = {
            "case_id": case["case_id"],
            "status": case["status"],
            "head_revision_id": case["head_revision_id"],
            "review_version": case["review_version"],
            "revisions": [
                {
                    key: row[key]
                    for key in (
                        "revision_id",
                        "parent_revision_id",
                        "run_id",
                        "result_id",
                        "result_revision",
                        "inputs_manifest_sha256",
                    )
                }
                for row in case["revisions"]
            ],
            "jobs": [
                {
                    key: row[key]
                    for key in ("run_id", "status", "result_id", "result_revision", "usage")
                }
                for row in jobs
            ],
            "native_workflows": native,
            "review_event_ids": [event["event_id"] for event in case["events"]],
            "unreviewed_item_count": len(case["items"]),
            "all_new_items_unreviewed": True,
        }
        (directory / f"{args.mode}.json").write_text(json.dumps(summary, indent=2))
        log("saved_supplement_revision", **summary)

    try:
        runtime.start()
        if args.mode == "crash":
            job = runtime.submit(actor, bootstrap["request"], "review-sigkill-one-task")
            run_id = job["run_id"]
            await_job(run_id, "waiting_review")
            batch = screening.candidates(actor, bootstrap["request"]["profile_revision"], {})
            case = reviews.create(
                owner,
                run_id=run_id,
                candidate_batch_id=batch["batch_id"],
                idempotency_key="review-sigkill-one-case",
                reviewer_ids=[reviewer.owner_id],
            )
            log("created_case", run_id=run_id, case_id=case["case_id"])
        else:
            cases = db.rows("SELECT case_id FROM review_cases")
            jobs = db.rows("SELECT run_id FROM durable_jobs")
            count = 2 if args.window == "review_successor_committed" else 1
            if len(cases) != 1 or len(jobs) != count:
                raise RuntimeError("recovery requires the exact existing case and task count")
            case = reviews.get(owner, cases[0]["case_id"])
            run_id = case["revisions"][0]["run_id"]
            log("recovering_existing", run_id=run_id, case_id=case["case_id"])

        if args.window == "review_successor_committed":
            successor_recovery(case)
            return

        if args.mode != "read-saved":
            # Every item is independently submitted by a real persisted session.
            # No fake HTTP approval, bulk default, or test-fixture review endpoint.
            for item in list(case["items"]):
                if item["latest_event"] is not None:
                    continue
                current = reviews.get(reviewer, case["case_id"])
                revision = next(r for r in current["revisions"] if r["is_head"])
                reviews.mutate(
                    "decide",
                    reviewer,
                    case["case_id"],
                    revision_id=current["head_revision_id"],
                    assessment_id=item["assessment_id"],
                    condition_id=item["condition_id"],
                    expected_version=current["review_version"],
                    command={
                        "decision": "confirm",
                        "expected_result_revision": revision["result_revision"],
                        "reason": PRIVATE_REASON + ":" + item["condition_id"],
                        "idempotency_key": "decision-"
                        + hashlib.sha256(
                            (item["assessment_id"] + ":" + item["condition_id"]).encode()
                        ).hexdigest(),
                    },
                )
            case = reviews.get(owner, case["case_id"])
            if case["status"] != "closed":
                reviews.mutate(
                    "close",
                    owner,
                    case["case_id"],
                    expected_version=case["review_version"],
                    idempotency_key="explicit-close-once",
                    reason=PRIVATE_REASON + ":explicit-owner-close",
                )

        saved = await_job(run_id, "completed")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with closing(sqlite3.connect(runtime.path)) as conn:
                native = conn.execute(
                    "SELECT workflow_uuid,status,name,parent_workflow_id "
                    "FROM workflow_status ORDER BY workflow_uuid"
                ).fetchall()
            if native and all(row[1] == "SUCCESS" for row in native):
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("native root did not checkpoint the explicit close")
        with closing(sqlite3.connect(runtime.path)) as conn:
            checkpoints = conn.execute(
                "SELECT workflow_uuid,function_id,function_name,"
                "output IS NOT NULL,error IS NOT NULL "
                "FROM operation_outputs ORDER BY workflow_uuid,function_id"
            ).fetchall()
        result = db.rows(
            "SELECT body_sha256 FROM qualification_results WHERE result_id=:id",
            {"id": saved["result_id"]},
        )[0]
        summary = {
            "run_id": run_id,
            "case_id": case["case_id"],
            "status": saved["status"],
            "result_id": saved["result_id"],
            "result_revision": saved["result_revision"],
            "result_body_sha256": result["body_sha256"],
            "inputs_manifest_sha256": saved["inputs_manifest_sha256"],
            "usage": saved["usage"],
            "native_workflows": native,
            "checkpoints": checkpoints,
            "review_event_ids": [
                r["event_id"] for r in db.rows("SELECT event_id FROM review_events")
            ],
        }
        (directory / f"{args.mode}.json").write_text(json.dumps(summary, indent=2))
        log("saved_completed_result", **summary)
    finally:
        runtime.close()
        db.engine.dispose()


if __name__ == "__main__":
    main()
