"""Fixed source workflows on the application's existing DBOS singleton."""

from contextlib import suppress

from dbos import DBOS, DBOSClient

from bidops.company.authorization import CompanyError
from bidops.fetcher import FetchFailure
from bidops.ingestion.repository import ACTIVE, TERMINAL
from bidops.ingestion.schedules import Schedules

QUEUE = "bidops_ingestion_v1"
SCHEDULE_QUEUE = "bidops_ingestion_schedule_v1"
WORKFLOW = "bidops_ingestion_run_v1"
SCHEDULE_WORKFLOW = "bidops_ingestion_schedule_v1"


class IngestionRuntime:
    def __init__(self, service):
        self.service, self.repository = service, service.repository
        self.schedules = Schedules(service)

    def client(self):
        return DBOSClient(
            system_database_url=f"sqlite:///{self.service.jobs.path}",
            application_name="bidops-jobs",
            retry_connection_errors=False,
        )

    def _stop(self, run_id, code, *, cancelled=False):
        record = self.repository.get_system(run_id)
        if record["status"] in TERMINAL:
            return
        if cancelled or record["status"] == "cancel_requested":
            self.repository.finish(run_id, "cancelled")
        else:
            self.repository.record_failure(run_id, code)
            self.repository.finish(run_id, "failed", code)

    def before_launch(self, client):
        # No scheduler or recovered workflow runs before its current guards are checked.
        for generation in self.repository.db.rows(
            "SELECT native_name FROM ingestion_schedule_versions"
        ):
            if client.get_schedule(generation["native_name"]):
                client.pause_schedule(generation["native_name"])
        for row in self.repository.db.rows(
            "SELECT r.run_id,r.native_id,s.status,s.quarantined FROM ingestion_runs r "
            "JOIN ingestion_run_state s ON s.run_id=r.run_id"
        ):
            if row["status"] not in ACTIVE:
                continue
            try:
                self.service.enabled()
                self.repository.require_execution(row["run_id"])
            except CompanyError as exc:
                client.cancel_workflow(row["native_id"], cancel_children=True)
                if not row["quarantined"]:
                    self._stop(row["run_id"], exc.code)
            else:
                native = client.list_workflows(
                    workflow_ids=[row["native_id"]], load_input=False, load_output=False
                )
                if native and native[0].status in {"ERROR", "CANCELLED", "SUCCESS"}:
                    self._stop(row["run_id"], "ingestion_native_terminal_mismatch")

    def register(self):
        @DBOS.step(name="bidops_ingestion_page_v1")
        def page(run_id, page_seq):
            return self.service.process_page(run_id, page_seq)

        @DBOS.workflow(name=WORKFLOW)
        def workflow(run_id):
            try:
                self.service.enabled()
                record = self.repository.get_system(run_id)
                if record["status"] in TERMINAL:
                    return {"run_id": run_id, "status": record["status"]}
                record = self.repository.start(run_id)
                pages = (
                    len(record["request"]["pages"]) if record["method"] == "manual_import" else 1
                )
                for index in range(pages):
                    # Each replayed step also has a current guard outside the SDK cache.
                    self.repository.require_execution(run_id)
                    receipt = page(run_id, index)
                    if receipt["status"] == "partial":
                        return {"run_id": run_id, "status": "partial"}
                self.repository.finish(run_id, "completed")
                return {"run_id": run_id, "status": "completed"}
            except (CompanyError, FetchFailure) as exc:
                self._stop(run_id, exc.code)
                return {"run_id": run_id, "status": self.repository.get_system(run_id)["status"]}
            except Exception:
                self._stop(run_id, "ingestion_processing_failed")
                return {"run_id": run_id, "status": "failed"}

        @DBOS.step(name="bidops_ingestion_schedule_admit_v1")
        def admit(native_name, native_id):
            return self.schedules.admit(native_name, native_id)

        @DBOS.workflow(name=SCHEDULE_WORKFLOW)
        def scheduled(scheduled_at, context):
            native_id = DBOS.workflow_id
            result = admit(context["native_name"], native_id)
            if result["run_id"]:
                record = self.repository.get_system(result["run_id"])
                # Client enqueue creates an independent root, idempotently by native_id.
                # Business admission persists before the native enqueue gap.
                if record["status"] == "queued":
                    self.repository.require_execution(record["run_id"])
                    self.enqueue(record)
            return result

        self.workflow, self.schedule_workflow = workflow, scheduled
        self.schedules.workflow = scheduled

    def after_launch(self):
        DBOS.register_queue(
            QUEUE, worker_concurrency=1, global_concurrency=1, polling_interval_sec=0.1
        )
        DBOS.register_queue(
            SCHEDULE_QUEUE, worker_concurrency=1, global_concurrency=1, polling_interval_sec=0.1
        )
        if not self.service.settings.enabled:
            return
        self.schedules.reconcile()
        for row in self.repository.db.rows(
            "SELECT run_id FROM ingestion_run_state WHERE status='queued' AND quarantined=0"
        ):
            with suppress(CompanyError):
                self.enqueue(self.repository.require_execution(row["run_id"]))

    def enqueue(self, record):
        client = self.client()
        try:
            client.enqueue(
                {
                    "workflow_name": WORKFLOW,
                    "queue_name": QUEUE,
                    "workflow_id": record["native_id"],
                    "app_version": record["application_version"],
                },
                record["run_id"],
            )
        finally:
            client.destroy()

    def cancel(self, run_id):
        record = self.repository.get_system(run_id)
        if record["status"] in TERMINAL:
            return
        client = self.client()
        try:
            client.cancel_workflow(record["native_id"], cancel_children=True)
        except Exception:
            raise CompanyError("ingestion_backend_unavailable", 503) from None
        finally:
            client.destroy()
        self._stop(run_id, "ingestion_cancelled", cancelled=True)
