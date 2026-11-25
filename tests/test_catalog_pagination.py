"""B02-05: synthetic engineering rows, never independent procurement samples."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from bidops.agent_demo.catalog import Catalog
from bidops.agent_demo.schemas import Query
from bidops.api import create_app
from bidops.catalog_query import query_notices
from bidops.config import Settings
from bidops.storage import Store
from fastapi.testclient import TestClient

NOW = datetime(2026, 10, 22, tzinfo=UTC)


@pytest.fixture(scope="module")
def large_store(tmp_path_factory):
    root = tmp_path_factory.mktemp("unit-pagination")
    settings = Settings(
        data_dir=root / "data",
        database_url=f"sqlite:///{root}/notices.sqlite3",
        source_registry=Path(__file__).resolve().parents[1] / "config/sources.json",
    )
    store = Store(settings)
    for index in range(507):
        store.ingest(
            store.sources[0].allowed_urls[0],
            (
                f"项目编号：UNIT-PAGE-{index:04d}\n项目名称：分页工程夹具{index}\n"
                f"预算金额：{index % 3 + 1}元\n"
            ).encode(),
            manual_attestation=True,
            fetched_at=NOW,
        )
    yield store
    store.db.engine.dispose()


def test_target_after_old_preload_limit_is_searchable(large_store):
    catalog = Catalog(large_store)
    assert catalog.count == 507
    assert not hasattr(catalog, "notices")
    result = catalog.search(Query(keyword="UNIT-PAGE-0506"), NOW)
    assert result.total == 1
    assert result.items[0].project_code == "UNIT-PAGE-0506"
    assert not result.has_more and not result.truncated
    assert catalog.read("UNIT-PAGE-0506", "budget").total == 1


@pytest.mark.parametrize("sort_by", ["project_code", "budget", "deadline", "publication"])
@pytest.mark.parametrize("descending", [False, True])
def test_every_row_once_across_ties_and_unknown_values(large_store, sort_by, descending):
    ids = []
    for offset in range(0, 507, 20):
        page = query_notices(
            large_store.db, Query(offset=offset, sort_by=sort_by, descending=descending), NOW
        )
        assert page["total"] == 507
        assert page["offset"] == offset and page["limit"] == 20
        assert page["has_more"] is (offset + len(page["items"]) < 507)
        assert page["truncated"]
        ids.extend(n.notice_id for n in page["items"])
    assert len(ids) == len(set(ids)) == 507
    first = query_notices(large_store.db, Query(sort_by=sort_by, descending=descending), NOW)
    assert ids[:20] == [n.notice_id for n in first["items"]]


def test_count_filters_full_set_and_empty_page_metadata(large_store):
    filtered = query_notices(large_store.db, Query(min_budget_yuan=3, offset=160), NOW)
    assert filtered["total"] == 169 and len(filtered["items"]) == 9
    assert not filtered["has_more"] and filtered["truncated"]
    beyond = query_notices(large_store.db, Query(offset=1000), NOW)
    assert beyond["total"] == 507 and not beyond["items"] and not beyond["has_more"]
    literal = query_notices(large_store.db, Query(keyword="% _ ' OR 1=1"), NOW)
    assert literal["total"] == 0


def test_http_search_and_pagination_cover_full_catalog(large_store):
    with TestClient(create_app(large_store.settings)) as client:
        for prefix in ("", "/api"):
            response = client.get(prefix + "/notices?q=UNIT-PAGE-0506&limit=1")
            assert response.status_code == 200
            assert response.json()["total"] == 1
            assert response.json()["items"][0]["project_code"] == "UNIT-PAGE-0506"
            page = client.get(prefix + "/notices?offset=500&limit=5").json()
            assert len(page["items"]) == 5 and page["total"] == 507 and page["has_more"]
            assert client.get(prefix + "/notices?offset=-1").status_code == 422
            assert client.get(prefix + "/notices?sort_by=untrusted_SQL").status_code == 422


def test_exact_decimal_order_does_not_round_to_sqlite_real(store, ingest):
    # Deliberately use values REAL would collapse to the same IEEE float.
    ids = []
    for index, amount in enumerate(["999999999999.0000000002", "999999999999.0000000001"]):
        snap = ingest(f"项目编号：UNIT-DECIMAL-{index}\n预算金额：1元")
        payload = store.get_notice(snap.snapshot_id).model_dump(mode="json")
        payload["budget"]["amount"] = amount
        store.db.execute(
            "UPDATE notices SET payload_json=:p WHERE notice_id=:id",
            {"p": json.dumps(payload), "id": snap.snapshot_id},
        )
        ids.append(snap.snapshot_id)
    page = query_notices(store.db, Query(sort_by="budget"), NOW)
    assert [n.notice_id for n in page["items"]] == ids[::-1]
    assert query_notices(store.db, Query(min_budget_yuan=999999999999.0), NOW)["total"] == 2


def test_existing_catalog_observes_later_ingest_and_rechecks_bytes(store, ingest):
    catalog = Catalog(store)
    assert catalog.count == 0
    snap = ingest("项目编号：UNIT-LATER\n项目名称：新增工程夹具")
    assert catalog.search(Query(keyword="UNIT-LATER"), NOW).total == 1
    (store.raw / snap.sha256).write_bytes(b"corrupt after catalog construction")
    with pytest.raises(ValueError):
        catalog.search(Query(keyword="UNIT-LATER"), NOW)
