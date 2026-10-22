"""Registered URL boundaries and explicitly reviewed acquisition permissions.

A successful HTTP response is never evidence of permission. Legacy metadata stays
readable, but cannot authorize a request without a complete, current policy.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from bidops.models import StrictModel

AcquisitionMode = Literal["manual_import", "manual_fetch", "scheduled_fetch"]
NetworkMode = Literal["manual_fetch", "scheduled_fetch"]


class SourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    status: Literal["allowed", "denied", "unknown"]
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_reference: str = Field(min_length=1, max_length=2048)
    reviewed_by: str = Field(min_length=1, max_length=256)
    reviewed_at: AwareDatetime
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    acquisition_modes: list[AcquisitionMode] = Field(min_length=1, max_length=3)
    url_scope: list[str] = Field(min_length=1, max_length=50)
    rate_limit_seconds: float = Field(ge=5, le=86400, allow_inf_nan=False, strict=True)
    max_attempts: int = Field(ge=1, le=3, strict=True)
    max_requests: int = Field(ge=1, le=10000, strict=True)
    max_bytes: int = Field(gt=0, le=10_000_000, strict=True)
    total_timeout_seconds: float = Field(gt=0, le=180, allow_inf_nan=False, strict=True)

    @field_validator("reviewed_at", "valid_from", "valid_until", mode="before")
    @classmethod
    def explicit_time(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("policy_time_requires_explicit_timezone")
        return value

    @field_validator("evidence_reference", "reviewed_by")
    @classmethod
    def bounded_text(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid_policy_text")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.valid_from >= self.valid_until or self.reviewed_at >= self.valid_until:
            raise ValueError("invalid_policy_time_window")
        if len(set(self.acquisition_modes)) != len(self.acquisition_modes):
            raise ValueError("duplicate_acquisition_modes")
        if len(set(self.url_scope)) != len(self.url_scope):
            raise ValueError("duplicate_policy_urls")
        return self

    @property
    def sha256(self) -> str:
        value = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(value.encode()).hexdigest()


class Source(StrictModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

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
    policy: SourcePolicy | None = None

    @model_validator(mode="after")
    def policy_scope(self):
        if self.policy:
            for url in self.policy.url_scope:
                if canonical_url(url, [self]) != url or url not in self.allowed_urls:
                    raise ValueError("policy_url_outside_source_allowlist")
        return self


def load_sources(path: Path) -> list[Source]:
    sources = [Source.model_validate(s) for s in json.loads(path.read_text())]
    if len({s.host for s in sources}) != len(sources):
        raise ValueError("duplicate_source_hosts")
    if len({s.id for s in sources}) != len(sources):
        raise ValueError("duplicate_source_ids")
    for source in sources:
        if any(canonical_url(url, [source]) != url for url in source.allowed_urls):
            raise ValueError("allowlist_not_canonical")
    return sources


def canonical_url(url: str, sources: list[Source]) -> str:
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
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
    # No ambiguous encodings, traversal, redirects, or arbitrary destinations.
    if (
        "%" in parsed.path
        or "\\" in parsed.path
        or any(p in {".", ".."} for p in parsed.path.split("/"))
    ):
        raise ValueError("ambiguous_url_path")
    return urlunsplit(("https", parsed.hostname, parsed.path or "/", "", ""))


def network_permission(
    url: str,
    sources: list[Source],
    *,
    now: datetime | None = None,
    mode: NetworkMode = "manual_fetch",
) -> tuple[bool, str]:
    url = canonical_url(url, sources)
    source = next(s for s in sources if s.host == urlsplit(url).hostname)
    if url not in source.allowed_urls:
        return False, "url_not_allowlisted"
    if source.robots_status != "allowed" or source.terms_status != "allowed":
        return False, "source_permission_unconfirmed"
    if not source.permission_evidence or not source.reviewed_at:
        return False, "permission_evidence_missing"
    # Deep validation also rejects model_copy/construct or in-memory mutations.
    try:
        source = Source.model_validate(source.model_dump(mode="python", warnings=False))
    except (ValueError, TypeError):
        return False, "invalid_source_policy"
    policy = source.policy
    if policy is None:
        return False, "source_policy_missing"
    if policy.status != "allowed":
        return False, "source_policy_unconfirmed"
    instant = now if now is not None else datetime.now(UTC)
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        return False, "invalid_policy_clock"
    if instant < policy.reviewed_at or instant < policy.valid_from:
        return False, "source_policy_not_yet_valid"
    if instant >= policy.valid_until:
        return False, "source_policy_expired"
    if mode not in {"manual_fetch", "scheduled_fetch"} or mode not in policy.acquisition_modes:
        return False, "acquisition_mode_not_allowed"
    if url not in policy.url_scope:
        return False, "url_outside_policy_scope"
    return True, "allowed"
