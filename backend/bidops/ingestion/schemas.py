"""Bounded acquisition commands; actor, acquisition time and outcomes are server-owned."""

import base64
import binascii
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX_REQUEST_BYTES = 10 * 1024 * 1024
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
ErrorCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,79}$")]
Version = Annotated[int, Field(strict=True, ge=0)]
PageCount = Annotated[int, Field(strict=True, ge=1, le=2000)]
EncodedBody = Annotated[str, StringConstraints(min_length=4, max_length=14_000_000)]


def decode_body(value: str, max_bytes: int = MAX_REQUEST_BYTES) -> bytes:
    """Strict base64 with a bound before decoding, including service-configured limits."""
    if len(value) > 4 * ((max_bytes + 2) // 3):
        raise ValueError("ingestion_blob_too_large")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("ingestion_invalid_base64") from None
    if not data:
        raise ValueError("ingestion_empty_blob")
    if len(data) > max_bytes:
        raise ValueError("ingestion_blob_too_large")
    return data


def valid_url(value: str) -> str:
    # The fetcher additionally validates configured host/path scope and redirects.
    if len(value) > 4096 or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError("ingestion_invalid_url")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.query
        ):
            raise ValueError("ingestion_invalid_url")
        _ = parsed.port
    except ValueError:
        raise ValueError("ingestion_invalid_url") from None
    return value


class IngestionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class AttachmentTarget(IngestionModel):
    attachment_key: Identifier
    filename: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    source_url: str
    content_type: Annotated[str, StringConstraints(min_length=1, max_length=100)] = (
        "application/octet-stream"
    )
    expected_page_count: PageCount | None = None

    _url = field_validator("source_url")(valid_url)

    @field_validator("filename")
    @classmethod
    def filename_safe(cls, value):
        if value in {".", ".."} or any(
            char in "/\\" or ord(char) < 32 or ord(char) == 127 for char in value
        ):
            raise ValueError("ingestion_invalid_filename")
        return value


class HttpItem(IngestionModel):
    source_url: str
    attachments: list[AttachmentTarget] = Field(default_factory=list, max_length=50)

    _url = field_validator("source_url")(valid_url)

    @model_validator(mode="after")
    def unique_attachments(self):
        keys = [item.attachment_key for item in self.attachments]
        if len(keys) != len(set(keys)):
            raise ValueError("ingestion_duplicate_attachment_key")
        return self


class HttpRun(IngestionModel):
    source_id: Identifier
    idempotency_key: Identifier
    items: list[HttpItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        urls = [item.source_url for item in self.items]
        if len(urls) != len(set(urls)):
            raise ValueError("ingestion_duplicate_url")
        return self


class ManualAttachment(IngestionModel):
    attachment_key: Identifier
    filename: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    source_url: str | None = None
    content_type: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    encoding: Literal["utf-8", "gb18030"] = "utf-8"
    body_base64: EncodedBody | None = None
    failed: Annotated[bool, Field(strict=True)] = False
    error_code: ErrorCode | None = None
    expected_page_count: PageCount | None = None

    _filename = field_validator("filename")(AttachmentTarget.filename_safe.__func__)

    @field_validator("source_url")
    @classmethod
    def url_optional(cls, value):
        return None if value is None else valid_url(value)

    @model_validator(mode="after")
    def acquisition(self):
        if self.failed:
            if self.body_base64 is not None or self.error_code is None:
                raise ValueError("ingestion_failed_attachment_requires_only_error")
        elif self.body_base64 is None or self.error_code is not None:
            raise ValueError("ingestion_attachment_requires_body")
        else:
            decode_body(self.body_base64)
        return self


class ManualItem(IngestionModel):
    source_url: str
    content_type: Literal["text/html", "text/plain"]
    encoding: Literal["utf-8", "gb18030"] = "utf-8"
    body_base64: EncodedBody
    source_complete: Annotated[bool, Field(strict=True)] = False
    attachments: list[ManualAttachment] = Field(default_factory=list, max_length=50)

    _url = field_validator("source_url")(valid_url)

    @model_validator(mode="after")
    def body_and_keys(self):
        decode_body(self.body_base64)
        keys = [item.attachment_key for item in self.attachments]
        if len(keys) != len(set(keys)):
            raise ValueError("ingestion_duplicate_attachment_key")
        return self


class ManualPage(IngestionModel):
    items: list[ManualItem] = Field(min_length=1, max_length=100)
    cursor_after: Annotated[str, StringConstraints(max_length=1024)] | None = None


class ManualImport(IngestionModel):
    source_id: Identifier
    idempotency_key: Identifier
    rights_attestation: Annotated[bool, Field(strict=True)]
    responsibility_reason: Reason
    pages: list[ManualPage] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def rights_and_total(self):
        if self.rights_attestation is not True:
            raise ValueError("ingestion_rights_attestation_required")
        if sum(len(page.items) for page in self.pages) > 500:
            raise ValueError("ingestion_too_many_items")
        return self


class CancelCommand(IngestionModel):
    expected_state_version: Version


class ScheduleCommand(HttpRun):
    schedule_id: Identifier
    expected_state_version: Version
    cron: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    enabled: Annotated[bool, Field(strict=True)]
    reason: Reason
