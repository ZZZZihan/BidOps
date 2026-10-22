"""Real SQLite, private source ACLs, authenticated sessions; no external services."""

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from bidops.company.authorization import CompanyError, transaction
from bidops.enterprise.auth import digest
from bidops.qualification.candidates import build_batch
from bidops.qualification.repository import QualificationRepository
from bidops.qualification.sources import ScreeningInputManifest
from bidops.review import HumanContext, ReviewRepository
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_company_access import ALICE, material, payload
from test_jobs_repository import assessment_group, running
from test_jobs_repository import jobs as jobs
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources


def session(db, owner, roles, *, key=None):
    now = time.time()
    token_hash = digest(key or (owner + "-review-login"))
    db.execute(
        "UPDATE enterprise_subjects SET roles_json=:roles WHERE owner_id=:id",
        {"roles": json.dumps(roles), "id": owner},
    )
    db.execute(
        "INSERT INTO enterprise_logins VALUES (:hash,:owner,:name,:roles,'csrf',:until)",
        {
            "hash": token_hash,
            "owner": owner,
            "name": owner,
            "roles": json.dumps(roles),
            "until": now + 10000,
        },
    )
    db.execute(
        "INSERT INTO enterprise_login_identities "
        "(token_hash,issuer,subject,sid,authenticated_at,fresh_until) "
        "VALUES (:hash,'https://unit.example',:owner,NULL,:now,:until)",
        {"hash": token_hash, "owner": owner, "now": now, "until": now + 10000},
    )
    return HumanContext(owner, token_hash)


@pytest.fixture
def reviews(jobs):
    job = running(jobs)
    job_repo = jobs[0]
    repo = ReviewRepository(job_repo.db, job_repo.sources)
    alice = session(repo.db, "alice", ["analyst"])
    bob = session(repo.db, "bob", ["reviewer"])
    admin = session(repo.db, "admin", ["admin"])
    viewer = session(repo.db, "viewer", ["viewer"])
    manifest = job["manifest"]
    adapter = repo.sources
    QualificationRepository(repo.db, adapter).save(
        ALICE,
        manifest,
        {
            "kind": "checklist",
            "state": "completed",
            "run_id": "fixed-business-result",
            "result_revision": "result-v1",
            "groups": [assessment_group(job_repo, job, "result-v1")],
        },
        result_id="fixed-business-result",
        provenance={"business_run_id": job["run_id"]},
    )
    job = job_repo.transition(
        job["run_id"],
        job["state_version"],
        "waiting_review",
        result_id="fixed-business-result",
        result_revision="result-v1",
    )
    bundle = adapter.resolve(
        ALICE,
        manifest["profile_revision"],
        manifest["project_id"],
        datetime.fromisoformat(manifest["as_of"]),
    )
    batch = build_batch(adapter, bundle, {})
    QualificationRepository(repo.db, adapter).save(ALICE, manifest, batch, result_id="review-batch")
    for ref in bundle.manifest.company_revisions:
        adapter.company.auth.grant(ALICE, ref.object_id, "bob", ["read"])
    return repo, alice, bob, admin, viewer, job, jobs


def create_case(data, **overrides):
    repo, alice, _, _, _, job, _ = data
    return repo.create(
        alice,
        **{
            "run_id": job["run_id"],
            "candidate_batch_id": "review-batch",
            "idempotency_key": "create-review",
            "reviewer_ids": ["bob"],
            **overrides,
        },
    )


def decide(data, case, *, actor=None, **overrides):
    repo, _, bob, *_ = data
    item = case["items"][0]
    revision = next(value for value in case["revisions"] if value["is_head"])
    params = {
        "revision_id": case["head_revision_id"],
        "assessment_id": item["assessment_id"],
        "condition_id": item["condition_id"],
        "expected_version": case["review_version"],
        "command": {
            "decision": "confirm",
            "expected_result_revision": revision["result_revision"],
            "reason": "Synthetic reviewer checked the fixed original evidence",
            "idempotency_key": "d1",
        },
    }
    params.update(overrides)
    return repo.decide(actor or bob, case["case_id"], **params)


def error(code, status=None):
    caught = pytest.raises(CompanyError, match=code)
    return caught


def test_case_projects_real_result_and_full_candidate_without_model_raw(reviews):
    case = create_case(reviews)
    assert case["review_version"] == 0
    assert case["items"][0]["agent_assessment"]["status"] == "information_insufficient"
    assert case["items"][0]["latest_decision"] is None
    assert case["candidate_batch"]["result"]["items"]
    assert "raw_model_outputs" not in json.dumps(case)
    assert "decide" not in case["allowed_actions"]  # owner is not a reviewer
    bob_view = reviews[0].get(reviews[2], case["case_id"])
    assert "decide" in bob_view["allowed_actions"]
    assert "assign" not in bob_view["allowed_actions"]


def test_case_create_replays_and_changed_key_payload_conflicts(reviews):
    first = create_case(reviews)
    assert create_case(reviews)["case_id"] == first["case_id"]
    with error("review_idempotency_conflict"):
        create_case(reviews, reviewer_ids=[])
    with error("review_job_already_linked"):
        create_case(reviews, idempotency_key="new-key")


def test_human_event_preserves_agent_and_exact_authenticated_actor(reviews):
    case = create_case(reviews)
    original = copy.deepcopy(case["revisions"][0]["result"])
    decided = decide(reviews, case)
    event = decided["events"][0]
    assert event["review_event"]["origin"] == "server_authenticated"
    assert event["review_event"]["actor"] == {
        "actor_kind": "human",
        "principal_id": "bob",
        "authorization_context_id": reviews[2].token_hash,
    }
    assert decided["revisions"][0]["result"] == original
    assert decided["items"][0]["latest_decision"] == "confirm"
    assert decided["items"][0]["effective"]
    assert decided["review_version"] == 1


def test_replay_happens_before_cas_and_changed_reason_is_conflict(reviews):
    case = create_case(reviews)
    decided = decide(reviews, case)
    assert decide(reviews, case)["events"] == decided["events"]  # old version is a replay
    command = copy.deepcopy(decided["events"][0]["review_event"]["command"])
    command["reason"] = "Changed reason on same key"
    with error("review_idempotency_conflict"):
        decide(reviews, case, command=command)
    assert len(reviews[0].get(reviews[1], case["case_id"])["events"]) == 1


def test_correction_appends_and_must_target_latest_same_item(reviews):
    case = decide(reviews, create_case(reviews))
    command = {
        "decision": "reject",
        "expected_result_revision": case["revisions"][0]["result_revision"],
        "reason": "Corrected after checking evidence",
        "idempotency_key": "correction",
    }
    with error("review_supersedes_required"):
        decide(reviews, case, command=command)
    corrected = decide(
        reviews, case, command=command, supersedes_event_id=case["events"][0]["event_id"]
    )
    assert len(corrected["events"]) == 2
    assert corrected["events"][0]["reason"] == case["events"][0]["reason"]
    assert corrected["items"][0]["latest_decision"] == "reject"
    with error("review_supersedes_required"):
        decide(
            reviews,
            corrected,
            command={**command, "idempotency_key": "wrong-parent"},
            supersedes_event_id=case["events"][0]["event_id"],
        )


def test_two_real_reviewers_same_version_only_one_wins(reviews):
    repo, alice, bob, _, _, _, _ = reviews
    # Alice explicitly obtains the reviewer role AND assignment, not ownership privilege.
    repo.db.execute(
        'UPDATE enterprise_subjects SET roles_json=\'["analyst","reviewer"]\' '
        "WHERE owner_id='alice'"
    )
    alice2 = session(repo.db, "alice", ["analyst", "reviewer"], key="alice-new-review")
    case = create_case(reviews, reviewer_ids=["alice", "bob"])
    barrier = Barrier(2)

    def submit(actor):
        barrier.wait()
        try:
            return decide(reviews, case, actor=actor)["review_version"]
        except CompanyError as exc:
            return exc.status_code, exc.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, [alice2, bob]))
    assert sorted(str(value) for value in results) == ["(409, 'review_version_conflict')", "1"]
    assert len(repo.get(alice, case["case_id"])["events"]) == 1


@pytest.mark.parametrize(
    "field,value",
    [("assessment_id", "invented"), ("condition_id", "invented"), ("revision_id", "invented")],
)
def test_forged_item_targets_reject(reviews, field, value):
    with pytest.raises(CompanyError) as caught:
        decide(reviews, create_case(reviews), **{field: value})
    assert caught.value.status_code == 409


@pytest.mark.parametrize(
    "table,column",
    [
        ("review_cases", "owner_id"),
        ("review_revisions", "result_revision"),
        ("review_events", "actor_id"),
        ("review_event_bodies", "body_json"),
        ("review_sources", "sha256"),
    ],
)
def test_immutable_history_cannot_be_overwritten(reviews, table, column):
    decide(reviews, create_case(reviews))
    with pytest.raises(IntegrityError, match="immutable"):
        reviews[0].db.execute(f"UPDATE {table} SET {column}='rewritten'")


def test_close_requires_each_item_and_separate_owner_action(reviews):
    repo, alice, bob, *_ = reviews
    case = create_case(reviews)
    with error("review_not_fully_decided"):
        repo.close(
            alice, case["case_id"], expected_version=0, idempotency_key="close", reason="Reviewed"
        )
    case = decide(reviews, case)
    with error("review_manage_denied"):
        repo.close(
            bob, case["case_id"], expected_version=1, idempotency_key="close", reason="Reviewed"
        )
    closed = repo.close(
        alice,
        case["case_id"],
        expected_version=1,
        idempotency_key="close",
        reason="All individual decisions recorded",
    )
    assert closed["status"] == "closed"
    assert {event["kind"] for event in repo.pending_events()} == {"decision", "closed"}
    assert (
        repo.close(
            alice,
            case["case_id"],
            expected_version=1,
            idempotency_key="close",
            reason="All individual decisions recorded",
        )["review_version"]
        == 2
    )


def test_export_contains_complete_candidates_evidence_and_history(reviews):
    case = decide(reviews, create_case(reviews))
    package = reviews[0].export(reviews[2], case["case_id"])
    assert package["case"]["candidate_batch"]["result"]["items"]
    assert package["case"]["items"][0]["condition"]["notice_evidence"][0]["quote"]
    assert package["case"]["events"][0]["review_event"]["actor"]["principal_id"] == "bob"
    assert not package["automatic_bidding"]


def test_revocation_clears_access_to_all_projections_and_worker(reviews):
    repo, alice, bob, *_ = reviews
    case = decide(reviews, create_case(reviews))
    event = repo.pending_events()[0]
    source = repo.db.rows("SELECT object_id FROM review_sources LIMIT 1")[0]["object_id"]
    repo.sources.company.auth.revoke(ALICE, source, "bob")
    for reader in (repo.get, repo.export):
        with pytest.raises(CompanyError) as caught:
            reader(bob, case["case_id"])
        assert caught.value.status_code == 404
    assert repo.list(bob)["items"] == []
    with pytest.raises(CompanyError):
        repo.load_event_for_worker(
            event["event_id"], run_id=event["run_id"], review_epoch=event["review_epoch"]
        )
    assert repo.get(alice, case["case_id"])["items"]


def test_final_source_recheck_blocks_revocation_during_derived_projection(reviews, monkeypatch):
    repo, _, bob, *_ = reviews
    case = create_case(reviews)
    original = repo._projection

    def racing(context, fixed, **page):
        result = original(context, fixed, **page)
        source = repo.db.rows("SELECT object_id FROM review_sources LIMIT 1")[0]["object_id"]
        repo.sources.company.auth.revoke(ALICE, source, "bob")
        return result

    monkeypatch.setattr(repo, "_projection", racing)
    with pytest.raises(CompanyError) as caught:
        repo.get(bob, case["case_id"])
    assert caught.value.status_code == 404


def test_worker_event_survives_normal_logout_but_not_subject_role_loss(reviews):
    repo, _, bob, *_ = reviews
    decide(reviews, create_case(reviews))
    event = repo.pending_events()[0]
    repo.db.execute(
        "DELETE FROM enterprise_logins WHERE token_hash=:hash", {"hash": bob.token_hash}
    )
    loaded = repo.load_event_for_worker(
        event["event_id"], run_id=event["run_id"], review_epoch=event["review_epoch"]
    )
    assert not loaded["consumed"]
    repo.consume_event(
        event["event_id"], run_id=event["run_id"], review_epoch=event["review_epoch"]
    )
    assert repo.load_event_for_worker(
        event["event_id"], run_id=event["run_id"], review_epoch=event["review_epoch"]
    )["consumed"]
    repo.db.execute("UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id='bob'")
    with error("review_decide_denied"):
        repo.load_event_for_worker(
            event["event_id"], run_id=event["run_id"], review_epoch=event["review_epoch"]
        )


def test_supplied_actor_and_unknown_extra_fields_cannot_be_commands(reviews):
    case = create_case(reviews)
    command = {
        "decision": "confirm",
        "expected_result_revision": "result-v1",
        "reason": "Attempt",
        "idempotency_key": "fake",
        "actor": {"principal_id": "bob"},
    }
    with error("review_invalid_command"):
        decide(reviews, case, command=command)


def test_missing_body_and_omitted_source_ref_fail_closed(reviews):
    repo = reviews[0]
    case = create_case(reviews)
    repo.db.execute(
        "DELETE FROM review_sources WHERE object_id=(SELECT object_id FROM review_sources LIMIT 1)"
    )
    with error("review_source_closure_mismatch"):
        repo.get(reviews[1], case["case_id"])


def test_worker_consumption_rejects_omitted_source_closure(reviews):
    repo = reviews[0]
    decide(reviews, create_case(reviews))
    event = repo.pending_events()[0]
    repo.db.execute(
        "DELETE FROM review_sources WHERE object_id=(SELECT object_id FROM review_sources LIMIT 1)"
    )
    for consume in (repo.load_event_for_worker, repo.consume_event):
        with pytest.raises(CompanyError, match="review_source_closure_mismatch"):
            consume(event["event_id"], run_id=event["run_id"], review_epoch=event["review_epoch"])
    assert repo.db.rows("SELECT consumed_at FROM review_outbox")[0]["consumed_at"] is None


def test_assignment_change_audits_and_removes_access_immediately(reviews):
    repo, alice, bob, *_ = reviews
    case = create_case(reviews)
    updated = repo.assign(
        alice,
        case["case_id"],
        reviewer_ids=[],
        expected_version=0,
        idempotency_key="remove",
        reason="Reviewer removed",
    )
    assert updated["events"][0]["event_kind"] == "assignment"
    with error("review_case_not_found"):
        repo.get(bob, case["case_id"])


def test_atomic_successor_link_cas_failure_rolls_back_new_job(reviews):
    repo, alice, _, _, _, _, job_case = reviews
    case = decide(
        reviews,
        create_case(reviews),
        command={
            "decision": "request_materials",
            "expected_result_revision": "result-v1",
            "reason": "Need actual certificate",
            "idempotency_key": "need",
        },
    )
    prepared = copy.deepcopy(job_case[2])
    # A failed case CAS happens in the job insert transaction, no orphan/enqueue.
    before = len(repo.db.rows("SELECT run_id FROM durable_jobs"))
    with pytest.raises(CompanyError, match="review_version_conflict"):
        with transaction(repo.db) as conn:
            conn.execute(
                text(
                    "INSERT INTO durable_jobs VALUES ('new-orphan','alice','new','x','x','x',:now)"
                ),
                {"now": time.time()},
            )
            repo.link_successor(
                conn,
                alice,
                case["case_id"],
                expected_version=0,
                revision_id=case["head_revision_id"],
                trigger_event_id=case["events"][0]["event_id"],
                new_job_record={"run_id": "new-orphan", "manifest": prepared["manifest"]},
                idempotency_key="supplement",
                reason="New material",
            )
    assert len(repo.db.rows("SELECT run_id FROM durable_jobs")) == before


def successor_job(data, case, *, change=True, packages=None):
    repo, alice, _, _, _, old_job, job_case = data
    fixed = ScreeningInputManifest.model_validate(old_job["manifest"])
    extra = []
    if change:
        doc = material(repo.sources.company, data=b"SYNTHETIC new supplementary certificate")
        extra = [{k: doc[k] for k in ("object_id", "version_id", "sha256")}]
    bundle = repo.sources.resolve(
        ALICE, fixed.profile_revision, fixed.project_id, datetime.now(UTC), extra
    )
    request = copy.deepcopy(job_case[1])
    request["additional_material_revisions"] = extra
    if packages is not None:
        request["target_package_ids"] = packages
    prepared = copy.deepcopy(job_case[2])
    prepared["manifest"] = bundle.manifest.model_dump(mode="json")
    job = job_case[0].get_or_create(
        ALICE, idempotency_key="successor-job", request=request, prepare=lambda: prepared
    )
    return job, {
        "expected_version": case["review_version"],
        "revision_id": case["head_revision_id"],
        "trigger_event_id": case["events"][-1]["event_id"],
        "new_job_record": job,
        "idempotency_key": "supplement-link",
        "reason": "New fixed source requires complete recalculation",
    }


def request_materials(data):
    return decide(
        data,
        create_case(data),
        command={
            "decision": "request_materials",
            "expected_result_revision": "result-v1",
            "reason": "Need actual certificate",
            "idempotency_key": "need",
        },
    )


def test_successor_is_fixed_linked_replayable_and_new_revision_starts_unreviewed(reviews):
    repo, alice, _, _, _, _, job_case = reviews
    case = request_materials(reviews)
    old_event_id = case["events"][0]["event_id"]
    assert repo.pending_events()[0]["event_id"] == old_event_id
    job, params = successor_job(reviews, case)
    with transaction(repo.db) as conn:
        assert not repo.link_successor(conn, alice, case["case_id"], **params)["replayed"]
    linked = repo.get(alice, case["case_id"])
    assert linked["status"] == "recomputing"
    assert not linked["items"][0]["effective"]
    assert len(repo.db.rows("SELECT * FROM review_sources")) == 3
    old_receipt = repo.db.rows(
        "SELECT * FROM review_outbox WHERE event_id=:id", {"id": old_event_id}
    )[0]
    assert old_receipt["frozen"] and old_receipt["consumed_at"] is None
    with pytest.raises(CompanyError, match="review_event_not_current"):
        repo.load_event_for_worker(
            old_event_id, run_id=old_receipt["run_id"], review_epoch=linked["review_epoch"]
        )
    superseded = repo.pending_events()
    assert len(superseded) == 1 and superseded[0]["kind"] == "superseded"
    consumed = repo.consume_event(
        superseded[0]["event_id"],
        run_id=superseded[0]["run_id"],
        review_epoch=linked["review_epoch"],
    )
    assert consumed["outbox_kind"] == "superseded"
    assert repo.pending_events() == []
    with transaction(repo.db) as conn:
        assert repo.link_successor(conn, alice, case["case_id"], **params)["replayed"]
    assert len(repo.get(alice, case["case_id"])["events"]) == 2
    job = job_case[0].transition(job["run_id"], 0, "running")
    result_id = "supplement-result"
    QualificationRepository(repo.db, repo.sources).save(
        ALICE,
        job["manifest"],
        {
            "kind": "checklist",
            "state": "completed",
            "run_id": result_id,
            "result_revision": "new-result-v2",
            "groups": [assessment_group(job_case[0], job, "new-result-v2")],
        },
        result_id=result_id,
        provenance={"business_run_id": job["run_id"]},
    )
    job_case[0].transition(
        job["run_id"],
        job["state_version"],
        "waiting_review",
        result_id=result_id,
        result_revision="new-result-v2",
    )
    attached = repo.attach_revision(
        alice,
        case["case_id"],
        run_id=job["run_id"],
        expected_version=linked["review_version"],
        idempotency_key="attach",
        reason="Read the recalculated result",
    )
    assert len(attached["revisions"]) == 2
    assert attached["revisions"][1]["parent_revision_id"] == case["head_revision_id"]
    assert attached["items"][0]["latest_decision"] is None
    assert not attached["items"][0]["effective"]
    assert attached["events"][0]["review_event"] == case["events"][0]["review_event"]
    assert repo.pending_events() == []
    # The newly added private source protects even the historical case envelope.
    with pytest.raises(CompanyError) as caught:
        repo.get(reviews[2], case["case_id"])
    assert caught.value.status_code == 404


@pytest.mark.parametrize("change,packages", [(False, None), (True, ["quietly-drop-old-packages"])])
def test_successor_rejects_asof_only_changes_and_changed_package_scope(reviews, change, packages):
    case = request_materials(reviews)
    _, params = successor_job(reviews, case, change=change, packages=packages)
    with pytest.raises(CompanyError, match="review_successor_binding_mismatch"):
        with transaction(reviews[0].db) as conn:
            reviews[0].link_successor(conn, reviews[1], case["case_id"], **params)
    assert reviews[0].db.rows("SELECT * FROM review_successors") == []


def test_source_update_preserves_history_but_never_presents_old_approval_as_current(reviews):
    repo, alice, _, _, _, _, job_case = reviews
    case = decide(reviews, create_case(reviews))
    old_profile = job_case[4]
    repo.sources.company.repo.put(
        ALICE,
        payload(display_name="New facts requiring fresh review"),
        object_id=old_profile["object_id"],
        expected_version=old_profile["version_id"],
    )
    historical = repo.get(alice, case["case_id"])
    assert historical["events"] == case["events"]
    assert historical["items"][0]["latest_decision"] == "confirm"
    assert not historical["items"][0]["effective"]
    assert "source_changed" in historical["items"][0]["ineffective_reasons"]
    assert "profile_head_changed" in historical["source_state"]["stale_reasons"]
    assert not repo.export(alice, case["case_id"])["current"]
    with pytest.raises(CompanyError, match="review_sources_changed"):
        decide(
            reviews,
            historical,
            command={
                "decision": "reject",
                "expected_result_revision": "result-v1",
                "reason": "Cannot decide stale inputs",
                "idempotency_key": "stale",
            },
            supersedes_event_id=case["events"][0]["event_id"],
        )


def test_restore_epoch_isolation_needs_explicit_assignment_and_new_decisions(reviews):
    repo, alice, bob, *_ = reviews
    first = create_case(reviews)
    decided = decide(reviews, first)
    repo.db.execute(
        "UPDATE review_case_state SET quarantined=1,review_epoch='new-restore-epoch',"
        "review_version=review_version+1"
    )
    repo.db.execute("DELETE FROM review_assignments")
    repo.db.execute("UPDATE review_outbox SET frozen=1")
    blocked = repo.get(alice, first["case_id"])
    assert blocked["items"] == [] and blocked["allowed_actions"] == ["reauthorize"]
    restored = repo.reauthorize(
        alice,
        first["case_id"],
        expected_version=2,
        idempotency_key="reauth",
        reason="Verified restored identities and source grants",
    )
    assert not restored["items"][0]["effective"]
    assert restored["assignments"] == []
    restored = repo.assign(
        alice,
        first["case_id"],
        reviewer_ids=["bob"],
        expected_version=3,
        idempotency_key="new-assignment",
        reason="Explicit fresh assignment",
    )
    with pytest.raises(CompanyError, match="review_event_epoch_changed"):
        decide(reviews, first)
    confirmed = decide(
        reviews,
        restored,
        command={
            "decision": "confirm",
            "expected_result_revision": "result-v1",
            "reason": "Fresh post-restore decision",
            "idempotency_key": "new-epoch",
        },
    )
    assert confirmed["items"][0]["effective"]
    assert len(confirmed["events"]) == 4
    old = decided["events"][0]
    with pytest.raises(CompanyError, match="review_event_not_current"):
        repo.consume_event(
            old["event_id"], run_id=old["review_event"]["run_id"], review_epoch=old["review_epoch"]
        )


def test_reauthorizing_pending_successor_preserves_attach_path_without_releasing_job(reviews):
    repo, alice, *_ = reviews
    case = request_materials(reviews)
    job, params = successor_job(reviews, case)
    with transaction(repo.db) as conn:
        repo.link_successor(conn, alice, case["case_id"], **params)
    repo.db.execute(
        "UPDATE review_case_state SET quarantined=1,review_epoch='restored-pending',"
        "review_version=review_version+1 WHERE case_id=:id",
        {"id": case["case_id"]},
    )
    repo.db.execute("DELETE FROM review_assignments")
    repo.db.execute("UPDATE review_outbox SET frozen=1")
    repo.db.execute(
        "UPDATE durable_job_state SET quarantined=1 WHERE run_id=:id", {"id": job["run_id"]}
    )
    restored = repo.reauthorize(
        alice,
        case["case_id"],
        expected_version=3,
        idempotency_key="pending-reauth",
        reason="Keep the fixed pending computation chain",
    )
    assert restored["status"] == "recomputing"
    assert "attach" in restored["allowed_actions"]
    assert restored["assignments"] == []
    assert repo.db.rows(
        "SELECT quarantined FROM durable_job_state WHERE run_id=:id", {"id": job["run_id"]}
    ) == [{"quarantined": 1}]
    assert not restored["items"][0]["effective"]


def test_create_same_request_concurrently_yields_single_case(reviews):
    barrier = Barrier(2)

    def creating(_):
        barrier.wait()
        return create_case(reviews)["case_id"]

    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(creating, range(2)))
    assert ids[0] == ids[1]
    assert len(reviews[0].db.rows("SELECT * FROM review_cases")) == 1


def test_consumer_callback_and_receipt_commit_or_rollback_in_one_transaction(reviews):
    repo = reviews[0]
    decide(reviews, create_case(reviews))
    event = repo.pending_events()[0]
    before = repo.db.rows(
        "SELECT progress_json FROM durable_job_state WHERE run_id=:id", {"id": event["run_id"]}
    )

    def crashing(conn, event):
        conn.execute(
            text("UPDATE durable_job_state SET progress_json='{}' WHERE run_id=:id"),
            {"id": event["run_id"]},
        )
        raise RuntimeError("Synthetic before commit")

    with pytest.raises(RuntimeError, match="Synthetic before commit"):
        repo.consume_event(
            event["event_id"],
            run_id=event["run_id"],
            review_epoch=event["review_epoch"],
            apply=crashing,
        )
    assert repo.db.rows("SELECT consumed_at FROM review_outbox")[0]["consumed_at"] is None
    assert (
        repo.db.rows(
            "SELECT progress_json FROM durable_job_state WHERE run_id=:id", {"id": event["run_id"]}
        )
        == before
    )
    seen = []
    repo.consume_event(
        event["event_id"],
        run_id=event["run_id"],
        review_epoch=event["review_epoch"],
        apply=lambda conn, value: seen.append(value["event_id"]),
    )
    assert seen == [event["event_id"]]
    assert repo.db.rows("SELECT consumed_at FROM review_outbox")[0]["consumed_at"] is not None


@pytest.mark.parametrize("table", ["review_events", "review_revisions", "review_cases"])
def test_audit_metadata_cannot_be_deleted_but_retention_can_erase_reason_body(reviews, table):
    decide(reviews, create_case(reviews))
    with pytest.raises(IntegrityError, match="cannot be deleted"):
        reviews[0].db.execute(f"DELETE FROM {table}")
    reviews[0].db.execute("DELETE FROM review_event_bodies")
    assert reviews[0].db.rows("SELECT * FROM review_events")
    with pytest.raises(CompanyError, match="review_history_unavailable"):
        reviews[0].get(reviews[1], create_case(reviews)["case_id"])


def test_source_revoked_after_preflight_never_creates_a_human_decision(reviews, monkeypatch):
    repo = reviews[0]
    case = create_case(reviews)
    original = repo.sources.changes
    revoked = []

    def racing(context, manifest):
        result = original(context, manifest)
        if not revoked:
            ref = repo.db.rows("SELECT object_id FROM review_sources LIMIT 1")[0]["object_id"]
            repo.sources.company.auth.revoke(ALICE, ref, "bob")
            revoked.append(ref)
        return result

    monkeypatch.setattr(repo.sources, "changes", racing)
    with pytest.raises(CompanyError):
        decide(reviews, case)
    assert repo.db.rows("SELECT * FROM review_events") == []
