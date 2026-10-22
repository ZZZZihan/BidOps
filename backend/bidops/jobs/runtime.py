"""Single-process DBOS integration for fixed qualification tasks.

The framework owns queuing, model checkpoints, child workflow recovery and
review waits. Business state and usage receipts remain in the business database.
Only run IDs enter workflow arguments; private step outputs stay in the private
system database and follow source authorization and retention.
"""

import asyncio
import hashlib
import os
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

from dbos import DBOS, DBOSClient, SetWorkflowID
from dbos import error as dbos_error
from pydantic_ai.durable_exec.dbos import DBOSDurability

from bidops.company.authorization import CompanyError, ToolContext, transaction
from bidops.jobs.config import JobsSettings
from bidops.jobs.model import ACTIVE_MODEL, DurableModel, JobModelProxy, summarize_attempts
from bidops.jobs.repository import JobsRepository
from bidops.qualification.api import ChecklistRequest
from bidops.qualification.runtime import assessment_agent, extraction_agent
from bidops.qualification.service import implementation_version
from bidops.qualification.sources import ScreeningInputManifest, digest
from bidops.screening.schemas import ReviewEvent

ACTIVE = {"queued", "running", "waiting_review", "interrupted", "cancel_requested"}
QUEUE = "bidops_qualification_jobs_v1"


def application_version():
    root = Path(__file__).resolve().parents[3]
    files = sorted(Path(__file__).resolve().parents[1].rglob("*.py"))
    source = digest(
        {
            str(p.relative_to(Path(__file__).resolve().parents[1])): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in files
        }
    )
    lock = Path(os.environ.get("BIDOPS_DURABLE_LOCK_PATH", str(root / "requirements/durable.lock")))
    overlay = hashlib.sha256(lock.read_bytes()).hexdigest()
    base = implementation_version()
    if not base["lock_sha256"]:
        raise CompanyError("job_dependency_lock_missing", 503)
    return {
        **base,
        "durable_lock_sha256": overlay,
        "job_implementation_sha256": source,
        "application_version": digest([source, base["lock_sha256"], overlay]),
    }


class Execution:
    def __init__(self, runtime, record, context, bundle, deadline):
        self.runtime, self.record = runtime, record
        self.context, self.bundle, self.deadline = context, bundle, deadline
        self.result_id, self.revision = (
            record["versions"]["result_id"],
            record["versions"]["result_revision"],
        )
        self.guard = runtime.guard(record, context, bundle, self.recheck)
        self.agents = runtime.agents

    def recheck(self):
        self.runtime.require_worker(self.record["run_id"])

    async def before_step(self, phase, completed, total):
        self.recheck()
        await self.runtime.progress_step(self.record["run_id"], phase, completed, total)

    async def persist(self, result, provenance, raw):
        self.recheck()
        return await self.runtime.result_step(
            self.record["run_id"],
            result,
            {
                **provenance,
                "business_run_id": self.record["run_id"],
                "durable_application_version": self.runtime.versions["application_version"],
            },
            raw,
        )


class JobsService:
    def __init__(self, screening, data_dir, settings=None, *, fault=None):
        self.screening, self.settings = screening, settings or JobsSettings()
        self.repository = JobsRepository(screening.repository.db, screening.sources)
        self.path = Path(data_dir) / "private/jobs/dbos.sqlite3"
        self.versions = application_version()
        self.fault, self.started = fault, False
        self.slots = asyncio.Semaphore(1)
        self.agents = None

    def status(self):
        return {
            **self.screening.status(),
            "enabled": self.settings.enabled,
            "configured": self.started and self.screening.status()["configured"],
            "backend": "dbos",
        }

    def enabled(self, *, worker=False):
        if not self.settings.enabled:
            raise CompanyError("jobs_disabled", 503)
        self.screening._enabled()
        if worker and not self.started:
            raise CompanyError("job_backend_unavailable", 503)

    def fixed_settings(self):
        return self.screening.settings.model_dump(
            mode="json",
            exclude={
                "api_key",
                "ledger_restore_token",
                "ledger_verified",
                "live_calls_enabled",
                "enabled",
            },
        )

    def require_execution(self, run_id):
        record = self.repository.require_execution(run_id)
        if record["versions"]["application_version"] != self.versions["application_version"]:
            raise CompanyError("job_application_version_changed", 409)
        if record["settings"] != self.fixed_settings():
            raise CompanyError("job_settings_changed", 409)
        context = ToolContext(record["owner_id"])
        changes = self.screening.sources.changes(context, record["manifest"])
        if set(changes["stale_reasons"]) - {"public_sources_stale"}:
            raise CompanyError("job_inputs_changed", 409)
        return record

    def guard(self, record, context, bundle, check):
        model, offline = self.screening._model()
        return DurableModel(
            model,
            db=self.repository.db,
            settings=self.screening.settings,
            adapter=self.screening.sources,
            context=context,
            bundle=bundle,
            run_id=record["run_id"],
            offline=offline,
            jobs=self.repository,
            execution_check=check,
            fault=self.fault,
        )

    def _interrupt(self, run_id, code):
        with suppress(CompanyError):
            row = self.repository.get_system(run_id)
            if row["status"] == "cancel_requested":
                self.repository.transition(run_id, row["state_version"], "cancelled")
            elif row["status"] in ACTIVE and not row["quarantined"]:
                self.repository.transition(run_id, row["state_version"], "interrupted", error=code)

    def stop_worker(self, run_id, code):
        # An ordinary exception would become a permanent failed step outcome.
        # Use the SDK's cancellation path so an authorized retry can replay
        # completed checkpoints without repeating model calls or that failure.
        # A separate client avoids adding a new step to this workflow's sequence.
        client = DBOSClient(
            system_database_url=f"sqlite:///{self.path}",
            application_name="bidops-jobs",
            retry_connection_errors=False,
        )
        try:
            client.cancel_workflow(run_id, cancel_children=True)
        finally:
            client.destroy()
        self._interrupt(run_id, code)
        raise dbos_error.DBOSWorkflowCancelledError("job_execution_interrupted") from None

    def require_worker(self, run_id):
        try:
            return self.require_execution(run_id)
        except CompanyError as exc:
            self.stop_worker(run_id, exc.code)

    def resumable(self, run_id):
        record = self.require_execution(run_id)
        parent = DBOS.get_workflow_status(run_id)
        child = DBOS.get_workflow_status(run_id + "-calculation")
        if parent is None:
            # The business INSERT can commit before the first native enqueue.
            # Recreate only that empty boundary, never missing executed history.
            if (
                record["result_id"]
                or record["progress"]
                or child
                or self.repository.list_attempts_system(run_id)
            ):
                raise CompanyError("job_checkpoint_not_resumable", 409)
            return None
        if parent.status in {"SUCCESS", "ERROR"} or (child is not None and child.status == "ERROR"):
            # The locked SDK does not resume terminal success/error. A new
            # task is required rather than reporting a no-op as continuation.
            raise CompanyError("job_checkpoint_not_resumable", 409)
        return child

    def start(self):
        if not self.settings.enabled:
            return
        if not self.screening.status()["configured"]:
            # Saved history remains readable when the provider or live-budget
            # gate is unavailable; no worker or replacement model is started.
            return
        if self.path.is_symlink() or any(p.is_symlink() for p in self.path.parents):
            raise CompanyError("job_private_path_invalid", 503)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        # A newly created SQLite file is private before DBOS opens it.
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.path.chmod(0o600)
        import sqlite3

        with sqlite3.connect(self.path) as conn:
            initialized = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_status'"
                ).fetchone()
            )
        if initialized:
            client = DBOSClient(
                system_database_url=f"sqlite:///{self.path}",
                application_name="bidops-jobs",
                retry_connection_errors=False,
            )
            try:
                self.prune(client=client)
                for record in self.repository.list_system(statuses=ACTIVE):
                    try:
                        self.require_execution(record["run_id"])
                        native_rows = client.list_workflows(
                            workflow_ids=[record["run_id"]], load_input=False, load_output=False
                        )
                        native = native_rows[0] if native_rows else None
                        if (
                            native
                            and native.status in {"CANCELLED", "ERROR", "SUCCESS"}
                            and record["status"] in {"running", "waiting_review"}
                        ):
                            self._interrupt(record["run_id"], "job_native_resume_required")
                    except CompanyError as exc:
                        client.cancel_workflow(record["run_id"], cancel_children=True)
                        self._interrupt(record["run_id"], exc.code)
            finally:
                client.destroy()
        DBOS(
            config={
                "name": "bidops-jobs",
                "application_version": self.versions["application_version"],
                "executor_id": "bidops-single-worker",
                "system_database_url": f"sqlite:///{self.path}",
                "enable_otlp": False,
                "run_admin_server": False,
                "log_level": "ERROR",
            }
        )
        model, _ = self.screening._model()
        proxy = JobModelProxy(model)
        self.agents = (
            extraction_agent(
                proxy,
                self.screening.settings,
                name="bidops_job_extraction_v1",
                capabilities=[DBOSDurability(parallel_execution_mode="sequential")],
            ),
            assessment_agent(
                proxy,
                self.screening.settings,
                name="bidops_job_assessment_v1",
                capabilities=[DBOSDurability(parallel_execution_mode="sequential")],
            ),
        )

        def checked_call(function):
            @wraps(function)
            async def checked(run_id, *args, **kwargs):
                try:
                    return await function(run_id, *args, **kwargs)
                except CompanyError as exc:
                    self.stop_worker(run_id, exc.code)

            return checked

        @DBOS.step(name="bidops_job_started_v1")
        @checked_call
        async def started(run_id):
            record = self.require_execution(run_id)
            if record["status"] in {"queued", "interrupted"}:
                self.repository.transition(run_id, record["state_version"], "running")
            return time.time() + self.screening.settings.max_seconds

        @DBOS.step(name="bidops_job_progress_v1")
        @checked_call
        async def progress(run_id, phase, completed, total):
            record = self.require_execution(run_id)
            self.repository.transition(
                run_id,
                record["state_version"],
                "running",
                progress={"phase": phase, "completed": completed, "total": total},
            )

        self.progress_step = progress

        @DBOS.step(name="bidops_job_result_v1")
        @checked_call
        async def result_step(run_id, result, provenance, raw):
            record = self.require_execution(run_id)
            context = ToolContext(record["owner_id"])
            result_id = record["versions"]["result_id"]
            if not self.repository.db.rows(
                "SELECT 1 FROM qualification_results WHERE result_id=:id", {"id": result_id}
            ):
                if self.fault:
                    self.fault("before_result_commit")

                def authorize(conn):
                    live = self.repository._authorize(context, run_id, conn)
                    if live["status"] != "running":
                        raise CompanyError("job_not_executable", 409)

                self.screening.repository.save(
                    context,
                    record["manifest"],
                    result,
                    raw_model_outputs=raw,
                    provenance=provenance,
                    result_id=result_id,
                    authorize_extra=authorize,
                )
                if self.fault:
                    self.fault("after_result_commit")
            saved = self.screening.repository.get(context, result_id)
            if (
                saved["manifest"]["closure_sha256"] != record["inputs_manifest_sha256"]
                or saved["result"]["result_revision"] != record["versions"]["result_revision"]
            ):
                raise CompanyError("job_result_mismatch", 409)
            return {
                "result_id": result_id,
                "result_revision": saved["result"]["result_revision"],
                "state": saved["result"]["state"],
                "error_code": saved["result"]["error_code"],
            }

        self.result_step = result_step

        @DBOS.workflow(name="bidops_qualification_calculation_v1")
        @checked_call
        async def calculation(run_id):
            async with self.slots:
                record = self.require_execution(run_id)
                deadline = await started(run_id)
                context = ToolContext(record["owner_id"])
                manifest = ScreeningInputManifest.model_validate(record["manifest"])
                bundle = self.screening.sources.resolve(
                    context,
                    manifest.profile_revision,
                    manifest.project_id,
                    manifest.as_of,
                    manifest.material_revisions,
                )
                execution = Execution(self, record, context, bundle, deadline)
                execution.guard.deadline = deadline
                token = ACTIVE_MODEL.set(execution.guard)
                try:
                    targets = (
                        record["request"].get("target_package_ids")
                        or manifest.target_package_ids
                        or [None]
                    )
                    model, offline = self.screening._model()
                    return await self.screening._check(
                        context, bundle, targets, model, offline, execution=execution
                    )
                finally:
                    ACTIVE_MODEL.reset(token)

        @DBOS.step(name="bidops_job_waiting_v1")
        @checked_call
        async def waiting(run_id, result):
            record = self.require_execution(run_id)
            status = "waiting_review" if result["state"] == "completed" else result["state"]
            self.repository.transition(
                run_id,
                record["state_version"],
                status,
                result_id=result["result_id"],
                result_revision=result["result_revision"],
                error=result["error_code"],
                progress={"phase": status, "completed": 1, "total": 1},
            )
            return status

        @DBOS.step(name="bidops_job_review_fixture_v1")
        @checked_call
        async def review_step(run_id, event_id):
            record = self.require_execution(run_id)
            rows = self.repository.db.rows(
                "SELECT b.event_json,e.event_sha256 FROM durable_review_fixtures e "
                "JOIN durable_review_fixture_bodies b "
                "ON b.event_id=e.event_id WHERE e.run_id=:id AND e.event_id=:event",
                {"id": run_id, "event": event_id},
            )
            if not rows:
                raise CompanyError("job_review_not_found", 409)
            if (
                hashlib.sha256(rows[0]["event_json"].encode()).hexdigest()
                != rows[0]["event_sha256"]
            ):
                raise CompanyError("job_review_integrity_failed", 409)
            event = ReviewEvent.model_validate_json(rows[0]["event_json"])
            if event.origin != "test_fixture" or event.result_revision != record["result_revision"]:
                raise CompanyError("job_review_stale", 409)
            if event.command.decision == "request_materials":
                return "waiting_review"
            self.repository.transition(
                run_id,
                record["state_version"],
                "completed",
                progress={"phase": "fixture_review_completed", "completed": 1, "total": 1},
            )
            return "completed"

        @DBOS.workflow(name="bidops_qualification_job_v1")
        async def workflow(run_id):
            try:
                self.require_execution(run_id)
                with SetWorkflowID(run_id + "-calculation"):
                    child = await DBOS.start_workflow_async(calculation, run_id)
                result = await child.get_result()
                self.require_execution(run_id)
                status = await waiting(run_id, result)
                if status != "waiting_review":
                    return {"run_id": run_id, "status": status}
                while True:
                    self.require_execution(run_id)
                    event_id = await DBOS.recv_async(topic="review", timeout_seconds=3600)
                    self.require_execution(run_id)
                    if event_id and await review_step(run_id, event_id) == "completed":
                        return {"run_id": run_id, "status": "completed"}
            except CompanyError as exc:
                self.stop_worker(run_id, exc.code)
            except Exception:
                self.stop_worker(run_id, "job_backend_failed")

        self.workflow = workflow
        DBOS.launch()
        DBOS.register_queue(
            QUEUE,
            worker_concurrency=self.settings.max_active_jobs,
            global_concurrency=self.settings.max_active_jobs,
            polling_interval_sec=0.1,
        )
        self.started = True
        for record in self.repository.list_system(statuses={"queued"}):
            with suppress(CompanyError):
                self.require_execution(record["run_id"])
                self.enqueue(record["run_id"])
        # Persisted review fixtures form an outbox: a crash between the business
        # transaction and DBOS.send is retried using the native message key.
        for row in self.repository.db.rows(
            "SELECT e.run_id,e.event_id FROM durable_review_fixtures e "
            "JOIN durable_job_state s ON s.run_id=e.run_id "
            "WHERE s.status='waiting_review' AND s.quarantined=0 AND s.purge_state='live'"
        ):
            with suppress(CompanyError):
                self.require_execution(row["run_id"])
                DBOS.send(
                    row["run_id"], row["event_id"], topic="review", idempotency_key=row["event_id"]
                )

    def enqueue(self, run_id):
        with SetWorkflowID(run_id):
            DBOS.enqueue_workflow(QUEUE, self.workflow, run_id)

    def submit(self, context, request, idempotency_key):
        self.enabled(worker=True)
        payload = ChecklistRequest.model_validate(request)

        def prepare():
            model, offline = self.screening._model()
            bundle = self.screening.sources.resolve(
                context,
                payload.profile_revision,
                payload.project_id,
                payload.as_of or datetime.now(UTC),
                payload.additional_material_revisions,
            )
            if len(set(payload.target_package_ids)) != len(payload.target_package_ids) or not set(
                payload.target_package_ids
            ) <= set(bundle.manifest.target_package_ids):
                raise CompanyError("screening_package_outside_closure", 422)
            return {
                "manifest": bundle.manifest.model_dump(mode="json"),
                "settings": self.fixed_settings(),
                "versions": {
                    **self.versions,
                    "result_id": uuid.uuid4().hex,
                    "result_revision": uuid.uuid4().hex,
                    "offline": offline,
                    "requested_model": model.model_name,
                },
            }

        record = self.repository.get_or_create(
            context,
            idempotency_key=idempotency_key,
            request=request,
            prepare=prepare,
            max_active_jobs=self.settings.max_active_jobs,
        )
        if record["status"] == "queued":
            self.enqueue(record["run_id"])
        return {**self.read(context, record["run_id"]), "created": record["created"]}

    def read(self, context, run_id):
        self.enabled()
        record = self.repository.get(context, run_id)
        result = self.screening.read(context, record["result_id"]) if record["result_id"] else None
        usage = summarize_attempts(
            self.repository,
            run_id,
            self.screening.settings,
            record["versions"]["requested_model"],
            record["versions"]["offline"],
        )
        actions = (
            ["cancel"]
            if self.started
            and record["status"]
            in {"queued", "running", "waiting_review", "interrupted", "cancel_requested"}
            else []
        )
        if self.started and record["status"] == "interrupted":
            try:
                self.resumable(run_id)
                actions.append("resume")
            except CompanyError:
                pass
        # The final check covers every assembled private field after reads above.
        with transaction(self.repository.db) as conn:
            self.repository._authorize(context, run_id, conn)
        return {
            k: v
            for k, v in {
                **record,
                "usage": usage,
                "result": result,
                "allowed_actions": actions,
            }.items()
            if k not in {"settings", "request", "idempotency_key", "owner_id"}
        }

    def list(self, context, offset=0, limit=20):
        self.enabled()
        page = self.repository.list(context, offset=offset, limit=limit)
        items = [self.read(context, r["run_id"]) for r in page["items"]]
        with transaction(self.repository.db) as conn:
            self.repository.auth.require_subject(context, conn)
            for record in items:
                self.repository._authorize(context, record["run_id"], conn)
        return {
            **page,
            "items": [
                {k: v for k, v in r.items() if k not in {"manifest", "result", "versions"}}
                for r in items
            ],
        }

    def cancel(self, context, run_id, expected_version):
        self.enabled(worker=True)
        self.repository.request_cancel(context, run_id, expected_version)
        try:
            DBOS.cancel_workflow(run_id, cancel_children=True)
        except Exception:
            # The durable request already blocks every later execution guard.
            # Keep a retry action until the backend confirms cancellation.
            raise CompanyError("job_backend_unavailable", 503) from None
        record = self.repository.get_system(run_id)
        if record["status"] == "cancel_requested":
            self.repository.transition(run_id, record["state_version"], "cancelled")
        return self.read(context, run_id)

    def resume(self, context, run_id, expected_version):
        self.enabled(worker=True)
        record = self.repository.get(context, run_id)
        if record["status"] != "interrupted":
            raise CompanyError("job_resume_forbidden", 409)
        child = self.resumable(run_id)
        # The successful waiting step is cached by DBOS. Restore its business
        # state here rather than expecting its side effect to run a second time.
        status = (
            "waiting_review"
            if record["result_id"] and child and child.status == "SUCCESS"
            else "running"
        )
        self.repository.transition(run_id, expected_version, status, _context=context)
        try:
            if child and child.status != "SUCCESS":
                DBOS.resume_workflow(run_id + "-calculation")
            if DBOS.get_workflow_status(run_id) is None:
                self.enqueue(run_id)
            else:
                DBOS.resume_workflow(run_id)
        except Exception:
            self._interrupt(run_id, "job_backend_unavailable")
            raise CompanyError("job_backend_unavailable", 503) from None
        return self.read(context, run_id)

    def review_fixture(self, run_id, event, expected_version):
        """Engineering-only caller; never routed to HTTP or model tools."""
        self.require_execution(run_id)
        result = self.repository.record_review_fixture(run_id, event, expected_version)
        if not result["created"]:
            raise CompanyError("job_review_duplicate", 409)
        DBOS.send(run_id, result["event_id"], topic="review", idempotency_key=result["event_id"])
        return result

    def prune(self, client=None):
        self.repository.prune()
        for row in self.repository.db.rows(
            "SELECT run_id FROM durable_job_state WHERE purge_state='purged'"
        ):
            backend = client or DBOS
            backend.cancel_workflow(row["run_id"])
            backend.delete_workflow(row["run_id"], delete_children=True)

    def close(self):
        if self.started:
            DBOS.destroy(destroy_registry=True)
            self.started = False
