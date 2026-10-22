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


class AttachmentRecord(AttachmentInput):
    record_id: NonBlank
    attachment_id: NonBlank
    raw_sha256: Sha256 | None
    text_sha256: Sha256 | None
    byte_size: int = Field(ge=0)
    normalizer_version: Literal["normalized_text_v2"] | None
    # Current availability is independent of immutable acquisition history.
    integrity_status: Literal["not_checked", "verified", "unavailable", "corrupt"] = "not_checked"
    integrity_error: str | None = None

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
