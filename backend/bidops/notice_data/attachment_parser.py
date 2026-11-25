"""Bounded local attachment preparation, always outside the database transaction.

PDF text and page maps are immutable parser receipts, not semantic completeness
or OCR claims. Successful old receipts are verified from stored bytes and never
re-extracted using whatever parser version happens to be installed later.
"""

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from bidops.notice_data.schemas import (
    AttachmentInput,
    PDFParseReceipt,
    PDFParserLimits,
    PreparedAttachmentInput,
)
from bidops.parser import canonical_text

ParserLimits = PDFParserLimits
WORKER = Path(__file__).with_name("attachment_worker.py")
OPTIONS = {
    "strict": True,
    "extraction_mode": "plain",
    "ocr": False,
    "bounded_decoder_configuration": "pypdf_limits_v1",
}
_SLOT = threading.Lock()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


@dataclass(frozen=True)
class PreparedAttachment:
    input: PreparedAttachmentInput
    data: bytes | None = field(repr=False)
    normalized_text: str | None = field(default=None, repr=False)
    receipt: PDFParseReceipt | None = None


def verify_receipt(receipt, raw, normalized):
    """Verify a fixed receipt and its complete page/global-span crosswalk."""
    try:
        receipt = PDFParseReceipt.model_validate(receipt)
    except (ValidationError, ValueError):
        raise ValueError("attachment_receipt_invalid") from None
    if (
        not isinstance(raw, bytes)
        or not isinstance(normalized, str)
        or receipt.raw_sha256 != sha(raw)
        or receipt.text_sha256 != sha(normalized)
        or receipt.options_sha256 != sha(canonical(OPTIONS))
        or receipt.page_map_sha256 != sha(canonical([p.model_dump() for p in receipt.pages]))
        or len(normalized) > receipt.limits.max_text_chars
    ):
        raise ValueError("attachment_receipt_integrity_mismatch")
    offset = 0
    for page in receipt.pages:
        if page.status == "text" and offset:
            if normalized[offset : offset + 1] != "\n":
                raise ValueError("attachment_page_map_invalid")
            offset += 1
        if page.start != offset or page.end > len(normalized):
            raise ValueError("attachment_page_map_invalid")
        value = normalized[page.start : page.end]
        if page.text_sha256 != sha(value) or len(value) > receipt.limits.max_page_chars:
            raise ValueError("attachment_page_map_invalid")
        if page.status == "text" and (
            not value.strip() or canonical_text(value.encode(), "text/plain", "utf-8") != value
        ):
            raise ValueError("attachment_page_map_invalid")
        offset = page.end
    if offset != len(normalized):
        raise ValueError("attachment_page_map_invalid")
    return receipt


@contextmanager
def _slot():
    """One parser per OS user across threads/processes; no stale lock on SIGKILL."""
    descriptor = None
    acquired = _SLOT.acquire(blocking=False)
    try:
        if not acquired:
            yield False
            return
        root = Path(tempfile.gettempdir()) / f"bidops-pdf-{os.getuid()}"
        root.mkdir(mode=0o700, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("pdf_private_directory_invalid")
        descriptor = os.open(root / "parser.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("pdf_private_directory_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if acquired:
            _SLOT.release()


def _rss(pid):
    # macOS has no /proc; use the OS utility with a server-created integer PID.
    result = subprocess.run(
        ["/bin/ps", "-o", "rss=", "-p", str(pid)], capture_output=True, timeout=1, check=False
    )
    if result.returncode != 0 or not result.stdout.strip().isdigit():
        raise ValueError("pdf_resource_monitor_unavailable")
    return int(result.stdout.strip()) * 1024


def _run_pdf(data, limits, expected_page_count):
    failure = "pdf_parse_failed"
    with tempfile.TemporaryDirectory(prefix="bidops-pdf-input-") as temporary:
        directory = Path(temporary)
        for name, value in (
            ("input.pdf", data),
            (
                "request.json",
                canonical(
                    {
                        "limits": limits.model_dump(),
                        "expected_page_count": expected_page_count,
                    }
                ).encode(),
            ),
        ):
            descriptor = os.open(directory / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
        process = subprocess.Popen(
            [sys.executable, "-I", str(WORKER), str(directory)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        deadline = time.monotonic() + limits.wall_seconds
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    failure = "pdf_timeout"
                    break
                try:
                    rss = _rss(process.pid)
                except (ValueError, OSError, subprocess.SubprocessError):
                    # Exiting between poll and ps is normal; an alive process
                    # without its resource monitor fails closed.
                    if process.poll() is None:
                        failure = "pdf_resource_monitor_unavailable"
                        break
                else:
                    if rss > limits.memory_bytes:
                        failure = "pdf_memory_limit"
                        break
                try:
                    process.wait(timeout=0.02)
                except subprocess.TimeoutExpired:
                    pass
            else:
                failure = None
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        response = directory / "response.json"
        bound = limits.max_text_chars * 6 + limits.max_pages * 512 + 16384
        if failure is None and process.returncode == 0 and response.is_file():
            if response.stat().st_size <= bound:
                try:
                    return json.loads(response.read_text())
                except (ValueError, UnicodeError):
                    pass
        return {"status": "failed", "error_code": failure or "pdf_parse_failed"}


def prepare_attachment(request, data, *, expected_page_count=None, limits=None):
    """Parse before INSERT; all PDF failure outcomes are ordinary persistable data."""
    value = AttachmentInput.model_validate(request)
    if expected_page_count is not None and (
        type(expected_page_count) is not int or not 1 <= expected_page_count <= 2000
    ):
        raise ValueError("attachment_expected_page_count_invalid")
    limits = ParserLimits.model_validate(limits or {})
    if (value.acquisition_status == "downloaded") != (data is not None):
        raise ValueError("attachment_acquisition_bytes_mismatch")
    if data is not None and (not isinstance(data, bytes) or not 0 < len(data) <= 10_000_000):
        raise ValueError("attachment_blob_size_mismatch")
    if data is not None and value.declared_sha256 not in {None, sha(data)}:
        raise ValueError("attachment_declared_hash_mismatch")
    metadata = value.model_dump()
    if data is None:
        return PreparedAttachment(PreparedAttachmentInput.model_validate(metadata), None)
    if value.content_type != "application/pdf":
        normalized = None
        if value.content_type in {"text/plain", "text/html"}:
            try:
                normalized = canonical_text(data, value.content_type, value.encoding)
                if not normalized:
                    raise ValueError("empty")
            except (ValueError, UnicodeError):
                metadata.update(parse_status="failed", error_code="attachment_parse_failed")
            else:
                metadata.update(parse_status="parsed", error_code=None)
        else:
            metadata.update(parse_status="unsupported", error_code=None)
        return PreparedAttachment(
            PreparedAttachmentInput.model_validate(metadata), data, normalized
        )
    if len(data) > limits.max_bytes:
        result = {"status": "failed", "error_code": "pdf_byte_limit"}
    else:
        with _slot() as available:
            result = (
                _run_pdf(data, limits, expected_page_count)
                if available
                else {
                    "status": "failed",
                    "error_code": "pdf_parser_busy",
                }
            )
    normalized = result.get("text", "")
    receipt = PDFParseReceipt.model_validate(
        {
            "options_sha256": sha(canonical(OPTIONS)),
            "raw_sha256": sha(data),
            "text_sha256": sha(normalized),
            "page_map_sha256": sha(canonical(result.get("pages", []))),
            "page_count": result.get("page_count"),
            "expected_page_count": expected_page_count,
            "pages": result.get("pages", []),
            "complete": result["status"] == "parsed",
            "status": result["status"],
            "error_code": result["error_code"],
            "limits": limits,
            "memory_enforcement": result.get(
                "memory_enforcement",
                "rss_watchdog" if sys.platform == "darwin" else "rlimit_as_and_rss",
            ),
        }
    )
    verify_receipt(receipt, data, normalized)
    metadata.update(
        parse_status=receipt.status,
        error_code=receipt.error_code,
        diagnostic_origin="parser"
        if receipt.status in {"encrypted", "scan_unrecognized"}
        else None,
    )
    return PreparedAttachment(
        PreparedAttachmentInput.model_validate(metadata), data, normalized, receipt
    )


def probe_address_space_limit():
    result = subprocess.run(
        [sys.executable, "-I", str(WORKER), "--probe-address-space"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=5,
        check=False,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"},
    )
    value = result.stdout.decode().strip()
    return value if value in {"enforced", "unavailable", "not_enforced"} else "unavailable"
