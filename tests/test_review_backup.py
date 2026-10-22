"""Actual paired SQLite/DBOS snapshots; all review records are synthetic fixtures."""

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest
from bidops.company import CompanyService
from bidops.company.authorization import CompanyError, transaction
from bidops.enterprise.backup import backup, restore, sha
from bidops.jobs import backup as jobs_backup
from bidops.qualification.repository import QualificationRepository
from bidops.qualification.sources import canonical_json
from bidops.review import backup as review_backup
from bidops.screening.schemas import ReviewEvent
from test_company_access import ALICE
from test_jobs_backup import add_calculation, add_checkpoint, attempt, running
from test_jobs_backup import case as case
from test_jobs_backup import system_template as system_template
from test_jobs_repository import assessment_group, review
from test_jobs_repository import jobs as jobs
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources
from test_review_repository import create_case, decide, request_materials, successor_job
from test_review_repository import reviews as reviews

SECRET_REVIEW = "SYNTHETIC_PRIVATE_FORMAL_REVIEW_REASON_681"


def test_actual_authenticated_review_inventory_matches_server_bound_items(reviews):
    repo, alice, _, _, _, job, _ = reviews
    case = decide(reviews, create_case(reviews))
    closed = repo.close(
        alice,
        case["case_id"],
        expected_version=case["review_version"],
        idempotency_key="explicit-close",
        reason="Synthetic owner explicitly closed the review",
    )
    with closing(sqlite3.connect(repo.db.engine.url.database)) as conn:
        summary = review_backup.inventory(conn)
    assert summary["cases"] == [case["case_id"]]
    assert summary["revisions"] == [case["head_revision_id"]]
    assert summary["events"] == sorted(event["event_id"] for event in closed["events"])
    assert summary["successor_run_ids"] == []
    assert job["status"] == "waiting_review"


def test_complete_successor_attachment_preserves_exact_old_and_new_inventory(reviews):
    repo, alice, _, _, _, old_job, job_case = reviews
    case = request_materials(reviews)
    job, params = successor_job(reviews, case)
    with transaction(repo.db) as conn:
        repo.link_successor(conn, alice, case["case_id"], **params)
    linked = repo.get(alice, case["case_id"])
    cancelling = job_case[0].request_cancel(ALICE, old_job["run_id"], old_job["state_version"])
    job_case[0].transition(old_job["run_id"], cancelling["state_version"], "cancelled")
    job = job_case[0].transition(job["run_id"], 0, "running")
    QualificationRepository(repo.db, repo.sources).save(
        ALICE,
        job["manifest"],
        {
            "kind": "checklist",
            "state": "completed",
            "run_id": "successor-result",
            "result_revision": "successor-v2",
            "groups": [assessment_group(job_case[0], job, "successor-v2")],
        },
        result_id="successor-result",
        provenance={"business_run_id": job["run_id"]},
    )
    job_case[0].transition(
        job["run_id"],
        job["state_version"],
        "waiting_review",
        result_id="successor-result",
        result_revision="successor-v2",
    )
    attached = repo.attach_revision(
        alice,
        case["case_id"],
        run_id=job["run_id"],
        expected_version=linked["review_version"],
        idempotency_key="attach-successor",
        reason="Synthetic new result explicitly attached",
    )
    with closing(sqlite3.connect(repo.db.engine.url.database)) as conn:
        summary = review_backup.inventory(conn)
    assert summary["revisions"] == sorted(r["revision_id"] for r in attached["revisions"])
    assert summary["successor_run_ids"] == [job["run_id"]]
    assert not attached["items"][0]["effective"]
    assert len(attached["revisions"]) == 2


@pytest.mark.parametrize("result_id", ["review-batch", "fixed-business-result"])
def test_standalone_inventory_rejects_missing_live_candidate_or_revision_body(reviews, result_id):
    repo = reviews[0]
    decide(reviews, create_case(reviews))
    repo.db.execute(
        "DELETE FROM qualification_result_bodies WHERE result_id=:id", {"id": result_id}
    )
    with closing(sqlite3.connect(repo.db.engine.url.database)) as conn:
        with pytest.raises(ValueError, match="Review backup result body is missing"):
            review_backup.inventory(conn)


def test_actual_job_reauthorization_cannot_restore_review_assignment_or_decision(reviews):
    repo, alice, bob, _, _, job, job_case = reviews
    case = decide(reviews, create_case(reviews))
    old_epoch = case["review_epoch"]
    event_id = case["events"][0]["event_id"]
    report = review_backup.quarantine_after_restore(repo.db.engine.url.database, now=repo.clock())
    assert report == {"quarantined_review_cases": 1, "review_assignments_revoked": 1}
    # Existing job/result grants are intentionally still valid here. Even that
    # stronger capability must not undo the separate review restore barrier.
    assert (
        job_case[0].reauthorize(ALICE, job["run_id"], expected_version=job["state_version"])[
            "run_id"
        ]
        == job["run_id"]
    )
    quarantined = repo.get(alice, case["case_id"])
    assert quarantined["quarantined"] == 1
    assert quarantined["allowed_actions"] == ["reauthorize"]
    assert quarantined["candidate_batch"] is None
    assert quarantined["events"] == quarantined["revisions"] == quarantined["items"] == []
    with pytest.raises(CompanyError):
        repo.get(bob, case["case_id"])
    with pytest.raises(CompanyError, match="review_event_not_current"):
        repo.consume_event(event_id, run_id=job["run_id"], review_epoch=old_epoch)
    restored = repo.reauthorize(
        alice,
        case["case_id"],
        expected_version=case["review_version"] + 1,
        idempotency_key="restore-case-separately",
        reason="Synthetic owner independently reauthorized the review",
    )
    assert restored["review_epoch"] != old_epoch
    assert not restored["items"][0]["effective"]
    assert repo.db.rows("SELECT * FROM review_assignments") == []
    assert repo.pending_events() == []
    with pytest.raises(CompanyError):
        repo.get(bob, case["case_id"])


@pytest.fixture
def review_case(case):
    repo, job, system, _ = case
    current = running(case)
    attempt(case, unknown=True)
    revision = "review-result-v1"
    result_repo = QualificationRepository(repo.db, repo.sources)
    group = assessment_group(repo, job, revision)
    result = result_repo.save(
        ALICE,
        job["manifest"],
        {
            "kind": "checklist",
            "state": "completed",
            "run_id": "review-result",
            "result_revision": revision,
            "groups": [group],
        },
        result_id="review-result",
        provenance={"business_run_id": job["run_id"]},
    )
    result_repo.save(
        ALICE,
        job["manifest"],
        {"kind": "candidate_batch", "items": [{"project_id": job["manifest"]["project_id"]}]},
        result_id="review-batch",
    )
    waiting = repo.transition(
        job["run_id"],
        current["state_version"],
        "waiting_review",
        result_id=result["result_id"],
        result_revision=revision,
    )
    add_calculation(system, job["run_id"])
    event = review(waiting, event_id="review-event", origin="server_authenticated")
    event["actor"]["principal_id"] = "alice"
    event["actor"]["authorization_context_id"] = "a" * 64
    event["command"]["reason"] = SECRET_REVIEW
    event["command"]["idempotency_key"] = "decision-1"
    event = ReviewEvent.model_validate(event)
    envelope = {
        "kind": "decision",
        "reason": SECRET_REVIEW,
        "review_event": event.model_dump(mode="json"),
        "condition_id": "synthetic-condition",
        "item_sha256": hashlib.sha256(
            canonical_json(
                {
                    "condition": group["assessment"]["conditions"][0],
                    "agent_assessment": group["assessment"]["assessments"][0],
                }
            ).encode()
        ).hexdigest(),
    }
    body = canonical_json(envelope)
    event_sha = hashlib.sha256(body.encode()).hexdigest()
    now = time.time()
    repo.db.execute(
        "INSERT INTO review_cases VALUES ('case-1','alice','review-batch','create-1',:sha,:at)",
        {"sha": "c" * 64, "at": now},
    )
    repo.db.execute(
        "INSERT INTO review_case_state VALUES "
        "('case-1','revision-1',1,'open','original-epoch',0,'live',NULL,:at)",
        {"at": now},
    )
    repo.db.execute(
        "INSERT INTO review_revisions VALUES "
        "('revision-1','case-1',NULL,:run,'review-result',:revision,:manifest,NULL,:at)",
        {
            "run": job["run_id"],
            "revision": revision,
            "manifest": job["inputs_manifest_sha256"],
            "at": now,
        },
    )
    repo.db.execute("INSERT INTO review_assignments VALUES ('case-1','alice')")
    repo.db.execute(
        "INSERT INTO review_sources SELECT 'case-1',object_id,version_id,sha256 "
        "FROM durable_job_sources WHERE run_id=:run",
        {"run": job["run_id"]},
    )
    repo.db.execute(
        "INSERT INTO review_events VALUES "
        "('review-event','case-1','revision-1',:assessment,'synthetic-condition','decision',"
        "'alice',:auth,:at,1,:request,:sha,'decision-1',NULL,'original-epoch')",
        {
            "assessment": event.assessment_id,
            "auth": event.actor.authorization_context_id,
            "at": event.occurred_at.timestamp(),
            "request": "d" * 64,
            "sha": event_sha,
        },
    )
    repo.db.execute("INSERT INTO review_event_bodies VALUES ('review-event',:body)", {"body": body})
    repo.db.execute(
        "INSERT INTO review_outbox VALUES "
        "('review-event',:run,'decision','original-epoch',0,NULL,NULL,0,NULL)",
        {"run": job["run_id"]},
    )
    return case


def metadata(bundle):
    return json.loads((bundle / "manifest.json").read_text())


def rehash(bundle):
    value = metadata(bundle)
    value["files"]["bidops.sqlite3"] = sha(bundle / "bidops.sqlite3")
    (bundle / "manifest.json").write_text(json.dumps(value))


def test_v4_review_tables_and_private_body_hashes_are_in_the_paired_closure(
    review_case, settings, tmp_path
):
    bundle = tmp_path / "bundle"
    backup(settings, bundle)
    value = metadata(bundle)
    mapping = value["durable_snapshot"]["mapping"]
    assert value["version"] == 4
    assert mapping["reviews"] == {
        "cases": ["case-1"],
        "revisions": ["revision-1"],
        "events": ["review-event"],
        "successor_run_ids": [],
    }
    assert {
        "review_cases",
        "review_case_state",
        "review_events",
        "review_event_bodies",
        "review_assignments",
        "review_outbox",
        "review_sources",
        "review_successors",
    } <= mapping["business_table_sha256"].keys()
    assert SECRET_REVIEW not in (bundle / "manifest.json").read_text()
    assert len(mapping["workflows"]) == 2
    assert {r["name"] for r in mapping["workflows"]} == {
        jobs_backup.WORKFLOW_NAME,
        jobs_backup.CALCULATION_WORKFLOW_NAME,
    }


@pytest.mark.parametrize("status", ["open", "awaiting_materials", "recomputing", "closed"])
def test_restore_rotates_review_epoch_revokes_assignment_freezes_delivery_and_keeps_history(
    review_case, settings, tmp_path, monkeypatch, status
):
    repo, _, _, _ = review_case
    repo.db.execute("UPDATE review_case_state SET status=:status", {"status": status})
    events = repo.db.rows("SELECT * FROM review_events")
    bodies = repo.db.rows("SELECT * FROM review_event_bodies")
    bundle, destination = tmp_path / "bundle", tmp_path / "restored"
    backup(settings, bundle)

    def no_launch(*args, **kwargs):
        pytest.fail("restore launched a worker")

    from dbos import DBOS

    monkeypatch.setattr(DBOS, "launch", no_launch)
    report = restore(bundle, destination)
    assert report["quarantined_review_cases"] == report["review_assignments_revoked"] == 1
    with closing(sqlite3.connect(destination / "bidops.sqlite3")) as conn:
        state = conn.execute(
            "SELECT status,review_epoch,quarantined,review_version FROM review_case_state"
        ).fetchone()
        assert state[0] == status and state[1] != "original-epoch" and state[2:] == (1, 2)
        assert conn.execute("SELECT * FROM review_assignments").fetchall() == []
        assert conn.execute("SELECT frozen FROM review_outbox").fetchall() == [(1,)]
        assert [
            dict(zip(events[0], r, strict=True))
            for r in conn.execute("SELECT * FROM review_events")
        ] == events
        assert [
            dict(zip(bodies[0], r, strict=True))
            for r in conn.execute("SELECT * FROM review_event_bodies")
        ] == bodies
    # Restoring the same backup again never reuses the first restoration's authority.
    second = tmp_path / "restored-again"
    restore(bundle, second)
    with closing(sqlite3.connect(second / "bidops.sqlite3")) as conn:
        assert conn.execute("SELECT review_epoch FROM review_case_state").fetchone()[0] != state[1]


@pytest.mark.parametrize(
    "change",
    [
        "DELETE FROM review_assignments",
        "UPDATE review_case_state SET status='closed'",
        "UPDATE review_outbox SET consumed_at=1",
        "DELETE FROM review_event_bodies",
        "DELETE FROM review_sources",
    ],
)
def test_review_change_and_recomputed_single_database_hash_cannot_bypass_pairing(
    review_case, settings, tmp_path, change
):
    bundle = tmp_path / "bundle"
    backup(settings, bundle)
    with closing(sqlite3.connect(bundle / "bidops.sqlite3")) as conn, conn:
        conn.execute(change)
    rehash(bundle)
    with pytest.raises(ValueError):
        restore(bundle, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
    assert not list(tmp_path.glob(".bidops-restore-*"))


@pytest.mark.parametrize(
    "change",
    [
        "DELETE FROM review_event_bodies",
        "DELETE FROM review_sources",
        "UPDATE review_outbox SET review_epoch='foreign-epoch'",
        "UPDATE review_case_state SET head_revision_id='foreign-revision'",
    ],
)
def test_invalid_live_review_closure_refuses_backup_without_partial_bundle(
    review_case, settings, tmp_path, change
):
    review_case[0].db.execute(change)
    with pytest.raises(ValueError):
        backup(settings, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_restore_expiry_erases_review_prose_and_native_history_keeps_event_receipts_and_ledger(
    review_case, settings, tmp_path, monkeypatch
):
    import bidops.company as company
    import bidops.enterprise.backup as module

    repo, job, system, _ = review_case
    add_checkpoint(system, job["run_id"])
    deadline = time.time() + 60
    repo.db.execute("UPDATE company_objects SET retention_until=:at", {"at": deadline})
    events = repo.db.rows("SELECT * FROM review_events")
    ledger = repo.db.rows("SELECT * FROM agent_runs")
    bundle, destination = tmp_path / "bundle", tmp_path / "restored"
    backup(settings, bundle)
    future = deadline + 1
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: future))
    monkeypatch.setattr(
        company, "CompanyService", lambda db, path: CompanyService(db, path, clock=lambda: future)
    )
    restore(bundle, destination)
    with closing(sqlite3.connect(destination / "bidops.sqlite3")) as conn:
        assert conn.execute("SELECT count(*) FROM review_event_bodies").fetchone()[0] == 0
        assert conn.execute("SELECT purge_state FROM review_case_state").fetchone()[0] == "purged"
        assert conn.execute("SELECT frozen FROM review_outbox").fetchall() == [(1,)]
        assert [
            dict(zip(events[0], r, strict=True))
            for r in conn.execute("SELECT * FROM review_events")
        ] == events
        assert [
            dict(zip(ledger[0], r, strict=True)) for r in conn.execute("SELECT * FROM agent_runs")
        ] == ledger
    with closing(sqlite3.connect(destination / jobs_backup.SYSTEM_DATABASE)) as conn:
        assert conn.execute("SELECT count(*) FROM workflow_status").fetchone()[0] == 0
    for path in (destination / "bidops.sqlite3", destination / jobs_backup.SYSTEM_DATABASE):
        assert SECRET_REVIEW.encode() not in path.read_bytes()


def test_legacy_database_without_review_tables_keeps_original_v4_mapping_contract(case):
    # A restored B05 snapshot is verified before migration adds the empty B06 tables.
    with closing(sqlite3.connect(":memory:")) as conn:
        assert review_backup.inventory(conn) is None
        conn.execute("CREATE TABLE review_cases(case_id TEXT)")
        with pytest.raises(ValueError, match="schema is incomplete"):
            review_backup.inventory(conn)
