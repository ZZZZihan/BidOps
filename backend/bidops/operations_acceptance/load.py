"""Local engineering fixture for real HTTP load, never a production runner.

Only OIDC transport and authored FunctionModel answers are simulated. Existing
HTTP, signature validation, ACL, SQLite, DBOS, queue and review paths stay real.
Synthetic data, session secrets and databases remain in the caller's private
output directory. This module never fetches an external source or provider.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

from bidops.operations_acceptance.capacity import CapacityManifest, canonical_sha256

CURRENT_JOB = contextvars.ContextVar("capacity_current_job", default=None)


def append_json(path: Path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def exact_bytes(prefix: str, size: int) -> bytes:
    data = prefix.encode()
    if len(data) > size:
        raise ValueError("Synthetic prefix exceeds frozen byte size")
    return data + b"X" * (size - len(data))


def fixed_notice_ids(data: Path) -> list[str]:
    """Independent expected page membership, frozen before timed HTTP arrivals."""
    import sqlite3

    with sqlite3.connect(data / "business.sqlite3") as connection:
        return [
            r[0]
            for r in connection.execute(
                "SELECT notice_id FROM notices ORDER BY project_code,notice_id"
            )
        ]


def verify_notice_page(value, expected_ids, offset, limit):
    items = value.get("items", [])
    ids = [item.get("notice_id") for item in items if isinstance(item, dict)]
    expected = expected_ids[offset : offset + limit]
    return (
        ids == expected
        and len(ids) == len(set(ids))
        and value.get("total") == len(expected_ids)
        and value.get("offset") == offset
        and value.get("limit") == limit
        and value.get("has_more") == (offset + len(expected) < len(expected_ids))
    )


def seed_notices(store, manifest: CapacityManifest, output: Path):
    from bidops.models import Snapshot
    from bidops.notice_data.schemas import AttachmentInput

    if store.db.rows("SELECT COUNT(*) AS n FROM nd_versions")[0]["n"]:
        raise ValueError("Capacity seed requires a new empty notice database")
    at = datetime.now(UTC)
    chosen = None
    packages_each, remainder = divmod(
        manifest.data.procurement_packages, manifest.data.notice_versions
    )
    if remainder:
        raise ValueError("Capacity seed requires an integral package count per notice")
    for index in range(manifest.data.notice_versions):
        body = exact_bytes(
            f"项目名称：SYNTHETIC_CAPACITY_{index:05d}\n项目编号：SYN-CAP-{index:05d}\n"
            "采购人名称：SYNTHETIC容量采购中心\n公告类型：招标公告\n"
            "投标截止时间：2099年11月30日10:00\n预算金额：100元\n"
            + "\n".join(f"第{n + 1}包" for n in range(packages_each))
            + "\nCAPACITY_REQUIREMENT synthetic certificate required.\n",
            manifest.data.notice_text_bytes_each,
        )
        sha = hashlib.sha256(body).hexdigest()
        url = f"https://synthetic.invalid/capacity/{index:05d}"
        snapshot = Snapshot(
            snapshot_id=hashlib.sha256((url + "\n" + sha).encode()).hexdigest(),
            source_url=url,
            sha256=sha,
            byte_size=len(body),
            fetched_at=at,
            content_type="text/plain",
            encoding="utf-8",
            access_method="manual_import",
            manual_attestation=True,
        )
        prepared = store.prepare(snapshot, body)
        with store.db.engine.begin() as conn:
            saved = store.persist(prepared, conn, source_complete=True)
        version = store.notice_data.get_version(saved.version_id)
        if len(version.packages) != packages_each:
            raise ValueError("Synthetic package parser did not realize frozen inventory")
        if index < manifest.data.attachments:
            attachment = exact_bytes(
                f"SYNTHETIC_ATTACHMENT_{index:05d}\n", manifest.data.attachment_bytes_each
            )
            store.notice_data.record_attachment(
                AttachmentInput(
                    parent_version_id=version.version_id,
                    attachment_key=f"synthetic-{index}",
                    filename=f"synthetic-{index}.txt",
                    observed_at=at,
                    content_type="text/plain",
                    acquisition_status="downloaded",
                    parse_status="parsed",
                ),
                attachment,
            )
        chosen = version
        if (index + 1) % 1000 == 0:
            append_json(
                output / "setup.jsonl", {"event": "notice_seed_progress", "versions": index + 1}
            )
    result = {"project_id": chosen.project_id, "package_id": chosen.packages[0].package_id}
    (output / "notice-seed.json").write_text(json.dumps(result))
    return result


def seed_company(data: Path, manifest: CapacityManifest, owner_ids: dict[str, str]):
    """Setup-only direct service calls after actual signed HTTP login; no load is counted."""
    from bidops.company import CompanyService
    from bidops.company.authorization import ToolContext
    from bidops.db import Database

    db = Database(f"sqlite:///{data / 'business.sqlite3'}")
    company = CompanyService(db, data)
    owner = ToolContext(owner_ids[manifest.account_ids[0]])
    first = None
    for index in range(manifest.data.materials):
        item = company.materials.put(
            owner,
            filename=f"synthetic-{index}.txt",
            content_type="text/plain",
            data=exact_bytes(
                f"SYNTHETIC_PRIVATE_MATERIAL_{index:03d}\n", manifest.data.material_bytes_each
            ),
        )
        first = first or item
    citation = company.materials.citation(owner, first["object_id"], first["version_id"], 0, 30)
    profile = company.repo.put(
        owner,
        {
            "display_name": "SYNTHETIC capacity profile",
            "provenance_kind": "engineering_fixture",
            "facts": [
                {
                    "fact_id": "SYN-CAP-FACT",
                    "kind": "qualification",
                    "label": "Synthetic engineering certificate",
                    "value": "SYNTHETIC",
                    "status": "document_supported",
                    "evidence": [citation],
                }
            ],
        },
    )
    for subject in owner_ids.values():
        if subject != owner.owner_id:
            for ref in (profile, first):
                company.auth.grant(owner, ref["object_id"], subject, ["read"])
    db.engine.dispose()
    return {key: profile[key] for key in ("object_id", "version_id", "sha256")}


def inventory(data: Path, manifest: CapacityManifest):
    """Re-read fixed bytes and metadata before and after the measured window."""
    from bidops.db import Database

    db = Database(f"sqlite:///{data / 'business.sqlite3'}")
    versions = db.rows(
        "SELECT version_id,raw_sha256,text_sha256,normalized_text "
        "FROM nd_versions ORDER BY version_id"
    )
    lengths = [len(r["normalized_text"].encode()) for r in versions]
    files = []
    for row in versions:
        raw = (data / "raw" / row["raw_sha256"]).read_bytes()
        if (
            hashlib.sha256(raw).hexdigest() != row["raw_sha256"]
            or hashlib.sha256(row["normalized_text"].encode()).hexdigest() != row["text_sha256"]
        ):
            raise ValueError("Capacity notice inventory hash mismatch")
        files.append([row["version_id"], row["raw_sha256"], row["text_sha256"]])
    materials = db.rows(
        "SELECT version_id,sha256,byte_size,blob_path,text_path,text_sha256 "
        "FROM company_material_revisions ORDER BY version_id"
    )
    material_sizes = []
    for row in materials:
        raw = (data / "private/company" / row["blob_path"]).read_bytes()
        text = (data / "private/company" / row["text_path"]).read_bytes()
        if (
            len(raw) != row["byte_size"]
            or hashlib.sha256(raw).hexdigest() != row["sha256"]
            or hashlib.sha256(text).hexdigest() != row["text_sha256"]
        ):
            raise ValueError("Capacity material inventory hash mismatch")
        material_sizes.append(len(raw))
    attachments = db.rows("SELECT record_id,raw_sha256 FROM nd_attachments ORDER BY record_id")
    attachment_sizes = []
    for row in attachments:
        raw = (data / "attachments" / row["raw_sha256"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["raw_sha256"]:
            raise ValueError("Capacity attachment inventory hash mismatch")
        attachment_sizes.append(len(raw))
        files.append([row["record_id"], row["raw_sha256"]])
    result = {
        "enterprises": 1,
        "accounts": db.rows("SELECT COUNT(*) AS n FROM enterprise_subjects")[0]["n"],
        "notice_versions": len(versions),
        "procurement_packages": db.rows(
            "SELECT COUNT(DISTINCT package_id) AS n FROM nd_package_versions"
        )[0]["n"],
        "notice_text_bytes_total": sum(lengths),
        "notice_text_bytes_min": min(lengths, default=0),
        "notice_text_bytes_max": max(lengths, default=0),
        "material_sizes_bytes": material_sizes,
        "attachment_sizes_bytes": attachment_sizes,
        "inventory_sha256": canonical_sha256([files, materials]),
    }
    db.engine.dispose()
    return result


class OfflineIdentityProvider:
    """RS256 and PKCE are actually checked; only the external HTTP transport is local."""

    def __init__(self, origin):
        from joserfc.jwk import RSAKey

        self.origin, self.issuer = origin, "https://synthetic-identity.invalid/realms/capacity"
        self.key = RSAKey.generate_key(2048, parameters={"kid": "synthetic-capacity"})

    def handle(self, request):
        import base64

        import httpx2
        from joserfc import jwt

        if request.url.path.endswith("openid-configuration"):
            return httpx2.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": self.issuer + "/auth",
                    "token_endpoint": self.issuer + "/token",
                    "jwks_uri": self.issuer + "/keys",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        if request.url.path.endswith("/keys"):
            return httpx2.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if not request.url.path.endswith("/token"):
            raise ValueError("Unexpected offline identity transport")
        fields = parse_qs(request.content.decode())
        code = json.loads(fields["code"][0])
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(fields["code_verifier"][0].encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if challenge != code["challenge"] or fields["redirect_uri"] != [
            self.origin + "/api/auth/callback"
        ]:
            raise ValueError("Synthetic OIDC PKCE mismatch")
        if code["sub"] not in {f"account-{i:02d}" for i in range(1, 21)}:
            raise ValueError("Unknown synthetic account")
        claims = {
            "iss": self.issuer,
            "sub": code["sub"],
            "aud": "capacity-client",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": code["nonce"],
            "name": "SYNTHETIC " + code["sub"],
            "bidops_roles": ["analyst", "reviewer"],
        }
        token = jwt.encode({"alg": "RS256", "kid": "synthetic-capacity"}, claims, self.key)
        return httpx2.Response(
            200,
            json={
                "access_token": "SYNTHETIC_OFFLINE_TOKEN",
                "token_type": "Bearer",
                "id_token": token,
            },
        )


def scripted_answer(messages, info):
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.usage import RequestUsage

    prompt = next(
        json.loads(p.content)
        for m in reversed(messages)
        for p in reversed(m.parts)
        if isinstance(p, UserPromptPart) and isinstance(p.content, str)
    )
    if prompt["stage"] == "extraction":
        segments = []
        for segment in prompt["segments"]:
            conditions = []
            for match in re.finditer(r"^CAPACITY_REQUIREMENT[^\n]*", segment["text"], re.M):
                start = segment["start"] + match.start()
                end = start + len(match.group())
                conditions.append(
                    {
                        "start": start,
                        "end": end,
                        "hard_requirement": True,
                        "expression": {
                            "start": start,
                            "end": end,
                            "operator": "atom",
                            "text": match.group(),
                            "operands": [],
                        },
                        "scope_kind": "all",
                        "validity": {"kind": "not_required"},
                    }
                )
            segments.append(
                {
                    "segment_id": segment["segment_id"],
                    "disposition": "requirement" if conditions else "not_requirement",
                    "conditions": conditions,
                }
            )
        answer = {"document_id": prompt["document"]["document_id"], "segments": segments}
    else:
        condition = prompt["condition"]
        answer = {
            "condition_id": condition["condition_id"],
            "judgments": [
                {
                    "node_id": condition["expression"]["node_id"],
                    "status": "information_insufficient",
                    "reason": "Authored synthetic capacity answer, not qualification evidence",
                    "company_evidence": [],
                    "missing_items": ["Synthetic independent qualification evidence unavailable"],
                }
            ],
        }
    return ModelResponse(
        parts=[ToolCallPart(info.output_tools[0].name, answer)],
        model_name="offline-capacity-function",
        usage=RequestUsage(input_tokens=100, output_tokens=10),
    )


def create_load_app(output: Path, manifest: CapacityManifest, origin: str):
    import httpx2
    from pydantic_ai import models
    from pydantic_ai.models.function import FunctionModel

    from bidops.agent_demo.runtime import DemoSettings
    from bidops.api import create_app
    from bidops.config import Settings
    from bidops.enterprise.config import EnterpriseSettings
    from bidops.enterprise.health import HealthSettings
    from bidops.ingestion.config import IngestionSettings
    from bidops.jobs.config import JobsSettings
    from bidops.jobs.runtime import JobsService
    from bidops.qualification.config import ScreeningSettings
    from bidops.review.config import ReviewSettings
    from bidops.review.service import ReviewService

    actual_constants = {
        "asgi_workers": 1,
        "configured_compute_concurrency": 1,
        "json_request_bytes": 16384,
        "material_text_chars": 200000,
    }
    if any(getattr(manifest.limits, key) != value for key, value in actual_constants.items()):
        raise ValueError("Capacity manifest differs from actual fixed implementation limits")
    models.ALLOW_MODEL_REQUESTS = False
    data = output / "data"
    data.mkdir(mode=0o700)
    registry = output / "synthetic-sources.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "id": "synthetic",
                    "name": "Synthetic capacity data",
                    "host": "synthetic.invalid",
                    "home_url": "https://synthetic.invalid/",
                    "decision_reason": "Authored offline synthetic bytes only",
                }
            ]
        )
    )
    settings = Settings(
        data_dir=data,
        database_url=f"sqlite:///{data / 'business.sqlite3'}",
        source_registry=registry,
        network_enabled=False,
        max_bytes=manifest.limits.material_upload_bytes,
        http_timeout_seconds=15,
        http_rate_limit_seconds=5,
        http_attempts=3,
    )
    provider = OfflineIdentityProvider(origin)
    enterprise = EnterpriseSettings(
        _env_file=None,
        enabled=True,
        public_url=origin,
        oidc_issuer=provider.issuer,
        oidc_client_id="capacity-client",
        oidc_client_secret="SYNTHETIC_OFFLINE_SECRET",
        cookie_secret=secrets.token_urlsafe(48),
        allow_insecure_loopback=True,
        session_seconds=3600,
        identity_snapshot_seconds=240,
        identity_provider="keycloak",
        history_days=30,
        requests_per_minute=20,
        organization="SYNTHETIC capacity enterprise",
        deployment="single_organization_single_worker",
    )
    app = create_app(
        settings,
        DemoSettings(enabled=False, _env_file=None, DEEPSEEK_API_KEY=None),
        enterprise,
        screening_settings=ScreeningSettings(
            _env_file=None,
            enabled=True,
            live_calls_enabled=False,
            ledger_verified=False,
            ledger_restore_token=None,
            DEEPSEEK_API_KEY=None,
            model_name="deepseek-flash",
            max_requests=manifest.limits.qualification_requests,
            max_seconds=manifest.limits.qualification_seconds,
            max_segment_chars=4000,
            max_request_bytes=manifest.limits.qualification_input_bytes,
            max_output_tokens=manifest.limits.qualification_output_tokens,
            budget_cny="10",
            input_cny_per_million="2",
            output_cny_per_million="8",
        ),
        jobs_settings=JobsSettings(
            _env_file=None, enabled=False, max_active_jobs=manifest.limits.max_active_jobs
        ),
        review_settings=ReviewSettings(_env_file=None, enabled=False),
        ingestion_settings=IngestionSettings(
            _env_file=None,
            enabled=False,
            max_active_runs=10,
            max_schedules=20,
            freshness_seconds=86400,
        ),
        health_settings=HealthSettings(
            _env_file=None,
            enabled=False,
            interval_seconds=30,
            stale_seconds=120,
            minimum_free_bytes=268435456,
            backlog_count=80,
            oldest_queued_seconds=120,
        ),
    )
    native = app.router.lifespan_context
    active, delayed = {}, {}
    lock = threading.Lock()

    def clock():
        path = output / "clock.json"
        return json.loads(path.read_text())["monotonic_ns"] if path.exists() else None

    def log(event, **fields):
        base = clock()
        append_json(
            output / "worker-events.jsonl",
            {
                "event": event,
                "monotonic_ns": time.monotonic_ns(),
                "offset_ms": (time.monotonic_ns() - base) // 1_000_000 if base else None,
                **fields,
            },
        )

    async def answer(messages, info):
        run_id = CURRENT_JOB.get()
        if run_id and clock() is not None and delayed.get(run_id, 0) < 2:
            begin = time.monotonic_ns()
            delayed[run_id] = delayed.get(run_id, 0) + 1
            log("model_wait_started", run_id=run_id)
            await asyncio.sleep(manifest.simulation.requested_delay_ms / 2000)
            log(
                "model_wait_part_finished",
                run_id=run_id,
                observed_delay_ms=(time.monotonic_ns() - begin) // 1_000_000,
            )
        log("local_model_call", run_id=run_id, real_model_calls=0, real_source_calls=0)
        return scripted_answer(messages, info)

    @asynccontextmanager
    async def lifespan(app):
        async with native(app):
            app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
                provider.handle
            )
            seed_notices(app.state.store, manifest, output)
            app.state.screening.model_override = FunctionModel(
                answer, model_name="offline-capacity-function"
            )
            original_check = app.state.screening._check

            async def measured(*args, **kwargs):
                execution = kwargs.get("execution")
                run_id = execution.record["run_id"] if execution else None
                token = CURRENT_JOB.set(run_id)
                if run_id:
                    with lock:
                        active[run_id] = True
                        log("compute_started", run_id=run_id)
                try:
                    return await original_check(*args, **kwargs)
                finally:
                    if run_id:
                        with lock:
                            log("compute_finished", run_id=run_id)
                            active.pop(run_id, None)
                    CURRENT_JOB.reset(token)

            app.state.screening._check = measured
            jobs = JobsService(
                app.state.screening,
                data,
                JobsSettings(
                    _env_file=None,
                    enabled=True,
                    max_active_jobs=manifest.limits.max_active_jobs,
                ),
            )
            app.state.jobs = jobs
            app.state.review = ReviewService(
                app.state.screening, jobs, ReviewSettings(_env_file=None, enabled=True)
            )
            await asyncio.to_thread(jobs.start)
            stop = threading.Event()

            def resources():
                while clock() is None and not stop.wait(0.1):
                    pass
                base = clock()
                if base is None:
                    return
                scheduled = 0
                while not stop.is_set():
                    delay = (base + scheduled * 1_000_000 - time.monotonic_ns()) / 1_000_000_000
                    if delay > 0 and stop.wait(delay):
                        break
                    with lock:
                        count = len(active)
                        at = max(0, (time.monotonic_ns() - base) // 1_000_000)
                    process = subprocess.run(
                        ["ps", "-p", str(os.getpid()), "-o", "rss=,%cpu="],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.split()
                    rows = app.state.store.db.rows(
                        "SELECT status,COUNT(*) n FROM durable_job_state GROUP BY status"
                    )
                    counts = {r["status"]: r["n"] for r in rows}
                    append_json(
                        output / "resources.jsonl",
                        {
                            "offset_ms": at,
                            "rss_bytes": int(process[0]) * 1024,
                            "cpu_percent": float(process[1]),
                            "disk_free_bytes": shutil.disk_usage(output).free,
                            "compute_active": count,
                            "active_jobs": sum(
                                counts.get(s, 0)
                                for s in (
                                    "queued",
                                    "running",
                                    "waiting_review",
                                    "interrupted",
                                    "cancel_requested",
                                )
                            ),
                            "queued_jobs": counts.get("queued", 0),
                            "worker_alive": True,
                            "oom_events": 0,
                            "deadlock_events": 0,
                        },
                    )
                    scheduled += manifest.workload.resource_interval_ms

            sampler = threading.Thread(target=resources, daemon=True)
            sampler.start()
            log(
                "worker_ready",
                pid=os.getpid(),
                versions=jobs.versions,
                effective_limits={
                    "max_requests": app.state.screening.settings.max_requests,
                    "max_seconds": app.state.screening.settings.max_seconds,
                    "max_request_bytes": app.state.screening.settings.max_request_bytes,
                    "max_output_tokens": app.state.screening.settings.max_output_tokens,
                    "max_active_jobs": jobs.settings.max_active_jobs,
                    "identity_snapshot_seconds": enterprise.identity_snapshot_seconds,
                    "session_seconds": enterprise.session_seconds,
                    "ingestion_enabled": app.state.ingestion.settings.enabled,
                    "health_enabled": app.state.dependency_health.settings.enabled,
                },
            )
            try:
                yield
            finally:
                stop.set()
                sampler.join(timeout=5)
                await asyncio.to_thread(jobs.close)

    app.router.lifespan_context = lifespan
    return app
