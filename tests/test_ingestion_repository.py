"""Real SQLite atomic acquisition tests; all permissions and documents synthetic."""

import base64
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from bidops.company.authorization import CompanyAuthorization, CompanyError, ToolContext
from bidops.enterprise.auth import ROLE_PERMISSIONS
from bidops.ingestion.config import IngestionSettings
from bidops.ingestion.repository import IngestionRepository, canonical, sha
from bidops.models import Snapshot
from bidops.notice_data.attachment_parser import prepare_attachment
from bidops.sources import SourcePolicy, load_sources
from bidops.storage import snapshot_id
from sqlalchemy.exc import IntegrityError

ADMIN, OTHER, VIEWER = (ToolContext(name) for name in ("ingest-admin", "other-admin", "viewer"))


def encoded(data):
    return base64.b64encode(data).decode()


def document(code="ONE", kind="公开招标公告"):
    return (
        f"项目编号：ENGINEERING-{code}\n项目名称：工程夹具\n"
        f"公告类型：{kind}\n采购测试，非真实公告。"
    ).encode()


@pytest.fixture
def ingestion(store, settings, tmp_path, monkeypatch):
    monkeypatch.setitem(ROLE_PERMISSIONS, "admin", ROLE_PERMISSIONS["admin"] | {"source:manage"})
    instant = [time.time()]
    registry = json.loads(settings.source_registry.read_text())
    registry[1]["allowed_urls"] = ["https://www.ccgp-tianjin.gov.cn/engineering-notice"]
    path = tmp_path / "controlled-sources.json"
    path.write_text(json.dumps(registry))
    settings.source_registry = path
    store.sources = load_sources(path)
    auth = CompanyAuthorization(store.db, clock=lambda: instant[0])
    for actor, roles in [(ADMIN, ["admin"]), (OTHER, ["admin"]), (VIEWER, ["viewer"])]:
        store.db.execute(
            "INSERT INTO enterprise_subjects VALUES (:id,'https://fixture.example',:id,'active',"
            ":roles,:now,:until,:now)",
            {
                "id": actor.owner_id,
                "roles": json.dumps(roles),
                "now": instant[0],
                "until": instant[0] + 100000,
            },
        )
    repo = IngestionRepository(
        store,
        auth,
        IngestionSettings(enabled=True),
        {"application_version": "a" * 64, "lock_sha256": "b" * 64},
        clock=lambda: instant[0],
    )
    repo.test_clock = instant
    return repo


def manual(repo, *, key="fixture-import", source=0, item_count=1, attachments=None, pages=1):
    source = repo.store.sources[source]
    items = []
    for index in range(item_count):
        items.append(
            {
                "source_url": source.allowed_urls[index],
                "content_type": "text/plain",
                "body_base64": encoded(document(str(index))),
                "source_complete": True,
                "attachments": copy.deepcopy(attachments or []),
            }
        )
    return {
        "source_id": source.id,
        "idempotency_key": key,
        "rights_attestation": True,
        "responsibility_reason": "PRIVATE RESPONSIBILITY synthetic fixture only",
        "pages": [
            {"items": copy.deepcopy(items), "cursor_after": f"cursor-{i}"} for i in range(pages)
        ],
    }


def http(repo, *, key="fixture-http", max_requests=10):
    source = repo.store.sources[0]
    values = json.loads(repo.store.settings.source_registry.read_text())
    now = datetime.fromtimestamp(repo.clock(), UTC)
    policy = SourcePolicy(
        revision="fixture-policy-1",
        status="allowed",
        evidence_sha256=sha("fixture evidence"),
        evidence_reference="ENGINEERING ONLY NOT live permission",
        reviewed_by="fixture-authority",
        reviewed_at=now - timedelta(seconds=10),
        valid_from=now - timedelta(seconds=10),
        valid_until=now + timedelta(hours=1),
        acquisition_modes=["manual_import", "manual_fetch", "scheduled_fetch"],
        url_scope=source.allowed_urls,
        rate_limit_seconds=5,
        max_attempts=3,
        max_requests=max_requests,
        max_bytes=2_000_000,
        total_timeout_seconds=60,
    )
    values[0].update(
        robots_status="allowed",
        terms_status="allowed",
        permission_evidence="fixture",
        reviewed_at=now.isoformat(),
        policy=policy.model_dump(mode="json"),
    )
    repo.store.settings.source_registry.write_text(json.dumps(values))
    repo.store.settings.network_enabled = True
    return {
        "source_id": source.id,
        "idempotency_key": key,
        "items": [{"source_url": source.allowed_urls[0], "attachments": []}],
    }


def prepare_page(repo, run_id, seq=0):
    started = repo.begin_page(run_id, seq)
    record = repo.get_system(run_id)
    observed = datetime.fromisoformat(started["observed_at"])
    declared = (
        record["request"]["pages"][seq]["items"]
        if record["method"] == "manual_import"
        else record["request"]["items"]
    )
    items = []
    for item in declared:
        data = (
            base64.b64decode(item["body_base64"])
            if record["method"] == "manual_import"
            else document()
        )
        digest = sha(data)
        snapshot = Snapshot(
            snapshot_id=snapshot_id(item["source_url"], digest),
            source_url=item["source_url"],
            sha256=digest,
            byte_size=len(data),
            fetched_at=observed,
            content_type=item.get("content_type", "text/plain"),
            encoding="utf-8",
            access_method=record["method"],
            manual_attestation=record["method"] == "manual_import",
            http_status=200 if record["method"] == "http_fetch" else None,
        )
        notice = repo.store.prepare(snapshot, data)
        attachments = []
        for target in item.get("attachments", []):
            failed = target.get("failed", False)
            attachment = {
                "parent_version_id": "pending-server-bind",
                "attachment_key": target["attachment_key"],
                "filename": target["filename"],
                "source_url": target.get("source_url"),
                "content_type": target["content_type"],
                "observed_at": observed,
                "acquisition_status": "failed" if failed else "downloaded",
                "parse_status": "pending",
                "error_code": target.get("error_code"),
            }
            attachments.append(
                prepare_attachment(
                    attachment,
                    None if failed else base64.b64decode(target["body_base64"]),
                    expected_page_count=target.get("expected_page_count"),
                )
            )
        items.append((notice, attachments, item.get("source_complete", False)))
    return {
        "expected_cursor_version": started["expected_cursor_version"],
        "page_sha256": sha(canonical(declared)),
        "items": items,
        "cursor_after": record["request"]["pages"][seq].get("cursor_after")
        if record["method"] == "manual_import"
        else None,
        "observed_at": observed,
    }


def start(repo, payload=None, context=ADMIN, method="manual_import"):
    record = repo.create(context, payload or manual(repo), method)
    return repo.start(record["run_id"])


def state(repo, source_id="ccgp"):
    return repo.db.rows(
        "SELECT * FROM ingestion_source_state WHERE source_id=:id", {"id": source_id}
    )[0]


def commit(repo, record, seq=0):
    return repo.commit_page(record["run_id"], seq, **prepare_page(repo, record["run_id"], seq))


def test_unknown_source_manual_requires_attestation_and_server_actor(ingestion):
    repo = ingestion
    command = manual(repo)
    created = repo.create(ADMIN, command, "manual_import")
    assert created["owner_id"] == ADMIN.owner_id and created["source"]["policy"] is None
    assert created["request"] == command
    assert created["input_sha256"] == sha(canonical(command))
    assert created["created"] is True
    for changed in [{**command, "rights_attestation": False}, {**command, "actor": "admin"}]:
        with pytest.raises(CompanyError, match="ingestion_invalid_request"):
            repo.create(ADMIN, changed, "manual_import")
    with pytest.raises(CompanyError, match="ingestion_admin_required"):
        repo.create(VIEWER, command, "manual_import")


def test_same_key_replay_before_capacity_and_conflicting_payload(ingestion):
    repo = ingestion
    repo.settings.max_active_runs = 1
    command = manual(repo)
    first = repo.create(ADMIN, command, "manual_import")
    repo.test_clock[0] += 500
    replay = repo.create(ADMIN, copy.deepcopy(command), "manual_import")
    assert replay["run_id"] == first["run_id"] and not replay["created"]
    assert replay["created_at"] == first["created_at"]
    with pytest.raises(CompanyError, match="ingestion_idempotency_conflict"):
        repo.create(ADMIN, {**command, "responsibility_reason": "changed"}, "manual_import")
    with pytest.raises(CompanyError, match="ingestion_capacity_reached"):
        repo.create(ADMIN, manual(repo, key="other", source=1), "manual_import")
    with pytest.raises(CompanyError, match="ingestion_source_busy"):
        repo.create(ADMIN, manual(repo, key="same-source-other"), "manual_import")


def test_concurrent_identical_submits_create_one_run(ingestion):
    repo = ingestion
    command = manual(repo)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repo.create(ADMIN, command, "manual_import"), range(2)))
    assert {r["created"] for r in results} == {True, False}
    assert len({r["run_id"] for r in results}) == 1
    assert repo.db.rows("SELECT COUNT(*) n FROM ingestion_runs")[0]["n"] == 1


def test_atomic_capacity_across_different_sources(ingestion):
    repo = ingestion
    repo.settings.max_active_runs = 1
    commands = [manual(repo, key="capacity-0"), manual(repo, key="capacity-1", source=1)]

    def submit(command):
        try:
            return repo.create(ADMIN, command, "manual_import")["run_id"]
        except CompanyError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, commands))
    assert sum(isinstance(r, str) for r in results) == 1 and results.count(429) == 1


def test_owner_and_admin_read_scope_no_private_body_and_downgraded_owner_can_stop(ingestion):
    repo = ingestion
    record = start(repo)
    with pytest.raises(CompanyError, match="ingestion_run_not_found"):
        repo.read(VIEWER, record["run_id"])
    assert repo.read(OTHER, record["run_id"])["run_id"] == record["run_id"]
    assert repo.list(VIEWER)["items"] == []
    repo.db.execute(
        "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id=:id",
        {"id": ADMIN.owner_id},
    )
    public = repo.read(ADMIN, record["run_id"])
    assert public["pages"] == []
    assert "PRIVATE RESPONSIBILITY" not in canonical(public)
    assert "body_base64" not in canonical(repo.list(ADMIN))
    assert not {"request", "source", "versions", "body_sha256"} & public.keys()
    with pytest.raises(CompanyError, match="ingestion_admin_required"):
        repo.require_execution(record["run_id"])
    cancelled = repo.request_cancel(ADMIN, record["run_id"], public["state_version"])
    assert cancelled["status"] == "cancel_requested" and "request" not in cancelled
    repo.finish(record["run_id"], "cancelled")
    with pytest.raises(CompanyError, match="ingestion_terminal_state_conflict"):
        repo.finish(record["run_id"], "completed")
    assert state(repo)["active_run_id"] is None


def test_begin_page_freezes_time_and_cursor_before_any_body_commit(ingestion):
    repo = ingestion
    record = start(repo)
    first = repo.begin_page(record["run_id"], 0)
    repo.test_clock[0] += 100
    again = repo.begin_page(record["run_id"], 0)
    assert first == again
    assert state(repo)["last_attempt"] == repo.clock()
    assert state(repo)["last_success"] is None and state(repo)["cursor_version"] == 0
    assert repo.db.rows("SELECT COUNT(*) n FROM snapshots")[0]["n"] == 0


def test_successful_page_is_atomic_and_replay_never_adds_observations(ingestion):
    repo = ingestion
    record = start(repo, manual(repo, item_count=2))
    args = prepare_page(repo, record["run_id"])
    first = repo.commit_page(record["run_id"], 0, **args)
    before = repo.db.rows("SELECT * FROM nd_observations ORDER BY snapshot_id")
    repo.finish(record["run_id"], "completed")
    repo.test_clock[0] += 1000
    assert repo.commit_page(record["run_id"], 0, **args) == first
    assert repo.db.rows("SELECT * FROM nd_observations ORDER BY snapshot_id") == before
    assert first["counts"] == {
        "pages": 1,
        "items": 2,
        "imported": 2,
        "unchanged": 0,
        "failed": 0,
        "attachments": 0,
        "attachment_failed": 0,
    }
    assert (
        state(repo)["cursor_version"] == 1 and json.loads(state(repo)["cursor_json"]) == "cursor-0"
    )
    assert len(repo.read(ADMIN, record["run_id"])["pages"][0]["receipt"]["version_ids"]) == 2
    with pytest.raises(CompanyError, match="ingestion_page_replay_conflict"):
        repo.commit_page(record["run_id"], 0, **{**args, "page_sha256": "f" * 64})


def test_second_item_db_failure_rolls_back_everything_but_prepared_files(ingestion, monkeypatch):
    repo = ingestion
    record = start(repo, manual(repo, item_count=2))
    args = prepare_page(repo, record["run_id"])
    real_persist = repo.store.persist
    calls = []

    def persist(prepared, conn, **kwargs):
        calls.append(prepared)
        if len(calls) == 2:
            raise RuntimeError("synthetic second-item DB failure")
        return real_persist(prepared, conn, **kwargs)

    monkeypatch.setattr(repo.store, "persist", persist)
    with pytest.raises(RuntimeError, match="second-item"):
        repo.commit_page(record["run_id"], 0, **args)
    for table in [
        "snapshots",
        "notices",
        "nd_versions",
        "nd_observations",
        "ingestion_pages",
        "audit_events",
    ]:
        assert repo.db.rows(f"SELECT COUNT(*) n FROM {table}")[0]["n"] == 0
    assert state(repo)["cursor_version"] == 0 and state(repo)["last_success"] is None
    assert repo.get_system(record["run_id"])["next_page"] == 0
    assert len(list(repo.store.raw.iterdir())) == 2
    monkeypatch.setattr(repo.store, "persist", real_persist)
    assert repo.commit_page(record["run_id"], 0, **args)["cursor_advanced"]


def test_failed_attachment_is_visible_partial_without_cursor_or_last_success(ingestion):
    repo = ingestion
    attachment = {
        "attachment_key": "missing",
        "filename": "fixture.pdf",
        "content_type": "application/pdf",
        "failed": True,
        "error_code": "fixture_download_failed",
    }
    record = start(repo, manual(repo, attachments=[attachment]))
    receipt = commit(repo, record)
    assert receipt["status"] == "partial" and not receipt["cursor_advanced"]
    assert receipt["counts"]["attachment_failed"] == 1
    assert repo.get_system(record["run_id"])["status"] == "partial"
    current = state(repo)
    assert current["cursor_version"] == 0 and current["last_success"] is None
    assert current["active_run_id"] is None
    saved = repo.store.notice_data.get_attachment(receipt["attachment_record_ids"][0])
    assert saved.acquisition_status == "failed" and saved.error_code == "fixture_download_failed"
    assert repo.db.rows("SELECT source_complete FROM nd_observations")[0]["source_complete"] == 0


def test_attachment_parent_is_server_bound_and_all_rows_share_transaction(ingestion):
    repo = ingestion
    attachment = {
        "attachment_key": "terms",
        "filename": "fixture.txt",
        "content_type": "text/plain",
        "body_base64": encoded(b"ENGINEERING attachment terms"),
    }
    record = start(repo, manual(repo, attachments=[attachment]))
    receipt = commit(repo, record)
    saved = repo.store.notice_data.get_attachment(receipt["attachment_record_ids"][0])
    assert saved.parent_version_id == receipt["version_ids"][0]
    assert saved.parse_status == "parsed"
    assert repo.db.rows("SELECT source_complete FROM nd_observations")[0]["source_complete"] == 1


@pytest.mark.parametrize("change", ["cursor", "time", "members", "bytes", "completeness"])
def test_commit_rejects_forged_page_bindings(ingestion, change):
    repo = ingestion
    record = start(repo)
    args = prepare_page(repo, record["run_id"])
    if change == "cursor":
        args["cursor_after"] = "invented"
    elif change == "time":
        args["observed_at"] += timedelta(seconds=1)
    elif change == "members":
        args["items"] = []
    elif change == "bytes":
        prepared, attachments, complete = args["items"][0]
        from dataclasses import replace

        bad = prepared.snapshot.model_copy(update={"sha256": "f" * 64})
        args["items"][0] = (replace(prepared, snapshot=bad), attachments, complete)
    else:
        raw = repo.get_system(record["run_id"])["request"]
        raw["pages"][0]["items"][0]["source_complete"] = False
        # New run with an actual non-attested command, rather than mutating frozen input.
        repo.request_cancel(
            ADMIN, record["run_id"], repo.read(ADMIN, record["run_id"])["state_version"]
        )
        repo.finish(record["run_id"], "cancelled")
        raw["idempotency_key"] = "not-complete"
        record = start(repo, raw)
        args = prepare_page(repo, record["run_id"])
        prepared, attachments, _ = args["items"][0]
        args["items"][0] = (prepared, attachments, True)
    with pytest.raises((CompanyError, ValueError)):
        repo.commit_page(record["run_id"], 0, **args)
    assert repo.db.rows("SELECT COUNT(*) n FROM snapshots")[0]["n"] == 0
    assert state(repo)["cursor_version"] == 0


@pytest.mark.parametrize(
    "change", ["role", "freshness", "quarantine", "version", "cancel", "policy"]
)
def test_execution_always_rechecks_current_authority(ingestion, change):
    repo = ingestion
    command = http(repo)
    record = start(repo, command, method="http_fetch")
    if change == "role":
        repo.db.execute(
            "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id=:id",
            {"id": ADMIN.owner_id},
        )
    elif change == "freshness":
        repo.db.execute(
            "UPDATE enterprise_subjects SET fresh_until=0 WHERE owner_id=:id",
            {"id": ADMIN.owner_id},
        )
    elif change == "quarantine":
        repo.db.execute(
            "UPDATE ingestion_run_state SET quarantined=1 WHERE run_id=:id",
            {"id": record["run_id"]},
        )
    elif change == "version":
        repo.versions["application_version"] = "c" * 64
    elif change == "cancel":
        repo.request_cancel(ADMIN, record["run_id"], record["state_version"])
    else:
        values = json.loads(repo.store.settings.source_registry.read_text())
        values[0]["policy"]["reviewed_by"] = "changed-with-same-readable-revision"
        repo.store.settings.source_registry.write_text(json.dumps(values))
    with pytest.raises(CompanyError):
        repo.require_execution(record["run_id"])
    with pytest.raises(CompanyError):
        repo.reserve_request(record["run_id"], record["source_id"], record["policy_revision"])
    assert repo.get_system(record["run_id"])["request_count"] == 0


def test_request_slots_are_persistent_bounded_and_wait_without_consuming(ingestion):
    repo = ingestion
    command = http(repo, max_requests=2)
    record = start(repo, command, method="http_fetch")
    args = (record["run_id"], record["source_id"], record["policy_revision"])
    assert repo.reserve_request(*args) == 0
    restarted = IngestionRepository(
        repo.store, repo.auth, repo.settings, repo.versions, clock=repo.clock
    )
    assert restarted.reserve_request(*args) == 5
    assert restarted.get_system(record["run_id"])["request_count"] == 1
    repo.test_clock[0] += 5
    assert restarted.reserve_request(*args) == 0
    repo.test_clock[0] += 5
    with pytest.raises(CompanyError, match="ingestion_request_limit"):
        restarted.reserve_request(*args)
    assert restarted.get_system(record["run_id"])["request_count"] == 2


def test_failure_does_not_refresh_success_or_notice_observations(ingestion):
    repo = ingestion
    first = start(repo)
    commit(repo, first)
    repo.finish(first["run_id"], "completed")
    previous, observations = (
        state(repo)["last_success"],
        repo.db.rows("SELECT * FROM nd_observations"),
    )
    repo.test_clock[0] += 1000
    second = start(repo, manual(repo, key="failed-refresh"))
    repo.begin_page(second["run_id"], 0)
    repo.record_failure(second["run_id"], "fixture_timeout", page_index=0)
    repo.finish(second["run_id"], "failed", error_code="fixture_timeout")
    assert state(repo)["last_success"] == previous
    assert state(repo)["last_attempt"] > previous
    assert repo.db.rows("SELECT * FROM nd_observations") == observations
    assert repo.db.rows("SELECT code FROM ingestion_failures")[0]["code"] == "fixture_timeout"


def test_final_policy_recheck_rolls_back_notice_and_page(ingestion, monkeypatch):
    repo = ingestion
    command = http(repo)
    record = start(repo, command, method="http_fetch")
    args = prepare_page(repo, record["run_id"])
    real_persist = repo.store.persist

    def persist(*args, **kwargs):
        result = real_persist(*args, **kwargs)
        values = json.loads(repo.store.settings.source_registry.read_text())
        values[0]["terms_status"] = "denied"
        repo.store.settings.source_registry.write_text(json.dumps(values))
        return result

    monkeypatch.setattr(repo.store, "persist", persist)
    with pytest.raises(CompanyError, match="source_permission_unconfirmed"):
        repo.commit_page(record["run_id"], 0, **args)
    assert repo.db.rows("SELECT COUNT(*) n FROM snapshots")[0]["n"] == 0
    assert repo.db.rows("SELECT COUNT(*) n FROM ingestion_pages")[0]["n"] == 0
    assert state(repo)["cursor_version"] == 0


def test_inputs_runs_pages_and_policy_metadata_are_immutable(ingestion):
    repo = ingestion
    record = start(repo)
    commit(repo, record)
    for table in [
        "ingestion_runs",
        "ingestion_inputs",
        "ingestion_policy_revisions",
        "ingestion_pages",
        "ingestion_page_attempts",
    ]:
        with pytest.raises(IntegrityError, match="immutable_ingestion"):
            repo.db.execute(f"DELETE FROM {table}")
    with pytest.raises(IntegrityError, match="immutable_ingestion_input"):
        repo.db.execute("UPDATE ingestion_inputs SET body_json='{}'")


def test_concurrent_page_commit_replays_one_receipt(ingestion):
    repo = ingestion
    record = start(repo)
    args = prepare_page(repo, record["run_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: repo.commit_page(record["run_id"], 0, **args), range(2)))
    assert receipts[0] == receipts[1]
    assert repo.db.rows("SELECT COUNT(*) n FROM ingestion_pages")[0]["n"] == 1
    assert repo.db.rows("SELECT COUNT(*) n FROM nd_observations")[0]["n"] == 1
    assert state(repo)["cursor_version"] == 1


def test_different_owners_same_key_are_isolated_and_old_owner_cannot_read_other(ingestion):
    repo = ingestion
    first = start(repo)
    repo.request_cancel(ADMIN, first["run_id"], first["state_version"])
    repo.finish(first["run_id"], "cancelled")
    second = repo.create(OTHER, manual(repo), "manual_import")
    assert second["run_id"] != first["run_id"]
    repo.db.execute(
        "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id=:id",
        {"id": ADMIN.owner_id},
    )
    with pytest.raises(CompanyError, match="ingestion_run_not_found"):
        repo.read(ADMIN, second["run_id"])
    assert [r["run_id"] for r in repo.list(ADMIN)["items"]] == [first["run_id"]]


def test_cancel_is_cas_and_can_never_be_restarted(ingestion):
    repo = ingestion
    record = start(repo)
    cancelled = repo.request_cancel(ADMIN, record["run_id"], record["state_version"])
    with pytest.raises(CompanyError, match="ingestion_state_conflict"):
        repo.request_cancel(ADMIN, record["run_id"], record["state_version"])
    assert cancelled["state_version"] == record["state_version"] + 1
    repo.finish(record["run_id"], "cancelled")
    with pytest.raises(CompanyError, match="ingestion_cancelled"):
        repo.start(record["run_id"])
    assert state(repo)["active_run_id"] is None


def test_concurrent_request_slots_consume_one_and_persist_wait(ingestion):
    repo = ingestion
    record = start(repo, http(repo), method="http_fetch")
    with ThreadPoolExecutor(max_workers=2) as pool:
        waits = list(
            pool.map(
                lambda _: repo.reserve_request(
                    record["run_id"], record["source_id"], record["policy_revision"]
                ),
                range(2),
            )
        )
    assert sorted(waits) == [0, 5]
    assert repo.get_system(record["run_id"])["request_count"] == 1


def test_all_manual_pages_required_and_cursor_can_repeat_without_skipping(ingestion):
    repo = ingestion
    command = manual(repo, pages=2)
    command["pages"][1]["cursor_after"] = command["pages"][0]["cursor_after"]
    record = start(repo, command)
    commit(repo, record)
    with pytest.raises(CompanyError, match="ingestion_pages_incomplete"):
        repo.finish(record["run_id"], "completed")
    repo.test_clock[0] += 5
    second = commit(repo, record, seq=1)
    assert second["from_cursor_version"] == 1 and second["cursor_version"] == 2
    completed = repo.finish(record["run_id"], "completed")
    assert completed["counts"]["imported"] == 1 and completed["counts"]["unchanged"] == 1
    assert completed["counts"]["pages"] == 2
    assert state(repo)["cursor_version"] == 2


def test_cursor_cas_failure_never_persists_prepared_notice(ingestion):
    repo = ingestion
    record = start(repo)
    args = prepare_page(repo, record["run_id"])
    repo.db.execute("UPDATE ingestion_source_state SET cursor_version=cursor_version+1")
    with pytest.raises(CompanyError, match="ingestion_cursor_conflict"):
        repo.commit_page(record["run_id"], 0, **args)
    assert repo.db.rows("SELECT COUNT(*) n FROM snapshots")[0]["n"] == 0


@pytest.mark.parametrize("change", ["denied", "missing_mode", "expired", "future", "outside_scope"])
def test_manual_policy_explicit_restrictions_are_not_bypassed(ingestion, change):
    repo = ingestion
    http(repo)
    values = json.loads(repo.store.settings.source_registry.read_text())
    policy = values[0]["policy"]
    if change == "denied":
        policy["status"] = "denied"
    elif change == "missing_mode":
        policy["acquisition_modes"] = ["manual_fetch"]
    elif change == "expired":
        repo.test_clock[0] += 3600
    elif change == "future":
        policy["reviewed_at"] = datetime.fromtimestamp(repo.clock() + 5, UTC).isoformat()
    else:
        policy["url_scope"] = [values[0]["allowed_urls"][1]]
    repo.store.settings.source_registry.write_text(json.dumps(values))
    with pytest.raises(CompanyError):
        repo.create(ADMIN, manual(repo), "manual_import")
    assert repo.db.rows("SELECT COUNT(*) n FROM ingestion_runs")[0]["n"] == 0


def test_http_unknown_or_legacy_allowed_cannot_create_a_run(ingestion):
    repo = ingestion
    command = {
        "source_id": "ccgp",
        "idempotency_key": "unlicensed",
        "items": [{"source_url": repo.store.sources[0].allowed_urls[0]}],
    }
    repo.store.settings.network_enabled = True
    with pytest.raises(CompanyError, match="source_permission_unconfirmed"):
        repo.create(ADMIN, command, "http_fetch")
    values = json.loads(repo.store.settings.source_registry.read_text())
    values[0].update(
        robots_status="allowed", terms_status="allowed", permission_evidence="legacy only"
    )
    repo.store.settings.source_registry.write_text(json.dumps(values))
    with pytest.raises(CompanyError, match="source_policy_missing"):
        repo.create(ADMIN, command, "http_fetch")


def test_cross_host_manual_attachment_cannot_be_admitted(ingestion):
    repo = ingestion
    attachment = {
        "attachment_key": "x",
        "filename": "fixture.txt",
        "source_url": "https://www.ccgp-tianjin.gov.cn/engineering-notice",
        "content_type": "text/plain",
        "body_base64": encoded(b"synthetic"),
    }
    with pytest.raises(CompanyError, match="ingestion_source_scope_denied"):
        repo.create(ADMIN, manual(repo, attachments=[attachment]), "manual_import")


def test_record_failure_has_public_safe_code_and_preserves_private_body(ingestion):
    repo = ingestion
    record = start(repo)
    repo.record_failure(record["run_id"], "fixture_timeout", page_index=0)
    public = repo.read(ADMIN, record["run_id"])
    assert public["failures"][0]["code"] == "fixture_timeout"
    assert public["counts"]["failed"] == 1
    assert "PRIVATE RESPONSIBILITY" not in canonical(public)
    with pytest.raises(CompanyError, match="ingestion_invalid_error_code"):
        repo.record_failure(record["run_id"], "SECRET exception: raw-body")


def test_missing_attachment_and_mutated_failed_receipt_are_rejected(ingestion):
    from dataclasses import replace

    repo = ingestion
    attachment = {
        "attachment_key": "x",
        "filename": "fixture.txt",
        "content_type": "text/plain",
        "failed": True,
        "error_code": "fixture_missing",
    }
    record = start(repo, manual(repo, attachments=[attachment]))
    args = prepare_page(repo, record["run_id"])
    prepared, attachments, complete = args["items"][0]
    args["items"][0] = (prepared, [], complete)
    with pytest.raises(CompanyError, match="ingestion_attachment_members_mismatch"):
        repo.commit_page(record["run_id"], 0, **args)
    args["items"][0] = (
        prepared,
        [
            replace(
                attachments[0],
                input=attachments[0].input.model_copy(update={"error_code": "invented"}),
            )
        ],
        complete,
    )
    with pytest.raises(CompanyError, match="ingestion_attachment_binding_mismatch"):
        repo.commit_page(record["run_id"], 0, **args)
    assert repo.db.rows("SELECT COUNT(*) n FROM nd_attachments")[0]["n"] == 0


def schedule(repo, monkeypatch):
    from types import SimpleNamespace

    from bidops.ingestion.schedules import Schedules

    payload = http(repo)
    service = SimpleNamespace(repository=repo, settings=repo.settings, enabled=lambda: None)
    schedules = Schedules(service)
    monkeypatch.setattr(schedules, "reconcile", lambda: None)
    command = {
        **payload,
        "schedule_id": "engineering-plan",
        "expected_state_version": 0,
        "cron": "0 * * * *",
        "enabled": True,
        "reason": "engineering fixture, no actual source acquisition",
    }
    schedules.command(ADMIN, command)
    current = repo.db.rows("SELECT * FROM ingestion_schedules")[0]
    return schedules, payload, command, current["native_name"]


def test_pause_after_admit_before_tick_crash_never_executes_orphan(ingestion, monkeypatch):
    repo = ingestion
    schedules, _, command, native_name = schedule(repo, monkeypatch)
    retained, create = [], repo.create

    class ProcessCrash(BaseException):
        pass

    def crash_after_business_commit(*args, **kwargs):
        record = create(*args, **kwargs)
        retained.append(record)
        schedules.command(
            ADMIN,
            {
                **command,
                "idempotency_key": "pause-after-business-commit",
                "expected_state_version": 1,
                "enabled": False,
            },
        )
        raise ProcessCrash

    monkeypatch.setattr(repo, "create", crash_after_business_commit)
    with pytest.raises(ProcessCrash):
        schedules.admit(native_name, "engineering-crashed-native-tick")
    assert repo.db.rows("SELECT * FROM ingestion_schedule_ticks") == []
    assert repo.get_system(retained[0]["run_id"])["status"] == "queued"
    with pytest.raises(CompanyError, match="ingestion_schedule_not_current"):
        repo.require_execution(retained[0]["run_id"])
    assert repo.db.rows("SELECT * FROM snapshots") == []


def test_schedule_generation_required_and_manual_cannot_claim_one(ingestion, monkeypatch):
    repo = ingestion
    _, payload, _, name = schedule(repo, monkeypatch)
    with pytest.raises(CompanyError, match="ingestion_invalid_schedule_binding"):
        repo.create(ADMIN, payload, "http_fetch", trigger_kind="schedule")
    with pytest.raises(CompanyError, match="ingestion_invalid_schedule_binding"):
        repo.create(ADMIN, payload, "http_fetch", schedule_native_name=name)
    with pytest.raises(CompanyError, match="ingestion_schedule_not_current"):
        repo.create(
            ADMIN,
            payload,
            "http_fetch",
            trigger_kind="schedule",
            schedule_native_name="unregistered-generation",
        )
    assert repo.db.rows("SELECT * FROM ingestion_runs") == []


@pytest.mark.parametrize("change", ["owner", "items", "generation_body"])
def test_schedule_admission_exact_owner_selection_and_generation(ingestion, monkeypatch, change):
    repo = ingestion
    _, payload, _, name = schedule(repo, monkeypatch)
    actor = ADMIN
    if change == "owner":
        actor = OTHER
    elif change == "items":
        payload["items"][0]["source_url"] = repo.store.sources[0].allowed_urls[1]
    else:
        repo.db.execute("UPDATE ingestion_schedules SET body_json='{}'")
    with pytest.raises(CompanyError, match="ingestion_schedule_(not_current|integrity_failed)"):
        repo.create(
            actor, payload, "http_fetch", trigger_kind="schedule", schedule_native_name=name
        )
    assert repo.db.rows("SELECT * FROM ingestion_runs") == []
    assert repo.db.rows("SELECT * FROM ingestion_source_state") == []


def test_pause_revokes_running_generation_before_request_or_page_commit(ingestion, monkeypatch):
    repo = ingestion
    schedules, payload, command, name = schedule(repo, monkeypatch)
    created = repo.create(
        ADMIN, payload, "http_fetch", trigger_kind="schedule", schedule_native_name=name
    )
    run_id = created["run_id"]
    repo.start(run_id)
    args = prepare_page(repo, run_id)
    assert repo.reserve_request(run_id, created["source_id"], created["policy_revision"]) == 0
    assert repo.reserve_request(run_id, created["source_id"], created["policy_revision"]) == 5
    schedules.command(
        ADMIN,
        {
            **command,
            "idempotency_key": "pause-running",
            "expected_state_version": 1,
            "enabled": False,
        },
    )
    for operation in (
        lambda: repo.require_execution(run_id),
        lambda: repo.begin_page(run_id, 0),
        lambda: repo.reserve_request(run_id, created["source_id"], created["policy_revision"]),
        lambda: repo.commit_page(run_id, 0, **args),
        lambda: repo.create(
            ADMIN, payload, "http_fetch", trigger_kind="schedule", schedule_native_name=name
        ),
    ):
        with pytest.raises(CompanyError, match="ingestion_schedule_not_current"):
            operation()
    assert repo.read(ADMIN, run_id)["schedule_native_name"] == name
    assert repo.db.rows("SELECT * FROM snapshots") == []
    assert repo.db.rows("SELECT * FROM ingestion_pages") == []
    assert repo.get_system(run_id)["request_count"] == 1


def test_generation_replacement_never_rebinds_existing_idempotency_key(ingestion, monkeypatch):
    repo = ingestion
    schedules, payload, command, name = schedule(repo, monkeypatch)
    created = repo.create(
        ADMIN, payload, "http_fetch", trigger_kind="schedule", schedule_native_name=name
    )
    replay = repo.create(
        ADMIN, payload, "http_fetch", trigger_kind="schedule", schedule_native_name=name
    )
    assert replay["run_id"] == created["run_id"] and not replay["created"]
    schedules.command(
        ADMIN,
        {
            **command,
            "idempotency_key": "replace-generation",
            "expected_state_version": 1,
            "cron": "30 * * * *",
        },
    )
    latest = repo.db.rows("SELECT * FROM ingestion_schedules")[0]["native_name"]
    assert latest != name
    with pytest.raises(CompanyError, match="ingestion_schedule_not_current"):
        repo.require_execution(created["run_id"])
    with pytest.raises(CompanyError, match="ingestion_idempotency_conflict"):
        repo.create(
            ADMIN, payload, "http_fetch", trigger_kind="schedule", schedule_native_name=latest
        )
    assert repo.get_system(created["run_id"])["schedule_native_name"] == name


def test_schedule_metadata_is_immutable_and_manual_generation_check_is_sql_enforced(
    ingestion, monkeypatch
):
    repo = ingestion
    _, payload, _, name = schedule(repo, monkeypatch)
    created = repo.create(
        ADMIN, payload, "http_fetch", trigger_kind="schedule", schedule_native_name=name
    )
    for statement in (
        "UPDATE ingestion_schedule_versions SET owner_id='forged'",
        "DELETE FROM ingestion_schedule_versions",
        "UPDATE ingestion_runs SET schedule_native_name=NULL",
    ):
        with pytest.raises(IntegrityError, match="immutable_ingestion"):
            repo.db.execute(statement)
    assert repo.get_system(created["run_id"])["schedule_native_name"] == name
