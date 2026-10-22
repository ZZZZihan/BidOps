import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field

from bidops.models import StrictModel


class Source(StrictModel):
    id: str
    name: str
    host: str
    home_url: str
    robots_status: Literal["allowed", "denied", "unknown"] = "unknown"
    terms_status: Literal["allowed", "denied", "unknown"] = "unknown"
    permission_evidence: str | None = None
    reviewed_at: str | None = None
    decision_reason: str
    allowed_urls: list[str] = Field(default_factory=list, max_length=50)


def load_sources(path: Path) -> list[Source]:
    sources = [Source.model_validate(s) for s in json.loads(path.read_text())]
    if len({s.host for s in sources}) != len(sources):
        raise ValueError("duplicate_source_hosts")
    for source in sources:
        if any(canonical_url(url, [source]) != url for url in source.allowed_urls):
            raise ValueError("allowlist_not_canonical")
    return sources


def canonical_url(url: str, sources: list[Source]) -> str:
    if len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError("invalid_url")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValueError("only_clean_https_urls_allowed")
    if parsed.hostname not in {s.host for s in sources}:
        raise ValueError("host_not_registered")
    # Reject ambiguous encodings/traversal; no expanding redirects or arbitrary destinations.
    if (
        "%" in parsed.path
        or "\\" in parsed.path
        or any(p in {".", ".."} for p in parsed.path.split("/"))
    ):
        raise ValueError("ambiguous_url_path")
    return urlunsplit(("https", parsed.hostname, parsed.path or "/", "", ""))


def network_permission(url: str, sources: list[Source]) -> tuple[bool, str]:
    url = canonical_url(url, sources)
    source = next(s for s in sources if s.host == urlsplit(url).hostname)
    if url not in source.allowed_urls:
        return False, "url_not_allowlisted"
    if source.robots_status != "allowed" or source.terms_status != "allowed":
        return False, "source_permission_unconfirmed"
    if not source.permission_evidence or not source.reviewed_at:
        return False, "permission_evidence_missing"
    return True, "allowed"
