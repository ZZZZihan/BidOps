"""Notice history and acquisition contracts, independent of screening decisions."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints, model_validator

from bidops.models import Notice, StrictModel
from bidops.screening.schemas import VersionRef

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
AcquisitionStatus = Literal["not_downloaded", "downloaded", "failed"]
ParseStatus = Literal[
    "pending", "parsed", "failed", "encrypted", "scan_unrecognized", "unsupported"
]


class DataModel(StrictModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class PackageVersion(DataModel):
    package_id: NonBlank
    label: NonBlank
    normalized_label: NonBlank
    evidence_start: int = Field(ge=0)
    evidence_end: int = Field(gt=0)


class NoticeVersion(DataModel):
    version_id: NonBlank
    snapshot_id: NonBlank
    logical_notice_id: NonBlank
    project_id: NonBlank | None
    association_state: Literal["matched", "pending_review"]
    parser_version: NonBlank
    normalizer_version: Literal["normalized_text_v2"]
    available_at: AwareDatetime
    raw_sha256: Sha256
    text_sha256: Sha256
    payload_sha256: Sha256
    notice: Notice
    packages: list[PackageVersion]
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime

    @property
    def version_ref(self) -> VersionRef:
        return VersionRef(
            object_id=self.logical_notice_id, version_id=self.version_id, sha256=self.raw_sha256
        )


class AttachmentInput(DataModel):
    parent_version_id: NonBlank
    attachment_key: NonBlank
    filename: NonBlank
    observed_at: AwareDatetime
    source_url: NonBlank | None = None
    content_type: NonBlank = "application/octet-stream"
    encoding: Literal["utf-8", "gb18030"] = "utf-8"
    acquisition_status: AcquisitionStatus
    parse_status: ParseStatus
    error_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")] | None = None
    diagnostic_origin: Literal["imported_metadata"] | None = None
    declared_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def states(self):
        if self.acquisition_status != "downloaded" and self.parse_status != "pending":
            raise ValueError("attachment_not_acquired")
        if self.acquisition_status == "failed" and self.error_code is None:
            raise ValueError("attachment_failure_requires_code")
        if self.parse_status == "failed" and self.error_code is None:
            raise ValueError("attachment_failure_requires_code")
        if self.parse_status in {"encrypted", "scan_unrecognized"}:
            if self.diagnostic_origin != "imported_metadata":
                raise ValueError("attachment_diagnostic_requires_provenance")
        elif self.diagnostic_origin is not None:
            raise ValueError("unexpected_attachment_diagnostic")
        if self.parse_status == "parsed" and self.content_type not in {"text/plain", "text/html"}:
            raise ValueError("attachment_parser_unsupported")
        if self.parse_status == "unsupported" and self.content_type in {"text/plain", "text/html"}:
            raise ValueError("attachment_parser_supported")
        return self


class PreparedAttachmentInput(AttachmentInput):
    """Server-produced PDF outcome; legacy AttachmentInput remains unchanged."""

    diagnostic_origin: Literal["imported_metadata", "parser"] | None = None

    @model_validator(mode="after")
    def states(self):
        if self.content_type != "application/pdf":
            return super().states()
        if self.acquisition_status != "downloaded" and self.parse_status != "pending":
            raise ValueError("attachment_not_acquired")
        if self.acquisition_status == "failed" and self.error_code is None:
            raise ValueError("attachment_failure_requires_code")
        if self.parse_status == "failed" and self.error_code is None:
            raise ValueError("attachment_failure_requires_code")
        if self.parse_status in {"encrypted", "scan_unrecognized"}:
            if self.diagnostic_origin not in {"imported_metadata", "parser"}:
                raise ValueError("attachment_diagnostic_requires_provenance")
        elif self.diagnostic_origin is not None:
            raise ValueError("unexpected_attachment_diagnostic")
        return self


class PDFParserLimits(DataModel):
    max_bytes: int = Field(default=10_000_000, strict=True, ge=1, le=10_000_000)
    max_pages: int = Field(default=200, strict=True, ge=1, le=2000)
    max_decoded_stream_bytes: int = Field(default=8_000_000, strict=True, ge=1024, le=16_000_000)
    max_page_chars: int = Field(default=100_000, strict=True, ge=1, le=2_000_000)
    max_text_chars: int = Field(default=500_000, strict=True, ge=1, le=2_000_000)
    wall_seconds: float = Field(default=20, gt=0, le=60, allow_inf_nan=False)
    memory_bytes: int = Field(
        default=256 * 1024 * 1024, strict=True, ge=32 * 1024 * 1024, le=1024 * 1024 * 1024
    )


class PDFPageSpan(DataModel):
    page: int = Field(strict=True, ge=1)
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, ge=0)
    text_sha256: Sha256
    status: Literal["text", "unrecognized", "failed"]
    error_code: Annotated[str, Field(pattern=r"^pdf_[a-z0-9_]{1,70}$")] | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.end < self.start or (self.status == "text") != (self.end > self.start):
            raise ValueError("pdf_page_span_invalid")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("pdf_page_failure_requires_code")
        return self


class PDFParseReceipt(DataModel):
    schema_version: Literal["pdf_text_receipt_v1"] = "pdf_text_receipt_v1"
    engine: Literal["pypdf"] = "pypdf"
    engine_version: Literal["6.21.2"] = "6.21.2"
    adapter_version: Literal["pypdf_text_v1"] = "pypdf_text_v1"
    options_sha256: Sha256
    raw_sha256: Sha256
    text_sha256: Sha256
    page_map_sha256: Sha256
    page_count: int | None = Field(default=None, strict=True, ge=1)
    expected_page_count: int | None = Field(default=None, strict=True, ge=1)
    pages: list[PDFPageSpan]
    complete: bool
    status: Literal["parsed", "failed", "encrypted", "scan_unrecognized"]
    error_code: Annotated[str, Field(pattern=r"^pdf_[a-z0-9_]{1,70}$")] | None
    limits: PDFParserLimits
    memory_enforcement: Literal["rlimit_as_and_rss", "rss_watchdog"]

    @model_validator(mode="after")
    def shape(self):
        if [page.page for page in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("pdf_page_sequence_invalid")
        if self.pages and self.page_count != len(self.pages):
            raise ValueError("pdf_page_count_invalid")
        if self.complete != (self.status == "parsed"):
            raise ValueError("pdf_complete_status_invalid")
        if self.complete and (
            not self.pages
            or any(page.status != "text" for page in self.pages)
            or self.expected_page_count not in {None, self.page_count}
        ):
            raise ValueError("pdf_incomplete_pages")
        if (self.status == "parsed") != (self.error_code is None):
            raise ValueError("pdf_failure_requires_code")
        return self


class AttachmentRecord(PreparedAttachmentInput):
    record_id: NonBlank
    attachment_id: NonBlank
    raw_sha256: Sha256 | None
    text_sha256: Sha256 | None
    byte_size: int = Field(ge=0)
    normalizer_version: Literal["normalized_text_v2"] | None
    # Current availability is independent of immutable acquisition history.
    integrity_status: Literal["not_checked", "verified", "unavailable", "corrupt"] = "not_checked"
    integrity_error: str | None = None
    parser_receipt_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def pdf_receipt(self):
        if self.content_type == "application/pdf" and self.parse_status == "parsed":
            if self.parser_receipt_sha256 is None:
                raise ValueError("pdf_receipt_required")
        if self.parser_receipt_sha256 is not None and self.content_type != "application/pdf":
            raise ValueError("pdf_receipt_media_mismatch")
        return self

    @property
    def version_ref(self) -> VersionRef | None:
        if self.raw_sha256 is None:
            return None
        return VersionRef(
            object_id=self.attachment_id, version_id=self.record_id, sha256=self.raw_sha256
        )


class ProjectStatus(DataModel):
    project_id: NonBlank
    as_of: AwareDatetime
    status: Literal["unknown", "open", "closed", "terminated"]
    reason: NonBlank
    observed_at: AwareDatetime | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    is_fresh: bool = False
    source_complete: bool = False
    source_observations: dict[str, AwareDatetime] = Field(default_factory=dict)
    integrity_issues: dict[str, str] = Field(default_factory=dict)
    version_ids: list[str] = Field(default_factory=list)
    basis_version_id: str | None = None
    missing_attachment_ids: list[str] = Field(default_factory=list)
