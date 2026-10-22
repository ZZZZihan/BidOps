"""Bounded, permission-gated downloads; no redirects or content decompression.

The injected before_request hook owns durable per-source slots/cancellation and
request accounting. The local limiter is an additional bound, not a replacement
for that persistent guard. Callers must recheck policy at their commit boundary.
"""

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlsplit

import httpx

from bidops.parser import canonical_text
from bidops.sources import NetworkMode, Source, canonical_url, network_permission
from bidops.storage import Store

DownloadPurpose = Literal["notice", "feed", "attachment"]


@dataclass
class FetchFailure(Exception):
    code: str
    status: int | None = None
    attempts: int = 0


@dataclass(frozen=True)
class BoundedDownload:
    source_id: str
    url: str
    purpose: DownloadPurpose
    policy_revision: str
    policy_sha256: str
    data: bytes = field(repr=False)
    sha256: str
    byte_size: int
    received_at: datetime
    content_type: str
    encoding: str | None
    http_status: int
    etag: str | None
    last_modified: str | None
    attempts: int


class _HTMLBounds(HTMLParser):
    """Check size-independent tree bounds before BeautifulSoup normalization."""

    VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 10000:
            raise ValueError("html_node_limit")
        if tag not in self.VOID:
            self.stack.append(tag)
            if len(self.stack) > 64:
                raise ValueError("html_depth_limit")

    def handle_startendtag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 10000:
            raise ValueError("html_node_limit")

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        # Unmatched end tags cannot decrease the tracked depth.
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()


def _reject_constant(value):
    raise ValueError("non_finite_json")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non_finite_json")
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


class Fetcher:
    def __init__(
        self,
        store: Store,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
        *,
        now: Callable[[], datetime] | None = None,
        policy_reader: Callable[[str], Source] | None = None,
        before_request: Callable[[str, str], None] | None = None,
    ):
        self.store = store
        self.settings = store.settings
        self.client = client
        self.owned_client = client is None
        self.sleep = sleep
        self.clock = clock
        self.now = now or (lambda: datetime.now(UTC))
        self.policy_reader = policy_reader
        self.before_request = before_request
        self.last_request: float | None = None
        self.request_counts: dict[tuple[str, str, str], int] = {}

    def close(self):
        if self.owned_client and self.client:
            self.client.close()

    def _current(self, url: str, mode: NetworkMode) -> Source:
        if not self.settings.network_enabled:
            raise FetchFailure("network_disabled")
        original = next(s for s in self.store.sources if s.host == urlsplit(url).hostname)
        try:
            value = self.policy_reader(original.id) if self.policy_reader else original
            source = Source.model_validate(value.model_dump(mode="python", warnings=False))
        except (AttributeError, ValueError, TypeError) as exc:
            raise FetchFailure("invalid_source_policy") from exc
        if (source.id, source.host, source.home_url) != (
            original.id,
            original.host,
            original.home_url,
        ):
            raise FetchFailure("source_identity_changed")
        allowed, reason = network_permission(url, [source], now=self.now(), mode=mode)
        if not allowed:
            raise FetchFailure(reason)
        return source

    def fetch(self, url: str, execute: bool = False) -> dict:
        url = canonical_url(url, self.store.sources)
        if not execute:
            # Dry runs never acquire durable request slots or call the transport.
            original = next(s for s in self.store.sources if s.host == urlsplit(url).hostname)
            source = self.policy_reader(original.id) if self.policy_reader else original
            allowed, reason = network_permission(url, [source], now=self.now())
            return {
                "url": url,
                "state": "dry_run",
                "permission": reason,
                "would_fetch": allowed and self.settings.network_enabled,
            }
        try:
            downloaded = self.download(url)
            # Recheck after streaming/normalizing and immediately before legacy ingest.
            try:
                current = self._current(url, "manual_fetch")
            except FetchFailure as exc:
                exc.attempts = max(exc.attempts, downloaded.attempts)
                raise
            if (current.policy.revision, current.policy.sha256) != (
                downloaded.policy_revision,
                downloaded.policy_sha256,
            ):
                raise FetchFailure("source_policy_changed", attempts=downloaded.attempts)
            try:
                snapshot, new = self.store.ingest(
                    url,
                    downloaded.data,
                    content_type=downloaded.content_type,
                    encoding=downloaded.encoding,
                    access_method="http_fetch",
                    fetched_at=downloaded.received_at,
                    http_status=downloaded.http_status,
                    etag=downloaded.etag,
                    last_modified=downloaded.last_modified,
                )
            except ValueError as exc:
                raise FetchFailure(
                    "unverified_notice_fields", attempts=downloaded.attempts
                ) from exc
            return {
                "url": url,
                "state": "captured" if new else "duplicate",
                "snapshot_id": snapshot.snapshot_id,
                "sha256": snapshot.sha256,
                "http_status": downloaded.http_status,
                "attempts": downloaded.attempts,
            }
        except FetchFailure as exc:
            self.store.db.audit(
                "fetch", exc.code, url, http_status=exc.status, attempts=exc.attempts
            )
            return {
                "url": url,
                "state": "blocked" if exc.attempts == 0 else "failed",
                "code": exc.code,
                "http_status": exc.status,
                "attempts": exc.attempts,
            }

    def download(
        self,
        url: str,
        *,
        purpose: DownloadPurpose = "notice",
        mode: NetworkMode = "manual_fetch",
        expected_policy_revision: str | None = None,
    ) -> BoundedDownload:
        url = canonical_url(url, self.store.sources)
        if purpose not in {"notice", "feed", "attachment"}:
            raise FetchFailure("unsupported_download_purpose")
        source = self._current(url, mode)
        policy = source.policy
        assert policy is not None  # _current requires a complete policy.
        pinned = (policy.revision, policy.sha256)
        if expected_policy_revision is not None and expected_policy_revision != pinned[0]:
            raise FetchFailure("source_policy_changed")
        deadline = self.clock() + policy.total_timeout_seconds
        attempts_limit = min(self.settings.http_attempts, policy.max_attempts, policy.max_requests)
        byte_limit = min(self.settings.max_bytes, policy.max_bytes)
        count_key = (source.id, *pinned)
        attempted = 0

        def check() -> Source:
            current = self._current(url, mode)
            if (current.policy.revision, current.policy.sha256) != pinned:
                raise FetchFailure("source_policy_changed", attempts=attempted)
            if self.clock() >= deadline:
                raise FetchFailure("total_deadline_exceeded", attempts=attempted)
            return current

        def pause(seconds: float):
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise FetchFailure("total_deadline_exceeded", attempts=attempted)
            self.sleep(min(seconds, remaining))
            # Injected test clocks need not advance when sleep is recorded.
            if seconds >= remaining:
                raise FetchFailure("total_deadline_exceeded", attempts=attempted)

        try:
            for attempt in range(1, attempts_limit + 1):
                if self.last_request is not None:
                    pause(
                        max(
                            0,
                            max(self.settings.http_rate_limit_seconds, policy.rate_limit_seconds)
                            - (self.clock() - self.last_request),
                        )
                    )
                check()
                if self.request_counts.get(count_key, 0) >= policy.max_requests:
                    raise FetchFailure("source_request_limit", attempts=attempted)
                if self.before_request:
                    self.before_request(source.id, pinned[0])
                check()  # The slot hook may wait, cancel, or observe a policy change.
                if self.client is None:
                    self.client = httpx.Client(follow_redirects=False)
                self.last_request = self.clock()
                self.request_counts[count_key] = self.request_counts.get(count_key, 0) + 1
                attempted = attempt
                try:
                    remaining = deadline - self.clock()
                    with self.client.stream(
                        "GET",
                        url,
                        headers={
                            "User-Agent": self.settings.user_agent,
                            "Accept": {
                                "notice": "text/html,text/plain",
                                "feed": "application/json",
                                "attachment": "text/html,text/plain,application/pdf",
                            }[purpose],
                            "Accept-Encoding": "identity",
                        },
                        timeout=min(self.settings.http_timeout_seconds, remaining),
                        follow_redirects=False,
                    ) as response:
                        status = response.status_code
                        self.store.db.audit(
                            "http_attempt",
                            "response",
                            url,
                            http_status=status,
                            attempt=attempt,
                            content_type=response.headers.get("content-type"),
                        )
                        check()
                        if status in {401, 403, 404, 429} or 300 <= status < 400:
                            raise FetchFailure(f"http_{status}", status, attempt)
                        if status in {500, 502, 503, 504} and attempt < attempts_limit:
                            pause(min(2 ** (attempt - 1), 4))
                            continue
                        if status != 200:
                            raise FetchFailure(f"http_{status}", status, attempt)
                        if response.headers.get(
                            "content-encoding", "identity"
                        ).strip().lower() not in {"", "identity"}:
                            raise FetchFailure("compressed_response_rejected", status, attempt)
                        media = (
                            response.headers.get("content-type", "").split(";")[0].lower().strip()
                        )
                        accepted = {
                            "notice": {"text/html", "text/plain"},
                            "feed": {"application/json"},
                            "attachment": {"text/html", "text/plain", "application/pdf"},
                        }[purpose]
                        if media not in accepted:
                            raise FetchFailure("unsupported_content_type", status, attempt)
                        length = response.headers.get("content-length")
                        if length is not None:
                            if not re.fullmatch(r"[0-9]+", length) or len(length) > 12:
                                raise FetchFailure("invalid_content_length", status, attempt)
                            if int(length) > byte_limit:
                                raise FetchFailure("body_too_large", status, attempt)
                        chunks, size = [], 0
                        # Pre-consumed MockTransport identity responses have the same
                        # bytes. Real streams use raw iteration, never decoder APIs.
                        raw = (
                            [response.content]
                            if response.is_stream_consumed
                            else response.iter_raw()
                        )
                        for chunk in raw:
                            check()
                            size += len(chunk)
                            if size > byte_limit:
                                raise FetchFailure("body_too_large", status, attempt)
                            chunks.append(chunk)
                        data = b"".join(chunks)
                        if not data:
                            raise FetchFailure("empty_response", status, attempt)
                        if length is not None and size != int(length):
                            raise FetchFailure("content_length_mismatch", status, attempt)
                        encoding = self._validate_body(
                            data,
                            media,
                            response.headers.get("content-type", ""),
                            purpose,
                            status,
                            attempt,
                        )
                        check()
                        return BoundedDownload(
                            source_id=source.id,
                            url=url,
                            purpose=purpose,
                            policy_revision=pinned[0],
                            policy_sha256=pinned[1],
                            data=data,
                            sha256=hashlib.sha256(data).hexdigest(),
                            byte_size=size,
                            received_at=self.now(),
                            content_type=media,
                            encoding=encoding,
                            http_status=status,
                            etag=response.headers.get("etag"),
                            last_modified=response.headers.get("last-modified"),
                            attempts=attempt,
                        )
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    code = "timeout" if isinstance(exc, httpx.TimeoutException) else "network_error"
                    self.store.db.audit("http_attempt", code, url, attempt=attempt)
                    if attempt == attempts_limit:
                        raise FetchFailure(code, attempts=attempt) from exc
                    pause(min(2 ** (attempt - 1), 4))
                except httpx.HTTPError as exc:
                    raise FetchFailure("http_transport_error", attempts=attempt) from exc
        except FetchFailure as exc:
            exc.attempts = max(exc.attempts, attempted)
            raise
        raise AssertionError("unreachable retry state")

    @staticmethod
    def _validate_body(data, media, content_header, purpose, status, attempt) -> str | None:
        if media == "application/pdf":
            # Downloaded bytes are not a claim that the attachment was parsed.
            if not data.startswith(b"%PDF-"):
                raise FetchFailure("invalid_pdf_signature", status, attempt)
            return None
        encoding_match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_header, re.I)
        if not encoding_match and purpose != "feed":
            encoding_match = re.search(
                r"charset\s*=\s*[\"']?([\w-]+)", data[:2048].decode("ascii", "ignore"), re.I
            )
        name = encoding_match.group(1).lower() if encoding_match else "utf-8"
        encoding = {
            "utf-8": "utf-8",
            "utf8": "utf-8",
            "gbk": "gb18030",
            "gb2312": "gb18030",
            "gb18030": "gb18030",
        }.get(name)
        if not encoding or (purpose == "feed" and encoding != "utf-8"):
            raise FetchFailure("unsupported_encoding", status, attempt)
        try:
            if purpose == "feed":
                value = json.loads(
                    data.decode("utf-8"),
                    parse_constant=_reject_constant,
                    parse_float=_finite_float,
                    object_pairs_hook=_unique_object,
                )
                if not isinstance(value, dict):
                    raise ValueError("feed_requires_object")
                return encoding
            if media == "text/html":
                bounds = _HTMLBounds()
                bounds.feed(data.decode(encoding))
                bounds.close()
            normalized = canonical_text(data, media, encoding)
            if not normalized.strip():
                raise ValueError("empty_normalized_text")
        except (UnicodeError, ValueError, RecursionError) as exc:
            code = "invalid_feed_json" if purpose == "feed" else "invalid_encoding"
            if str(exc) in {"html_node_limit", "html_depth_limit"}:
                code = "html_structure_limit"
            raise FetchFailure(code, status, attempt) from exc
        if re.search(
            r"验证码|安全验证|访问过于频繁|verify you are human|captcha|access denied",
            normalized,
            re.I,
        ):
            raise FetchFailure("access_challenge", status, attempt)
        if purpose == "notice" and (
            len(normalized) < 80 or "采购" not in normalized or "公告" not in normalized
        ):
            raise FetchFailure("unverified_notice_body", status, attempt)
        return encoding
