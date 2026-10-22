"""Independent readiness polling, producing local alert/recovery receipts only."""

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx2

from bidops.enterprise.health import COMPONENTS


class Watchdog:
    def __init__(self):
        self.last = None

    def observe(self, healthy, *, at):
        if type(healthy) is not bool:
            raise ValueError("watchdog_requires_boolean_state")
        event = None
        if (self.last is None and not healthy) or (self.last is not None and self.last != healthy):
            event = {
                "event": "readiness_alert",
                "state": "resolved" if healthy else "firing",
                "observed_at": at,
            }
        self.last = healthy
        return event


def origin(value):
    parts = urlsplit(value)
    if (
        parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
        or not parts.hostname
        or (
            parts.scheme != "https"
            and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"})
        )
    ):
        raise ValueError("HTTPS_or_explicit_loopback_origin_required")
    return value.rstrip("/")


def check(client, url):
    """Bound response bytes and per-I/O waits; no redirects or credentials.

    The ten-second elapsed guard is checked between raw chunks. HTTPX's five
    second timeout applies per I/O, not as a hard wall-clock request deadline.
    """
    try:
        started = time.monotonic()
        with client.stream(
            "GET",
            url + "/api/ready",
            follow_redirects=False,
            timeout=5,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            if (
                response.status_code not in {200, 503}
                or response.headers.get("Content-Encoding", "identity").lower() != "identity"
            ):
                return False, ["unavailable"]
            body = bytearray()
            for chunk in response.iter_raw():
                if len(body) + len(chunk) > 16384 or time.monotonic() - started > 10:
                    return False, ["unavailable"]
                body.extend(chunk)
            payload = json.loads(body)
            status_code = response.status_code
        checks = payload.get("checks", {})
        failures = [
            name for name in (*COMPONENTS, "observation_current") if checks.get(name) is False
        ]
        healthy = status_code == 200 and payload.get("ready") is True and not failures
        return healthy, failures
    except (httpx2.HTTPError, ValueError, TypeError, AttributeError):
        return False, ["unavailable"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        url = origin(args.origin)
        if not 1 <= args.interval_seconds <= 60 or not 1 <= args.duration_seconds <= 86400:
            raise ValueError
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except (OSError, ValueError):
        print('{"status":"FAIL","code":"invalid_input_or_existing_output"}')
        return 2
    watcher, started = Watchdog(), time.monotonic()
    with os.fdopen(fd, "w") as output, httpx2.Client(trust_env=False) as client:
        while True:
            at = datetime.now(UTC).isoformat()
            healthy, failures = check(client, url)
            event = watcher.observe(healthy, at=at)
            output.write(
                json.dumps(
                    {"observed_at": at, "healthy": healthy, "failures": failures, "alert": event}
                )
                + "\n"
            )
            output.flush()
            if event:
                print(json.dumps(event), flush=True)
            remaining = args.duration_seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(args.interval_seconds, remaining))
    return 0
