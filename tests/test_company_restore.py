"""B03-06: actual backup/restore and loss of the original instance, offline."""

import json
import shutil
import time

import pytest
from bidops.company import CompanyService
from bidops.company.authorization import CompanyError
from bidops.db import Database
from bidops.enterprise.backup import backup, restore
from bidops.enterprise.operations import SingleWorker
from test_company_access import ADMIN, ALICE, BOB, material
from test_company_access import domain as company_domain

domain = company_domain  # pytest registers this imported shared fixture by attribute name.


def reopen(destination):
    db = Database(f"sqlite:///{destination}/bidops.sqlite3")
    return db, CompanyService(db, destination)


def test_real_restore_after_post_backup_revocation_deletion_and_instance_loss(
    domain, settings, tmp_path
):
    doc = material(domain)
    domain.auth.grant(ALICE, doc["object_id"], BOB.owner_id, ["read"])
    bundle = tmp_path / "backup"
    result = backup(settings, bundle)
    assert result["files"] == 3 and result["requires_stopped_service"]
    domain.auth.revoke(ALICE, doc["object_id"], BOB.owner_id)
    domain.repo.delete(ALICE, doc["object_id"])
    domain.db.execute("UPDATE enterprise_subjects SET status='disabled' WHERE owner_id='alice'")
    # Lose the original files and database, not merely re-open the same instance.
    original_db = domain.db.engine.url.database
    domain.db.engine.dispose()
    from pathlib import Path

    Path(original_db).unlink()
    shutil.rmtree(settings.data_dir)
    destination = tmp_path / "restored"
    outcome = restore(bundle, destination)
    assert outcome["quarantined_objects"] == 1 and outcome["old_private_grants_removed"]
    db, recovered = reopen(destination)
    try:
        assert not db.rows("SELECT * FROM company_grants")
        assert not db.rows("SELECT * FROM enterprise_logins")
        assert all(row["fresh_until"] == 0 for row in db.rows("SELECT * FROM enterprise_subjects"))
        for who in (ALICE, BOB, ADMIN):
            with pytest.raises(CompanyError):
                recovered.materials.read(who, doc["object_id"])
        # A current identity snapshot still cannot make any old ACL trustworthy.
        # This is a protocol/domain fixture, not a real IdP login acceptance.
        db.execute(
            "UPDATE enterprise_subjects SET fresh_until=:fresh", {"fresh": time.time() + 240}
        )
        for who in (ALICE, BOB, ADMIN):
            with pytest.raises(CompanyError):
                recovered.materials.read(who, doc["object_id"])
        with pytest.raises(CompanyError, match="recovery_admin_required"):
            recovered.auth.reauthorize(ALICE, doc["object_id"], ALICE.owner_id, ["read"])
        recovered.auth.reauthorize(ADMIN, doc["object_id"], BOB.owner_id, ["read"])
        assert recovered.materials.read(BOB, doc["object_id"])[1] == b"SYNTHETIC certificate alpha"
        for who in (ALICE, ADMIN):
            with pytest.raises(CompanyError):
                recovered.materials.read(who, doc["object_id"])
    finally:
        db.engine.dispose()


@pytest.mark.parametrize(
    "kind", ["missing_blob", "missing_text", "tamper", "extra", "traversal", "expired"]
)
def test_restore_rejects_incomplete_private_bundle_before_publishing(
    domain, settings, tmp_path, kind
):
    material(domain)
    bundle, destination = tmp_path / "backup", tmp_path / "restored"
    backup(settings, bundle)
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_text())
    if kind.startswith("missing_"):
        name = next(n for n in manifest["files"] if f"/{kind.removeprefix('missing_')}/" in n)
        del manifest["files"][name]
    elif kind == "tamper":
        name = next(n for n in manifest["files"] if n.startswith("private/"))
        (bundle / name).write_bytes(b"tampered")
    elif kind in {"extra", "traversal"}:
        name = "../outside" if kind == "traversal" else "private/company/blob/" + "0" * 32
        manifest["files"][name] = "0" * 64
    else:
        manifest["created_at"] = time.time() - 10 * 86400
        manifest["expires_at"] = time.time() - 3 * 86400
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore(bundle, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".bidops-restore-*"))


def test_backup_requires_stopped_enterprise_worker(domain, settings, tmp_path):
    material(domain)
    worker = SingleWorker(settings.database_url)
    try:
        with pytest.raises(RuntimeError, match="exactly one worker"):
            backup(settings, tmp_path / "unsafe-backup")
        assert not (tmp_path / "unsafe-backup").exists()
    finally:
        worker.close()


def test_restore_purges_expired_private_files_before_publication(
    domain, settings, tmp_path, monkeypatch
):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import bidops.company as company
    import bidops.enterprise.backup as backup_module

    now = time.time()
    doc = material(domain, retention_until=datetime.fromtimestamp(now + 60, UTC))
    bundle, destination = tmp_path / "backup", tmp_path / "restored"
    backup(settings, bundle)
    future = now + 120
    monkeypatch.setattr(backup_module, "time", SimpleNamespace(time=lambda: future))
    monkeypatch.setattr(
        company, "CompanyService", lambda db, path: CompanyService(db, path, clock=lambda: future)
    )
    restore(bundle, destination)
    db, recovered = reopen(destination)
    try:
        assert db.rows("SELECT purge_state FROM company_objects")[0]["purge_state"] == "purged"
        assert not db.rows("SELECT * FROM company_material_revisions")
        assert (
            db.rows("SELECT * FROM company_revision_tombstones")[0]["version_id"]
            == doc["version_id"]
        )
        assert not list((destination / "private/company/blob").iterdir())
        assert not list((destination / "private/company/text").iterdir())
        with pytest.raises(CompanyError):
            recovered.materials.read(ALICE, doc["object_id"])
    finally:
        db.engine.dispose()
