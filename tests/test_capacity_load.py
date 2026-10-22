"""Real local seed/auth/storage boundaries, all data explicitly synthetic."""

import asyncio
import hashlib
import importlib.util
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from bidops.operations_acceptance.capacity import CapacityManifest, load_manifest
from bidops.operations_acceptance.load import (
    create_load_app,
    exact_bytes,
    fixed_notice_ids,
    inventory,
    seed_company,
    seed_notices,
    verify_notice_page,
)
from fastapi.testclient import TestClient

MANIFEST = Path(__file__).resolve().parents[1] / "evals/capacity-v1/manifest.json"


def probe_module():
    path = MANIFEST.parents[2] / "scripts/capacity_probe.py"
    spec = importlib.util.spec_from_file_location("capacity_probe_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def unit_manifest():
    value = load_manifest(MANIFEST, hashlib.sha256(MANIFEST.read_bytes()).hexdigest()).model_dump()
    value["profile_id"] = "UNIT-loader-only-not-capacity"
    value["data"].update(notice_versions=10, procurement_packages=50, materials=2, attachments=1)
    return CapacityManifest.model_validate(value)


def signed_login(client, account):
    client.cookies.clear()
    response = client.get("/api/auth/login", follow_redirects=False)
    fields = parse_qs(urlsplit(response.headers["location"]).query)
    response = client.get(
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
    assert response.status_code == 303
    return client.get("/api/auth/me").json()


def test_exact_frozen_bytes_and_oversize_rejection():
    assert len(exact_bytes("中文_SYNTHETIC", 4096)) == 4096
    with pytest.raises(ValueError, match="exceeds"):
        exact_bytes("too large", 1)


@pytest.fixture
def loaded(tmp_path, unit_manifest):
    app = create_load_app(tmp_path, unit_manifest, "http://127.0.0.1")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        identities = {a: signed_login(client, a) for a in unit_manifest.account_ids}
        profile = seed_company(
            tmp_path / "data",
            unit_manifest,
            {a: v["user"]["subject_id"] for a, v in identities.items()},
        )
        yield app, client, identities, profile, tmp_path / "data", unit_manifest


def test_real_seed_signed_identities_and_full_inventory_are_not_metadata_only(loaded):
    app, client, identities, profile, data, manifest = loaded
    receipt = inventory(data, manifest)
    assert receipt["accounts"] == 20
    assert receipt["notice_versions"] == 10 and receipt["procurement_packages"] == 50
    assert receipt["notice_text_bytes_total"] == 10 * 4096
    assert receipt["material_sizes_bytes"] == [16384, 16384]
    assert receipt["attachment_sizes_bytes"] == [16384]
    identity = signed_login(client, "account-02")
    assert identity["authenticated"] and "review:decide" in identity["permissions"]
    assert client.get(f"/api/company/profiles/{profile['object_id']}").status_code == 200
    page = client.get("/api/notices", params={"limit": 3, "offset": 6}).json()
    assert page["total"] == 10 and len(page["items"]) == 3 and page["has_more"]
    assert app.state.jobs.slots._value == 1
    assert identities["account-01"]["user"]["subject_id"] != identity["user"]["subject_id"]
    expected = fixed_notice_ids(data)
    assert verify_notice_page(page, expected, 6, 3)
    page["items"][1] = page["items"][0]
    assert not verify_notice_page(page, expected, 6, 3)
    earlier = client.get("/api/notices", params={"limit": 3, "offset": 0}).json()
    page["items"] = earlier["items"]
    assert not verify_notice_page(page, expected, 6, 3)


def test_manifest_limits_override_host_environment_and_other_services_stay_disabled(
    tmp_path,
    unit_manifest,
    monkeypatch,
):
    for name, value in {
        "BIDOPS_SCREENING_MAX_REQUESTS": "30",
        "BIDOPS_SCREENING_MAX_SECONDS": "300",
        "BIDOPS_SCREENING_MAX_REQUEST_BYTES": "200000",
        "BIDOPS_SCREENING_MAX_OUTPUT_TOKENS": "8000",
        "BIDOPS_JOBS_MAX_ACTIVE_JOBS": "3",
        "BIDOPS_ENTERPRISE_IDENTITY_SNAPSHOT_SECONDS": "30",
        "BIDOPS_ENTERPRISE_SESSION_SECONDS": "60",
        "BIDOPS_INGESTION_ENABLED": "true",
        "BIDOPS_OPERATIONS_ENABLED": "true",
    }.items():
        monkeypatch.setenv(name, value)
    app = create_load_app(tmp_path, unit_manifest, "http://127.0.0.1")
    with TestClient(app, base_url="http://127.0.0.1"):
        actual = app.state.screening.settings
        assert (actual.max_requests, actual.max_seconds) == (12, 90)
        assert (actual.max_request_bytes, actual.max_output_tokens) == (120000, 6000)
        assert app.state.jobs.settings.max_active_jobs == 100
        assert app.state.identity.settings.identity_snapshot_seconds == 240
        assert app.state.identity.settings.session_seconds == 3600
        assert not app.state.ingestion.settings.enabled
        assert not app.state.dependency_health.settings.enabled


@pytest.mark.parametrize(
    "field,value",
    [
        ("asgi_workers", 2),
        ("configured_compute_concurrency", 2),
        ("json_request_bytes", 32768),
        ("material_text_chars", 300000),
    ],
)
def test_manifest_cannot_claim_unimplemented_runtime_limits(tmp_path, unit_manifest, field, value):
    payload = unit_manifest.model_dump()
    payload["limits"][field] = value
    with pytest.raises(ValueError, match="actual fixed implementation limits|Input should be 1"):
        create_load_app(tmp_path, CapacityManifest.model_validate(payload), "http://127.0.0.1")


def test_probe_child_environment_removes_host_configuration_and_credentials():
    module = probe_module()
    assert module.isolated_environment(
        {
            "PATH": "/bin",
            "BIDOPS_JOBS_ENABLED": "true",
            "BIDOPS_SCREENING_MAX_SECONDS": "300",
            "DEEPSEEK_API_KEY": "synthetic",
            "HTTPS_PROXY": "synthetic",
            "ACCESS_TOKEN": "synthetic",
        }
    ) == {"PATH": "/bin"}


def test_job_receipt_uses_persisted_admission_time_and_preserves_missing_execution(tmp_path):
    module = probe_module()
    data = tmp_path / "data"
    data.mkdir()
    started = datetime(2026, 10, 23, tzinfo=UTC)
    with sqlite3.connect(data / "business.sqlite3") as connection:
        connection.execute("CREATE TABLE durable_jobs(run_id TEXT,created_at REAL)")
        connection.execute("CREATE TABLE durable_job_state(run_id TEXT,status TEXT,result_id TEXT)")
        connection.execute(
            "INSERT INTO durable_jobs VALUES('job-a',?)", (started.timestamp() + 0.125,)
        )
        connection.execute(
            "INSERT INTO durable_job_state VALUES('job-a','waiting_review','result-a')"
        )
        connection.execute(
            "INSERT INTO durable_jobs VALUES('job-b',?)", (started.timestamp() + 0.5,)
        )
        connection.execute("INSERT INTO durable_job_state VALUES('job-b','queued',NULL)")
    origin = 1_000_000_000
    for event in [
        {"event": "compute_started", "monotonic_ns": origin + 200_000_000},
        {"event": "model_wait_part_finished", "observed_delay_ms": 30001},
        {"event": "compute_finished", "monotonic_ns": origin + 30_201_000_000},
    ]:
        module.append_json(tmp_path / "worker-events.jsonl", {**event, "run_id": "job-a"})
    requests = [
        {
            "operation": "submit_task",
            "run_id": run_id,
            "event_id": run_id + "-request",
            "finished_offset_ms": 900,
        }
        for run_id in ("job-a", "job-b")
    ]
    receipts = module.job_receipts(data, tmp_path, requests, origin, 30000, started)
    assert len(receipts) == 1
    assert receipts[0]["accepted_offset_ms"] == 125
    assert receipts[0]["compute_started_offset_ms"] == 200
    assert receipts[0]["result_ids"] == ["result-a"]
    assert module.read_jsonl(tmp_path / "incomplete-jobs.jsonl") == [
        {"run_id": "job-b", "status": "queued"}
    ]


@pytest.mark.parametrize("fails", [False, True])
def test_candidate_setup_deadline_is_separate_from_timed_http_budget_and_failures_remain(
    tmp_path,
    fails,
):
    import httpx

    module = probe_module()
    deadlines = []

    async def run():
        async def handle(request):
            deadlines.append(request.extensions["timeout"]["read"])
            if fails and request.url.path == "/api/screening/candidates":
                raise httpx.ReadTimeout("synthetic setup deadline", request=request)
            return httpx.Response(200, json={"batch_id": "synthetic-batch"})

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1",
            timeout=10,
            transport=httpx.MockTransport(handle),
        ) as client:
            if fails:
                with pytest.raises(httpx.ReadTimeout):
                    await module.setup_candidate_batch(client, tmp_path, {})
            else:
                assert await module.setup_candidate_batch(client, tmp_path, {}) == {
                    "batch_id": "synthetic-batch"
                }
            await module.require(client, "GET", "/api/notices")

    asyncio.run(run())
    assert deadlines == [300, 10]
    receipt = module.read_jsonl(tmp_path / "setup-operations.jsonl")[0]
    assert receipt["outcome"] == ("FAIL" if fails else "PASS")
    assert receipt["deadline_ms"] == 300000
    assert receipt["included_in_timed_capacity_metrics"] is False


@pytest.mark.parametrize("kind", ["notice", "material", "attachment"])
def test_inventory_rechecks_actual_fixed_bytes(loaded, kind):
    app, _client, _identities, _profile, data, manifest = loaded
    if kind == "notice":
        row = app.state.store.db.rows("SELECT raw_sha256 FROM nd_versions LIMIT 1")[0]
        path = data / "raw" / row["raw_sha256"]
    elif kind == "material":
        row = app.state.store.db.rows("SELECT blob_path FROM company_material_revisions LIMIT 1")[0]
        path = data / "private/company" / row["blob_path"]
    else:
        row = app.state.store.db.rows("SELECT raw_sha256 FROM nd_attachments LIMIT 1")[0]
        path = data / "attachments" / row["raw_sha256"]
    path.write_bytes(b"SYNTHETIC_CORRUPTION")
    with pytest.raises(ValueError, match="inventory hash mismatch"):
        inventory(data, manifest)


def test_seed_refuses_reusing_existing_database(loaded, tmp_path):
    app, _client, _identities, _profile, _data, manifest = loaded
    with pytest.raises(ValueError, match="new empty"):
        seed_notices(app.state.store, manifest, tmp_path)


def test_fake_or_unknown_identity_never_appears_as_twenty_real_accounts(tmp_path, unit_manifest):
    app = create_load_app(tmp_path, unit_manifest, "http://127.0.0.1")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/auth/login", follow_redirects=False)
        fields = parse_qs(urlsplit(response.headers["location"]).query)
        code = {
            "sub": "account-999",
            "nonce": fields["nonce"][0],
            "challenge": fields["code_challenge"][0],
        }
        response = client.get(
            "/api/auth/callback",
            params={"state": fields["state"][0], "code": json.dumps(code)},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/?login_error=1"
        assert not client.get("/api/auth/me").json()["authenticated"]
        assert app.state.store.db.rows("SELECT COUNT(*) n FROM enterprise_subjects")[0]["n"] == 0
