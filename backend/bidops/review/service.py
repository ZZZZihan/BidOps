"""Human review delivery through the existing DBOS workflow, with no new engine."""

from sqlalchemy import text

from bidops.company.authorization import CompanyError, ToolContext, rows, transaction
from bidops.qualification.sources import canonical_json, digest
from bidops.review.config import ReviewSettings
from bidops.review.repository import ReviewRepository

REVIEW_EXPORT_MAX_BYTES = 60_000_000


class ReviewService:
    def __init__(self, screening, jobs=None, settings=None, *, fault=None):
        self.screening, self.jobs = screening, jobs
        self.settings = settings or ReviewSettings()
        self.repository = ReviewRepository(screening.repository.db, screening.sources)
        self.fault = fault
        # Existing formal cases remain an authority boundary when the feature is
        # disabled; a restart must not reactivate the B05 fixture-only path.
        if jobs is not None:
            jobs.reviews = self

    def status(self):
        return {
            "enabled": self.settings.enabled,
            "configured": self.settings.enabled,
            "worker_available": bool(
                self.settings.enabled and self.jobs and self.jobs.status()["configured"]
            ),
        }

    def enabled(self):
        if not self.settings.enabled:
            raise CompanyError("reviews_disabled", 503)

    def create(self, context, **command):
        self.enabled()
        if self.jobs is None:
            raise CompanyError("jobs_disabled", 503)
        record = self.jobs.repository.get(ToolContext(context.owner_id), command["run_id"])
        if record["versions"]["application_version"] != self.jobs.versions["application_version"]:
            raise CompanyError("review_job_version_incompatible", 409)
        result = self.repository.create(context, **command)
        return self.get(context, result["case_id"])

    def get(self, context, case_id, *, candidate_offset=0, candidate_limit=20):
        self.enabled()
        case = self.repository.get(
            context, case_id, candidate_offset=candidate_offset, candidate_limit=candidate_limit
        )
        with transaction(self.repository.db) as conn:
            current = self.repository.authorization.require_case(
                context, case_id, conn=conn, quarantine=True
            )
            if any(
                current[field] != case[field]
                for field in (
                    "review_version",
                    "review_epoch",
                    "quarantined",
                    "purge_state",
                    "head_revision_id",
                    "status",
                )
            ):
                raise CompanyError("review_version_conflict", 409)
            delivery = (
                rows(
                    self.repository.db,
                    "SELECT o.event_id,o.kind,o.delivered_at,o.consumed_at,"
                    "o.frozen,o.last_error_code "
                    "FROM review_outbox o JOIN review_events e ON e.event_id=o.event_id "
                    "WHERE e.case_id=:id ORDER BY e.review_version",
                    {"id": case_id},
                    conn,
                )
                if not case["quarantined"]
                else []
            )
        return {
            **case,
            "delivery": {
                "pending_count": sum(
                    not r["frozen"] and r["consumed_at"] is None for r in delivery
                ),
                "frozen_count": sum(bool(r["frozen"]) for r in delivery),
                "items": delivery,
                "worker_available": self.status()["worker_available"],
            },
        }

    def mutate(self, name, context, case_id, **command):
        self.enabled()
        if name not in {"assign", "decide", "close", "attach_revision", "reauthorize"}:
            raise CompanyError("review_action_unknown", 422)
        getattr(self.repository, name)(context, case_id, **command)
        if self.fault:
            self.fault("review_committed_before_delivery")
        self.deliver_pending()
        return self.get(context, case_id)

    def successor(self, context, case_id, *, request, **command):
        self.enabled()
        if self.jobs is None:
            raise CompanyError("jobs_disabled", 503)
        self.repository.authorization.require_case(context, case_id, action="manage")

        def link(conn, new_job):
            self.repository.link_successor(
                conn, context, case_id, new_job_record=new_job, **command
            )

        key = "review_" + digest([context.owner_id, case_id, command["idempotency_key"]])
        self.jobs.submit(ToolContext(context.owner_id), request, key, link=link)
        if self.fault:
            self.fault("review_successor_committed")
        self.deliver_pending()
        return self.get(context, case_id)

    def export(self, context, case_id):
        self.enabled()
        value = self.repository.export(context, case_id)
        encoded = canonical_json(value).encode()
        if len(encoded) > REVIEW_EXPORT_MAX_BYTES:
            raise CompanyError("review_export_too_large", 413)
        with transaction(self.repository.db) as conn:
            self.repository.authorization.require_case(context, case_id, conn=conn)
        return encoded

    def knows_event(self, event_id):
        return bool(
            self.repository.db.rows(
                "SELECT 1 FROM review_outbox WHERE event_id=:id", {"id": event_id}
            )
        )

    def has_case(self, run_id):
        return bool(
            self.repository.db.rows(
                "SELECT 1 FROM review_revisions WHERE run_id=:id", {"id": run_id}
            )
        )

    def _outbox(self, run_id, event_id):
        found = self.repository.db.rows(
            "SELECT * FROM review_outbox WHERE run_id=:run AND event_id=:id",
            {"run": run_id, "id": event_id},
        )
        if not found:
            raise CompanyError("review_event_not_found")
        return found[0]

    def consume(self, run_id, event_id):
        self.enabled()
        row = self._outbox(run_id, event_id)

        def apply(conn, event):
            if event["outbox_kind"] != "closed":
                return
            record = self.jobs.repository._row(run_id, conn)
            owner = ToolContext(record["owner_id"])
            self.jobs.repository.auth.require_creator(owner, conn)
            self.jobs.repository._authorize(owner, run_id, conn)
            body = self.jobs.repository._body(record, conn)
            if body["versions"]["application_version"] != self.jobs.versions["application_version"]:
                raise CompanyError("job_application_version_changed", 409)
            if record["status"] == "completed" and event["consumed"]:
                return
            if record["status"] != "waiting_review":
                raise CompanyError("job_not_executable", 409)
            self.jobs.repository._validate_result(
                record, record["result_id"], record["result_revision"], "completed", conn
            )
            conn.execute(
                text(
                    "UPDATE durable_job_state SET status='completed',state_version=state_version+1,"
                    "updated_at=:now,error_code=NULL,progress_json=:progress WHERE run_id=:id"
                ),
                {
                    "id": run_id,
                    "now": self.repository.clock(),
                    "progress": canonical_json(
                        {
                            "phase": "human_review_completed",
                            "completed": 1,
                            "total": 1,
                            "event_id": event_id,
                        }
                    ),
                },
            )

        event = self.repository.consume_event(
            event_id, run_id=run_id, review_epoch=row["review_epoch"], apply=apply
        )
        if self.fault:
            self.fault("review_consumed_before_checkpoint")
        return "completed" if event["outbox_kind"] == "closed" else "waiting_review"

    def finished(self, run_id):
        """Recover the business-commit/native-checkpoint gap without any model step."""
        record = self.jobs.repository.get_system(run_id)
        if (
            record["status"] != "completed"
            or record["progress"].get("phase") != "human_review_completed"
        ):
            return False
        event_id = record["progress"].get("event_id")
        outbox = self._outbox(run_id, event_id)
        if outbox["consumed_at"] is None or outbox["kind"] != "closed":
            raise CompanyError("review_completion_unconfirmed", 409)
        self.jobs.repository.get(ToolContext(record["owner_id"]), run_id)
        if record["versions"]["application_version"] != self.jobs.versions["application_version"]:
            raise CompanyError("job_application_version_changed", 409)
        self.repository.load_event_for_worker(
            event_id, run_id=run_id, review_epoch=outbox["review_epoch"]
        )
        return True

    def deliver_pending(self):
        if not self.settings.enabled or self.jobs is None or not self.jobs.started:
            return
        from dbos import DBOS

        # This is an outbox relay into DBOS, not a second execution queue.
        for row in self.repository.pending_events(limit=1000):
            event_id, run_id, epoch = row["event_id"], row["run_id"], row["review_epoch"]
            try:
                self.repository.load_event_for_worker(event_id, run_id=run_id, review_epoch=epoch)
                if row["kind"] == "superseded":
                    record = self.jobs.repository.get_system(run_id)
                    if record["status"] not in {"cancelled", "completed", "failed", "timed_out"}:
                        self.jobs.cancel(
                            ToolContext(record["owner_id"]), run_id, record["state_version"]
                        )
                    self.repository.consume_event(event_id, run_id=run_id, review_epoch=epoch)
                else:
                    self.jobs.require_execution(run_id)
                    DBOS.send(run_id, event_id, topic="review", idempotency_key=event_id)
                if self.fault:
                    self.fault("review_sent_before_delivery_mark")
                self.repository.mark_delivered(event_id, review_epoch=epoch)
            except CompanyError as error:
                self.repository.mark_delivered(event_id, review_epoch=epoch, error_code=error.code)
            except Exception:
                self.repository.mark_delivered(
                    event_id, review_epoch=epoch, error_code="review_backend_unavailable"
                )
