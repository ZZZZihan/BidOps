"""Independent tests of Store's caller-owned atomic persistence boundary."""

from datetime import UTC, datetime

import pytest
from bidops.models import Snapshot
from bidops.storage import Store, snapshot_id
from test_ingestion_repository import document, sha


def prepared(store, url, code="ONE"):
    body = document(code)
    digest = sha(body)
    source = Snapshot(
        snapshot_id=snapshot_id(url, digest),
        source_url=url,
        sha256=digest,
        byte_size=len(body),
        fetched_at=datetime.now(UTC),
        content_type="text/plain",
        encoding="utf-8",
        access_method="manual_import",
        manual_attestation=True,
    )
    return store.prepare(source, body)


def test_prepare_has_only_an_immutable_raw_file_effect(store, url):
    item = prepared(store, url)
    assert (store.raw / item.snapshot.sha256).read_bytes() == document()
    assert (store.raw / item.snapshot.sha256).stat().st_mode & 0o777 == 0o600
    for table in ["snapshots", "notices", "nd_versions", "nd_observations", "audit_events"]:
        assert store.db.rows(f"SELECT COUNT(*) n FROM {table}")[0]["n"] == 0


def test_persist_joins_caller_transaction_and_rolls_back_audit(store, url):
    item = prepared(store, url)
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        with store.db.engine.begin() as conn:
            result = store.persist(item, conn)
            assert result.created
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM nd_versions").scalar_one() == 1
            raise RuntimeError("synthetic rollback")
    for table in ["snapshots", "notices", "nd_versions", "nd_observations", "audit_events"]:
        assert store.db.rows(f"SELECT COUNT(*) n FROM {table}")[0]["n"] == 0


@pytest.fixture
def bundle(store, url, tmp_path):
    for code in ["ONE", "TWO"]:
        item = prepared(store, url, code)
        with store.db.engine.begin() as conn:
            store.persist(item, conn)
    folder = tmp_path / "bundle"
    store.export(folder, include_raw=True)
    return folder


@pytest.fixture
def destination(settings, tmp_path):
    value = Store(
        settings.model_copy(
            update={
                "database_url": f"sqlite:///{tmp_path}/destination.sqlite3",
                "data_dir": tmp_path / "destination",
            }
        )
    )
    yield value
    value.db.engine.dispose()


def test_bundle_actual_second_insert_failure_is_atomic(bundle, destination, monkeypatch):
    real_persist = destination.persist
    count = [0]

    def persist(*args, **kwargs):
        count[0] += 1
        if count[0] == 2:
            raise RuntimeError("synthetic bundle DB failure")
        return real_persist(*args, **kwargs)

    monkeypatch.setattr(destination, "persist", persist)
    with pytest.raises(RuntimeError, match="bundle DB failure"):
        destination.import_bundle(bundle)
    for table in ["snapshots", "notices", "nd_versions", "nd_observations", "audit_events"]:
        assert destination.db.rows(f"SELECT COUNT(*) n FROM {table}")[0]["n"] == 0
    monkeypatch.setattr(destination, "persist", real_persist)
    assert destination.import_bundle(bundle) == {"created": 2, "duplicates": 0}


def test_replaying_old_bundle_is_not_a_new_verification(bundle, destination):
    assert destination.import_bundle(bundle)["created"] == 2
    before = destination.db.rows("SELECT * FROM nd_observations ORDER BY snapshot_id,observed_at")
    assert destination.import_bundle(bundle) == {"created": 0, "duplicates": 2}
    assert (
        destination.db.rows("SELECT * FROM nd_observations ORDER BY snapshot_id,observed_at")
        == before
    )
