"""Formal review/service integration over real HTTP, SQLite and native DBOS.

Only authored FunctionModel answers and the local signed IdP are fixtures. No
external model/source requests are permitted by the suite's transport guards.
"""

import time

import pytest
from bidops.company.authorization import CompanyError, ToolContext
from bidops.jobs.runtime import JobsService
from bidops.review.config import ReviewSettings
from bidops.review.service import ReviewService
from dbos import DBOS
from test_jobs_api import accepted, waiting
from test_jobs_repository import review as fixture_event
from test_review_api import command, make_case, url, wait_native
from test_review_api import company_http as company_http
from test_review_api import jobs_http as jobs_http
from test_review_api import review_http as review_http
from test_review_api import screening_http as screening_http


def eventually(read, predicate, *, message, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.025)
    pytest.fail(f"{message}: {value}")


def post_decision(case, review, **updates):
    response = case["client"].post(
        url(review, "/decisions"),
        headers=case["headers"],
        json=command(review, **updates),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_preexisting_queued_fixture_cannot_complete_a_new_formal_review_case(review_http):
    case = review_http
    run = waiting(case, accepted(case)["run_id"])
    before = len(case["calls"])
    event = fixture_event(run)
    # The B05 crash window has a durable fixture receipt but no native send yet.
    case["jobs"].repository.record_review_fixture(run["run_id"], event, run["state_version"])
    created = case["client"].post(
        "/api/reviews/cases",
        headers=case["headers"],
        json={
            "run_id": run["run_id"],
            "candidate_batch_id": case["batch"]["batch_id"],
            "reviewer_ids": [case["owner"]],
            "idempotency_key": "formal-after-fixture-receipt",
        },
    )
    assert created.status_code == 201, created.text
    DBOS.send(run["run_id"], event["event_id"], topic="review", idempotency_key=event["event_id"])
    stopped = eventually(
        lambda: case["jobs"].repository.get_system(run["run_id"]),
        lambda row: row["status"] == "interrupted",
        message="native fixture was not rejected at consumption",
    )
    assert stopped["error_code"] == "review_fixture_forbidden"
    stored = case["client"].get(url(created.json())).json()
    assert stored["status"] == "open" and stored["events"] == []
    assert all(item["latest_decision"] is None for item in stored["items"])
    assert len(case["calls"]) == before


def test_disabled_review_still_rejects_saved_fixture_after_worker_restart(review_http):
    case = review_http
    run = waiting(case, accepted(case)["run_id"])
    before = len(case["calls"])
    event = fixture_event(run)
    case["jobs"].repository.record_review_fixture(run["run_id"], event, run["state_version"])
    created = case["client"].post(
        "/api/reviews/cases",
        headers=case["headers"],
        json={
            "run_id": run["run_id"],
            "candidate_batch_id": case["batch"]["batch_id"],
            "reviewer_ids": [case["owner"]],
            "idempotency_key": "formal-before-feature-disabled",
        },
    )
    assert created.status_code == 201, created.text
    case["jobs"].close()
    restarted = JobsService(
        case["app"].state.screening,
        case["app"].state.store.settings.data_dir,
        case["jobs"].settings,
    )
    disabled = ReviewService(
        case["app"].state.screening,
        restarted,
        ReviewSettings(enabled=False, _env_file=None),
    )
    case["app"].state.jobs = restarted
    case["app"].state.review = disabled
    try:
        # A fresh native worker automatically resends the persisted fixture.
        restarted.start()
        stopped = eventually(
            lambda: restarted.repository.get_system(run["run_id"]),
            lambda row: row["status"] in {"interrupted", "completed"},
            message="restarted worker did not process its durable fixture outbox",
        )
        assert stopped["status"] == "interrupted"
        assert stopped["error_code"] == "review_fixture_forbidden"
        with pytest.raises(CompanyError, match="review_fixture_forbidden"):
            restarted.review_fixture(run["run_id"], event, stopped["state_version"])
        assert case["client"].get(url(created.json())).status_code == 503
        db = case["app"].state.store.db
        state = db.rows(
            "SELECT status FROM review_case_state WHERE case_id=:id",
            {"id": created.json()["case_id"]},
        )[0]
        assert state["status"] == "open" and db.rows("SELECT * FROM review_events") == []
        result = restarted.screening.repository.get(
            ToolContext(case["owner"]), stopped["result_id"]
        )
        assert result["result"]["human_review_state"] == "unreviewed"
        assert len(case["calls"]) == before
    finally:
        restarted.close()


def test_native_send_failure_preserves_human_event_then_relay_consumes_without_model(
    review_http, monkeypatch
):
    case = review_http
    run, review = make_case(case)
    before = len(case["calls"])

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("synthetic native send unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(DBOS, "send", unavailable)
        saved = post_decision(case, review)
    assert len(saved["events"]) == 1
    delivery = saved["delivery"]["items"][0]
    assert delivery["last_error_code"] == "review_backend_unavailable"
    assert delivery["delivered_at"] is None and delivery["consumed_at"] is None
    assert case["app"].state.store.db.rows("SELECT * FROM review_event_bodies")
    case["review"].deliver_pending()
    delivered = eventually(
        lambda: case["client"].get(url(review)).json(),
        lambda value: value["delivery"]["pending_count"] == 0,
        message="stored human event did not survive native send failure",
    )
    assert delivered["events"] == saved["events"]
    assert delivered["delivery"]["items"][0]["consumed_at"] is not None
    assert case["jobs"].repository.get_system(run["run_id"])["status"] == "waiting_review"
    assert len(case["calls"]) == before


def test_closed_event_repeated_consumption_and_checkpoint_gap_use_saved_result(review_http):
    case = review_http
    run, review = make_case(case)
    before = len(case["calls"])
    for index, item in enumerate(review["items"]):
        review = post_decision(case, review, item=item, key=f"close-item-{index}")
    response = case["client"].post(
        url(review, "/close"),
        headers=case["headers"],
        json={
            "expected_version": review["review_version"],
            "idempotency_key": "close-repeat-consumption",
            "reason": "Explicit owner closure after all item decisions",
        },
    )
    assert response.status_code == 200, response.text
    wait_native(run["run_id"], "SUCCESS")
    event = next(event for event in response.json()["events"] if event["kind"] == "close")
    state = case["jobs"].repository.get_system(run["run_id"])
    for _ in range(2):
        assert case["review"].consume(run["run_id"], event["event_id"]) == "completed"
        assert case["review"].finished(run["run_id"])
    after = case["jobs"].repository.get_system(run["run_id"])
    assert after["state_version"] == state["state_version"]
    assert after["result_id"] == run["result_id"] and after["status"] == "completed"
    assert len(case["calls"]) == before
    company, actor = case["app"].state.company, ToolContext(case["owner"])
    payload = company.repo.get(actor, case["profile"]["object_id"])["payload"]
    payload["display_name"] = "SYNTHETIC updated after historical completion"
    company.repo.put(
        actor,
        payload,
        object_id=case["profile"]["object_id"],
        expected_version=case["profile"]["version_id"],
    )
    with pytest.raises(CompanyError, match="review_sources_changed"):
        case["review"].consume(run["run_id"], event["event_id"])
    historical = case["client"].get(url(review))
    assert historical.status_code == 200
    assert not historical.json()["source_state"]["current"]
    assert (
        case["jobs"].repository.get_system(run["run_id"])["state_version"] == state["state_version"]
    )
    assert len(case["calls"]) == before


@pytest.mark.parametrize("phase", ["before_preflight", "after_preflight"])
def test_close_consumption_rechecks_changed_profile_after_native_execution_guard(
    review_http, monkeypatch, phase
):
    case = review_http
    run, review = make_case(case)
    for index, item in enumerate(review["items"]):
        review = post_decision(case, review, item=item, key=f"race-item-{index}")
    consume, injected, checked = case["review"].consume, [], []

    def update_profile():
        company, actor = case["app"].state.company, ToolContext(case["owner"])
        profile = case["profile"]
        payload = company.repo.get(actor, profile["object_id"], profile["version_id"])["payload"]
        payload["display_name"] = "SYNTHETIC changed after native guard"
        injected.append(
            company.repo.put(
                actor,
                payload,
                object_id=profile["object_id"],
                expected_version=profile["version_id"],
            )
        )

    def change_profile_after_guard(run_id, event_id):
        # Native human_review_step has already run require_execution, but the
        # final review/job business transaction has not begun.
        event = case["app"].state.store.db.rows(
            "SELECT kind FROM review_outbox WHERE event_id=:id", {"id": event_id}
        )[0]
        if event["kind"] == "closed" and not injected:
            if phase == "before_preflight":
                update_profile()
            else:
                sources = case["review"].repository.sources
                changes = sources.changes

                def change_after_preflight(context, manifest):
                    state = changes(context, manifest)
                    checked.append(state)
                    if not injected:
                        update_profile()
                    return state

                with monkeypatch.context() as patch:
                    patch.setattr(sources, "changes", change_after_preflight)
                    return consume(run_id, event_id)
        return consume(run_id, event_id)

    monkeypatch.setattr(case["review"], "consume", change_profile_after_guard)
    before = len(case["calls"])
    response = case["client"].post(
        url(review, "/close"),
        headers=case["headers"],
        json={
            "expected_version": review["review_version"],
            "idempotency_key": "close-before-current-source-changes",
            "reason": "Explicit closure cannot commit across a source head update",
        },
    )
    assert response.status_code == 200, response.text
    stopped = eventually(
        lambda: case["jobs"].repository.get_system(run["run_id"]),
        lambda row: row["status"] in {"interrupted", "completed"},
        message="native close did not reach its final guarded business commit",
    )
    assert len(injected) == 1
    if phase == "after_preflight":
        assert checked[0]["current"] and any(not state["current"] for state in checked[1:])
    assert stopped["status"] == "interrupted"
    assert stopped["error_code"] in {"job_inputs_changed", "review_sources_changed"}
    closed = case["app"].state.store.db.rows("SELECT * FROM review_outbox WHERE kind='closed'")[0]
    assert closed["consumed_at"] is None
    saved = case["client"].get(url(review)).json()
    assert saved["source_state"]["current"] is False
    assert len(case["calls"]) == before


def test_successor_service_rolls_back_new_job_on_case_cas_then_replays_one_link(
    review_http, monkeypatch
):
    case = review_http
    old_run, review = make_case(case)
    with monkeypatch.context() as patch:
        patch.setattr(case["review"], "deliver_pending", lambda: None)
        review = post_decision(case, review, decision="request_materials")
    old_event = review["events"][-1]["event_id"]
    upload = case["client"].post(
        "/api/company/materials",
        headers={**case["headers"], "Content-Type": "text/plain", "X-Filename": "extra.txt"},
        content=b"SYNTHETIC new supplementary qualification source",
    )
    assert upload.status_code == 201, upload.text
    extra = {key: upload.json()[key] for key in ("object_id", "version_id", "sha256")}
    payload = {
        "expected_version": review["review_version"] - 1,
        "revision_id": review["head_revision_id"],
        "trigger_event_id": review["events"][-1]["event_id"],
        "idempotency_key": "actual-service-successor",
        "reason": "Recalculate every item using an additional fixed source",
        "request": {
            "profile_revision": case["profile"],
            "project_id": case["notice"].project_id,
            "additional_material_revisions": [extra],
        },
    }
    db = case["app"].state.store.db
    before_jobs = db.rows("SELECT run_id FROM durable_jobs")
    before_calls = len(case["calls"])
    denied = case["client"].post(url(review, "/successors"), headers=case["headers"], json=payload)
    assert denied.status_code == 409 and denied.json()["detail"] == "review_version_conflict"
    assert db.rows("SELECT run_id FROM durable_jobs") == before_jobs
    assert db.rows("SELECT * FROM review_successors") == []
    assert len(case["calls"]) == before_calls
    old_receipt = db.rows("SELECT * FROM review_outbox WHERE event_id=:id", {"id": old_event})[0]
    assert not old_receipt["frozen"] and old_receipt["consumed_at"] is None
    payload["expected_version"] = review["review_version"]
    linked = case["client"].post(url(review, "/successors"), headers=case["headers"], json=payload)
    assert linked.status_code == 202, linked.text
    new_run = linked.json()["successors"][0]["run_id"]
    waiting(case, new_run)
    after_calls = len(case["calls"])
    replay = case["client"].post(url(review, "/successors"), headers=case["headers"], json=payload)
    assert replay.status_code == 202, replay.text
    assert replay.json()["successors"][0]["run_id"] == new_run
    assert len(db.rows("SELECT * FROM durable_jobs")) == len(before_jobs) + 1
    assert len(db.rows("SELECT * FROM review_successors")) == 1
    assert case["jobs"].repository.get_system(old_run["run_id"])["status"] == "cancelled"
    assert len(case["calls"]) == after_calls
    old_receipt = db.rows("SELECT * FROM review_outbox WHERE event_id=:id", {"id": old_event})[0]
    assert old_receipt["frozen"] and old_receipt["consumed_at"] is None
    delivery = replay.json()["delivery"]
    assert delivery["pending_count"] == 0 and delivery["frozen_count"] == 1
    replacement = next(item for item in delivery["items"] if item["kind"] == "superseded")
    assert not replacement["frozen"] and replacement["consumed_at"] is not None
    attached = case["client"].post(
        url(review, "/revisions"),
        headers=case["headers"],
        json={
            "run_id": new_run,
            "expected_version": replay.json()["review_version"],
            "idempotency_key": "attach-after-unconsumed-old-notification",
            "reason": "Review the complete new result without stale delivery work",
        },
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["delivery"]["pending_count"] == 0
    assert all(item["latest_decision"] is None for item in attached.json()["items"])
    assert len(case["calls"]) == after_calls


def test_relay_rotates_past_permanently_unauthorized_event(review_http, monkeypatch):
    case = review_http
    run, review = make_case(case)
    relay = case["review"].deliver_pending
    with monkeypatch.context() as patch:
        patch.setattr(case["review"], "deliver_pending", lambda: None)
        case["headers"] = case["login"]("bob")
        first = post_decision(case, review, key="revoked-old-event")
        case["headers"] = case["login"]("alice")
        second_payload = command(first, key="valid-new-event")
        second_payload["supersedes_event_id"] = first["events"][-1]["event_id"]
        second = case["client"].post(
            url(first, "/decisions"), headers=case["headers"], json=second_payload
        )
        assert second.status_code == 200, second.text
    first_id = first["events"][-1]["event_id"]
    second_id = second.json()["events"][-1]["event_id"]
    db = case["app"].state.store.db
    db.execute(
        "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id=:id",
        {"id": case["bob"]},
    )
    repository = case["review"].repository
    pending = repository.pending_events
    # A batch of one exposes the same starvation boundary as production's 1000.
    monkeypatch.setattr(repository, "pending_events", lambda *, limit=100: pending(limit=1))
    before = len(case["calls"])
    relay()
    first_receipt = db.rows("SELECT * FROM review_outbox WHERE event_id=:id", {"id": first_id})[0]
    assert first_receipt["last_error_code"] == "review_decide_denied"
    assert first_receipt["attempts"] == 1 and first_receipt["consumed_at"] is None
    assert pending(limit=1)[0]["event_id"] == second_id
    relay()
    eventually(
        lambda: db.rows("SELECT * FROM review_outbox WHERE event_id=:id", {"id": second_id})[0],
        lambda value: value["consumed_at"] is not None,
        message="fresh authorized event was starved by an older revoked reviewer",
    )
    assert (
        db.rows("SELECT consumed_at FROM review_outbox WHERE event_id=:id", {"id": first_id})[0][
            "consumed_at"
        ]
        is None
    )
    assert case["jobs"].repository.get_system(run["run_id"])["status"] == "waiting_review"
    assert len(case["calls"]) == before
