from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    source_snapshot_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str
    locator: Literal["normalized_text_v1", "normalized_text_v2"] = "normalized_text_v2"
    confidence_type: Literal["rule"] = "rule"


class Money(StrictModel):
    amount: str
    currency: Literal["CNY"] = "CNY"
    raw: str
    unit: Literal["元", "万元"]
    basis: Literal["total", "package"] = "total"


class Package(StrictModel):
    label: str
    budget: Money | None = None
    evidence: Evidence


class Snapshot(StrictModel):
    snapshot_id: str
    source_url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_size: int = Field(gt=0)
    fetched_at: datetime
    http_status: int | None = None
    content_type: Literal["text/html", "text/plain"]
    encoding: Literal["utf-8", "gb18030"]
    access_method: Literal["manual_import", "http_fetch"]
    etag: str | None = None
    last_modified: str | None = None
    source_completeness: Literal["notice_only"] = "notice_only"
    manual_attestation: bool = False

    @field_validator("fetched_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("fetched_at requires an explicit timezone")
        return value


class Notice(StrictModel):
    notice_id: str
    project_code: str | None = None
    title: str | None = None
    buyer_name: str | None = None
    notice_kind: str | None = None
    budget: Money | None = None
    published_at: str | None = None
    published_date: str | None = None
    publication_precision: Literal["date", "minute", "second"] | None = None
    bid_deadline_at: str | None = None
    document_deadline_at: str | None = None
    observed_at: str
    status_as_observed: Literal["unknown", "review_required"] = "unknown"
    packages: list[Package] = Field(default_factory=list)
    eligibility_conditions: list[Evidence] = Field(default_factory=list)
    source: Snapshot
    field_evidence: dict[str, list[Evidence]] = Field(default_factory=dict)
    abstain_reason: dict[str, str] = Field(default_factory=dict)
    parse_warnings: list[str] = Field(default_factory=list)
    parser_version: str = "p0.2"
