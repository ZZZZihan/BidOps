"""HTTP protocol mocks only; these responses are never real collection results."""

import httpx
import pytest
from bidops.fetcher import Fetcher


def permit(store):
    store.settings.network_enabled = True
    store.sources[0].robots_status = "allowed"
    store.sources[0].terms_status = "allowed"
    store.sources[0].reviewed_at = "2026-10-22T00:00:00Z"
    store.sources[0].permission_evidence = "test-double only; NOT live permission"


def mock_fetcher(store, handler):
    calls = []
    sleeps = []

    def tracked(request):
        calls.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(tracked))
    fetcher = Fetcher(store, client=client, sleep=sleeps.append, clock=lambda: 100)
    return fetcher, calls, sleeps


def test_offline_and_dry_run_never_connect(store, url):
    fetcher, calls, _ = mock_fetcher(store, lambda _: pytest.fail("unexpected network request"))
    assert fetcher.fetch(url)["state"] == "dry_run"
    assert fetcher.fetch(url, True)["code"] == "network_disabled"
    assert calls == []


def test_unknown_permission_even_with_network_enabled(store, url):
    store.settings.network_enabled = True
    fetcher, calls, _ = mock_fetcher(store, lambda _: pytest.fail("unexpected network request"))
    assert fetcher.fetch(url, True)["code"] == "source_permission_unconfirmed"
    assert calls == []


def test_exact_whitelist_required(store):
    permit(store)
    fetcher, calls, _ = mock_fetcher(store, lambda _: pytest.fail("unexpected network request"))
    assert (
        fetcher.fetch("https://www.ccgp.gov.cn/unlisted.htm", True)["code"] == "url_not_allowlisted"
    )
    assert calls == []


@pytest.mark.parametrize("status", [401, 403, 404, 429, 301, 302])
def test_no_retry_or_redirect_on_restrictions(store, url, status):
    permit(store)
    fetcher, calls, _ = mock_fetcher(
        store, lambda _: httpx.Response(status, headers={"location": "https://evil.test"})
    )
    result = fetcher.fetch(url, True)
    assert result["http_status"] == status and result["attempts"] == 1
    assert len(calls) == 1
    assert store.db.rows("SELECT COUNT(*) AS n FROM snapshots")[0]["n"] == 0


def test_timeout_bounded_and_audited(store, url):
    permit(store)

    def timeout(request):
        raise httpx.ReadTimeout("test timeout", request=request)

    fetcher, calls, sleeps = mock_fetcher(store, timeout)
    result = fetcher.fetch(url, True)
    assert result["code"] == "timeout" and result["attempts"] == 3
    assert len(calls) == 3
    assert sleeps == [1, 5, 2, 5]
    assert len(store.db.rows("SELECT * FROM audit_events WHERE event='http_attempt'")) == 3


def test_server_error_bounded(store, url):
    permit(store)
    fetcher, calls, _ = mock_fetcher(store, lambda _: httpx.Response(503))
    assert fetcher.fetch(url, True)["attempts"] == 3
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("body", "media", "expected"),
    [
        (b"%PDF-1.7", "application/pdf", "unsupported_content_type"),
        (b"<html>captcha</html>", "text/html", "access_challenge"),
        (b"<html>plain homepage</html>", "text/html", "unverified_notice_body"),
        (b"\xff\xff", "text/plain; charset=utf-8", "invalid_encoding"),
        (b"small text", "text/plain; charset=unsupported", "unsupported_encoding"),
    ],
)
def test_bad_200_response_not_success(store, url, body, media, expected):
    permit(store)
    fetcher, _, _ = mock_fetcher(
        store, lambda _: httpx.Response(200, content=body, headers={"content-type": media})
    )
    assert fetcher.fetch(url, True)["code"] == expected
    assert store.db.rows("SELECT COUNT(*) AS n FROM snapshots")[0]["n"] == 0


def test_oversized_response(store, url):
    permit(store)
    store.settings.max_bytes = 10
    fetcher, _, _ = mock_fetcher(
        store,
        lambda _: httpx.Response(200, content=b"x" * 11, headers={"content-type": "text/html"}),
    )
    assert fetcher.fetch(url, True)["code"] == "body_too_large"


def test_mock_success_metadata_and_serial_rate_limit(store, url):
    permit(store)
    # Deliberately marked protocol test; no purchaser, dates, or fabricated project story.
    body = (
        "项目编号：UNIT-HTTP\n项目名称：协议测试输入（非真实公告）\n公告类型：公开招标公告\n"
        + "采购解析单元测试，不是政府公告。" * 8
    )
    fetcher, calls, sleeps = mock_fetcher(
        store,
        lambda _: httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/plain; charset=utf-8", "etag": '"unit-test"'},
        ),
    )
    first = fetcher.fetch(url, True)
    again = fetcher.fetch(url, True)
    assert first["state"] == "captured" and again["state"] == "duplicate"
    assert len(calls) == 2 and sleeps == [5]
    notice = store.get_notice(first["snapshot_id"])
    assert notice.source.http_status == 200
    assert notice.source.etag == '"unit-test"'
    assert notice.source.access_method == "http_fetch"
    assert notice.source.fetched_at.utcoffset().total_seconds() == 0
