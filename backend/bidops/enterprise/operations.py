import fcntl
import json
import logging
import time
import uuid
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter, Histogram
from sqlalchemy.engine import make_url
from starlette.responses import JSONResponse

logger = logging.getLogger("bidops.requests")


class SingleWorker:
    """Fail startup rather than silently lose process-local admission/rate guarantees."""

    def __init__(self, database_url: str):
        path = Path(make_url(database_url).database).with_suffix(".enterprise.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a")
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise RuntimeError("Enterprise SQLite deployment requires exactly one worker") from None

    def close(self):
        fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


class Operations:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "bidops_http_requests_total",
            "HTTP responses",
            ["route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "bidops_http_duration_seconds",
            "HTTP latency",
            ["route"],
            registry=self.registry,
            buckets=(0.01, 0.1, 0.5, 1, 3, 10, 30, 60, 120),
        )


class OperationsMiddleware:
    def __init__(self, app, operations: Operations, enterprise: bool):
        self.app, self.operations, self.enterprise = app, operations, enterprise

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id, started, status = uuid.uuid4().hex, time.monotonic(), 500
        scope.setdefault("state", {})["request_id"] = request_id

        async def wrapped_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                ]
                if self.enterprise:
                    headers += [
                        (b"cache-control", b"no-store"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; style-src 'self'; "
                            b"img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                            b"base-uri 'none'; form-action 'self'",
                        ),
                    ]
                    if scope.get("scheme") == "https":
                        headers.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = headers
            await send(message)

        try:
            if self.enterprise and scope["method"] in {"POST", "PUT", "PATCH"}:
                body = b""
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body += message.get("body", b"")
                    if len(body) > 16384:
                        return await JSONResponse({"error": "request_too_large"}, 413)(
                            scope, receive, wrapped_send
                        )
                    if not message.get("more_body"):
                        break
                delivered = False

                async def buffered_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()

                await self.app(scope, buffered_receive, wrapped_send)
            else:
                await self.app(scope, receive, wrapped_send)
        finally:
            # Templates only: no user/session IDs, raw URLs, queries or tokens in labels/logs.
            route = getattr(scope.get("route"), "path", "unmatched")
            elapsed = time.monotonic() - started
            self.operations.requests.labels(route, str(status)).inc()
            self.operations.latency.labels(route).observe(elapsed)
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "route": route,
                        "status": status,
                        "elapsed_ms": round(elapsed * 1000),
                    }
                )
            )
