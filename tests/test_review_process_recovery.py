"""Four real SIGKILL windows over signed sessions, business SQLite and DBOS.

No HTTP approval is faked. RS256 IdP identities, UNIT model answers and authored
token counts are synthetic; the identity store, review service and workflows run.
"""

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest
from bidops.company.authorization import ToolContext
from bidops.enterprise.auth import IdentityService
from bidops.enterprise.backup import backup
from bidops.qualification.sources import canonical_json
from bidops.review.authorization import ReviewAuthorization
from bidops.review.repository import ReviewRepository
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.requests import Request
from test_company_access import ALICE
from test_enterprise import enterprise
from test_qualification_service import service as service
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tests/fixtures/review_recovery_worker.py"
PRIVATE_REASON = "SYNTHETIC_PRIVATE_REVIEW_REASON_PROCESS_748"
WINDOWS = [
    "review_committed_before_delivery",
    "review_sent_before_delivery_mark",
    "review_consumed_before_checkpoint",
]


def authenticated_session(db, settings, key, subject, roles):
    """Verify a genuine local RSA signature before the production session write."""
    now = int(time.time())
    nonce = "synthetic-review-process-nonce-" + subject
    claims = {
        "iss": settings.oidc_issuer,
        "sub": subject,
        "aud": settings.oidc_client_id,
        "iat": now,
        "exp": now + 240,
        "nonce": nonce,
        "name": "Synthetic process test identity",
        "bidops_roles": roles,
    }
    token = jwt.encode({"alg": "RS256", "kid": "review-process-key"}, claims, key)
    public = RSAKey.import_key(key.as_dict(private=False))
    verified = jwt.decode(token, public, algorithms=["RS256"])
    jwt.JWTClaimsRegistry(
        iss={"essential": True, "value": settings.oidc_issuer},
        sub={"essential": True, "value": subject},
        aud={"essential": True, "value": settings.oidc_client_id},
        nonce={"essential": True, "value": nonce},
        exp={"essential": True},
        iat={"essential": True},
    ).validate(verified.claims)
    request = Request({"type": "http", "session": {}, "headers": []})
    IdentityService(db, settings).establish(request, verified.claims)
    return request, hashlib.sha256(canonical_json(verified.claims).encode()).hexdigest()


def bootstrap(service, settings, tmp_path):
    screening, notice, profile, _, _ = service
    directory = tmp_path / "review-process-evidence"
    directory.mkdir(mode=0o700)
    key = RSAKey.generate_key(2048, parameters={"kid": "review-process-key"})
    identities, contexts, claims_hashes = {}, {}, {}
    auth = ReviewAuthorization(screening.repository.db, screening.sources)
    for who, roles in (("owner", ["analyst"]), ("reviewer", ["reviewer"])):
        request, digest = authenticated_session(
            screening.repository.db, enterprise(), key, "review-process-" + who, roles
        )
        identities[who] = dict(request.session)
        contexts[who] = auth.from_request(request)
        claims_hashes[who] = digest
        for ref in screening.sources.resolve_profile(
            ALICE, profile, notice.first_observed_at
        ).manifest.company_revisions:
            screening.sources.company.auth.grant(
                ALICE, ref.object_id, contexts[who].owner_id, ["read"]
            )
    target = directory / "bootstrap.json"
    target.write_text(
        json.dumps(
            {
                "database_url": settings.database_url,
                "data_dir": str(settings.data_dir),
                "request": {"profile_revision": profile, "project_id": notice.project_id},
                "sessions": identities,
            }
        )
    )
    target.chmod(0o600)
    (directory / "identity-proof.json").write_text(
        json.dumps(
            {
                "algorithm": "RS256",
                "verified_claim_sha256": claims_hashes,
                "principals": {who: context.owner_id for who, context in contexts.items()},
                "kind": "synthetic_signed_IdP_without_HTTP",
            },
            indent=2,
        )
    )
    return directory, contexts


def worker(directory, mode, window):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DBOS_", "OTEL_", "BIDOPS_", "DEEPSEEK_"))
    }
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT / "backend"), str(ROOT / "tests")])
    with (directory / f"{mode}.log").open("w") as log:
        try:
            return subprocess.run(
                [sys.executable, "-u", str(WORKER), str(directory), mode, "--window", window],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=55,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"{mode} process timed out; inspect {directory / f'{mode}.log'}")


def events(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]


def snapshot(service):
    db = service[0].repository.db
    return {
        table: db.rows(f"SELECT * FROM {table} ORDER BY rowid")
        for table in (
            "durable_jobs",
            "durable_job_state",
            "durable_job_inputs",
            "durable_job_sources",
            "durable_model_attempts",
            "qualification_results",
            "qualification_result_bodies",
            "agent_runs",
            "review_cases",
            "review_case_state",
            "review_revisions",
            "review_assignments",
            "review_events",
            "review_event_bodies",
            "review_outbox",
            "review_sources",
            "review_successors",
        )
    }


@pytest.mark.parametrize("window", WINDOWS)
def test_actual_review_sigkill_restarts_twice_without_duplicate_decisions_or_compute(
    service, settings, tmp_path, window
):
    directory, identities = bootstrap(service, settings, tmp_path)
    first = worker(directory, "crash", window)
    assert first.returncode == -signal.SIGKILL, (directory / "crash.log").read_text()
    log = events(directory)
    assert [item["window"] for item in log if item["event"] == "sigkill"] == [window]
    crashed = snapshot(service)
    assert len(crashed["durable_jobs"]) == len(crashed["review_cases"]) == 1
    assert len(crashed["qualification_results"]) == 2  # fixed candidate + checklist
    model_calls = [entry for entry in log if entry["event"] == "local_model_call"]
    assert len(model_calls) == 9
    assert len(crashed["agent_runs"]) == len(crashed["durable_model_attempts"]) == 9
    if window == "review_consumed_before_checkpoint":
        assert crashed["durable_job_state"][0]["status"] == "completed"
        closed = next(row for row in crashed["review_outbox"] if row["kind"] == "closed")
        assert closed["consumed_at"] is not None
    else:
        assert crashed["durable_job_state"][0]["status"] == "waiting_review"
        assert len(crashed["review_events"]) == 1
        assert crashed["review_outbox"][0]["delivered_at"] is None
        if window == "review_committed_before_delivery":
            assert crashed["review_outbox"][0]["consumed_at"] is None

    second = worker(directory, "recover", window)
    assert second.returncode == 0, (directory / "recover.log").read_text()
    recovered = snapshot(service)
    assert recovered["agent_runs"] == crashed["agent_runs"]
    assert recovered["durable_model_attempts"] == crashed["durable_model_attempts"]
    assert recovered["qualification_results"] == crashed["qualification_results"]
    assert recovered["qualification_result_bodies"] == crashed["qualification_result_bodies"]
    assert len(recovered["review_events"]) == len(recovered["review_outbox"]) == 9
    assert len({row["event_id"] for row in recovered["review_events"]}) == 9
    assert sum(row["event_kind"] == "close" for row in recovered["review_events"]) == 1
    assert all(row["consumed_at"] is not None for row in recovered["review_outbox"])
    assert all(row["state"] == "completed" for row in recovered["agent_runs"])
    assert sum(Decimal(str(row["estimated_cny"])) for row in recovered["agent_runs"]) == Decimal(
        "0.00252"
    )  # authored counts through the real ledger; actual charge is zero
    for event in recovered["review_events"]:
        expected_actor = identities["owner" if event["event_kind"] == "close" else "reviewer"]
        assert event["actor_id"] == expected_actor.owner_id
        assert event["authorization_context_id"] == expected_actor.token_hash
    bodies = [json.loads(row["body_json"]) for row in recovered["review_event_bodies"]]
    assert all(
        value["review_event"]["origin"] == "server_authenticated"
        for value in bodies
        if value["kind"] == "decision"
    )
    assert (
        service[0].repository.get(
            ToolContext(identities["owner"].owner_id),
            recovered["durable_job_state"][0]["result_id"],
        )["result"]["human_review_state"]
        == "unreviewed"
    )

    third = worker(directory, "read-saved", window)
    assert third.returncode == 0, (directory / "read-saved.log").read_text()
    assert snapshot(service) == recovered
    log = events(directory)
    assert [entry for entry in log if entry["event"] == "local_model_call"] == model_calls
    starts = [entry for entry in log if entry["event"] == "starting"]
    assert len({entry["pid"] for entry in starts}) == 3
    assert len({entry["application_version"] for entry in starts}) == 1
    assert all(
        entry["real_model_calls"] == 0 and entry["actual_cost_cny"] == "0" for entry in starts
    )
    after = json.loads((directory / "recover.json").read_text())
    reread = json.loads((directory / "read-saved.json").read_text())
    assert after == reread
    assert after["run_id"] != after["result_id"]
    assert after["status"] == "completed"
    assert len(after["native_workflows"]) == 2
    assert all(row[1] == "SUCCESS" for row in after["native_workflows"])
    assert any("human_review" in row[2] and row[3] and not row[4] for row in after["checkpoints"])
    native_path = settings.data_dir / "private/jobs/dbos.sqlite3"
    with closing(sqlite3.connect(native_path)) as conn:
        assert conn.execute("SELECT count(*) FROM workflow_status").fetchone()[0] == 2
    assert all(
        PRIVATE_REASON.encode() not in path.read_bytes()
        for path in native_path.parent.glob(native_path.name + "*")
    )
    bootstrap_secret = json.loads((directory / "bootstrap.json").read_text())["sessions"]
    all_logs = "".join(
        (directory / f"{mode}.log").read_text() for mode in ("crash", "recover", "read-saved")
    )
    assert PRIVATE_REASON not in all_logs
    assert all(session["login"] not in all_logs for session in bootstrap_secret.values())


def test_actual_supplement_link_sigkill_restarts_and_attaches_only_unreviewed_new_revision(
    service, settings, tmp_path
):
    directory, identities = bootstrap(service, settings, tmp_path)
    window = "review_successor_committed"
    first = worker(directory, "crash", window)
    assert first.returncode == -signal.SIGKILL, (directory / "crash.log").read_text()
    log = events(directory)
    assert [entry["window"] for entry in log if entry["event"] == "sigkill"] == [window]
    crashed = snapshot(service)
    assert len(crashed["durable_jobs"]) == 2
    assert len(crashed["review_cases"]) == len(crashed["review_revisions"]) == 1
    assert len(crashed["review_successors"]) == 1
    assert len(crashed["review_events"]) == len(crashed["review_outbox"]) == 2
    original_revision = crashed["review_revisions"][0]
    old_run = original_revision["run_id"]
    new_run = crashed["review_successors"][0]["run_id"]
    assert new_run != old_run
    assert (
        next(row for row in crashed["durable_job_state"] if row["run_id"] == old_run)["status"]
        == "waiting_review"
    )
    superseded = next(row for row in crashed["review_outbox"] if row["kind"] == "superseded")
    assert superseded["delivered_at"] is superseded["consumed_at"] is None
    old_decision = next(row for row in crashed["review_outbox"] if row["kind"] == "decision")
    assert old_decision["consumed_at"] is None and old_decision["frozen"] == 1

    second = worker(directory, "recover", window)
    assert second.returncode == 0, (directory / "recover.log").read_text()
    recovered = snapshot(service)
    assert len(recovered["durable_jobs"]) == 2
    assert len(recovered["qualification_results"]) == 3  # fixed candidate + old/new computation
    assert len(recovered["review_revisions"]) == 2
    assert recovered["review_revisions"][0] == original_revision
    assert len(recovered["review_successors"]) == 1
    assert recovered["review_successors"] == crashed["review_successors"]
    assert [row["event_kind"] for row in recovered["review_events"]] == [
        "decision",
        "successor_linked",
        "revision_attached",
    ]
    assert recovered["review_events"][:2] == crashed["review_events"]
    assert (
        next(row for row in recovered["review_outbox"] if row["kind"] == "superseded")[
            "consumed_at"
        ]
        is not None
    )
    old_decision = next(row for row in recovered["review_outbox"] if row["kind"] == "decision")
    assert old_decision["consumed_at"] is None and old_decision["frozen"] == 1
    for table, key in (
        ("qualification_results", "result_id"),
        ("qualification_result_bodies", "result_id"),
    ):
        fixed = {row[key]: row for row in recovered[table]}
        assert all(fixed[row[key]] == row for row in crashed[table])
    states = {row["run_id"]: row["status"] for row in recovered["durable_job_state"]}
    assert states == {old_run: "cancelled", new_run: "waiting_review"}
    assert len(recovered["durable_model_attempts"]) == len(recovered["agent_runs"]) == 18
    ledger = {row["run_id"]: row for row in recovered["agent_runs"]}
    for run_id in (old_run, new_run):
        attempts = [row for row in recovered["durable_model_attempts"] if row["run_id"] == run_id]
        assert len(attempts) == 9
        receipts = [ledger[row["ledger_id"]] for row in attempts]
        assert all(row["state"] == "completed" for row in receipts)
        assert sum(Decimal(str(row["estimated_cny"])) for row in receipts) == Decimal("0.00252")
    case = service[0].repository.db.rows("SELECT case_id FROM review_cases")[0]["case_id"]
    # The actual repository view rechecks the signed session and new source ACL.
    view = ReviewRepository(service[0].repository.db, service[0].sources).get(
        identities["reviewer"], case
    )
    assert view["status"] == "open"
    assert len(view["items"]) == 8
    assert all(
        item["latest_event"] is None and item["latest_decision"] is None and not item["effective"]
        for item in view["items"]
    )
    assert all(
        revision["result"]["human_review_state"] == "unreviewed" for revision in view["revisions"]
    )

    third = worker(directory, "read-saved", window)
    assert third.returncode == 0, (directory / "read-saved.log").read_text()
    assert snapshot(service) == recovered
    log = events(directory)
    assert len([entry for entry in log if entry["event"] == "local_model_call"]) == 18
    starts = [entry for entry in log if entry["event"] == "starting"]
    assert len({entry["pid"] for entry in starts}) == 3
    assert len({entry["application_version"] for entry in starts}) == 1
    assert all(
        entry["real_model_calls"] == 0 and entry["actual_cost_cny"] == "0" for entry in starts
    )
    after = json.loads((directory / "recover.json").read_text())
    assert after == json.loads((directory / "read-saved.json").read_text())
    assert after["all_new_items_unreviewed"] and after["unreviewed_item_count"] == 8
    assert len(after["native_workflows"]) == 4
    native_path = settings.data_dir / "private/jobs/dbos.sqlite3"
    assert all(
        PRIVATE_REASON.encode() not in path.read_bytes()
        for path in native_path.parent.glob(native_path.name + "*")
    )
    all_logs = "".join(
        (directory / f"{mode}.log").read_text() for mode in ("crash", "recover", "read-saved")
    )
    assert PRIVATE_REASON not in all_logs
    sessions = json.loads((directory / "bootstrap.json").read_text())["sessions"]
    assert all(session["login"] not in all_logs for session in sessions.values())

    # All three workers have stopped. Validate the v4 exact two-workflow-per-job
    # mapping against genuine SDK-generated checkpoints, including formal review.
    bundle = directory / "stopped-service-v4-bundle"
    backup(settings, bundle)
    metadata = json.loads((bundle / "manifest.json").read_text())
    assert metadata["version"] == 4
    mapping = metadata["durable_snapshot"]["mapping"]
    assert len(mapping["jobs"]) == 2 and len(mapping["workflows"]) == 4
    assert mapping["reviews"]["cases"] == [case]
    assert len(mapping["reviews"]["revisions"]) == 2
    assert len(mapping["reviews"]["events"]) == 3
    assert mapping["reviews"]["successor_run_ids"] == [new_run]
    assert PRIVATE_REASON not in (bundle / "manifest.json").read_text()
    assert snapshot(service) == recovered
