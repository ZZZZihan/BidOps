"""Permission-bound ingestion; DBOS owns execution and scheduling."""

import json
import time
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError

from bidops.company.authorization import CompanyError, rows
from bidops.fetcher import Fetcher, FetchFailure
from bidops.ingestion.config import IngestionSettings
from bidops.ingestion.repository import ACTIVE, IngestionRepository, canonical, sha
from bidops.ingestion.schemas import MAX_REQUEST_BYTES, HttpRun, ManualImport, decode_body
from bidops.models import Snapshot
from bidops.notice_data.attachment_parser import prepare_attachment
from bidops.sources import network_permission
from bidops.storage import snapshot_id


class IngestionService:
    def __init__(self, store, company, settings=None, jobs=None, *, fault=None, client=None):
        self.store, self.company, self.jobs = store, company, jobs
        self.settings = settings or IngestionSettings()
        self.fault, self.client = fault, client
        if jobs:
            versions = jobs.versions
            jobs.ingestion = self
        else:
            from bidops.jobs.runtime import application_version

            versions = application_version()
        self.repository = IngestionRepository(store, company.auth, self.settings, versions)
        self.runtime = None

    def enabled(self, *, worker=False):
        if not self.settings.enabled:
            raise CompanyError("ingestion_disabled", 503)
        if worker and not (self.jobs and self.jobs.started and self.runtime):
            raise CompanyError("ingestion_backend_unavailable", 503)

    def status(self):
        available = bool(self.jobs and self.jobs.started and self.runtime)
        return {
            "enabled": self.settings.enabled,
            "configured": self.settings.enabled and available,
            "worker_available": available,
            "backend": "dbos",
            "supported_methods": ["manual_import", "http_fetch"],
            "max_request_bytes": MAX_REQUEST_BYTES,
            "max_blob_bytes": self.store.settings.max_bytes,
        }

    def _public(self, record):
        result = dict(record)
        for field in ("created_at", "updated_at"):
            if isinstance(result.get(field), (int, float)):
                result[field] = datetime.fromtimestamp(result[field], UTC).isoformat()
        result["errors"] = result.get("failures", [])
        result["last_error_code"] = result.pop("error_code", None)
        result["allowed_actions"] = (
            ["cancel"] if record["status"] in ACTIVE and not record["quarantined"] else []
        )
        for field in ("owner_id", "idempotency_key", "policy_sha256", "input_sha256", "native_id"):
            result.pop(field, None)
        return result

    def read(self, context, run_id):
        self.enabled()
        return self._public(self.repository.read(context, run_id))

    def list(self, context, offset=0, limit=20):
        self.enabled()
        page = self.repository.list(context, offset, limit)
        return {**page, "items": [self._public(item) for item in page["items"]]}

    def sources(self, context):
        self.enabled()
        result = []
        for source in self.repository.sources(context):
            policy = source.get("policy")
            methods = ["manual_import"] if policy is None else list(policy["acquisition_modes"])
            if policy and (
                policy["status"] == "denied"
                or not datetime.fromisoformat(policy["valid_from"]).timestamp()
                <= self.repository.clock()
                < datetime.fromisoformat(policy["valid_until"]).timestamp()
            ):
                methods = []
            actual = self.repository._source(source["id"])
            allowed = bool(actual.policy and actual.allowed_urls)
            if allowed:
                allowed, _ = network_permission(actual.allowed_urls[0], [actual])
            if not allowed:
                methods = [method for method in methods if method == "manual_import"]
            last = source["last_success"]
            age = max(0, self.repository.clock() - last) if last is not None else None
            state = rows(
                self.store.db,
                "SELECT cursor_json,counts_json FROM ingestion_source_state WHERE source_id=:id",
                {"id": source["id"]},
            )
            result.append(
                {
                    "source_id": source["id"],
                    "name": source["name"],
                    "permission_status": "allowed"
                    if allowed
                    else (policy or {}).get("status", "unknown"),
                    "allowed_methods": methods,
                    "policy_expires_at": policy["valid_until"] if policy else None,
                    "reviewed_at": source["reviewed_at"],
                    "last_attempt": datetime.fromtimestamp(source["last_attempt"], UTC).isoformat()
                    if source["last_attempt"] is not None
                    else None,
                    "last_success": datetime.fromtimestamp(last, UTC).isoformat()
                    if last is not None
                    else None,
                    "age_seconds": age,
                    "freshness": "unknown"
                    if age is None
                    else "stale"
                    if age > self.settings.freshness_seconds
                    else "fresh",
                    "last_error_code": source["last_error_code"],
                    "cursor": json.loads(state[0]["cursor_json"]) if state else None,
                    "counts": json.loads(state[0]["counts_json"]) if state else {},
                }
            )
        self.company.auth.require_subject(context)
        return {"items": result, "total": len(result)}

    def _submit(self, context, payload, method):
        self.enabled(worker=True)
        self.repository.require_admin(context)
        try:
            model = (ManualImport if method == "manual_import" else HttpRun).model_validate(payload)
            value = model.model_dump(mode="json")
            if len(canonical(value).encode()) > MAX_REQUEST_BYTES:
                raise CompanyError("ingestion_request_too_large", 413)
            if method == "manual_import":
                total = 0
                for page in value["pages"]:
                    for item in page["items"]:
                        for blob in [item, *item["attachments"]]:
                            if blob.get("body_base64") is not None:
                                total += len(
                                    decode_body(blob["body_base64"], self.store.settings.max_bytes)
                                )
                if total > MAX_REQUEST_BYTES:
                    raise CompanyError("ingestion_request_too_large", 413)
        except ValidationError:
            raise CompanyError("ingestion_invalid_request", 422) from None
        except ValueError as exc:
            code = (
                "ingestion_blob_too_large" if "too_large" in str(exc) else "ingestion_invalid_blob"
            )
            raise CompanyError(code, 413 if "too_large" in code else 422) from None
        record = self.repository.create(
            context, value, method, native_id="source-" + uuid.uuid4().hex
        )
        if record["status"] == "queued":
            self.runtime.enqueue(record)
        return {**self.read(context, record["run_id"]), "created": record["created"]}

    def submit(self, context, payload):
        return self._submit(context, payload, "http_fetch")

    def import_manual(self, context, payload):
        return self._submit(context, payload, "manual_import")

    def cancel(self, context, run_id, expected_state_version):
        self.enabled()
        self.repository.request_cancel(context, run_id, expected_state_version)
        # The persistent guard blocks commits even if the SDK is unavailable.
        if self.runtime:
            self.runtime.cancel(run_id)
        else:
            self.repository.finish(run_id, "cancelled")
        return self.read(context, run_id)

    def _before_request(self, run_id, source_id, revision):
        while True:
            self.enabled()
            wait = self.repository.reserve_request(run_id, source_id, revision)
            if wait <= 0:
                return
            time.sleep(min(wait, 0.2))

    def process_page(self, run_id, page_seq):
        self.enabled()
        page = self.repository.begin_page(run_id, page_seq)
        if page["existing_receipt"] is not None:
            return page["existing_receipt"]
        record = self.repository.require_execution(run_id)
        request, manual = record["request"], record["method"] == "manual_import"
        member = (
            request["pages"][page_seq]
            if manual
            else {"items": request["items"], "cursor_after": None}
        )
        observed = datetime.fromisoformat(page["observed_at"])
        fetcher = (
            None
            if manual
            else Fetcher(
                self.store,
                client=self.client,
                policy_reader=self.repository._source,
                before_request=lambda source, revision: self._before_request(
                    run_id, source, revision
                ),
            )
        )
        items, total = [], 0
        mode = "scheduled_fetch" if record["trigger_kind"] == "schedule" else "manual_fetch"
        try:
            for item in member["items"]:
                self.repository.require_execution(run_id)
                downloaded = None
                if manual:
                    data = decode_body(item["body_base64"], self.store.settings.max_bytes)
                    content_type, encoding = item["content_type"], item["encoding"]
                else:
                    downloaded = fetcher.download(
                        item["source_url"],
                        mode=mode,
                        expected_policy_revision=record["policy_revision"],
                    )
                    data, content_type, encoding = (
                        downloaded.data,
                        downloaded.content_type,
                        downloaded.encoding,
                    )
                total += len(data)
                if total > MAX_REQUEST_BYTES:
                    raise CompanyError("ingestion_page_too_large", 413)
                digest = sha(data)
                snapshot = Snapshot(
                    snapshot_id=snapshot_id(item["source_url"], digest),
                    source_url=item["source_url"],
                    sha256=digest,
                    byte_size=len(data),
                    fetched_at=observed,
                    content_type=content_type,
                    encoding=encoding or "utf-8",
                    access_method=record["method"],
                    manual_attestation=manual,
                    http_status=downloaded.http_status if downloaded else None,
                    etag=downloaded.etag if downloaded else None,
                    last_modified=downloaded.last_modified if downloaded else None,
                )
                notice = self.store.prepare(snapshot, data)
                attachments = []
                for attachment in item["attachments"]:
                    self.repository.require_execution(run_id)
                    error = attachment.get("error_code")
                    body, media, enc = (
                        None,
                        attachment["content_type"],
                        attachment.get("encoding", "utf-8"),
                    )
                    if not attachment.get("failed"):
                        if manual:
                            body = decode_body(
                                attachment["body_base64"], self.store.settings.max_bytes
                            )
                        else:
                            try:
                                raw = fetcher.download(
                                    attachment["source_url"],
                                    purpose="attachment",
                                    mode=mode,
                                    expected_policy_revision=record["policy_revision"],
                                )
                                # Declared media must match the actual response.
                                if (
                                    media != "application/octet-stream"
                                    and raw.content_type != media
                                ):
                                    raise FetchFailure("attachment_media_mismatch")
                                body, media, enc = (
                                    raw.data,
                                    raw.content_type,
                                    raw.encoding or "utf-8",
                                )
                            except FetchFailure as exc:
                                error = exc.code
                    total += len(body or b"")
                    if total > MAX_REQUEST_BYTES:
                        raise CompanyError("ingestion_page_too_large", 413)
                    prepared = prepare_attachment(
                        {
                            "parent_version_id": "pending-ingestion-parent",
                            "attachment_key": attachment["attachment_key"],
                            "filename": attachment["filename"],
                            "source_url": attachment.get("source_url"),
                            "observed_at": observed,
                            "content_type": media,
                            "encoding": enc,
                            "acquisition_status": "downloaded" if body is not None else "failed",
                            "parse_status": "pending",
                            "error_code": error,
                        },
                        body,
                        expected_page_count=attachment.get("expected_page_count"),
                        limits={"max_bytes": self.store.settings.max_bytes},
                    )
                    attachments.append(prepared)
                items.append((notice, attachments, bool(item.get("source_complete", False))))
        finally:
            if fetcher:
                fetcher.close()
        if self.fault:
            self.fault("before_page_commit")
        receipt = self.repository.commit_page(
            run_id,
            page_seq,
            expected_cursor_version=page["expected_cursor_version"],
            page_sha256=sha(canonical(member)),
            items=items,
            cursor_after=member["cursor_after"],
            observed_at=observed,
        )
        if self.fault:
            self.fault("after_page_commit")
        return receipt

    def before_launch(self, client):
        from bidops.ingestion.runtime import IngestionRuntime

        self.runtime = IngestionRuntime(self)
        self.runtime.before_launch(client)

    def register(self):
        from bidops.ingestion.runtime import IngestionRuntime

        if self.runtime is None:
            self.runtime = IngestionRuntime(self)
        self.runtime.register()

    def after_launch(self):
        self.runtime.after_launch()

    def schedule(self, context, payload):
        self.enabled(worker=True)
        return self.runtime.schedules.command(context, payload)

    def schedules(self, context):
        self.enabled()
        self.repository.require_admin(context)
        from bidops.ingestion.schedules import Schedules

        return Schedules(self).list(context)
