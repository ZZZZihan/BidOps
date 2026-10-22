import json

import httpx
import pytest
from bidops.fetcher import Fetcher
from bidops.storage import Store
from test_fetcher import permit


def test_manifest_tamper_all_or_nothing(store, ingest, settings, tmp_path):
    ingest("项目编号：UNIT-A")
    ingest("项目编号：UNIT-B")
    folder = tmp_path / "bundle"
    store.export(folder, True)
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["snapshots"][1]["byte_size"] += 1
    (folder / "manifest.json").write_text(json.dumps(manifest))
    dest = Store(
        settings.model_copy(
            update={
                "database_url": f"sqlite:///{tmp_path}/dest.sqlite3",
                "data_dir": tmp_path / "dest",
            }
        )
    )
    with pytest.raises(ValueError, match="size_mismatch"):
        dest.import_bundle(folder)
    assert dest.db.rows("SELECT count(*) AS n FROM snapshots")[0]["n"] == 0
    dest.db.engine.dispose()


def test_200_keyword_page_with_no_notice_fields_is_not_success(store, url):
    permit(store)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                text="采购公告栏目首页；此处无公告字段。" * 20,
                headers={"content-type": "text/html"},
            )
        )
    )
    result = Fetcher(store, client).fetch(url, True)
    assert result["code"] == "unverified_notice_fields"
    assert store.db.rows("SELECT count(*) AS n FROM notices")[0]["n"] == 0
