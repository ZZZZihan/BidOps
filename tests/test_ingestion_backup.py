"""Real SQLite/installed SDK bundles; all permission/document inputs are synthetic."""

import copy
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bidops.enterprise.backup import backup, restore
from bidops.enterprise.budget_review import BudgetReviewRequired, require_budget_review
from bidops.ingestion.schedules import Schedules
from bidops.jobs import backup as durable
from bidops.notice_data import NoticeRepository
from bidops.notice_data.attachment_parser import prepare_attachment
from dbos import DBOS, DBOSClient
from test_attachment_parser import pdf
from test_ingestion_repository import ADMIN, commit, encoded, http, manual
from test_ingestion_repository import ingestion as ingestion
from test_jobs_backup import APPLICATION_VERSION, manifest, rehash
from test_jobs_backup import system_template as system_template
from test_notice_versions import persist


@pytest.fixture
def native(ingestion, system_template):
    path = ingestion.store.settings.data_dir / durable.SYSTEM_DATABASE
    path.parent.mkdir(parents=True, mode=0o700)
    shutil.copyfile(system_template, path)
    path.chmod(0o600)
    # This copied, empty SDK fixture was initialized under its fixture app name.
    # Claim the synthetic version for the actual ingestion scheduler owner.
    sql(path, "UPDATE application_versions SET application_name='bidops-jobs'")
    client = DBOSClient(
        system_database_url=f"sqlite:///{path}",
        application_name="bidops-jobs",
        retry_connection_errors=False,
    )
    for queue in ("bidops_ingestion_v1", "bidops_ingestion_schedule_v1"):
        client.register_queue(queue, worker_concurrency=1, global_concurrency=1)
    yield ingestion, path, client
    client.destroy()


def enqueue(case, record):
    _, _, client = case
    client.enqueue(
        {
            "workflow_name": "bidops_ingestion_run_v1",
            "queue_name": "bidops_ingestion_v1",
            "workflow_id": record["native_id"],
            "app_version": APPLICATION_VERSION,
        },
        record["run_id"],
    )


def create(case, *, pages=False, attachment=False):
    repo, _, _ = case
    attachments = (
        [
            {
                "attachment_key": "terms",
                "filename": "fixture.pdf",
                "content_type": "application/pdf",
                "body_base64": encoded(pdf("SYNTHETIC first", "SYNTHETIC second")),
                "expected_page_count": 2,
            }
        ]
        if attachment
        else []
    )
    record = repo.create(ADMIN, manual(repo, attachments=attachments), "manual_import")
    enqueue(case, record)
    if pages:
        repo.start(record["run_id"])
        commit(repo, record)
        repo.finish(record["run_id"], "completed")
    return record


def sql(path, statement, values=()):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(statement, values)


def rows(path, statement, values=()):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(statement, values).fetchall()


def schedule(case, monkeypatch, *, key="schedule-1", expected=0, enabled=True):
    repo, _, client = case
    payload = {
        **http(repo, key=key),
        "schedule_id": "controlled-schedule",
        "expected_state_version": expected,
        "cron": "0 * * * *",
        "enabled": enabled,
        "reason": "PRIVATE SYNTHETIC SCHEDULE REASON",
    }
    service = SimpleNamespace(repository=repo, settings=repo.settings, enabled=lambda: None)
    service.settings.max_schedules = 10
    commands = Schedules(service)
    monkeypatch.setattr(commands, "reconcile", lambda: None)
    commands.command(ADMIN, payload)
    record = repo.db.rows("SELECT * FROM ingestion_schedules")[0]
    client.create_schedule(
        schedule_name=record["native_name"],
        workflow_name="bidops_ingestion_schedule_v1",
        schedule=payload["cron"],
        context={"native_name": record["native_name"]},
        automatic_backfill=False,
        cron_timezone="UTC",
        queue_name="bidops_ingestion_schedule_v1",
        application_name="bidops-jobs",
    )
    if not enabled:
        client.pause_schedule(record["native_name"])
    return record


def tick(case, generation, *, business=False):
    repo, _, client = case
    # Actual installed SDK scheduler writes ID, timestamp args and metadata;
    # no worker is launched or external acquisition performed.
    native_id = client.trigger_schedule(generation["native_name"]).get_workflow_id()
    if business:
        repo.db.execute(
            "INSERT INTO ingestion_schedule_ticks VALUES (:native,:schedule,:app,:now,NULL,:error)",
            {
                "native": native_id,
                "schedule": generation["schedule_id"],
                "app": APPLICATION_VERSION,
                "now": repo.clock(),
                "error": "synthetic_policy_denied",
            },
        )
    return native_id


def test_only_ingestion_job_uses_paired_v4_and_hides_private_payload(native, tmp_path):
    repo, _, _ = native
    record = create(native, pages=True)
    bundle = tmp_path / "bundle"
    backup(repo.store.settings, bundle)
    metadata = manifest(bundle)
    assert metadata["version"] == 4
    mapping = metadata["durable_snapshot"]["mapping"]
    assert mapping["jobs"] == []
    assert mapping["ingestion"]["runs"][0]["run_id"] == record["run_id"]
    assert "ingestion_inputs" in mapping["business_table_sha256"]
    assert "nd_versions" in mapping["business_table_sha256"]
    assert "PRIVATE RESPONSIBILITY" not in json.dumps(metadata)
    assert "body_base64" not in json.dumps(metadata)


def test_restore_pdf_fixed_bytes_and_all_ingestion_isolation(native, tmp_path, monkeypatch):
    repo, _, _ = native
    record = create(native, pages=True, attachment=True)
    generation = schedule(native, monkeypatch)
    tick_id = tick(native, generation, business=True)
    ledger = (
        "INSERT INTO agent_runs VALUES ('kept-ledger','2026-10-23','unconfirmed',:sha,"
        "0.45,0.17,11,7,1,NULL)"
    )
    repo.db.execute(ledger, {"sha": "1" * 64})
    before = repo.db.rows("SELECT * FROM agent_runs")
    attachment = repo.db.rows("SELECT record_id FROM nd_attachment_receipts")[0]["record_id"]
    evidence = repo.store.notice_data.make_evidence(attachment, 10, 26, object_kind="attachment")
    bundle = tmp_path / "bundle"
    backup(repo.store.settings, bundle)
    monkeypatch.setattr(DBOS, "launch", lambda *_: pytest.fail("restore must not launch workers"))
    destination = tmp_path / "restored"
    report = restore(bundle, destination)
    business, system = destination / "bidops.sqlite3", destination / durable.SYSTEM_DATABASE
    assert report["quarantined_ingestion_runs"] == 1
    assert report["quarantined_ingestion_schedules"] == 1
    assert rows(business, "SELECT quarantined,status FROM ingestion_run_state") == [
        (1, "completed")
    ]
    assert rows(business, "SELECT enabled,quarantined FROM ingestion_schedules") == [(0, 1)]
    assert rows(business, "SELECT active_run_id FROM ingestion_source_state") == [(None,)]
    assert rows(system, "SELECT status FROM workflow_schedules") == [("PAUSED",)]
    assert set(rows(system, "SELECT workflow_uuid,status FROM workflow_status")) == {
        (record["native_id"], "CANCELLED"),
        (tick_id, "CANCELLED"),
    }
    assert all(
        value[0] == 0 for value in rows(business, "SELECT fresh_until FROM enterprise_subjects")
    )
    with closing(sqlite3.connect(business)) as conn:
        names = [column[1] for column in conn.execute("PRAGMA table_info(agent_runs)")]
        assert [
            dict(zip(names, value, strict=True))
            for value in conn.execute("SELECT * FROM agent_runs")
        ] == before
    # Original installation was not modified by restoring elsewhere.
    assert repo.db.rows("SELECT quarantined FROM ingestion_run_state")[0]["quarantined"] == 0
    from bidops.db import Database

    restored_db = Database(f"sqlite:///{business}")
    try:
        fixed = NoticeRepository(restored_db, destination / "raw")
        assert fixed.resolve_evidence(evidence) == evidence
        assert fixed.evidence_location(evidence)["location"]["pages"] == [1, 2]
        with pytest.raises(BudgetReviewRequired):
            with restored_db.engine.connect() as conn:
                require_budget_review(conn, None)
    finally:
        restored_db.engine.dispose()


def test_queued_cross_database_window_is_valid_but_running_missing_workflow_is_not(native):
    repo, path, _ = native
    record = repo.create(ADMIN, manual(repo), "manual_import")
    assert durable._inventory(repo.db.engine.url.database, path)["ingestion"]["runs"]
    repo.start(record["run_id"])
    with pytest.raises(ValueError, match="workflow is missing"):
        durable._inventory(repo.db.engine.url.database, path)


def test_ingestion_requires_system_database_even_when_queued(ingestion, tmp_path):
    repo = ingestion
    repo.create(ADMIN, manual(repo), "manual_import")
    with pytest.raises(ValueError, match="system database"):
        backup(repo.store.settings, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "other_workflow"),
        ("queue_name", "other_queue"),
        ("application_version", "f" * 64),
        ("parent_workflow_id", "foreign-parent"),
        ("schedule_name", "foreign-schedule"),
        ("serialization", "json"),
        ("inputs", "untrusted-opaque-pickle"),
    ],
)
def test_run_native_mapping_rejects_each_wrong_binding(native, field, value):
    repo, path, _ = native
    record = create(native)
    sql(
        path,
        f"UPDATE workflow_status SET {field}=? WHERE workflow_uuid=?",
        (value, record["native_id"]),
    )
    with pytest.raises(ValueError, match="workflow (mapping|inputs conflict)"):
        durable._inventory(repo.db.engine.url.database, path)


def test_unknown_workflow_is_rejected_without_unpickling(native, monkeypatch):
    from dbos._serialization import DefaultSerializer

    repo, path, client = native
    client.enqueue(
        {
            "workflow_name": "source-prefix-is-not-authorization",
            "workflow_id": "source-unmapped",
            "queue_name": "bidops_ingestion_v1",
        }
    )
    monkeypatch.setattr(
        DefaultSerializer, "deserialize", lambda *_: pytest.fail("untrusted pickle")
    )
    with pytest.raises(ValueError, match="scheduled workflow mapping"):
        durable._inventory(repo.db.engine.url.database, path)


@pytest.mark.parametrize("target", ["input", "policy", "receipt", "notice_ref", "attachment_ref"])
def test_fixed_inputs_page_and_reference_corruption_rejected(native, target):
    repo, path, _ = native
    record = create(native, pages=True, attachment=True)
    business = repo.db.engine.url.database
    with closing(sqlite3.connect(business)) as conn, conn:
        if target == "input":
            conn.execute("DROP TRIGGER ingestion_input_no_update")
            conn.execute("UPDATE ingestion_inputs SET body_json='{}'")
        elif target == "policy":
            conn.execute("DROP TRIGGER ingestion_policy_no_update")
            conn.execute("UPDATE ingestion_policy_revisions SET body_json='{}'")
        else:
            conn.execute("DROP TRIGGER ingestion_page_no_update")
            receipt = json.loads(
                conn.execute("SELECT receipt_json FROM ingestion_pages").fetchone()[0]
            )
            if target == "receipt":
                receipt["run_id"] = "another-run"
            elif target == "notice_ref":
                receipt["version_ids"] = ["missing-version"]
            else:
                receipt["attachment_record_ids"] = ["missing-attachment"]
            encoded_receipt = json.dumps(
                receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            conn.execute(
                "UPDATE ingestion_pages SET receipt_json=?,receipt_sha256=? WHERE run_id=?",
                (encoded_receipt, durable._sha(encoded_receipt), record["run_id"]),
            )
    with pytest.raises(ValueError, match="Ingestion backup"):
        durable._inventory(business, path)


@pytest.mark.parametrize("business", [False, True])
def test_exact_schedule_generation_and_pre_business_tick_window(
    native, tmp_path, monkeypatch, business
):
    repo, _, _ = native
    generation = schedule(native, monkeypatch)
    tick(native, generation, business=business)
    bundle = tmp_path / "bundle"
    backup(repo.store.settings, bundle)
    summary = manifest(bundle)["durable_snapshot"]["mapping"]["ingestion"]
    assert summary["schedule_versions"][0]["native_name"] == generation["native_name"]
    assert len(summary["ticks"]) == int(business)
    assert "PRIVATE SYNTHETIC" not in json.dumps(manifest(bundle))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_name", "other_workflow"),
        ("queue_name", "bidops_ingestion_v1"),
        ("context", "untrusted-opaque-context"),
        ("schedule", "* * * * *"),
        ("cron_timezone", "Asia/Shanghai"),
        ("automatic_backfill", 1),
        ("application_name", "other-app"),
    ],
)
def test_native_schedule_rejects_wrong_exact_fields(native, monkeypatch, field, value):
    repo, path, _ = native
    schedule(native, monkeypatch)
    sql(path, f"UPDATE workflow_schedules SET {field}=?", (value,))
    with pytest.raises(ValueError, match="native schedule mapping"):
        durable._inventory(repo.db.engine.url.database, path)


def test_schedule_generation_missing_sdk_allowed_only_before_tick(native, monkeypatch):
    repo, path, _ = native
    generation = schedule(native, monkeypatch)
    sql(path, "DELETE FROM workflow_schedules")
    assert durable._inventory(repo.db.engine.url.database, path)
    generation = schedule(native, monkeypatch)
    tick(native, generation)
    sql(path, "DELETE FROM workflow_schedules")
    with pytest.raises(ValueError, match="scheduled workflow mapping"):
        durable._inventory(repo.db.engine.url.database, path)


def test_old_schedule_generations_remain_exactly_bound_and_restore_pauses_all(
    native, tmp_path, monkeypatch
):
    repo, _, client = native
    old = schedule(native, monkeypatch)
    client.pause_schedule(old["native_name"])
    current = schedule(native, monkeypatch, key="schedule-2", expected=1)
    assert old["native_name"] != current["native_name"]
    bundle = tmp_path / "bundle"
    backup(repo.store.settings, bundle)
    destination = tmp_path / "restored"
    restore(bundle, destination)
    assert rows(destination / durable.SYSTEM_DATABASE, "SELECT status FROM workflow_schedules") == [
        ("PAUSED",),
        ("PAUSED",),
    ]


def test_cross_snapshot_database_swap_rehash_still_rejected(native, tmp_path):
    repo, _, _ = native
    create(native, pages=True)
    one, two = tmp_path / "one", tmp_path / "two"
    backup(repo.store.settings, one)
    backup(repo.store.settings, two)
    shutil.copyfile(two / durable.SYSTEM_DATABASE, one / durable.SYSTEM_DATABASE)
    rehash(one, durable.SYSTEM_DATABASE)
    with pytest.raises(ValueError, match="pairing mismatch"):
        restore(one, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("legacy_version", [1, 2, 3])
def test_legacy_format_cannot_hide_ingestion_records(native, tmp_path, legacy_version):
    repo, _, _ = native
    create(native)
    bundle = tmp_path / "bundle"
    backup(repo.store.settings, bundle)
    metadata = manifest(bundle)
    metadata["version"] = legacy_version
    metadata["files"].pop(durable.SYSTEM_DATABASE)
    metadata.pop("durable_snapshot")
    (bundle / "manifest.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="Legacy backup"):
        restore(bundle, tmp_path / "rejected")


def test_legacy_v4_mapping_supported_only_without_new_records(native):
    repo, path, _ = native
    business = repo.db.engine.url.database
    mapping = durable._inventory(business, path, legacy=True)
    binding = {
        "bundle_id": "1" * 32,
        "mapping_sha256": durable._sha(
            json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
        "mapping": copy.deepcopy(mapping),
    }
    durable._stamp(business, binding)
    durable._stamp(path, binding)
    durable.verify_backup(business, path, binding)
    create(native)
    with pytest.raises(ValueError, match="Legacy durable mapping"):
        durable.verify_backup(business, path, binding)


@pytest.mark.parametrize(
    "field,value",
    [("class_name", "ArbitraryClass"), ("config_name", "foreign"), ("application_name", "foreign")],
)
def test_native_run_rejects_unexpected_dispatch_identity(native, field, value):
    repo, path, _ = native
    create(native)
    sql(path, f"UPDATE workflow_status SET {field}=?", (value,))
    with pytest.raises(ValueError, match="workflow mapping"):
        durable._inventory(repo.db.engine.url.database, path)


def test_new_sdk_input_table_is_verified_without_deserialization(native, monkeypatch):
    from dbos._serialization import DefaultSerializer

    repo, path, _ = native
    create(native)
    assert rows(path, "SELECT inputs FROM workflow_status") == [(None,)]
    assert rows(path, "SELECT inputs FROM workflow_input")[0][0] is not None
    monkeypatch.setattr(
        DefaultSerializer, "deserialize", lambda *_: pytest.fail("untrusted pickle")
    )
    sql(path, "UPDATE workflow_input SET inputs='untrusted-opaque-input'")
    with pytest.raises(ValueError, match="workflow mapping"):
        durable._inventory(repo.db.engine.url.database, path)


def test_recorded_business_tick_requires_native_workflow(native, monkeypatch):
    repo, path, _ = native
    generation = schedule(native, monkeypatch)
    tick(native, generation, business=True)
    sql(path, "DELETE FROM workflow_status")
    with pytest.raises(ValueError, match="tick workflow is missing"):
        durable._inventory(repo.db.engine.url.database, path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schedule_name", "source-schedule-fake"),
        ("application_version", "f" * 64),
        ("queue_name", "bidops_ingestion_v1"),
        ("class_name", "foreign"),
    ],
)
def test_schedule_tick_exact_generation_and_native_identity(native, monkeypatch, field, value):
    repo, path, _ = native
    generation = schedule(native, monkeypatch)
    tick(native, generation, business=True)
    sql(path, f"UPDATE workflow_status SET {field}=?", (value,))
    with pytest.raises(ValueError, match="scheduled workflow mapping"):
        durable._inventory(repo.db.engine.url.database, path)


def test_missing_generation_cannot_be_allowed_by_native_prefix(native):
    repo, path, client = native
    client.create_schedule(
        schedule_name="source-schedule-" + "a" * 32,
        workflow_name="bidops_ingestion_schedule_v1",
        schedule="0 * * * *",
        context={"native_name": "source-schedule-" + "a" * 32},
        cron_timezone="UTC",
        queue_name="bidops_ingestion_schedule_v1",
        application_name="bidops-jobs",
    )
    with pytest.raises(ValueError, match="unknown native schedule"):
        durable._inventory(repo.db.engine.url.database, path)


def fixed_pdf(repo):
    parent = persist(repo.store.notice_data)
    prepared = prepare_attachment(
        {
            "parent_version_id": parent.version_id,
            "attachment_key": "fixed-terms",
            "filename": "fixture.pdf",
            "content_type": "application/pdf",
            "observed_at": parent.last_observed_at,
            "acquisition_status": "downloaded",
            "parse_status": "pending",
        },
        pdf("SYNTHETIC fixed text", "SYNTHETIC second page"),
    )
    return repo.store.notice_data.record_attachment(prepared)


@pytest.mark.parametrize("target", ["receipt", "text", "metadata", "parent"])
def test_standalone_v3_pdf_receipt_and_row_binding_reject_tampering(ingestion, tmp_path, target):
    repo = ingestion
    fixed_pdf(repo)
    bundle = tmp_path / "bundle"
    backup(repo.store.settings, bundle)
    assert manifest(bundle)["version"] == 3
    business = bundle / "bidops.sqlite3"
    with closing(sqlite3.connect(business)) as conn, conn:
        # Deliberately bypass immutable triggers to simulate disk corruption.
        if target == "receipt":
            conn.execute("DROP TRIGGER nd_attachment_receipts_no_update")
            conn.execute("UPDATE nd_attachment_receipts SET receipt_json='{}'")
        else:
            triggers = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='nd_attachments'"
            ).fetchall()
            for (name,) in triggers:
                conn.execute('DROP TRIGGER "' + name + '"')
            if target == "text":
                conn.execute("UPDATE nd_attachments SET normalized_text='tampered'")
            elif target == "metadata":
                metadata = json.loads(
                    conn.execute("SELECT metadata_json FROM nd_attachments").fetchone()[0]
                )
                metadata["normalizer_version"] = "forged_normalizer"
                conn.execute("UPDATE nd_attachments SET metadata_json=?", (json.dumps(metadata),))
            else:
                conn.execute("UPDATE nd_attachments SET parent_version_id='missing-parent'")
    rehash(bundle, "bidops.sqlite3")
    with pytest.raises(ValueError, match="(Attachment backup|database references)"):
        restore(bundle, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_standalone_pdf_roundtrip_never_reextracts_and_has_no_system_database(
    ingestion, tmp_path, monkeypatch
):
    from bidops.db import Database
    from bidops.notice_data import attachment_parser

    repo = ingestion
    record = fixed_pdf(repo)
    original = repo.store.notice_data.make_evidence(
        record.record_id, 8, 25, object_kind="attachment"
    )
    bundle = tmp_path / "bundle"
    backup(repo.store.settings, bundle)
    assert durable.SYSTEM_DATABASE not in manifest(bundle)["files"]
    monkeypatch.setattr(
        attachment_parser, "_run_pdf", lambda *_: pytest.fail("fixed old extraction")
    )
    destination = tmp_path / "restored"
    restore(bundle, destination)
    db = Database(f"sqlite:///{destination}/bidops.sqlite3")
    try:
        notices = NoticeRepository(db, destination / "raw")
        assert notices.resolve_evidence(original) == original
        assert notices.evidence_location(original)["location"]["pages"] == [1, 2]
    finally:
        db.engine.dispose()


@pytest.mark.parametrize("pdf_record", [False, True])
def test_migrated_or_v2_declared_pdf_cannot_lose_entire_receipt_table(ingestion, pdf_record):
    repo = ingestion
    if pdf_record:
        fixed_pdf(repo)
    business = repo.db.engine.url.database
    sql(business, "DROP TABLE nd_attachment_receipts")
    with pytest.raises(ValueError, match="receipt table is missing"):
        durable.verify_standalone_database(business)


def scheduled_run(case, generation):
    repo, _, _ = case
    body = json.loads(generation["body_json"])
    record = repo.create(
        ADMIN,
        {
            "source_id": body["source_id"],
            "items": body["items"],
            "idempotency_key": "scheduled-run",
        },
        "http_fetch",
        trigger_kind="schedule",
        schedule_native_name=generation["native_name"],
    )
    enqueue(case, record)
    return record


def test_historical_scheduled_run_binds_generation_after_pause(native, tmp_path, monkeypatch):
    repo, _, client = native
    original = schedule(native, monkeypatch)
    record = scheduled_run(native, original)
    client.pause_schedule(original["native_name"])
    schedule(native, monkeypatch, key="pause-command", expected=1, enabled=False)
    bundle = tmp_path / "bundle"
    backup(repo.store.settings, bundle)
    value = manifest(bundle)["durable_snapshot"]["mapping"]["ingestion"]["runs"][0]
    assert value["run_id"] == record["run_id"]
    assert value["schedule_native_name"] == original["native_name"]
    assert value["status"] == "queued"


@pytest.mark.parametrize("target", ["generation_owner", "command_hash", "selected_items"])
def test_scheduled_run_generation_and_command_closure_rejected(native, monkeypatch, target):
    repo, path, _ = native
    generation = schedule(native, monkeypatch)
    scheduled_run(native, generation)
    business = repo.db.engine.url.database
    with closing(sqlite3.connect(business)) as conn, conn:
        if target == "generation_owner":
            conn.execute("DROP TRIGGER ingestion_schedule_version_no_update")
            conn.execute("UPDATE ingestion_schedule_versions SET owner_id='foreign-owner'")
        elif target == "command_hash":
            conn.execute("UPDATE ingestion_schedule_commands SET input_sha256=?", ("0" * 64,))
        else:
            conn.execute("DROP TRIGGER ingestion_input_no_update")
            conn.execute("DROP TRIGGER ingestion_run_no_update")
            body = json.loads(conn.execute("SELECT body_json FROM ingestion_inputs").fetchone()[0])
            body["request"]["items"] = []
            encoded_body = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            request_sha = durable._sha(
                json.dumps(
                    body["request"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
            conn.execute("UPDATE ingestion_inputs SET body_json=?", (encoded_body,))
            conn.execute(
                "UPDATE ingestion_runs SET body_sha256=?,input_sha256=?",
                (durable._sha(encoded_body), request_sha),
            )
    with pytest.raises(ValueError, match="Ingestion backup"):
        durable._inventory(business, path)


def test_actual_sdk_utc_backfill_inputs_are_reconstructed_exactly(native, monkeypatch):
    repo, path, client = native
    generation = schedule(native, monkeypatch)
    handles = client.backfill_schedule(
        generation["native_name"],
        datetime(2026, 10, 22, 0, tzinfo=UTC),
        datetime(2026, 10, 22, 2, tzinfo=UTC),
    )
    assert len(handles) == 1  # Installed SDK treats the end of this range as exclusive.
    assert durable._inventory(repo.db.engine.url.database, path)
    assert all("-trigger-" not in handle.get_workflow_id() for handle in handles)


@pytest.mark.parametrize("target", ["inputs", "serialization", "timestamp", "context"])
def test_schedule_inputs_cannot_be_replaced_even_with_matching_metadata(
    native, monkeypatch, target
):
    from dbos._serialization import DefaultSerializer

    repo, path, _ = native
    generation = schedule(native, monkeypatch)
    native_id = tick(native, generation)
    monkeypatch.setattr(
        DefaultSerializer, "deserialize", lambda *_: pytest.fail("untrusted pickle")
    )
    if target == "serialization":
        sql(path, "UPDATE workflow_status SET serialization='json'")
    elif target == "timestamp":
        # A valid-looking prefix alone conveys no timestamp/argument authority.
        sql(
            path,
            "UPDATE workflow_status SET workflow_uuid=?",
            ("sched-" + generation["native_name"] + "-trigger-bad",),
        )
    else:
        value = (
            "untrusted-opaque-pickle"
            if target == "inputs"
            else DefaultSerializer().serialize(
                {"args": (datetime.now(UTC), {"native_name": "foreign"}), "kwargs": {}}
            )
        )
        sql(path, "UPDATE workflow_input SET inputs=? WHERE workflow_uuid=?", (value, native_id))
        sql(path, "UPDATE workflow_status SET inputs=NULL WHERE workflow_uuid=?", (native_id,))
    with pytest.raises(ValueError, match="scheduled (inputs|identity) failed"):
        durable._inventory(repo.db.engine.url.database, path)


def test_receipt_schema_without_parent_table_is_a_rejected_corrupt_backup(ingestion):
    repo = ingestion
    with sqlite3.connect(repo.db.engine.url.database) as conn:
        conn.execute("DROP TABLE nd_attachments")
    with pytest.raises(ValueError, match="Attachment backup parent table is missing"):
        durable.verify_standalone_database(repo.db.engine.url.database)
