import json

import pytest
from bidops.agent_demo.catalog import Catalog
from bidops.config import Settings
from bidops.enterprise.backup import backup, restore
from bidops.storage import Store


def test_backup_restore_preserves_source_hash_and_revokes_logins(store, settings, ingest, tmp_path):
    ingest("项目编号：UNIT-BACKUP\n项目名称：备份测试语法片段\n预算金额：1元\n")
    store.db.execute(
        "INSERT INTO enterprise_logins VALUES ('unit','owner','Unit','[]','csrf',9999999999)"
    )
    bundle, recovered = tmp_path / "backup", tmp_path / "recovered"
    assert backup(settings, bundle)["files"] == 2
    assert restore(bundle, recovered)["logins_revoked"]
    restored = Store(
        Settings(
            data_dir=recovered,
            database_url=f"sqlite:///{recovered}/bidops.sqlite3",
            source_registry=settings.source_registry,
        )
    )
    try:
        before = Catalog(store).read("UNIT-BACKUP", "budget").items[0]
        after = Catalog(restored).read("UNIT-BACKUP", "budget").items[0]
        assert before == after
        assert not restored.db.rows("SELECT * FROM enterprise_logins")
    finally:
        restored.db.engine.dispose()
    with pytest.raises(FileExistsError):
        backup(settings, bundle)
    with pytest.raises(FileExistsError):
        restore(bundle, recovered)


@pytest.mark.parametrize("damage", ["bytes", "path", "missing_raw"])
def test_backup_corruption_and_traversal_fail_closed(store, settings, ingest, tmp_path, damage):
    ingest("项目编号：UNIT-BACKUP\n项目名称：备份测试语法片段\n")
    bundle = tmp_path / "backup"
    backup(settings, bundle)
    manifest = json.loads((bundle / "manifest.json").read_text())
    if damage == "bytes":
        next((bundle / "raw").iterdir()).write_bytes(b"corrupt")
    elif damage == "path":
        manifest["files"]["../outside"] = "x"
        (bundle / "manifest.json").write_text(json.dumps(manifest))
    else:
        manifest["files"] = {"bidops.sqlite3": manifest["files"]["bidops.sqlite3"]}
        (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore(bundle, tmp_path / "restore")


def test_pre_enterprise_database_can_be_restored_before_upgrade(store, settings, tmp_path):
    store.db.execute("DROP TABLE enterprise_logins")
    store.db.execute("DROP TABLE enterprise_oauth_state")
    backup(settings, tmp_path / "old-backup")
    assert restore(tmp_path / "old-backup", tmp_path / "old-restored")["logins_revoked"]
