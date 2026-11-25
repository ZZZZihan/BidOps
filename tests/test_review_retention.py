"""Review prose erasure follows source dependencies without inventing job dependencies."""

import copy
import sqlite3
from contextlib import closing
from datetime import datetime

import pytest
from bidops.company.authorization import CompanyError, transaction
from bidops.company.retention import prune_company_materials
from bidops.review.retention import prune_reviews
from test_company_access import ALICE, material
from test_jobs_backup import case as case
from test_jobs_backup import system_template as system_template
from test_jobs_repository import jobs as jobs
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources
from test_review_backup import SECRET_REVIEW
from test_review_backup import review_case as review_case
from test_review_repository import create_case, decide
from test_review_repository import reviews as reviews


def test_source_expiry_erases_all_event_prose_before_private_unlink_and_keeps_receipts(
    review_case, monkeypatch
):
    repo, _, system, doc = review_case
    events = repo.db.rows("SELECT * FROM review_events")
    repo.db.execute(
        "UPDATE company_objects SET retention_until=1 WHERE object_id=:id", {"id": doc["object_id"]}
    )
    original = repo.sources.company.materials.path

    def cannot_unlink(path):
        # Even a private file failure occurs after all derived review prose is denied.
        assert repo.db.rows("SELECT * FROM review_event_bodies") == []
        raise OSError("synthetic private unlink failure")

    monkeypatch.setattr(repo.sources.company.materials, "path", cannot_unlink)
    report = prune_company_materials(repo.sources.company)
    assert report["status"] == "FAIL" and report["purged_review_case_ids"] == ["case-1"]
    assert repo.db.rows("SELECT * FROM review_events") == events
    assert repo.db.rows("SELECT * FROM review_assignments") == []
    assert repo.db.rows("SELECT frozen FROM review_outbox") == [{"frozen": 1}]
    assert repo.db.rows("SELECT purge_state FROM review_case_state") == [{"purge_state": "purged"}]
    with closing(sqlite3.connect(system)) as conn:
        assert conn.execute("SELECT count(*) FROM workflow_status").fetchone()[0] == 0
    monkeypatch.setattr(repo.sources.company.materials, "path", original)
    assert prune_company_materials(repo.sources.company)["status"] == "PASS"


def test_review_only_dependency_expiry_preserves_independent_original_job_and_result(review_case):
    repo, job, system, _ = review_case
    # Simulate a previously linked supplemental object that has since disappeared.
    # Its case-wide prose dependency must not erase this older calculation.
    repo.db.execute(
        "INSERT INTO review_sources VALUES ('case-1','expired-supplement','supplement-v1',:sha)",
        {"sha": "f" * 64},
    )
    jobs_before = repo.db.rows("SELECT * FROM durable_job_state")
    results_before = repo.db.rows("SELECT * FROM qualification_result_bodies")
    inputs_before = repo.db.rows("SELECT * FROM durable_job_inputs")
    with closing(sqlite3.connect(system)) as conn:
        native_before = conn.execute("SELECT workflow_uuid,status FROM workflow_status").fetchall()
    report = prune_reviews(repo.db, repo.auth.clock())
    assert report["purged_case_ids"] == ["case-1"]
    assert repo.db.rows("SELECT * FROM review_event_bodies") == []
    assert repo.db.rows("SELECT * FROM durable_job_state") == jobs_before
    assert repo.db.rows("SELECT * FROM qualification_result_bodies") == results_before
    assert repo.db.rows("SELECT * FROM durable_job_inputs") == inputs_before
    assert repo.get_system(job["run_id"])["status"] == "waiting_review"
    with closing(sqlite3.connect(system)) as conn:
        assert (
            conn.execute("SELECT workflow_uuid,status FROM workflow_status").fetchall()
            == native_before
        )


def test_purged_review_erasure_retries_without_rewriting_history_or_versions(review_case):
    repo = review_case[0]
    repo.db.execute("UPDATE company_objects SET retention_until=1")
    prune_reviews(repo.db, repo.auth.clock())
    state = repo.db.rows("SELECT * FROM review_case_state")
    events = repo.db.rows("SELECT * FROM review_events")
    # Simulate an old pending body left by an interrupted/mixed maintenance caller.
    repo.db.execute(
        "INSERT INTO review_event_bodies VALUES ('review-event',:body)", {"body": SECRET_REVIEW}
    )
    repo.db.execute("INSERT INTO review_assignments VALUES ('case-1','alice')")
    repo.db.execute("UPDATE review_outbox SET frozen=0")
    result = prune_reviews(repo.db, repo.auth.clock())
    assert result["purged_case_ids"] == ["case-1"]
    assert repo.db.rows("SELECT * FROM review_event_bodies") == []
    assert repo.db.rows("SELECT * FROM review_assignments") == []
    assert repo.db.rows("SELECT frozen FROM review_outbox") == [{"frozen": 1}]
    assert repo.db.rows("SELECT * FROM review_case_state") == state
    assert repo.db.rows("SELECT * FROM review_events") == events


@pytest.mark.parametrize("failure", ["missing_database", "native_delete"])
def test_native_cleanup_failure_cannot_leave_formal_review_prose_readable(
    review_case, monkeypatch, failure
):
    from dbos import DBOSClient

    repo, _, system, _ = review_case
    repo.db.execute("UPDATE company_objects SET retention_until=1")
    if failure == "missing_database":
        system.unlink()
    else:

        def fail(*args, **kwargs):
            raise RuntimeError(SECRET_REVIEW)

        monkeypatch.setattr(DBOSClient, "delete_workflow", fail)
    report = prune_company_materials(repo.sources.company)
    assert report["status"] == "FAIL"
    assert SECRET_REVIEW not in str(report)
    assert repo.db.rows("SELECT * FROM review_event_bodies") == []
    assert repo.db.rows("SELECT purge_state FROM review_case_state") == [{"purge_state": "purged"}]


def test_fresh_case_is_not_erased(review_case):
    repo = review_case[0]
    bodies = repo.db.rows("SELECT * FROM review_event_bodies")
    states = repo.db.rows("SELECT * FROM review_case_state")
    report = prune_reviews(repo.db, repo.auth.clock())
    assert report == {"status": "PASS", "purged_case_ids": [], "failure_ids": []}
    assert repo.db.rows("SELECT * FROM review_event_bodies") == bodies
    assert repo.db.rows("SELECT * FROM review_case_state") == states


def test_actual_review_commands_and_worker_cannot_recreate_expired_prose(reviews):
    repo, alice, _, _, _, job, job_case = reviews
    case = decide(reviews, create_case(reviews))
    event_id = case["events"][0]["event_id"]
    metadata = repo.db.rows("SELECT * FROM review_events")
    ledger = repo.db.rows("SELECT * FROM agent_runs")
    repo.db.execute(
        "UPDATE company_objects SET retention_until=1 WHERE object_id=:id",
        {"id": job_case[3]["object_id"]},
    )
    assert prune_reviews(repo.db, repo.clock())["purged_case_ids"] == [case["case_id"]]
    with pytest.raises(CompanyError, match="review_event_not_current"):
        repo.consume_event(event_id, run_id=job["run_id"], review_epoch=case["review_epoch"])
    for operation in (
        lambda: decide(reviews, case),
        lambda: repo.reauthorize(
            alice,
            case["case_id"],
            expected_version=case["review_version"] + 1,
            idempotency_key="cannot-restore-expired",
            reason="Synthetic retry after expiry",
        ),
    ):
        with pytest.raises(CompanyError):
            operation()
    assert repo.db.rows("SELECT * FROM review_event_bodies") == []
    assert repo.db.rows("SELECT * FROM review_events") == metadata
    assert repo.db.rows("SELECT * FROM agent_runs") == ledger
    assert repo.pending_events() == []


def test_actual_linked_successor_expiry_does_not_purge_original_job(reviews):
    repo, alice, _, _, _, original_job, job_case = reviews
    case = decide(
        reviews,
        create_case(reviews),
        command={
            "decision": "request_materials",
            "expected_result_revision": "result-v1",
            "reason": "Synthetic request for an additional certificate",
            "idempotency_key": "request-supplement",
        },
    )
    supplement = material(repo.sources.company, data=b"SYNTHETIC supplemental evidence")
    supplement_ref = {key: supplement[key] for key in ("object_id", "version_id", "sha256")}
    manifest = original_job["manifest"]
    bundle = repo.sources.resolve(
        ALICE,
        manifest["profile_revision"],
        manifest["project_id"],
        datetime.fromisoformat(manifest["as_of"]),
        [supplement_ref],
    )
    prepared = copy.deepcopy(job_case[2])
    prepared["manifest"] = bundle.manifest.model_dump(mode="json")
    successor = job_case[0].get_or_create(
        ALICE,
        idempotency_key="supplemental-job",
        request={**job_case[1], "additional_material_revisions": [supplement_ref]},
        prepare=lambda: prepared,
    )
    with transaction(repo.db) as conn:
        repo.link_successor(
            conn,
            alice,
            case["case_id"],
            expected_version=case["review_version"],
            revision_id=case["head_revision_id"],
            trigger_event_id=case["events"][0]["event_id"],
            new_job_record=successor,
            idempotency_key="link-supplement",
            reason="Synthetic supplemental job linked",
        )
    original_inputs = repo.db.rows(
        "SELECT * FROM durable_job_inputs WHERE run_id=:run", {"run": original_job["run_id"]}
    )
    original_result = repo.db.rows("SELECT * FROM qualification_result_bodies")
    metadata = repo.db.rows("SELECT * FROM review_events")
    repo.db.execute(
        "UPDATE company_objects SET retention_until=1 WHERE object_id=:id",
        {"id": supplement["object_id"]},
    )
    assert prune_reviews(repo.db, repo.clock())["purged_case_ids"] == [case["case_id"]]
    assert job_case[0].prune()["purged_ids"] == [successor["run_id"]]
    assert repo.db.rows("SELECT * FROM review_event_bodies") == []
    assert repo.db.rows("SELECT * FROM review_events") == metadata
    assert repo.db.rows("SELECT * FROM qualification_result_bodies") == original_result
    assert (
        repo.db.rows(
            "SELECT * FROM durable_job_inputs WHERE run_id=:run", {"run": original_job["run_id"]}
        )
        == original_inputs
    )
    assert job_case[0].get(ALICE, original_job["run_id"])["status"] == "waiting_review"
