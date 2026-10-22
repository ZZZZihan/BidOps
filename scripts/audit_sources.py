"""Explicit metadata audit, not notice collection. Never changes permission to allowed."""

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bidops.config import Settings
from bidops.sources import load_sources
from bs4 import BeautifulSoup


def run(output: Path, source_id: str | None = None) -> dict:
    settings = Settings()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    records = []
    last = None
    with httpx.Client(timeout=8, follow_redirects=False) as client:
        for source in load_sources(settings.source_registry):
            if source_id and source.id != source_id:
                continue
            queue = [("robots", source.home_url + "robots.txt"), ("homepage", source.home_url)]
            for kind, url in queue:
                if last is not None:
                    time.sleep(max(0, 5 - (time.monotonic() - last)))
                last = time.monotonic()
                record = {
                    "source": source.id,
                    "kind": kind,
                    "url": url,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "permission": "unknown",
                }
                try:
                    with client.stream(
                        "GET", url, headers={"User-Agent": settings.user_agent}
                    ) as response:
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            data.extend(chunk)
                            if len(data) > settings.max_bytes:
                                raise ValueError("audit_body_too_large")
                        record.update(
                            http_status=response.status_code,
                            content_type=response.headers.get("content-type"),
                            location=response.headers.get("location"),
                            sha256=hashlib.sha256(data).hexdigest(),
                            byte_size=len(data),
                        )
                        (output / record["sha256"]).write_bytes(data)
                        if response.status_code == 200 and kind == "homepage":
                            soup = BeautifulSoup(bytes(data), "html.parser")
                            links = []
                            for a in soup.find_all("a", href=True):
                                label = a.get_text(" ", strip=True)
                                if any(
                                    word in label
                                    for word in ["声明", "版权", "使用条款", "使用须知"]
                                ):
                                    link = urljoin(url, a["href"])
                                    parsed = urlsplit(link)
                                    if (
                                        parsed.scheme == "https"
                                        and parsed.hostname == source.host
                                        and not parsed.username
                                        and not parsed.query
                                    ):
                                        links.append(("terms", link))
                            queue.extend(list(dict.fromkeys(links))[:2])
                            record["terms_links_found"] = [link for _, link in links]
                        record["note"] = "Observation only; human permission review required"
                except (httpx.HTTPError, ValueError) as exc:
                    record["error"] = type(exc).__name__
                records.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
    report = {
        "audit_kind": "source_metadata_only",
        "records": records,
        "notice_http_requests": 0,
        "permission_decision": "unknown",
    }
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-metadata-audit", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/artifacts/source-audit"))
    parser.add_argument("--source-id", choices=["ccgp", "tianjin", "tjggzy"])
    args = parser.parse_args()
    run(args.output, args.source_id)
