"""HTTP protocol mocks only; these responses are never real collection results."""

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from bidops.fetcher import Fetcher
from bidops.sources import SourcePolicy


def permit(store):
    store.settings.network_enabled = True
    store.sources[0].robots_status = "allowed"
    store.sources[0].terms_status = "allowed"
    store.sources[0].reviewed_at = "2026-10-22T00:00:00Z"
    store.sources[0].permission_evidence = "test-double only; NOT live permission"
    now = datetime.now(UTC)
    store.sources[0].policy = SourcePolicy(
        revision="engineering-fixture-v1",
        status="allowed",
        evidence_sha256=hashlib.sha256(b"engineering fixture NOT live permission").hexdigest(),
        evidence_reference="engineering fixture only; NOT real permission",
        reviewed_by="fixture-operator",
        reviewed_at=now - timedelta(minutes=1),
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(days=1),
        acquisition_modes=["manual_fetch", "scheduled_fetch", "manual_import"],
        url_scope=store.sources[0].allowed_urls,
        rate_limit_seconds=5,
        max_attempts=3,
        max_requests=100,
        max_bytes=2_000_000,
        total_timeout_seconds=60,
    )


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


class RawStream(httpx.SyncByteStream):
    def __init__(self, chunks, before_chunk=None):
        self.chunks = chunks
        self.before_chunk = before_chunk
        self.read = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            if self.before_chunk:
                self.before_chunk(self.read)
            self.read += 1
            yield chunk

    def close(self):
        self.closed = True


def test_download_is_private_immutable_and_does_not_persist(store, url):
    from dataclasses import FrozenInstanceError

    permit(store)
    body = b'{"items": [], "cursor": "fixture-page-1"}'
    fetcher, calls, _ = mock_fetcher(
        store,
        lambda _: httpx.Response(200, content=body, headers={"content-type": "application/json"}),
    )
    receipt = fetcher.download(url, purpose="feed", mode="scheduled_fetch")
    assert receipt.data == body
    assert receipt.byte_size == len(body)
    assert receipt.sha256 == hashlib.sha256(body).hexdigest()
    assert receipt.policy_sha256 == store.sources[0].policy.sha256
    assert receipt.policy_revision == store.sources[0].policy.revision
    assert receipt.received_at.utcoffset().total_seconds() == 0
    assert receipt.encoding == "utf-8" and receipt.http_status == 200
    assert body.decode() not in repr(receipt)
    with pytest.raises(FrozenInstanceError):
        receipt.data = b"changed"
    assert calls[0].headers["accept"] == "application/json"
    assert calls[0].headers["accept-encoding"] == "identity"
    for table in ["snapshots", "notices", "nd_versions"]:
        assert store.db.rows(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0


@pytest.mark.parametrize(
    ("body", "media", "encoding"),
    [
        (b"%PDF-1.7\nfixture-only", "application/pdf", None),
        (b"<p>synthetic attachment</p>", "text/html", "utf-8"),
        (b"synthetic attachment", "text/plain", "utf-8"),
    ],
)
def test_attachment_download_keeps_raw_bytes_without_claiming_parse(
    store, url, body, media, encoding
):
    permit(store)
    fetcher, _, _ = mock_fetcher(
        store, lambda _: httpx.Response(200, content=body, headers={"content-type": media})
    )
    result = fetcher.download(url, purpose="attachment")
    assert result.data == body and result.encoding == encoding
    assert not hasattr(result, "parse_status")
    assert store.db.rows("SELECT COUNT(*) AS n FROM nd_attachments")[0]["n"] == 0


@pytest.mark.parametrize(
    ("body", "media", "purpose", "code"),
    [
        (b"not PDF", "application/pdf", "attachment", "invalid_pdf_signature"),
        (b"%PDF-1.7", "application/pdf", "feed", "unsupported_content_type"),
        (b"<p>homepage</p>", "text/html", "feed", "unsupported_content_type"),
        (b"[]", "application/json", "feed", "invalid_feed_json"),
        (b'{"n": NaN}', "application/json", "feed", "invalid_feed_json"),
        (b'{"n": 1e999}', "application/json", "feed", "invalid_feed_json"),
        (b'{"n":1,"n":2}', "application/json", "feed", "invalid_feed_json"),
        (b"{bad}", "application/json", "feed", "invalid_feed_json"),
        (b"\xff", "application/json", "feed", "invalid_feed_json"),
        (b"{}", "application/json;charset=gbk", "feed", "unsupported_encoding"),
        (b"", "application/json", "feed", "empty_response"),
    ],
)
def test_purpose_specific_failures_do_not_write(store, url, body, media, purpose, code):
    from bidops.fetcher import FetchFailure

    permit(store)
    fetcher, calls, _ = mock_fetcher(
        store, lambda _: httpx.Response(200, content=body, headers={"content-type": media})
    )
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose=purpose)
    assert failure.value.code == code and failure.value.attempts == 1
    assert len(calls) == 1
    assert store.db.rows("SELECT COUNT(*) AS n FROM snapshots")[0]["n"] == 0


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "gzip, identity"])
def test_compressed_responses_are_rejected_before_reading(store, url, encoding):
    from bidops.fetcher import FetchFailure

    permit(store)
    stream = RawStream([b"compressed bomb placeholder"])
    fetcher, _, _ = mock_fetcher(
        store,
        lambda _: httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "application/json", "content-encoding": encoding},
        ),
    )
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert failure.value.code == "compressed_response_rejected"
    assert stream.read == 0 and stream.closed


@pytest.mark.parametrize(
    ("length", "code"),
    [
        ("10000001", "body_too_large"),
        ("abc", "invalid_content_length"),
        ("-1", "invalid_content_length"),
        ("9" * 1000, "invalid_content_length"),
        ("100", "content_length_mismatch"),
    ],
)
def test_declared_body_size_is_verified(store, url, length, code):
    from bidops.fetcher import FetchFailure

    permit(store)
    stream = RawStream([b"{}"])
    fetcher, _, _ = mock_fetcher(
        store,
        lambda _: httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "application/json", "content-length": length},
        ),
    )
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert failure.value.code == code
    assert stream.closed
    if code != "content_length_mismatch":
        assert stream.read == 0


@pytest.mark.parametrize("bound", ["settings", "policy"])
def test_streamed_raw_bytes_respect_stricter_limit(store, url, bound):
    from bidops.fetcher import FetchFailure

    permit(store)
    if bound == "policy":
        store.sources[0].policy = store.sources[0].policy.model_copy(update={"max_bytes": 5})
    else:
        store.settings.max_bytes = 5
    stream = RawStream([b'{"a"', b':"x"}'])
    fetcher, _, _ = mock_fetcher(
        store,
        lambda _: httpx.Response(200, stream=stream, headers={"content-type": "application/json"}),
    )
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert failure.value.code == "body_too_large"
    assert stream.closed


def test_total_deadline_stops_a_trickling_stream(store, url):
    from bidops.fetcher import FetchFailure

    permit(store)
    elapsed = [0.0]

    def trickle(_):
        elapsed[0] += 25

    stream = RawStream([b'{"x":', b"1", b"}"], trickle)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, stream=stream, headers={"content-type": "application/json"}
            )
        )
    )
    fetcher = Fetcher(store, client, clock=lambda: elapsed[0], sleep=lambda _: None)
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert failure.value.code == "total_deadline_exceeded"
    assert failure.value.attempts == 1 and stream.closed


@pytest.mark.parametrize(
    "change", ["revision", "same_revision_hash", "expired", "denied", "cancel"]
)
def test_retry_wait_rechecks_policy_and_cancellation_before_get(store, url, change):
    from bidops.fetcher import FetchFailure

    permit(store)
    source = store.sources[0]
    calls = []
    instant = [datetime.now(UTC)]

    def sleep(_):
        if change == "revision":
            source.policy = source.policy.model_copy(update={"revision": "updated-policy"})
        elif change == "same_revision_hash":
            source.policy = source.policy.model_copy(update={"max_requests": 99})
        elif change == "expired":
            instant[0] = source.policy.valid_until
        elif change == "denied":
            source.terms_status = "denied"

    slots = []

    def guard(source_id, revision):
        slots.append((source_id, revision))
        if change == "cancel" and len(slots) > 1:
            raise FetchFailure("cancelled")

    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(503))
    )
    fetcher = Fetcher(
        store,
        client,
        sleep=sleep,
        clock=lambda: 100,
        now=lambda: instant[0],
        policy_reader=lambda _: source,
        before_request=guard,
    )
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert (
        failure.value.code
        == {
            "revision": "source_policy_changed",
            "same_revision_hash": "source_policy_changed",
            "expired": "source_policy_expired",
            "denied": "source_permission_unconfirmed",
            "cancel": "cancelled",
        }[change]
    )
    assert len(calls) == 1 and failure.value.attempts == 1


@pytest.mark.parametrize("change", ["policy", "expired", "network_off"])
def test_slot_wait_cannot_use_pre_wait_permission(store, url, change):
    from bidops.fetcher import FetchFailure

    permit(store)
    source = store.sources[0]
    instant = [datetime.now(UTC)]
    calls = []

    def guard(source_id, revision):
        assert source_id == source.id and revision == source.policy.revision
        if change == "policy":
            source.policy = source.policy.model_copy(update={"reviewed_by": "new-reviewer"})
        elif change == "expired":
            instant[0] = source.policy.valid_until
        else:
            store.settings.network_enabled = False

    client = httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request)))
    fetcher = Fetcher(
        store, client, now=lambda: instant[0], before_request=guard, policy_reader=lambda _: source
    )
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert failure.value.code in {
        "source_policy_changed",
        "source_policy_expired",
        "network_disabled",
    }
    assert failure.value.attempts == 0 and calls == []


def test_policy_revocation_during_stream_never_releases_receipt(store, url):
    from bidops.fetcher import FetchFailure

    permit(store)

    def revoke(_):
        store.sources[0].terms_status = "denied"

    stream = RawStream([b"{}"], revoke)
    fetcher, _, _ = mock_fetcher(
        store,
        lambda _: httpx.Response(200, stream=stream, headers={"content-type": "application/json"}),
    )
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert failure.value.code == "source_permission_unconfirmed"
    assert stream.closed


def test_policy_request_and_attempt_caps_are_enforced(store, url):
    from bidops.fetcher import FetchFailure

    permit(store)
    source = store.sources[0]
    source.policy = source.policy.model_copy(update={"max_attempts": 1, "max_requests": 1})
    fetcher, calls, _ = mock_fetcher(store, lambda _: httpx.Response(503))
    with pytest.raises(FetchFailure) as first:
        fetcher.download(url, purpose="feed")
    assert first.value.code == "http_503" and first.value.attempts == 1
    with pytest.raises(FetchFailure) as second:
        fetcher.download(url, purpose="feed")
    assert second.value.code == "source_request_limit" and second.value.attempts == 0
    assert len(calls) == 1


def test_expected_revision_mismatch_and_invalid_purpose_never_get(store, url):
    from bidops.fetcher import FetchFailure

    permit(store)
    fetcher, calls, _ = mock_fetcher(store, lambda _: pytest.fail("unexpected request"))
    for kwargs, code in [
        ({"expected_policy_revision": "not-current"}, "source_policy_changed"),
        ({"purpose": "arbitrary-crawler"}, "unsupported_download_purpose"),
        ({"mode": "manual_import"}, "acquisition_mode_not_allowed"),
    ]:
        with pytest.raises(FetchFailure) as failure:
            fetcher.download(url, **kwargs)
        assert failure.value.code == code and failure.value.attempts == 0
    assert calls == []


def test_policy_reader_cannot_replace_source_identity(store, url):
    from bidops.fetcher import FetchFailure

    permit(store)
    source = store.sources[0].model_copy(update={"id": "other-source"})
    fetcher, calls, _ = mock_fetcher(store, lambda _: pytest.fail("unexpected request"))
    fetcher.policy_reader = lambda _: source
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert failure.value.code == "source_identity_changed" and calls == []


@pytest.mark.parametrize(
    "body",
    [
        b"<div>" * 65,
        b"<div>" * 64 + b"</span><div>" * 500,
        b"<div><span>" * 32 + b"</div><div><span>" * 500,
        b"<br>" * 10001,
    ],
)
def test_html_structure_bounds_reject_malformed_depth_bypass(store, url, body):
    from bidops.fetcher import FetchFailure

    permit(store)
    fetcher, _, _ = mock_fetcher(
        store, lambda _: httpx.Response(200, content=body, headers={"content-type": "text/html"})
    )
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="attachment")
    assert failure.value.code == "html_structure_limit"


def test_rate_wait_is_capped_by_remaining_total_deadline(store, url):
    from bidops.fetcher import FetchFailure

    permit(store)
    source = store.sources[0]
    source.policy = source.policy.model_copy(
        update={"rate_limit_seconds": 86400.0, "total_timeout_seconds": 10.0}
    )
    fetcher, calls, sleeps = mock_fetcher(store, lambda _: httpx.Response(503))
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert failure.value.code == "total_deadline_exceeded"
    assert sleeps == [1, 10]
    assert len(calls) == 1


def test_slot_guard_rejects_after_total_deadline_without_get(store, url):
    from bidops.fetcher import FetchFailure

    permit(store)
    elapsed = [0.0]
    fetcher, calls, _ = mock_fetcher(store, lambda _: pytest.fail("unexpected request"))
    fetcher.clock = lambda: elapsed[0]
    fetcher.before_request = lambda *_: elapsed.__setitem__(0, 61.0)
    with pytest.raises(FetchFailure) as failure:
        fetcher.download(url, purpose="feed")
    assert failure.value.code == "total_deadline_exceeded"
    assert failure.value.attempts == 0 and calls == []


def test_transport_timeout_uses_remaining_total_deadline(store, url):
    permit(store)
    elapsed = [0.0]

    def guard(*_):
        elapsed[0] = 59.75

    fetcher, calls, _ = mock_fetcher(store, lambda _: httpx.Response(200, json={"items": []}))
    fetcher.clock = lambda: elapsed[0]
    fetcher.before_request = guard
    fetcher.download(url, purpose="feed")
    assert all(value == 0.25 for value in calls[0].extensions["timeout"].values())


def test_legacy_fetch_rechecks_permission_between_download_and_ingest(store, url):
    permit(store)
    body = "项目编号：UNIT-HTTP\n项目名称：协议测试输入（非真实公告）\n公告类型：公开招标公告\n" + (
        "采购解析单元测试，不是政府公告。" * 8
    )
    fetcher, calls, _ = mock_fetcher(
        store, lambda _: httpx.Response(200, text=body, headers={"content-type": "text/plain"})
    )
    real_download = fetcher.download

    def revoke_after_download(*args, **kwargs):
        result = real_download(*args, **kwargs)
        store.sources[0].terms_status = "denied"
        return result

    fetcher.download = revoke_after_download
    result = fetcher.fetch(url, True)
    assert result["state"] == "failed" and result["attempts"] == 1
    assert result["code"] == "source_permission_unconfirmed" and len(calls) == 1
    assert store.db.rows("SELECT COUNT(*) AS n FROM snapshots")[0]["n"] == 0


def test_dry_run_uses_current_policy_but_never_takes_persistent_slot(store, url):
    permit(store)
    current = store.sources[0].model_copy(update={"terms_status": "denied"})
    fetcher, calls, _ = mock_fetcher(store, lambda _: pytest.fail("unexpected request"))
    fetcher.policy_reader = lambda _: current
    fetcher.before_request = lambda *_: pytest.fail("dry run acquired durable slot")
    result = fetcher.fetch(url)
    assert result["permission"] == "source_permission_unconfirmed"
    assert result["would_fetch"] is False and calls == []
