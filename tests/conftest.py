from datetime import UTC, datetime
from pathlib import Path

import httpx
import httpx2
import pytest
from bidops.config import Settings
from bidops.storage import Store
from pydantic_ai import models

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live HTTP is forbidden in the offline test suite")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    monkeypatch.setenv("BIDOPS_NETWORK_ENABLED", "false")


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path}/db.sqlite3",
        source_registry=ROOT / "config/sources.json",
        network_enabled=False,
    )


@pytest.fixture
def store(settings):
    value = Store(settings)
    yield value
    value.db.engine.dispose()


@pytest.fixture
def url(store):
    return store.sources[0].allowed_urls[0]


@pytest.fixture
def ingest(store, url):
    def insert(text, **kwargs):
        return store.ingest(
            url,
            text.encode("utf-8"),
            manual_attestation=True,
            fetched_at=datetime(2026, 10, 22, 3, 0, tzinfo=UTC),
            **kwargs,
        )[0]

    return insert
