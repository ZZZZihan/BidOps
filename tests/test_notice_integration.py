"""B02 integration on local synthetic sources; no fresh holdout or collection."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from bidops.api import create_app
from bidops.enterprise.backup import backup, restore
from bidops.notice_data import AttachmentInput
from bidops.storage import Store
from fastapi.testclient import TestClient
from test_enterprise import ORIGIN, enterprise

AT = datetime(2026, 10, 22, 3, tzinfo=UTC)
BODY = "项目编号：UNIT-INTEGRATION\n采购人名称：工程采购人\n项目名称：工程夹具\n招标公告\n"


def test_store_duplicate_observation_and_append_only_reparse(store, ingest, url, monkeypatch):
    snapshot = ingest(BODY)
    first = store.notice_data.versions_for_notice(snapshot.snapshot_id)[0]
    original = store.get_notice(snapshot.snapshot_id)
    quoted = store.notice_data.make_evidence(first.version_id, 0, 8)
    again, created = store.ingest(
        url, BODY.encode(), manual_attestation=True, fetched_at=AT + timedelta(hours=1)
    )
    assert not created and again == snapshot
    versions = store.notice_data.versions_for_notice(snapshot.snapshot_id)
    assert len(versions) == 1 and versions[0].last_observed_at == AT + timedelta(hours=1)
    from bidops import storage

    parser = storage.parse_notice

    def new_parser(*args):
        notice = parser(*args)
        notice.parse_warnings.append("unit_new_parser_interpretation")
        return notice

    monkeypatch.setattr(storage, "parse_notice", new_parser)
    assert store.reparse() == 1
    assert len(store.notice_data.versions_for_notice(snapshot.snapshot_id)) == 2
    assert store.notice_data.get_version(first.version_id).notice == original
    assert store.notice_data.resolve_evidence(quoted) == quoted
    assert store.get_notice(snapshot.snapshot_id).parse_warnings[-1] == (
        "unit_new_parser_interpretation"
    )


def attachment(store, version):
    return store.notice_data.record_attachment(
        AttachmentInput(
            parent_version_id=version.version_id,
            attachment_key="unit-required-attachment",
            filename="unit.txt",
            content_type="text/plain",
            acquisition_status="downloaded",
            parse_status="parsed",
            observed_at=AT + timedelta(hours=1),
        ),
        b"UNIT attachment evidence",
    )


def test_api_versions_status_and_both_evidence_kinds(settings, store, ingest):
    snap = ingest(BODY)
    version = store.notice_data.versions_for_notice(snap.snapshot_id)[0]
    attached = attachment(store, version)
    with TestClient(create_app(settings)) as client:
        for prefix in ("", "/api"):
            chain = client.get(f"{prefix}/notices/{snap.snapshot_id}/versions").json()
            assert chain["total"] == 1 and not chain["has_more"]
            assert chain["items"][0]["version_id"] == version.version_id
            evidence = client.get(
                f"{prefix}/notice-versions/{version.version_id}/evidence?start=0&end=8"
            ).json()
            assert (
                client.post(prefix + "/notice-evidence/resolve", json=evidence).json() == evidence
            )
            evidence["quote"] = "tampered"
            assert (
                client.post(prefix + "/notice-evidence/resolve", json=evidence).status_code == 422
            )
            assert (
                client.get(
                    f"{prefix}/notice-versions/{version.version_id}/evidence?start=0&end=999999"
                ).status_code
                == 422
            )
            state = client.get(f"{prefix}/projects/{version.project_id}/status").json()
            assert state["status"] == "unknown" and state["observed_at"]
            assert (
                client.get(
                    f"{prefix}/projects/{version.project_id}/status?as_of=2026-10-22T12:00:00"
                ).status_code
                == 422
            )
            files = client.get(f"{prefix}/notice-versions/{version.version_id}/attachments").json()
            assert files["total"] == 1 and not files["missing"]
            result = client.get(
                f"{prefix}/notice-attachments/{attached.record_id}/evidence?start=0&end=4"
            ).json()
            assert result["quote"] == "UNIT"


@pytest.mark.parametrize("prefix", ["", "/api"])
def test_new_read_routes_require_identity(settings, prefix):
    paths = [
        "/notices/unknown/versions",
        "/notice-versions/unknown",
        "/notice-versions/unknown/evidence?start=0&end=1",
        "/notice-versions/unknown/attachments",
        "/notice-attachments/unknown/evidence?start=0&end=1",
        "/projects/unknown/versions",
        "/projects/unknown/status",
    ]
    with TestClient(
        create_app(settings, enterprise_settings=enterprise()), base_url=ORIGIN
    ) as client:
        for path in paths:
            assert client.get(prefix + path).status_code == 401


def test_backup_restores_attachment_and_old_evidence(store, settings, ingest, tmp_path):
    snap = ingest(BODY)
    version = store.notice_data.versions_for_notice(snap.snapshot_id)[0]
    attached = attachment(store, version)
    evidence = store.notice_data.make_evidence(attached.record_id, 0, 4, object_kind="attachment")
    bundle, destination = tmp_path / "backup", tmp_path / "restored"
    assert backup(settings, bundle)["files"] == 3
    assert restore(bundle, destination)["logins_revoked"]
    restored = Store(
        settings.model_copy(
            update={
                "data_dir": destination,
                "database_url": f"sqlite:///{destination}/bidops.sqlite3",
            }
        )
    )
    try:
        assert restored.notice_data.resolve_evidence(evidence) == evidence
        assert restored.notice_data.get_version(version.version_id) == version
    finally:
        restored.db.engine.dispose()


@pytest.mark.parametrize("damage", ["missing", "changed", "unreferenced"])
def test_restore_requires_exact_attachment_closure(store, settings, ingest, tmp_path, damage):
    snap = ingest(BODY)
    version = store.notice_data.versions_for_notice(snap.snapshot_id)[0]
    attached = attachment(store, version)
    bundle, destination = tmp_path / "backup", tmp_path / "restored"
    backup(settings, bundle)
    manifest = json.loads((bundle / "manifest.json").read_text())
    name = f"attachments/{attached.raw_sha256}"
    if damage == "missing":
        del manifest["files"][name]
    elif damage == "changed":
        (bundle / name).write_bytes(b"tampered")
    else:
        digest = hashlib.sha256(b"unreferenced").hexdigest()
        (bundle / "attachments" / digest).write_bytes(b"unreferenced")
        manifest["files"][f"attachments/{digest}"] = digest
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore(bundle, destination)
    assert not destination.exists()


def test_legacy_six_engineering_rows_migrate_repeat_and_restore(store, settings, ingest, tmp_path):
    for index in range(6):
        ingest(BODY.replace("UNIT-INTEGRATION", f"UNIT-LEGACY-{index}"))
    store.db.execute("INSERT INTO enterprise_sessions VALUES ('session','owner',1,1,0)")
    store.db.execute(
        "INSERT INTO enterprise_turns(owner_id,request_id,session_id,request_sha256,question,"
        "created_at,state,response_json,messages_json) "
        "VALUES ('owner','request','session','sha','engineering question',1,'completed',:p,:m)",
        {"p": '{"unit":"historical response"}', "m": '[{"unit":"old evidence"}]'},
    )
    before = store.db.rows("SELECT * FROM notices ORDER BY notice_id")
    history = store.db.rows("SELECT * FROM enterprise_turns")
    old = tmp_path / "old"
    backup(settings, old)
    with sqlite3.connect(old / "bidops.sqlite3") as conn:
        for table in (
            "nd_attachments",
            "nd_observations",
            "nd_package_versions",
            "nd_versions",
            "nd_logical_notices",
            "nd_projects",
        ):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("DELETE FROM schema_migrations WHERE version='004_notice_versions.sql'")
    manifest = json.loads((old / "manifest.json").read_text())
    manifest["files"]["bidops.sqlite3"] = hashlib.sha256(
        (old / "bidops.sqlite3").read_bytes()
    ).hexdigest()
    (old / "manifest.json").write_text(json.dumps(manifest))
    destination = tmp_path / "upgrade"
    restore(old, destination)
    new_settings = settings.model_copy(
        update={"data_dir": destination, "database_url": f"sqlite:///{destination}/bidops.sqlite3"}
    )
    upgraded = Store(new_settings)
    try:
        assert upgraded.db.rows("SELECT * FROM notices ORDER BY notice_id") == before
        assert upgraded.db.rows("SELECT * FROM enterprise_turns") == history
        assert len(upgraded.db.rows("SELECT * FROM nd_versions")) == 6
        upgraded.db.migrate()
        assert upgraded.notice_data.backfill_legacy() == {"created": 0, "existing": 6}
    finally:
        upgraded.db.engine.dispose()
    rollback = tmp_path / "rollback"
    restore(old, rollback)
    with sqlite3.connect(rollback / "bidops.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM enterprise_turns").fetchone()[0] == 1
        assert not conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version='004_notice_versions.sql'"
        ).fetchone()
