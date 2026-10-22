import hashlib
import json
from pathlib import Path

import pytest
from bidops.api import create_app
from bidops.cli import main
from bidops.sources import canonical_url
from bidops.storage import Store
from fastapi.testclient import TestClient


def test_url_references_not_capture(store, url):
    store.register_url(url)
    store.register_url(url)
    assert store.db.rows("SELECT COUNT(*) AS n FROM urls")[0]["n"] == 1
    assert store.db.rows("SELECT COUNT(*) AS n FROM notices")[0]["n"] == 0


def test_duplicate_url_bytes_preserve_metadata(store, url):
    data = "解析单元测试".encode()
    first, new = store.ingest(url, data, manual_attestation=True)
    again, new_again = store.ingest(url, data, manual_attestation=True)
    assert new and not new_again
    assert first == again
    second_url = store.sources[0].allowed_urls[1]
    other, _ = store.ingest(second_url, data, manual_attestation=True)
    assert other.sha256 == first.sha256 and other.snapshot_id != first.snapshot_id
    assert len(list(store.raw.iterdir())) == 1
    assert len(store.db.rows("SELECT * FROM snapshots")) == 2


def test_roundtrip_manifest_hash(store, ingest, settings, tmp_path):
    ingest("预算金额：12元")
    first = store.export(tmp_path / "export1", include_raw=True)
    ingest("预算金额：12元")
    second = store.export(tmp_path / "export2", include_raw=True)
    assert first == second
    restored = Store(
        settings.model_copy(
            update={
                "data_dir": tmp_path / "restored",
                "database_url": f"sqlite:///{tmp_path}/restored.sqlite3",
            }
        )
    )
    result = restored.import_bundle(tmp_path / "export1")
    assert result == {"created": 1, "duplicates": 0}
    assert restored.export(tmp_path / "export3", True) == first
    assert restored.import_bundle(tmp_path / "export1") == {"created": 0, "duplicates": 1}
    restored.db.engine.dispose()


def test_corrupt_bundle_rejected_before_mutation(store, ingest, tmp_path):
    snap = ingest("预算金额：12元")
    store.export(tmp_path / "bundle", True)
    (tmp_path / "bundle/raw" / snap.sha256).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        store.import_bundle(tmp_path / "bundle")


def test_existing_raw_corruption_not_overwritten(store, ingest):
    snap = ingest("单元测试片段")
    (store.raw / snap.sha256).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="existing_blob_corrupt"):
        ingest("单元测试片段")
    with pytest.raises(ValueError):
        store.reparse()


@pytest.mark.parametrize(
    "url",
    [
        "http://www.ccgp.gov.cn/a",
        "https://evil.test/",
        "https://www.ccgp.gov.cn.evil.test/",
        "https://u:p@www.ccgp.gov.cn/a",
        "https://www.ccgp.gov.cn:8080/a",
        "https://www.ccgp.gov.cn/a?token=secret",
        "https://www.ccgp.gov.cn/../a",
        "https://www.ccgp.gov.cn/%2e%2e/a",
        "https://127.0.0.1/a",
        "not a url",
    ],
)
def test_bad_urls(store, url):
    with pytest.raises(ValueError):
        canonical_url(url, store.sources)


def test_manual_attestation_required(store, url):
    with pytest.raises(ValueError, match="attestation"):
        store.ingest(url, b"test")


def test_api_empty_and_detail(settings, store, ingest):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert len(client.get("/sources").json()["items"]) == 3
        assert client.get("/notices").json()["total"] == 0
        assert client.get("/notices/no-such-id").status_code == 404
        assert client.get("/notices?limit=999").status_code == 422
        snap = ingest("项目编号：UNIT-API")
        response = client.get(f"/notices/{snap.snapshot_id}").json()
        assert response["source"]["sha256"] == snap.sha256
        assert response["title"] is None
        assert client.get("/notices").json()["total"] == 1
        assert client.post("/notices", json={}).status_code == 405


def test_cli_url_import_offline(settings, url, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BIDOPS_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("BIDOPS_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("BIDOPS_SOURCE_REGISTRY", str(settings.source_registry))
    assert main(["import-urls", "--url", url]) == 0
    assert json.loads(capsys.readouterr().out)["snapshots_captured"] == 0
    assert main(["fetch", "--url", url, "--execute"]) == 2
    assert json.loads(capsys.readouterr().out)["items"][0]["code"] == "network_disabled"
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("wrong_column\nx\n")
    assert main(["import-urls", "--csv", str(bad_csv)]) == 2


def test_migrations_idempotent(store):
    store.db.migrate()
    assert [
        r["version"] for r in store.db.rows("SELECT * FROM schema_migrations ORDER BY version")
    ] == [
        "001_initial.sql",
        "002_agent_demo.sql",
        "003_enterprise.sql",
        "004_notice_versions.sql",
        "005_company_access.sql",
        "006_identity_refresh.sql",
        "007_qualification_screening.sql",
        "008_durable_jobs.sql",
    ]


def test_source_snapshot_matches_bytes(store, ingest):
    snap = ingest("单元测试")
    assert hashlib.sha256((store.raw / snap.sha256).read_bytes()).hexdigest() == snap.sha256
    assert not Path(snap.source_url).exists()
