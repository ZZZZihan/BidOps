"""Business persistence against real SQLite and current company ACLs; no workers/network."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from bidops.company.authorization import CompanyError, ToolContext
from bidops.jobs import JobsRepository
from bidops.qualification.contracts import AssessmentGroup
from bidops.qualification.repository import QualificationRepository
from bidops.qualification.sources import ScreeningInputManifest, digest
from bidops.screening.schemas import TaskExecution
from sqlalchemy.exc import IntegrityError
from test_company_access import ADMIN, ALICE, BOB, VIEWER, payload
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources


@pytest.fixture
def jobs(sources):
    adapter, bundle, doc, profile, _, _ = sources
    repo = JobsRepository(adapter.company.db, adapter)
    prepared = {
        "manifest": bundle.manifest.model_dump(mode="json"),
        "settings": {"max_requests": 4, "max_seconds": 10, "execution_kind": "scorer_fixture"},
        "versions": {"code_sha": "a" * 40, "prompt": "fixture_v1", "scorer": "fixture_v1"},
    }
    request = {
        "kind": "checklist",
        "profile_revision": profile,
        "project_id": bundle.manifest.project_id,
    }
    return repo, request, prepared, doc, profile


def create(case, *, key="same-key", actor=ALICE, request=None, prepare=None, max_active_jobs=None):
    repo, original, prepared, _, _ = case
    return repo.get_or_create(
        actor,
        idempotency_key=key,
        request=request if request is not None else original,
        prepare=prepare or (lambda: copy.deepcopy(prepared)),
        max_active_jobs=max_active_jobs,
    )


def running(case):
    job = create(case)
    return case[0].transition(job["run_id"], 0, "running")


def waiting(case):
    job = running(case)
    store_result(case, job, result_id="fixed-business-result", revision="result-v1")
    return case[0].transition(
        job["run_id"],
        job["state_version"],
        "waiting_review",
        result_id="fixed-business-result",
        result_revision="result-v1",
    )


def assessment_group(repo, job, revision):
    """A real persisted notice citation and complete B01 group, for offline tests only."""
    manifest = ScreeningInputManifest.model_validate(job["manifest"])
    notice = manifest.notice_revisions[0]
    _, body = repo.sources.notices.read_text(notice.version_id)
    evidence = repo.sources.notices.make_evidence(notice.version_id, 0, min(10, len(body)))
    ref = evidence.model_dump(mode="json")
    condition = {
        "condition_id": "synthetic-condition",
        "original_text": evidence.quote,
        "hard_requirement": True,
        "package_scope": {"kind": "all"},
        "expression": {
            "node_id": "synthetic-node",
            "operator": "atom",
            "text": evidence.quote,
            "evidence_ids": [evidence.evidence_id],
        },
        "validity": {"kind": "not_required"},
        "notice_evidence": [ref],
    }
    return AssessmentGroup.model_validate(
        {
            "primary_notice_revision": notice,
            "assessment": {
                "assessment_id": digest(["jobs-review-fixture", job["run_id"]]),
                "result_revision": revision,
                "inputs": {
                    "project_family_id": manifest.project_id,
                    "project_id": manifest.project_id,
                    "notice_revision": notice,
                    "profile_revision": manifest.profile_revision,
                    "material_revisions": manifest.material_revisions,
                    "attachment_revisions": manifest.attachment_revisions,
                    "as_of": manifest.as_of,
                },
                "conditions": [condition],
                "assessments": [
                    {
                        "condition_id": condition["condition_id"],
                        "status": "information_insufficient",
                        "reason": "Synthetic fixture; no business acceptance claim",
                        "notice_evidence": [ref],
                        "missing_items": ["synthetic_fixture_missing"],
                    }
                ],
            },
        }
    ).model_dump(mode="json")


def store_result(
    case,
    job,
    *,
    result_id="business-result",
    revision="v1",
    state="completed",
    actor=ALICE,
    manifest=None,
    kind="checklist",
    groups=None,
):
    repo = QualificationRepository(case[0].db, case[0].sources)
    return repo.save(
        actor,
        manifest or job["manifest"],
        {
            "kind": kind,
            "state": state,
            "result_revision": revision,
            "run_id": job["run_id"],
            "groups": groups
            if groups is not None
            else ([] if manifest is not None else [assessment_group(case[0], job, revision)]),
        },
        result_id=result_id,
    )


def review(job, **updates):
    groups = (job.get("result") or {}).get("groups")
    assessment_id = (
        groups[0]["assessment"]["assessment_id"]
        if groups
        else digest(["jobs-review-fixture", job["run_id"]])
    )
    value = {
        "event_id": "fixture-event",
        "run_id": job["run_id"],
        "assessment_id": assessment_id,
        "result_revision": job["result_revision"],
        "inputs_manifest_sha256": job["inputs_manifest_sha256"],
        "command": {
            "decision": "confirm",
            "expected_result_revision": job["result_revision"],
            "reason": "synthetic fixture only",
            "idempotency_key": "fixture-review",
        },
        "actor": {
            "actor_kind": "human",
            "principal_id": "unit-fixture-human",
            "authorization_context_id": "fixture-only",
        },
        "occurred_at": datetime.now(UTC).isoformat(),
        "origin": "test_fixture",
    }
    return {**value, **updates}


def test_original_input_omitted_as_of_replayed_without_preparation_or_new_timestamp(jobs):
    repo, original, prepared, _, _ = jobs
    calls = []

    def prepare():
        calls.append(1)
        return prepared

    first = create(jobs, prepare=prepare)
    assert first["created"] and first["status"] == "queued" and first["state_version"] == 0
    assert "as_of" not in first["request"] and first["input_sha256"] == digest(original)
    assert first["manifest"]["as_of"] == prepared["manifest"]["as_of"]
    second = create(jobs, prepare=lambda: pytest.fail("idempotent replay reparsed inputs"))
    assert not second["created"] and second["run_id"] == first["run_id"] and len(calls) == 1
    assert repo.db.rows("SELECT COUNT(*) n FROM durable_jobs")[0]["n"] == 1
    assert repo.db.rows("SELECT * FROM agent_runs") == []
    assert TaskExecution(run_id=first["run_id"], status=first["status"]).status == "queued"


def test_same_key_changed_original_input_conflicts_even_if_resolved_manifest_same(jobs):
    create(jobs)
    with pytest.raises(CompanyError, match="job_idempotency_conflict") as error:
        create(
            jobs,
            request={**jobs[1], "as_of": jobs[2]["manifest"]["as_of"]},
            prepare=lambda: pytest.fail("conflict reparsed inputs"),
        )
    assert error.value.status_code == 409


def test_parallel_same_input_has_one_run_and_one_persisted_manifest(jobs):
    gate = Barrier(2)

    def submit(_):
        def prepare():
            gate.wait(timeout=5)
            return copy.deepcopy(jobs[2])

        return create(jobs, prepare=prepare)

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(submit, range(2)))
    assert len({r["run_id"] for r in records}) == 1
    assert sorted(r["created"] for r in records) == [False, True]
    assert jobs[0].db.rows("SELECT COUNT(*) n FROM durable_job_inputs")[0]["n"] == 1


def test_capacity_admission_is_atomic_for_concurrent_distinct_keys(jobs):
    gate = Barrier(2)

    def submit(number):
        def prepare():
            gate.wait(timeout=5)
            return copy.deepcopy(jobs[2])

        try:
            return create(jobs, key=f"parallel-{number}", prepare=prepare, max_active_jobs=1)
        except CompanyError as error:
            assert error.status_code == 429
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(submit, range(2)))
    assert sum(isinstance(record, dict) and record["created"] for record in records) == 1
    assert records.count("job_capacity_exhausted") == 1
    for table in ("durable_jobs", "durable_job_inputs", "durable_job_state"):
        assert jobs[0].db.rows(f"SELECT COUNT(*) n FROM {table}")[0]["n"] == 1
    assert len(jobs[0].db.rows("SELECT * FROM durable_job_sources")) == len(
        ScreeningInputManifest.model_validate(jobs[2]["manifest"]).company_revisions
    )


def test_capacity_same_key_concurrent_and_later_replays_need_no_slot(jobs):
    gate = Barrier(2)

    def submit(_):
        def prepare():
            gate.wait(timeout=5)
            return copy.deepcopy(jobs[2])

        return create(jobs, prepare=prepare, max_active_jobs=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(submit, range(2)))
    assert len({record["run_id"] for record in records}) == 1
    assert sorted(record["created"] for record in records) == [False, True]
    replayed = create(
        jobs,
        max_active_jobs=1,
        prepare=lambda: pytest.fail("capacity replay prepared again"),
    )
    assert not replayed["created"]
    with pytest.raises(CompanyError, match="job_capacity_exhausted"):
        create(jobs, key="new-key", max_active_jobs=1)
    with pytest.raises(CompanyError, match="job_idempotency_conflict"):
        create(jobs, request={**jobs[1], "changed": True}, max_active_jobs=1)


@pytest.mark.parametrize("limit", [0, -1, True, 101, "1", 1.0])
def test_capacity_rejects_invalid_limit_before_preparing(jobs, limit):
    with pytest.raises(CompanyError, match="job_invalid_capacity") as error:
        create(jobs, max_active_jobs=limit, prepare=lambda: pytest.fail("invalid capacity"))
    assert error.value.status_code == 422
    assert jobs[0].db.rows("SELECT * FROM durable_jobs") == []


@pytest.mark.parametrize(
    "status", ["queued", "running", "waiting_review", "interrupted", "cancel_requested"]
)
def test_capacity_includes_each_active_state_and_releases_after_terminal(jobs, status):
    repo = jobs[0]
    job = waiting(jobs) if status == "waiting_review" else create(jobs)
    if status in {"running", "interrupted", "cancel_requested"}:
        job = repo.transition(
            job["run_id"],
            job["state_version"],
            status,
            error="synthetic_pause" if status == "interrupted" else None,
        )
    with pytest.raises(CompanyError, match="job_capacity_exhausted"):
        create(jobs, key="next", max_active_jobs=1)
    if status == "cancel_requested":
        repo.transition(job["run_id"], job["state_version"], "cancelled")
    else:
        repo.transition(job["run_id"], job["state_version"], "failed", error="synthetic_failure")
    assert create(jobs, key="next", max_active_jobs=1)["created"]


def test_capacity_is_global_across_owners_and_restored_live_work(jobs):
    create(jobs)
    jobs[0].quarantine_all()
    for ref in ScreeningInputManifest.model_validate(jobs[2]["manifest"]).company_revisions:
        jobs[0].auth.grant(ALICE, ref.object_id, BOB.owner_id, ["read"])
    with pytest.raises(CompanyError, match="job_capacity_exhausted"):
        create(jobs, actor=BOB, key="other-owner", max_active_jobs=1)


def test_parallel_same_key_different_input_one_conflict(jobs):
    gate = Barrier(2)

    def submit(number):
        def prepare():
            gate.wait(timeout=5)
            return copy.deepcopy(jobs[2])

        try:
            return create(jobs, request={**jobs[1], "synthetic": number}, prepare=prepare)
        except CompanyError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(submit, range(2)))
    assert sum(isinstance(r, dict) for r in records) == 1
    assert records.count("job_idempotency_conflict") == 1


def test_owner_key_isolation_despite_identical_accessible_sources(jobs):
    first = create(jobs)
    for ref in jobs[2]["manifest"]["material_revisions"] + [
        jobs[2]["manifest"]["profile_revision"]
    ]:
        jobs[0].auth.grant(ALICE, ref["object_id"], BOB.owner_id, ["read"])
    other = create(jobs, actor=BOB)
    assert first["run_id"] != other["run_id"]
    assert jobs[0].list(BOB)["total"] == 1
    with pytest.raises(CompanyError, match="job_not_found"):
        jobs[0].get(BOB, first["run_id"])


@pytest.mark.parametrize("actor", [BOB, ADMIN, VIEWER, ToolContext("anonymous")])
def test_actual_owner_and_live_identity_required(jobs, actor):
    job = create(jobs)
    for operation in (
        lambda: jobs[0].get(actor, job["run_id"]),
        lambda: jobs[0].request_cancel(actor, job["run_id"], 0),
        lambda: jobs[0].list_attempts(actor, job["run_id"]),
    ):
        with pytest.raises(CompanyError):
            operation()


def test_viewer_cannot_create_even_with_source_grants(jobs):
    for ref in jobs[2]["manifest"]["material_revisions"] + [
        jobs[2]["manifest"]["profile_revision"]
    ]:
        jobs[0].auth.grant(ALICE, ref["object_id"], VIEWER.owner_id, ["read"])
    with pytest.raises(CompanyError, match="company_create_denied"):
        create(jobs, actor=VIEWER)


def test_parallel_expected_version_only_one_state_update(jobs):
    job = create(jobs)
    gate = Barrier(2)

    def update(_):
        gate.wait(timeout=5)
        try:
            return jobs[0].transition(job["run_id"], 0, "running")
        except CompanyError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(update, range(2)))
    assert sum(isinstance(r, dict) for r in records) == 1
    assert records.count("job_state_conflict") == 1
    assert jobs[0].get(ALICE, job["run_id"])["state_version"] == 1


def test_progress_failure_shape_and_fixed_business_result(jobs):
    repo = jobs[0]
    job = running(jobs)
    with pytest.raises(CompanyError, match="job_invalid_execution"):
        repo.transition(job["run_id"], 1, "failed")
    job = repo.transition(job["run_id"], 1, "running", progress={"completed_steps": 1})
    assert job["progress"] == {"completed_steps": 1}
    with pytest.raises(CompanyError, match="job_result_id_must_differ"):
        repo.transition(
            job["run_id"], 2, "completed", result_id=job["run_id"], result_revision="v1"
        )
    with pytest.raises(CompanyError, match="job_result_reference_required"):
        repo.transition(job["run_id"], 2, "completed", result_revision="v1")
    store_result(jobs, job)
    job = repo.transition(
        job["run_id"], 2, "waiting_review", result_id="business-result", result_revision="v1"
    )
    with pytest.raises(CompanyError, match="job_result_reference_immutable"):
        repo.transition(job["run_id"], 3, "completed", result_id="other-result")
    job = repo.transition(job["run_id"], 3, "completed")
    assert job["result_id"] == "business-result" and job["result_revision"] == "v1"
    with pytest.raises(CompanyError, match="job_transition_forbidden"):
        repo.transition(job["run_id"], 4, "running")


@pytest.mark.parametrize("state", ["failed", "timed_out", "interrupted"])
def test_failure_reason_persisted_for_all_frozen_failure_states(jobs, state):
    job = create(jobs)
    changed = jobs[0].transition(job["run_id"], 0, state, error="synthetic_failure")
    assert changed["error_code"] == "synthetic_failure"
    if state != "interrupted":
        with pytest.raises(CompanyError):
            jobs[0].transition(job["run_id"], 1, "running")
    else:
        assert jobs[0].transition(job["run_id"], 1, "running")["error_code"] is None


def test_cancel_is_persisted_prevents_steps_and_never_resurrects_after_reopen(jobs):
    repo = jobs[0]
    job = running(jobs)
    job = repo.request_cancel(ALICE, job["run_id"], 1)
    assert job["status"] == "cancel_requested"
    with pytest.raises(CompanyError, match="job_not_executable"):
        repo.require_execution(job["run_id"])
    with pytest.raises(CompanyError, match="job_attempt_not_running"):
        repo.record_attempt_started(job["run_id"], request_sha256="a" * 64)
    job = repo.transition(job["run_id"], 2, "cancelled")
    reopened = JobsRepository(repo.db, repo.sources)
    assert reopened.get_system(job["run_id"])["status"] == "cancelled"
    for state in ("queued", "running", "completed", "interrupted"):
        with pytest.raises(CompanyError, match="job_transition_forbidden"):
            reopened.transition(job["run_id"], 3, state)
    with pytest.raises(IntegrityError, match="cancelled task cannot resume"):
        repo.db.execute(
            "UPDATE durable_job_state SET status='running' WHERE run_id=:id", {"id": job["run_id"]}
        )


@pytest.mark.parametrize("stage", ["queued", "running", "waiting_review"])
def test_revoke_during_each_nonterminal_stage_denies_read_resume_attempts_and_listing(jobs, stage):
    job = (
        waiting(jobs)
        if stage == "waiting_review"
        else running(jobs)
        if stage == "running"
        else create(jobs)
    )
    jobs[0].auth.revoke(ALICE, jobs[3]["object_id"], ALICE.owner_id)
    assert jobs[0].list(ALICE)["items"] == []
    for operation in (
        lambda: jobs[0].get(ALICE, job["run_id"]),
        lambda: jobs[0].require_execution(job["run_id"]),
        lambda: jobs[0].list_attempts(ALICE, job["run_id"]),
    ):
        with pytest.raises(CompanyError, match="company_object_not_found"):
            operation()
    # Internal failure bookkeeping is still possible; it does not release input publicly.
    failed = jobs[0].transition(
        job["run_id"], job["state_version"], "failed", error="source_revoked"
    )
    assert failed["error_code"] == "source_revoked"


def test_final_release_rechecks_acl_after_source_validation(jobs, monkeypatch):
    job = create(jobs)
    original = jobs[0].sources.changes

    def changes(context, manifest):
        result = original(context, manifest)
        jobs[0].auth.revoke(ALICE, jobs[3]["object_id"], ALICE.owner_id)
        return result

    monkeypatch.setattr(jobs[0].sources, "changes", changes)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        jobs[0].get(ALICE, job["run_id"])


def test_old_job_readable_but_stale_after_new_material_head(jobs):
    job = create(jobs)
    jobs[0].sources.company.repo.put(
        ALICE,
        payload(display_name="new head"),
        object_id=jobs[4]["object_id"],
        expected_version=jobs[4]["version_id"],
    )
    historic = jobs[0].get(ALICE, job["run_id"])
    assert historic["manifest"] == job["manifest"]
    assert not historic["source_state"]["current"]
    assert "profile_head_changed" in historic["source_state"]["stale_reasons"]


def test_restore_quarantine_is_independent_and_needs_owner_and_current_sources(jobs):
    job = running(jobs)
    jobs[0].quarantine_all()
    reopened = JobsRepository(jobs[0].db, jobs[0].sources)
    assert reopened.list_system()[0]["quarantined"] is True
    assert reopened.list_system(statuses={"running"})[0]["run_id"] == job["run_id"]
    assert reopened.list_system(statuses={"queued"}) == []
    assert reopened.list(ALICE)["total"] == 0
    with pytest.raises(CompanyError, match="job_not_found"):
        reopened.get(ALICE, job["run_id"])
    with pytest.raises(CompanyError):
        reopened.reauthorize(ADMIN, job["run_id"], 2)
    with pytest.raises(CompanyError, match="job_state_conflict"):
        reopened.reauthorize(ALICE, job["run_id"], 1)
    jobs[0].auth.revoke(ALICE, jobs[3]["object_id"], ALICE.owner_id)
    with pytest.raises(CompanyError):
        reopened.reauthorize(ALICE, job["run_id"], 2)
    # An explicit fresh source grant is required, never copied from the backup.
    jobs[0].db.execute(
        "INSERT INTO company_grants VALUES (:id,'alice','read')", {"id": jobs[3]["object_id"]}
    )
    restored = reopened.reauthorize(ALICE, job["run_id"], 2)
    assert (
        not restored["quarantined"]
        and restored["status"] == "interrupted"
        and restored["error_code"] == "job_restored_needs_resume"
        and restored["state_version"] == 3
    )


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "running",
        "waiting_review",
        "interrupted",
        "cancel_requested",
        "cancelled",
        "completed",
        "failed",
        "timed_out",
    ],
)
def test_reauthorization_restores_visibility_without_resuming_saved_work(jobs, status):
    repo = jobs[0]
    job = waiting(jobs) if status == "waiting_review" else create(jobs)
    if status == "completed":
        job = repo.transition(job["run_id"], job["state_version"], "running")
        store_result(jobs, job)
        job = repo.transition(
            job["run_id"],
            job["state_version"],
            "completed",
            result_id="business-result",
            result_revision="v1",
        )
    elif status == "cancelled":
        job = repo.request_cancel(ALICE, job["run_id"], job["state_version"])
        job = repo.transition(job["run_id"], job["state_version"], "cancelled")
    elif status not in {"queued", "waiting_review"}:
        job = repo.transition(
            job["run_id"],
            job["state_version"],
            status,
            error="synthetic_failure" if status in {"interrupted", "failed", "timed_out"} else None,
        )
    repo.quarantine_all()
    repo.db.execute("UPDATE qualification_result_lifecycle SET quarantined=1")
    quarantined = repo.get_system(job["run_id"])
    restored = repo.reauthorize(ALICE, job["run_id"], quarantined["state_version"])
    expected = (
        "interrupted"
        if status in {"queued", "running", "waiting_review"}
        else "cancelled"
        if status == "cancel_requested"
        else status
    )
    assert restored["status"] == expected and not restored["quarantined"]
    assert restored["state_version"] == quarantined["state_version"] + 1
    assert restored["result_id"] == job["result_id"]
    assert restored["result_revision"] == job["result_revision"]
    assert restored["error_code"] == (
        "job_restored_needs_resume"
        if status in {"queued", "running", "waiting_review"}
        else None
        if status == "cancel_requested"
        else job["error_code"]
    )
    if job["result_id"]:
        result = QualificationRepository(repo.db, repo.sources).get(ALICE, job["result_id"])
        assert result["result"]["result_revision"] == job["result_revision"]
    again = repo.reauthorize(ALICE, job["run_id"], restored["state_version"])
    assert again == restored
    with pytest.raises(CompanyError, match="job_state_conflict"):
        repo.reauthorize(ALICE, job["run_id"], quarantined["state_version"])


@pytest.mark.parametrize("damage", ["body_missing", "purged", "hash", "revision"])
def test_reauthorization_rejects_invalid_saved_result_without_partial_visibility(jobs, damage):
    repo = jobs[0]
    job = waiting(jobs)
    repo.quarantine_all()
    repo.db.execute("UPDATE qualification_result_lifecycle SET quarantined=1")
    if damage == "body_missing":
        repo.db.execute("DELETE FROM qualification_result_bodies")
    elif damage == "purged":
        repo.db.execute("UPDATE qualification_result_lifecycle SET purge_state='purged'")
    elif damage == "hash":
        # A corrupted external restore is modelled by disabling immutability solely in this DB.
        trigger = repo.db.rows(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='qualification_result_bodies'"
        )
        for row in trigger:
            repo.db.execute(f"DROP TRIGGER {row['name']}")
        repo.db.execute("UPDATE qualification_result_bodies SET body_json='{}'")
    else:
        repo.db.execute("UPDATE durable_job_state SET result_revision='other-revision'")
    before = repo.get_system(job["run_id"])
    with pytest.raises(CompanyError, match="job_result_reference_invalid"):
        repo.reauthorize(ALICE, job["run_id"], before["state_version"])
    after = repo.get_system(job["run_id"])
    assert after == before and after["quarantined"]
    assert (
        repo.db.rows("SELECT quarantined FROM qualification_result_lifecycle")[0]["quarantined"]
        == 1
    )


def test_reauthorization_without_quarantine_is_a_cas_checked_noop(jobs):
    repo = jobs[0]
    job = running(jobs)
    assert (
        repo.reauthorize(ALICE, job["run_id"], job["state_version"])["state_version"]
        == job["state_version"]
    )
    assert repo.get_system(job["run_id"]) == job
    with pytest.raises(CompanyError, match="job_state_conflict"):
        repo.reauthorize(ALICE, job["run_id"], job["state_version"] - 1)


def test_reauthorization_never_releases_another_job_or_its_same_revision_result(jobs):
    repo = jobs[0]
    tasks = []
    for suffix in ("a", "b"):
        job = create(jobs, key=f"restored-task-{suffix}")
        job = repo.transition(job["run_id"], job["state_version"], "running")
        store_result(jobs, job, result_id=f"result-{suffix}", revision="same-revision")
        tasks.append(
            repo.transition(
                job["run_id"],
                job["state_version"],
                "waiting_review",
                result_id=f"result-{suffix}",
                result_revision="same-revision",
            )
        )
    repo.quarantine_all()
    repo.db.execute("UPDATE qualification_result_lifecycle SET quarantined=1")
    grants = repo.db.rows("SELECT * FROM company_grants ORDER BY object_id,subject_id,action")
    first, second = [repo.get_system(job["run_id"]) for job in tasks]
    released = repo.reauthorize(ALICE, first["run_id"], first["state_version"])
    assert not released["quarantined"] and released["status"] == "interrupted"
    assert repo.get_system(second["run_id"]) == second
    results = QualificationRepository(repo.db, repo.sources)
    assert results.get(ALICE, "result-a")["result"]["result_revision"] == "same-revision"
    with pytest.raises(CompanyError, match="screening_result_not_found"):
        results.get(ALICE, "result-b")
    assert repo.db.rows(
        "SELECT result_id,quarantined FROM qualification_result_lifecycle ORDER BY result_id"
    ) == [
        {"result_id": "result-a", "quarantined": 0},
        {"result_id": "result-b", "quarantined": 1},
    ]
    assert (
        repo.db.rows("SELECT * FROM company_grants ORDER BY object_id,subject_id,action") == grants
    )


def test_reauthorization_rechecks_role_and_never_grants_the_missing_source(jobs):
    repo = jobs[0]
    job = waiting(jobs)
    repo.quarantine_all()
    repo.db.execute("UPDATE qualification_result_lifecycle SET quarantined=1")
    quarantined = repo.get_system(job["run_id"])
    repo.db.execute(
        "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id='alice'"
    )
    with pytest.raises(CompanyError, match="company_create_denied"):
        repo.reauthorize(ALICE, job["run_id"], quarantined["state_version"])
    assert repo.get_system(job["run_id"]) == quarantined
    assert (
        repo.db.rows("SELECT quarantined FROM qualification_result_lifecycle")[0]["quarantined"]
        == 1
    )


def test_reauthorization_final_acl_failure_rolls_back_both_visibility_changes(jobs, monkeypatch):
    repo = jobs[0]
    job = waiting(jobs)
    repo.quarantine_all()
    repo.db.execute("UPDATE qualification_result_lifecycle SET quarantined=1")
    quarantined = repo.get_system(job["run_id"])
    original = repo.sources.recheck

    def revoke_after_read(context, manifest, **kwargs):
        result = original(context, manifest, **kwargs)
        repo.auth.revoke(ALICE, jobs[3]["object_id"], ALICE.owner_id)
        return result

    monkeypatch.setattr(repo.sources, "recheck", revoke_after_read)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        repo.reauthorize(ALICE, job["run_id"], quarantined["state_version"])
    assert repo.get_system(job["run_id"]) == quarantined
    assert (
        repo.db.rows("SELECT quarantined FROM qualification_result_lifecycle")[0]["quarantined"]
        == 1
    )
    assert (
        repo.db.rows(
            "SELECT * FROM company_grants WHERE object_id=:id AND subject_id='alice'",
            {"id": jobs[3]["object_id"]},
        )
        == []
    )


def test_db_input_and_source_revisions_are_immutable_and_index_corruption_is_detected(jobs):
    job = create(jobs)
    for sql in (
        "UPDATE durable_jobs SET input_sha256='x'",
        "UPDATE durable_job_inputs SET body_json='{}'",
        "UPDATE durable_job_sources SET version_id='forged'",
    ):
        with pytest.raises(IntegrityError, match="immutable"):
            jobs[0].db.execute(sql)
    jobs[0].db.execute(
        "DELETE FROM durable_job_sources WHERE run_id=:id AND object_id=:object",
        {"id": job["run_id"], "object": jobs[3]["object_id"]},
    )
    with pytest.raises(CompanyError, match="job_integrity_failed"):
        jobs[0].get(ALICE, job["run_id"])


def test_attempt_sequence_persists_restarts_and_response_deduplicates(jobs):
    job = running(jobs)
    first = jobs[0].record_attempt_started(job["run_id"], request_sha256="a" * 64)
    assert first["seq"] == 1 and first["status"] == "started" and first["completed_at"] is None
    finished = jobs[0].finish_attempt(
        job["run_id"],
        1,
        response={"text": "PRIVATE_MODEL_RESPONSE"},
        usage={"input_tokens": 12, "output_tokens": 3, "estimated_cost_cny": "0.000048"},
    )
    assert finished["status"] == "response_received" and finished["response_sha256"]
    assert finished["response"] == {"text": "PRIVATE_MODEL_RESPONSE"}
    assert (
        jobs[0].finish_attempt(
            job["run_id"], 1, response=finished["response"], usage=finished["usage"]
        )
        == finished
    )
    with pytest.raises(CompanyError, match="job_attempt_already_finished"):
        jobs[0].finish_attempt(job["run_id"], 1, response={"text": "different"})
    reopened = JobsRepository(jobs[0].db, jobs[0].sources)
    second = reopened.record_attempt_started(job["run_id"], request_sha256="b" * 64)
    assert second["seq"] == 2
    unknown = reopened.finish_attempt(
        job["run_id"], 2, unknown=True, usage={"usage_complete": False}
    )
    assert unknown["status"] == "unknown" and unknown["response"] is None
    assert len(reopened.list_attempts(ALICE, job["run_id"])) == 2


def test_concurrent_attempt_start_has_persistent_distinct_sequence(jobs):
    job = running(jobs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(
            pool.map(
                lambda _: jobs[0].record_attempt_started(job["run_id"], request_sha256="a" * 64),
                range(2),
            )
        )
    assert sorted(a["seq"] for a in attempts) == [1, 2]


def test_attempt_links_shared_ledger_and_accepts_inflight_response_after_cancel(jobs):
    job = running(jobs)
    repo = jobs[0]
    with pytest.raises(CompanyError, match="job_attempt_ledger_not_found"):
        repo.record_attempt_started(
            job["run_id"], ledger_id="not-reserved", request_sha256="a" * 64
        )
    repo.db.execute(
        "INSERT INTO agent_runs(run_id,created_at,state,question_sha256,reserved_cny) "
        "VALUES ('shared-ledger','unit','started',:sha,0.5)",
        {"sha": "a" * 64},
    )
    attempt = repo.record_attempt_started(
        job["run_id"], ledger_id="shared-ledger", request_sha256="a" * 64
    )
    assert attempt["ledger_id"] == "shared-ledger"
    assert (
        repo.record_attempt_started(
            job["run_id"], 1, ledger_id="shared-ledger", request_sha256="a" * 64
        )["seq"]
        == 1
    )
    with pytest.raises(CompanyError, match="job_attempt_conflict"):
        repo.record_attempt_started(
            job["run_id"], 1, ledger_id="shared-ledger", request_sha256="b" * 64
        )
    repo.request_cancel(ALICE, job["run_id"], 1)
    repo.transition(job["run_id"], 2, "cancelled")
    assert (
        repo.finish_attempt(job["run_id"], 1, response={"late": True}, usage={"input_tokens": 99})[
            "status"
        ]
        == "response_received"
    )
    assert repo.get_system(job["run_id"])["status"] == "cancelled"


@pytest.mark.parametrize("expiry", ["expired", "deleted", "purge_pending", "missing"])
def test_source_retention_immediately_denies_and_prunes_all_private_bodies_keeps_ledger(
    jobs, expiry
):
    job = waiting(jobs)
    repo = jobs[0]
    event = review(job)
    repo.record_review_fixture(job["run_id"], event, job["state_version"])
    repo.db.execute(
        "INSERT INTO agent_runs(run_id,created_at,state,question_sha256,reserved_cny) "
        "VALUES ('shared-ledger','unit','started',:sha,0.5)",
        {"sha": "a" * 64},
    )
    # A model call completed before the pause.
    repo.db.execute(
        "INSERT INTO durable_model_attempts VALUES "
        "(:id,1,'shared-ledger',:sha,'started',1,NULL,NULL,'{}')",
        {"id": job["run_id"], "sha": "a" * 64},
    )
    repo.finish_attempt(
        job["run_id"],
        1,
        response={"text": "PRIVATE_MODEL_RESPONSE"},
        usage={"input_tokens": 100, "output_tokens": 12},
    )
    before_ledger = repo.db.rows("SELECT * FROM agent_runs")
    oid = jobs[3]["object_id"]
    if expiry == "missing":
        # Model an inconsistent external restore; do not delete real company revisions.
        repo.db.execute("PRAGMA foreign_keys=OFF")
        repo.db.execute(
            "UPDATE company_objects SET object_id='lost-object' WHERE object_id=:id", {"id": oid}
        )
    else:
        column, value = {
            "expired": ("retention_until", 0),
            "deleted": ("deleted_at", 1),
            "purge_pending": ("purge_state", "pending"),
        }[expiry]
        repo.db.execute(
            f"UPDATE company_objects SET {column}=:value WHERE object_id=:id",
            {"value": value, "id": oid},
        )
    with pytest.raises(CompanyError):
        repo.get(ALICE, job["run_id"])
    report = repo.prune()
    assert report["purged_ids"] == [job["run_id"]] and report["failure_ids"] == []
    for table in ("durable_job_inputs", "durable_attempt_bodies", "durable_review_fixture_bodies"):
        assert repo.db.rows(f"SELECT * FROM {table}") == []
    assert repo.db.rows("SELECT * FROM agent_runs") == before_ledger
    metadata = repo.list_attempts_system(job["run_id"])[0]
    assert metadata["response"] is None and metadata["response_sha256"]
    assert metadata["ledger_id"] == "shared-ledger" and metadata["usage"] == {
        "input_tokens": 100,
        "output_tokens": 12,
    }
    assert (
        repo.db.rows("SELECT input_sha256 FROM durable_jobs")[0]["input_sha256"]
        == job["input_sha256"]
    )
    assert repo.prune()["purged_ids"] == []


def test_review_fixture_binds_manifest_revision_origin_and_deduplicates(jobs):
    job = waiting(jobs)
    event = review(job)
    assert jobs[0].record_review_fixture(job["run_id"], event, 2)["created"]
    assert not jobs[0].record_review_fixture(job["run_id"], event, 2)["created"]
    current = jobs[0].get(ALICE, job["run_id"])
    assert current["status"] == "waiting_review" and current["state_version"] == 3
    changed = copy.deepcopy(event)
    changed["command"]["decision"] = "reject"
    with pytest.raises(CompanyError, match="job_review_conflict"):
        jobs[0].record_review_fixture(job["run_id"], changed, 3)


def test_review_fixture_rejects_an_actual_assessment_from_another_job(jobs):
    job = waiting(jobs)
    other = create(jobs, key="different-job")
    foreign = assessment_group(jobs[0], other, job["result_revision"])
    event = review(job, assessment_id=foreign["assessment"]["assessment_id"])
    with pytest.raises(CompanyError, match="job_review_assessment_invalid"):
        jobs[0].record_review_fixture(job["run_id"], event, job["state_version"])
    assert jobs[0].db.rows("SELECT * FROM durable_review_fixtures") == []
    assert jobs[0].get_system(job["run_id"])["state_version"] == job["state_version"]


@pytest.mark.parametrize(
    "change",
    ["empty", "malformed", "duplicate", "revision", "project", "family", "as_of", "profile"],
)
def test_review_fixture_requires_well_formed_groups_bound_to_the_fixed_inputs(jobs, change):
    job = running(jobs)
    group = assessment_group(jobs[0], job, "v1")
    if change == "revision":
        group["assessment"]["result_revision"] = "other-revision"
    elif change in {"project", "family"}:
        field = "project_id" if change == "project" else "project_family_id"
        group["assessment"]["inputs"][field] = "other-project"
    elif change == "as_of":
        group["assessment"]["inputs"]["as_of"] = "2025-01-01T00:00:00Z"
    elif change == "profile":
        group["assessment"]["inputs"]["profile_revision"]["version_id"] = "forged-version"
    groups = (
        []
        if change == "empty"
        else [{}]
        if change == "malformed"
        else [group, copy.deepcopy(group)]
        if change == "duplicate"
        else [group]
    )
    store_result(jobs, job, groups=groups)
    job = jobs[0].transition(
        job["run_id"],
        job["state_version"],
        "waiting_review",
        result_id="business-result",
        result_revision="v1",
    )
    with pytest.raises(CompanyError, match="job_review_assessment_invalid"):
        jobs[0].record_review_fixture(job["run_id"], review(job), job["state_version"])
    assert jobs[0].db.rows("SELECT * FROM durable_review_fixtures") == []


@pytest.mark.parametrize("lifecycle", ["quarantine", "purged"])
def test_review_fixture_rechecks_the_current_result_lifecycle(jobs, lifecycle):
    job = waiting(jobs)
    column = "quarantined=1" if lifecycle == "quarantine" else "purge_state='purged'"
    jobs[0].db.execute(f"UPDATE qualification_result_lifecycle SET {column}")
    with pytest.raises(CompanyError, match="job_result_reference_invalid"):
        jobs[0].record_review_fixture(job["run_id"], review(job), job["state_version"])
    assert jobs[0].db.rows("SELECT * FROM durable_review_fixture_bodies") == []


@pytest.mark.parametrize("field", ["origin", "manifest", "revision", "stale", "future", "run"])
def test_forged_stale_or_server_authenticated_review_is_not_a_b05_fixture(jobs, field):
    job = waiting(jobs)
    event = review(job)
    if field == "origin":
        event["origin"] = "server_authenticated"
    elif field == "manifest":
        event["inputs_manifest_sha256"] = "0" * 64
    elif field == "revision":
        event["result_revision"] = event["command"]["expected_result_revision"] = "wrong-revision"
    elif field in {"stale", "future"}:
        event["occurred_at"] = (
            datetime.now(UTC) + timedelta(days=-1 if field == "stale" else 1)
        ).isoformat()
    else:
        event["run_id"] = "different-run"
    with pytest.raises(CompanyError):
        jobs[0].record_review_fixture(job["run_id"], event, 2)
    assert jobs[0].db.rows("SELECT * FROM durable_review_fixtures") == []


def test_json_nan_and_mutable_prepared_input_do_not_enter_storage(jobs):
    with pytest.raises(CompanyError, match="job_invalid_json"):
        create(jobs, request={"bad": float("nan")})
    prepared = copy.deepcopy(jobs[2])
    job = create(jobs, prepare=lambda: prepared)
    prepared["settings"]["max_requests"] = 999
    assert jobs[0].get_system(job["run_id"])["settings"]["max_requests"] == 4
    assert (
        json.loads(jobs[0].db.rows("SELECT body_json FROM durable_job_inputs")[0]["body_json"])[
            "request"
        ]
        == jobs[1]
    )


@pytest.mark.parametrize(
    "bad_ref",
    ["missing", "owner", "manifest", "revision", "kind", "quarantine", "purged", "failed"],
)
def test_completed_cannot_bind_missing_foreign_changed_or_failed_business_result(jobs, bad_ref):
    job = running(jobs)
    options = {}
    if bad_ref == "owner":
        for ref in jobs[2]["manifest"]["material_revisions"] + [
            jobs[2]["manifest"]["profile_revision"]
        ]:
            jobs[0].auth.grant(ALICE, ref["object_id"], BOB.owner_id, ["read"])
        options["actor"] = BOB
    elif bad_ref == "manifest":
        options["manifest"] = (
            jobs[0]
            .sources.resolve_profile(
                ALICE,
                job["manifest"]["profile_revision"],
                datetime.fromisoformat(job["manifest"]["as_of"]),
            )
            .manifest
        )
    elif bad_ref == "revision":
        options["revision"] = "wrong-revision"
    elif bad_ref == "kind":
        options["kind"] = "candidate_batch"
    elif bad_ref == "failed":
        options["state"] = "failed"
    if bad_ref != "missing":
        store_result(jobs, job, **options)
    if bad_ref in {"quarantine", "purged"}:
        update = "quarantined=1" if bad_ref == "quarantine" else "purge_state='purged'"
        jobs[0].db.execute(f"UPDATE qualification_result_lifecycle SET {update}")
    with pytest.raises(
        CompanyError, match="job_result_reference_invalid|job_result_state_mismatch"
    ):
        jobs[0].transition(
            job["run_id"], 1, "completed", result_id="business-result", result_revision="v1"
        )
    assert jobs[0].get_system(job["run_id"])["status"] == "running"


def test_partial_failed_ref_matches_stored_state_and_cannot_be_claimed_completed(jobs):
    job = running(jobs)
    store_result(jobs, job, state="failed")
    with pytest.raises(CompanyError, match="job_result_state_mismatch"):
        jobs[0].transition(
            job["run_id"], 1, "completed", result_id="business-result", result_revision="v1"
        )
    failed = jobs[0].transition(
        job["run_id"],
        1,
        "failed",
        result_id="business-result",
        result_revision="v1",
        error="synthetic_provider_failure",
    )
    assert failed["result_id"] == "business-result" and failed["status"] == "failed"


def test_missing_live_response_body_is_integrity_failure_not_replayable_unknown(jobs):
    job = running(jobs)
    jobs[0].record_attempt_started(job["run_id"], request_sha256="a" * 64)
    jobs[0].finish_attempt(job["run_id"], 1, response={"saved": True}, usage={"input_tokens": 10})
    jobs[0].db.execute("DELETE FROM durable_attempt_bodies WHERE run_id=:id", {"id": job["run_id"]})
    with pytest.raises(CompanyError, match="job_attempt_integrity_failed"):
        jobs[0].list_attempts_system(job["run_id"])
