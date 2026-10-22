import re
import time
from dataclasses import dataclass

import httpx

from bidops.parser import canonical_text
from bidops.sources import canonical_url, network_permission
from bidops.storage import Store


@dataclass
class FetchFailure(Exception):
    code: str
    status: int | None = None
    attempts: int = 0


class Fetcher:
    def __init__(
        self,
        store: Store,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.store = store
        self.settings = store.settings
        self.client = client
        self.owned_client = client is None
        self.sleep = sleep
        self.clock = clock
        self.last_request: float | None = None

    def close(self):
        if self.owned_client and self.client:
            self.client.close()

    def fetch(self, url: str, execute: bool = False) -> dict:
        url = canonical_url(url, self.store.sources)
        if not execute:
            allowed, reason = network_permission(url, self.store.sources)
            return {
                "url": url,
                "state": "dry_run",
                "permission": reason,
                "would_fetch": allowed and self.settings.network_enabled,
            }
        try:
            if not self.settings.network_enabled:
                raise FetchFailure("network_disabled")
            allowed, reason = network_permission(url, self.store.sources)
            if not allowed:
                raise FetchFailure(reason)
            return self._fetch_allowed(url)
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

    def _fetch_allowed(self, url: str) -> dict:
        if self.client is None:
            self.client = httpx.Client(follow_redirects=False)
        for attempt in range(1, self.settings.http_attempts + 1):
            if self.last_request is not None:
                self.sleep(
                    max(
                        0,
                        self.settings.http_rate_limit_seconds - (self.clock() - self.last_request),
                    )
                )
            self.last_request = self.clock()
            try:
                with self.client.stream(
                    "GET",
                    url,
                    headers={
                        "User-Agent": self.settings.user_agent,
                        "Accept": "text/html,text/plain",
                    },
                    timeout=self.settings.http_timeout_seconds,
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
                    if status in {403, 404, 401, 429} or 300 <= status < 400:
                        raise FetchFailure(f"http_{status}", status, attempt)
                    if status in {500, 502, 503, 504} and attempt < self.settings.http_attempts:
                        self.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    if status != 200:
                        raise FetchFailure(f"http_{status}", status, attempt)
                    media = response.headers.get("content-type", "").split(";")[0].lower().strip()
                    if media not in {"text/html", "text/plain"}:
                        raise FetchFailure("unsupported_content_type", status, attempt)
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.settings.max_bytes:
                            raise FetchFailure("body_too_large", status, attempt)
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    content_header = response.headers.get("content-type", "")
                    encoding_match = re.search(
                        r"charset\s*=\s*[\"']?([\w-]+)", content_header, re.I
                    )
                    if not encoding_match:
                        encoding_match = re.search(
                            r"charset\s*=\s*[\"']?([\w-]+)",
                            data[:2048].decode("ascii", "ignore"),
                            re.I,
                        )
                    encoding_name = encoding_match.group(1).lower() if encoding_match else "utf-8"
                    encoding = {
                        "utf-8": "utf-8",
                        "utf8": "utf-8",
                        "gbk": "gb18030",
                        "gb2312": "gb18030",
                        "gb18030": "gb18030",
                    }.get(encoding_name)
                    if not encoding:
                        raise FetchFailure("unsupported_encoding", status, attempt)
                    try:
                        normalized = canonical_text(data, media, encoding)
                    except (UnicodeError, ValueError) as exc:
                        raise FetchFailure("invalid_encoding", status, attempt) from exc
                    if re.search(
                        r"验证码|安全验证|访问过于频繁|verify you are human|captcha|access denied",
                        normalized,
                        re.I,
                    ):
                        raise FetchFailure("access_challenge", status, attempt)
                    if len(normalized) < 80 or "采购" not in normalized or "公告" not in normalized:
                        raise FetchFailure("unverified_notice_body", status, attempt)
                    snapshot, new = self.store.ingest(
                        url,
                        data,
                        content_type=media,
                        encoding=encoding,
                        access_method="http_fetch",
                        http_status=status,
                        etag=response.headers.get("etag"),
                        last_modified=response.headers.get("last-modified"),
                    )
                    return {
                        "url": url,
                        "state": "captured" if new else "duplicate",
                        "snapshot_id": snapshot.snapshot_id,
                        "sha256": snapshot.sha256,
                        "http_status": status,
                        "attempts": attempt,
                    }
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                code = "timeout" if isinstance(exc, httpx.TimeoutException) else "network_error"
                self.store.db.audit("http_attempt", code, url, attempt=attempt)
                if attempt == self.settings.http_attempts:
                    raise FetchFailure(code, attempts=attempt) from exc
                self.sleep(min(2 ** (attempt - 1), 4))
            except httpx.HTTPError as exc:
                raise FetchFailure("http_transport_error", attempts=attempt) from exc
            except ValueError as exc:
                raise FetchFailure("unverified_notice_fields", attempts=attempt) from exc
        raise AssertionError("unreachable retry state")
