"""Real SQLite/DBOS SDK bundles, synthetic private bytes, no models or network."""

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from bidops.company import CompanyService
from bidops.company.authorization import CompanyError
from bidops.db import Database
from bidops.enterprise.backup import backup, restore, sha
from bidops.enterprise.budget_review import BudgetReviewRequired, require_budget_review
from bidops.enterprise.operations import SingleWorker
from bidops.jobs import JobsRepository
from bidops.jobs import backup as jobs_backup
from bidops.notice_data import NoticeRepository
from bidops.qualification.repository import QualificationRepository
from bidops.qualification.sources import SourceAdapter, canonical_json
from dbos import DBOS, DBOSClient
from test_company_access import ALICE
from test_qualification_sources import domain as domain
from test_qualification_sources import sources as sources

APPLICATION_VERSION = "a" * 64
SECRET_INPUT = "SYNTHETIC_PRIVATE_JOB_INPUT_183"
SECRET_OUTPUT = "SYNTHETIC_PRIVATE_MODEL_RESPONSE_374"


@pytest.fixture(scope="session")
def system_template(tmp_path_factory):
    """Initialize the installed framework's real schema in an isolated process."""
    target = tmp_path_factory.mktemp("dbos-native-schema") / "system.sqlite3"
    program = """
import socket,sys
from dbos import DBOS
def deny(*args, **kwargs):
    raise RuntimeError('offline fixture')
socket.socket.connect=deny
socket.create_connection=deny
DBOS(config={'name':'bidops-backup-fixture','system_database_url':'sqlite:///'+sys.argv[1],
             'enable_otlp':False,'application_version':'a'*64})
DBOS.launch()
DBOS.destroy()
"""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("DBOS_", "OTEL_"))
    }
    completed = subprocess.run(
        [sys.executable, "-c", program, str(target)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return target


@pytest.fixture
def case(sources, settings, system_template):
    adapter, bundle, doc, _, _, _ = sources
    repository = JobsRepository(adapter.company.db, adapter)
    request = {"kind": "checklist", "private_fixture": SECRET_INPUT}
    prepared = {
        "manifest": bundle.manifest.model_dump(mode="json"),
        "settings": {"max_requests": 2, "execution_kind": "scorer_fixture"},
        "versions": {
            "application_version": APPLICATION_VERSION,
            "code_sha": "b" * 40,
            "lock_sha256": "c" * 64,
            "durable_lock_sha256": "d" * 64,
            "implementation_sha256": "e" * 64,
        },
    }
    job = repository.get_or_create(
        ALICE,
        idempotency_key="backup-fixture",
        request=request,
        prepare=lambda: copy.deepcopy(prepared),
    )
    path = settings.data_dir / jobs_backup.SYSTEM_DATABASE
    path.parent.mkdir(parents=True, mode=0o700)
    shutil.copyfile(system_template, path)
    path.chmod(0o600)
    client = DBOSClient(system_database_url=f"sqlite:///{path}", retry_connection_errors=False)
    try:
        client.register_queue("bidops-backup-fixture", worker_concurrency=1, global_concurrency=1)
        client.enqueue(
            {
                "workflow_name": jobs_backup.WORKFLOW_NAME,
                "queue_name": "bidops-backup-fixture",
                "workflow_id": job["run_id"],
                "app_version": APPLICATION_VERSION,
            },
            job["run_id"],
        )
    finally:
        client.destroy()
    return repository, job, path, doc


def running(case):
    repo, job, _, _ = case
    return repo.transition(job["run_id"], job["state_version"], "running")


def attempt(case, *, unknown=False):
    repo, job, _, _ = case
    ledger_id = "ledger-unknown" if unknown else "ledger-confirmed"
    repo.db.execute(
        "INSERT INTO agent_runs VALUES (:id,'2026-10-23','unconfirmed',:sha,0.45,0.17,11,7,1,NULL)",
        {"id": ledger_id, "sha": "1" * 64},
    )
    value = repo.record_attempt_started(job["run_id"], ledger_id=ledger_id, request_sha256="2" * 64)
    repo.finish_attempt(
        job["run_id"],
        value["seq"],
        unknown=unknown,
        response=None if unknown else {"fixture": SECRET_OUTPUT},
        usage={"input_tokens": 11, "output_tokens": 7},
    )
    if not unknown:
        repo.db.execute(
            "UPDATE agent_runs SET state='confirmed' WHERE run_id=:id", {"id": ledger_id}
        )


def add_checkpoint(path, run_id):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO operation_outputs(workflow_uuid,function_id,function_name,output) "
            "VALUES (?,1,'synthetic_model_checkpoint',?)",
            (run_id, SECRET_OUTPUT),
        )


def add_calculation(path, run_id):
    client = DBOSClient(system_database_url=f"sqlite:///{path}", retry_connection_errors=False)
    try:
        client.enqueue(
            {
                "workflow_name": jobs_backup.CALCULATION_WORKFLOW_NAME,
                "queue_name": "bidops-backup-fixture",
                "workflow_id": run_id + "-calculation",
                "app_version": APPLICATION_VERSION,
            },
            run_id,
        )
    finally:
        client.destroy()
    # Synthetic graph fixture in the installed SDK's schema; no calculation runs.
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "UPDATE workflow_status SET parent_workflow_id=? WHERE workflow_uuid=?",
            (run_id, run_id + "-calculation"),
        )


def manifest(bundle):
    return json.loads((bundle / "manifest.json").read_text())


def rehash(bundle, name):
    metadata = manifest(bundle)
    metadata["files"][name] = sha(bundle / name)
    (bundle / "manifest.json").write_text(json.dumps(metadata))


def reopened(destination):
    db = Database(f"sqlite:///{destination}/bidops.sqlite3")
    company = CompanyService(db, destination)
    return db, JobsRepository(db, SourceAdapter(company, NoticeRepository(db, destination / "raw")))


def test_v4_stopped_bundle_binds_actual_databases_inputs_attempts_and_result(
    case, settings, tmp_path
):
    repo, job, system, _ = case
    current = running(case)
    attempt(case)
    attempt(case, unknown=True)
    result = QualificationRepository(repo.db, repo.sources).save(
        ALICE,
        job["manifest"],
        {"kind": "checklist", "state": "completed", "result_revision": "revision-1"},
        result_id="immutable-result",
    )
    repo.transition(
        job["run_id"],
        current["state_version"],
        "completed",
        result_id=result["result_id"],
        result_revision="revision-1",
    )
    add_checkpoint(system, job["run_id"])
    bundle = tmp_path / "bundle"
    backup(settings, bundle)
    metadata = manifest(bundle)
    assert metadata["version"] == 4
    binding = metadata["durable_snapshot"]
    saved = binding["mapping"]["jobs"][0]
    assert saved["workflow_id"] == job["run_id"]
    assert saved["application_version"] == APPLICATION_VERSION
    assert saved["result_id"] == result["result_id"]
    assert saved["result_revision"] == "revision-1"
    assert [a["ledger_id"] for a in saved["attempts"]] == ["ledger-confirmed", "ledger-unknown"]
    assert SECRET_INPUT not in json.dumps(metadata) and SECRET_OUTPUT not in json.dumps(metadata)
    jobs_backup.verify_backup(
        bundle / "bidops.sqlite3", bundle / jobs_backup.SYSTEM_DATABASE, binding
    )


@pytest.mark.parametrize("status", ["queued", "running", "cancelled", "completed"])
def test_restore_quarantines_each_state_preserves_ledger_and_never_launches(
    case, settings, tmp_path, monkeypatch, status
):
    repo, job, system, _ = case
    if status != "queued":
        current = running(case)
        attempt(case, unknown=True)
        if status == "cancelled":
            current = repo.request_cancel(ALICE, job["run_id"], current["state_version"])
            repo.transition(job["run_id"], current["state_version"], "cancelled")
        elif status == "completed":
            result = QualificationRepository(repo.db, repo.sources).save(
                ALICE,
                job["manifest"],
                {"kind": "checklist", "state": "completed", "result_revision": "r1"},
                result_id="result-for-restore",
            )
            repo.transition(
                job["run_id"],
                current["state_version"],
                "completed",
                result_id=result["result_id"],
                result_revision="r1",
            )
    before = repo.db.rows("SELECT * FROM agent_runs ORDER BY run_id")
    metadata_before = repo.db.rows("SELECT * FROM durable_model_attempts")
    bundle, destination = tmp_path / "bundle", tmp_path / "restored"
    backup(settings, bundle)

    def forbidden_launch(*args, **kwargs):
        raise AssertionError("offline restore must not start workflow execution")

    monkeypatch.setattr(DBOS, "launch", forbidden_launch)
    report = restore(bundle, destination)
    assert report["quarantined_jobs"] == 1 and report["budget_review_required"]
    db, recovered = reopened(destination)
    try:
        state = db.rows("SELECT * FROM durable_job_state")[0]
        assert state["quarantined"] == 1 and state["status"] == status
        assert db.rows("SELECT * FROM agent_runs ORDER BY run_id") == before
        assert db.rows("SELECT * FROM durable_model_attempts") == metadata_before
        assert all(row["fresh_until"] == 0 for row in db.rows("SELECT * FROM enterprise_subjects"))
        assert not db.rows("SELECT * FROM company_grants")
        with pytest.raises(CompanyError):
            recovered.get(ALICE, job["run_id"])
        # Even a new identity cannot read a restored job from old grants.
        db.execute("UPDATE enterprise_subjects SET fresh_until=:now", {"now": time.time() + 300})
        with pytest.raises(CompanyError):
            recovered.get(ALICE, job["run_id"])
        with db.engine.begin() as conn:
            with pytest.raises(BudgetReviewRequired):
                require_budget_review(conn, None)
            require_budget_review(conn, report["budget_restore_token"])
        with closing(sqlite3.connect(destination / jobs_backup.SYSTEM_DATABASE)) as conn:
            assert conn.execute("SELECT status FROM workflow_status").fetchone()[0] == "CANCELLED"
    finally:
        db.engine.dispose()


@pytest.mark.parametrize("database", ["bidops.sqlite3", jobs_backup.SYSTEM_DATABASE])
def test_swapped_database_even_identical_logical_snapshot_and_recomputed_hash_rejected(
    case, settings, tmp_path, database
):
    first, second = tmp_path / "first", tmp_path / "second"
    backup(settings, first)
    backup(settings, second)
    assert (
        manifest(first)["durable_snapshot"]["mapping"]
        == manifest(second)["durable_snapshot"]["mapping"]
    )
    shutil.copyfile(second / database, first / database)
    rehash(first, database)
    with pytest.raises(ValueError, match="pairing mismatch"):
        restore(first, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


@pytest.mark.parametrize("damage", ["checkpoint", "ledger", "manifest_mapping", "result_ref"])
def test_changed_snapshot_content_with_recomputed_file_hash_rejected(
    case, settings, tmp_path, damage
):
    repo, job, _, _ = case
    running(case)
    attempt(case, unknown=True)
    bundle = tmp_path / "bundle"
    backup(settings, bundle)
    database = jobs_backup.SYSTEM_DATABASE if damage == "checkpoint" else "bidops.sqlite3"
    if damage == "manifest_mapping":
        metadata = manifest(bundle)
        metadata["durable_snapshot"]["mapping"]["jobs"][0]["state_version"] += 1
        metadata["durable_snapshot"]["mapping_sha256"] = hashlib.sha256(
            canonical_json(metadata["durable_snapshot"]["mapping"]).encode()
        ).hexdigest()
        (bundle / "manifest.json").write_text(json.dumps(metadata))
    else:
        with closing(sqlite3.connect(bundle / database)) as conn, conn:
            if damage == "checkpoint":
                conn.execute("UPDATE workflow_status SET status='SUCCESS'")
            elif damage == "ledger":
                conn.execute("UPDATE agent_runs SET reserved_cny=0,estimated_cny=0")
            else:
                conn.execute("UPDATE durable_job_state SET result_id='missing-result'")
        rehash(bundle, database)
    with pytest.raises(ValueError):
        restore(bundle, tmp_path / "restored")
    assert not list(tmp_path.glob(".bidops-restore-*"))


@pytest.mark.parametrize(
    "damage",
    [
        "missing_db",
        "app_version",
        "unknown_workflow",
        "missing_running",
        "missing_ledger",
        "missing_body",
    ],
)
def test_invalid_source_snapshot_refused_and_partial_backup_removed(
    case, settings, tmp_path, damage
):
    repo, job, system, _ = case
    if damage == "missing_db":
        system.unlink()
    elif damage in {"app_version", "unknown_workflow", "missing_running"}:
        with closing(sqlite3.connect(system)) as conn, conn:
            if damage == "app_version":
                conn.execute("UPDATE workflow_status SET application_version=?", ("f" * 64,))
            elif damage == "unknown_workflow":
                conn.execute("UPDATE workflow_status SET name='unregistered_application'")
            else:
                conn.execute("DELETE FROM workflow_status")
        if damage == "missing_running":
            running(case)
    elif damage == "missing_ledger":
        running(case)
        attempt(case, unknown=True)
        repo.db.execute("DELETE FROM agent_runs")
    else:
        repo.db.execute("DELETE FROM durable_job_inputs")
    with pytest.raises(ValueError):
        backup(settings, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_queued_job_before_enqueue_window_is_explicit_and_restore_still_isolated(
    case, settings, tmp_path
):
    _, _, system, _ = case
    client = DBOSClient(system_database_url=f"sqlite:///{system}", retry_connection_errors=False)
    try:
        client.delete_workflow(case[1]["run_id"])
    finally:
        client.destroy()
    bundle, recovered = tmp_path / "bundle", tmp_path / "restored"
    backup(settings, bundle)
    assert manifest(bundle)["durable_snapshot"]["mapping"]["workflows"] == []
    assert restore(bundle, recovered)["quarantined_jobs"] == 1


@pytest.mark.parametrize("damage", [None, "parent", "name", "version", "extra"])
def test_only_bound_calculation_child_is_allowed(case, settings, tmp_path, damage):
    _, job, system, _ = case
    add_calculation(system, job["run_id"])
    with closing(sqlite3.connect(system)) as conn, conn:
        if damage == "parent":
            conn.execute(
                "UPDATE workflow_status SET parent_workflow_id=NULL WHERE workflow_uuid=?",
                (job["run_id"] + "-calculation",),
            )
        elif damage == "name":
            conn.execute(
                "UPDATE workflow_status SET name='fake_calculation' "
                "WHERE parent_workflow_id IS NOT NULL"
            )
        elif damage == "version":
            conn.execute(
                "UPDATE workflow_status SET application_version=? "
                "WHERE parent_workflow_id IS NOT NULL",
                ("f" * 64,),
            )
        elif damage == "extra":
            conn.execute(
                "UPDATE workflow_status SET workflow_uuid=? WHERE parent_workflow_id IS NOT NULL",
                (job["run_id"] + "-unapproved",),
            )
    bundle = tmp_path / "bundle"
    if damage is not None:
        with pytest.raises(ValueError, match="workflow mapping"):
            backup(settings, bundle)
        assert not bundle.exists()
    else:
        backup(settings, bundle)
        assert len(manifest(bundle)["durable_snapshot"]["mapping"]["workflows"]) == 2
        destination = tmp_path / "restored"
        restore(bundle, destination)
        with closing(sqlite3.connect(destination / jobs_backup.SYSTEM_DATABASE)) as conn:
            assert conn.execute("SELECT DISTINCT status FROM workflow_status").fetchall() == [
                ("CANCELLED",)
            ]


def test_waiting_review_requires_preserved_calculation(case, settings, tmp_path):
    repo, job, _, _ = case
    current = running(case)
    result = QualificationRepository(repo.db, repo.sources).save(
        ALICE,
        job["manifest"],
        {"kind": "checklist", "state": "completed", "result_revision": "r1"},
        result_id="waiting-result",
    )
    repo.transition(
        job["run_id"],
        current["state_version"],
        "waiting_review",
        result_id=result["result_id"],
        result_revision="r1",
    )
    with pytest.raises(ValueError, match="calculation is missing"):
        backup(settings, tmp_path / "without-calculation")
    add_calculation(case[2], job["run_id"])
    backup(settings, tmp_path / "with-calculation")


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_formats_with_job_rows_cannot_drop_system_database(
    case, settings, tmp_path, version
):
    bundle = tmp_path / "bundle"
    backup(settings, bundle)
    metadata = manifest(bundle)
    metadata["version"] = version
    del metadata["files"][jobs_backup.SYSTEM_DATABASE]
    metadata.pop("durable_snapshot")
    (bundle / "manifest.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="Legacy backup"):
        restore(bundle, tmp_path / "restored")


def test_empty_jobs_schema_without_system_database_keeps_v3_compatibility(
    store, settings, tmp_path
):
    bundle = tmp_path / "bundle"
    backup(settings, bundle)
    assert manifest(bundle)["version"] == 3
    assert restore(bundle, tmp_path / "restored")["quarantined_jobs"] == 0


def test_same_single_worker_lock_blocks_two_database_backup(case, settings, tmp_path):
    worker = SingleWorker(settings.database_url)
    try:
        with pytest.raises(RuntimeError, match="exactly one worker"):
            backup(settings, tmp_path / "unsafe")
        assert not (tmp_path / "unsafe").exists()
    finally:
        worker.close()


def expire_on_restore(case, settings, tmp_path, monkeypatch):
    import bidops.company as company
    import bidops.enterprise.backup as module

    repo, job, system, doc = case
    running(case)
    attempt(case)
    attempt(case, unknown=True)
    add_checkpoint(system, job["run_id"])
    deadline = time.time() + 60
    repo.db.execute("UPDATE company_objects SET retention_until=:at", {"at": deadline})
    bundle = tmp_path / "bundle"
    backup(settings, bundle)
    future = deadline + 1
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: future))
    monkeypatch.setattr(
        company, "CompanyService", lambda db, path: CompanyService(db, path, clock=lambda: future)
    )
    return bundle


def test_expired_restore_erases_private_job_and_native_framework_history_but_keeps_usage(
    case, settings, tmp_path, monkeypatch
):
    bundle = expire_on_restore(case, settings, tmp_path, monkeypatch)
    before = case[0].db.rows("SELECT * FROM agent_runs ORDER BY run_id")
    metadata_before = case[0].db.rows("SELECT * FROM durable_model_attempts ORDER BY seq")
    destination = tmp_path / "restored"
    report = restore(bundle, destination)
    assert report["purged_job_ids"] == [case[1]["run_id"]]
    with closing(sqlite3.connect(destination / "bidops.sqlite3")) as conn:
        for table in (
            "durable_job_inputs",
            "durable_attempt_bodies",
            "durable_review_fixture_bodies",
        ):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute(
            "SELECT purge_state,progress_json FROM durable_job_state"
        ).fetchone() == ("purged", "{}")
    with closing(sqlite3.connect(destination / jobs_backup.SYSTEM_DATABASE)) as conn:
        assert conn.execute("SELECT count(*) FROM workflow_status").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM operation_outputs").fetchone()[0] == 0
    for name in ("bidops.sqlite3", jobs_backup.SYSTEM_DATABASE):
        assert SECRET_INPUT.encode() not in (destination / name).read_bytes()
        assert SECRET_OUTPUT.encode() not in (destination / name).read_bytes()
    db, _ = reopened(destination)
    try:
        assert db.rows("SELECT * FROM agent_runs ORDER BY run_id") == before
        assert db.rows("SELECT * FROM durable_model_attempts ORDER BY seq") == metadata_before
    finally:
        db.engine.dispose()


def test_native_history_cleanup_failure_does_not_publish_partial_restore(
    case, settings, tmp_path, monkeypatch
):
    bundle = expire_on_restore(case, settings, tmp_path, monkeypatch)

    def failed_cleanup(*args, **kwargs):
        raise RuntimeError("synthetic SDK deletion failure")

    monkeypatch.setattr(DBOSClient, "delete_workflow", failed_cleanup)
    with pytest.raises(ValueError, match="^Restored private retention cleanup failed$"):
        restore(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".bidops-restore-*"))
    # The original valid backup is not mutated by the failed staged restore.
    jobs_backup.verify_backup(
        bundle / "bidops.sqlite3",
        bundle / jobs_backup.SYSTEM_DATABASE,
        manifest(bundle)["durable_snapshot"],
    )


def test_copy_time_change_is_rechecked_in_staging_before_release(
    case, settings, tmp_path, monkeypatch
):
    import bidops.enterprise.backup as module

    bundle = tmp_path / "bundle"
    backup(settings, bundle)
    original = module.shutil.copyfile

    def changed_after_validation(source, target, **kwargs):
        result = original(source, target, **kwargs)
        if Path(source) == bundle / "bidops.sqlite3":
            with closing(sqlite3.connect(target)) as conn, conn:
                conn.execute("UPDATE durable_job_state SET state_version=state_version+1")
        return result

    monkeypatch.setattr(module.shutil, "copyfile", changed_after_validation)
    with pytest.raises(ValueError, match="integrity check"):
        restore(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".bidops-restore-*"))


def test_already_expired_backup_copy_omits_job_private_bytes_without_changing_source_ledger(
    case, settings, tmp_path
):
    repo, job, system, _ = case
    running(case)
    attempt(case)
    add_checkpoint(system, job["run_id"])
    ledger_before = repo.db.rows("SELECT * FROM agent_runs")
    repo.db.execute("UPDATE company_objects SET retention_until=1")
    bundle = tmp_path / "bundle"
    backup(settings, bundle)
    assert manifest(bundle)["durable_snapshot"]["mapping"]["jobs"][0]["purge_state"] == "purged"
    for name in ("bidops.sqlite3", jobs_backup.SYSTEM_DATABASE):
        assert SECRET_INPUT.encode() not in (bundle / name).read_bytes()
        assert SECRET_OUTPUT.encode() not in (bundle / name).read_bytes()
    assert repo.db.rows("SELECT * FROM agent_runs") == ledger_before
    assert repo.db.rows("SELECT body_json FROM durable_job_inputs")
    assert restore(bundle, tmp_path / "restored")["purged_job_ids"] == [job["run_id"]]


def test_previously_purged_job_retries_native_deletion_and_can_be_backed_up(
    case, settings, tmp_path
):
    repo, job, system, _ = case
    running(case)
    attempt(case)
    add_calculation(system, job["run_id"])
    add_checkpoint(system, job["run_id"])
    add_checkpoint(system, job["run_id"] + "-calculation")
    repo.db.execute("UPDATE company_objects SET retention_until=1")
    assert repo.prune()["purged_ids"] == [job["run_id"]]
    assert repo.prune()["purged_ids"] == []
    assert jobs_backup.prune_expired_jobs(
        Path(repo.db.engine.url.database), system, now=time.time()
    ) == [job["run_id"]]
    with closing(sqlite3.connect(system)) as conn:
        assert conn.execute("SELECT count(*) FROM operation_outputs").fetchone()[0] == 0
    # Avoid unrelated expired material-file copying: normal company maintenance owns it.
    from bidops.company.retention import prune_company_materials

    assert prune_company_materials(repo.sources.company)["status"] == "PASS"
    backup(settings, tmp_path / "after-purge")
