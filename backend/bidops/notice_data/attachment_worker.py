"""Private, spawned PDF parser protocol. No database, HTTP client, OCR or shell."""

import hashlib
import json
import logging
import math
import os
import re
import sys
from pathlib import Path


def normalize(value):
    # The text/plain normalized_text_v2 algorithm, without importing the server.
    value = value.lstrip("\ufeff")
    if "\ufffd" in value or "\x00" in value:
        raise ValueError("invalid text")
    return "\n".join(
        re.sub(r"[\t \u3000\xa0]+", " ", line).strip()
        for line in value.splitlines()
        if line.strip()
    )


def address_space_probe():
    """An actual bounded allocation probe, never a claim based on setrlimit alone."""
    import resource

    try:
        resource.setrlimit(resource.RLIMIT_AS, (96 * 1024 * 1024, 96 * 1024 * 1024))
        allocation = bytearray(160 * 1024 * 1024)
        allocation[0] = 1
    except MemoryError:
        return "enforced"
    except (OSError, ValueError):
        return "unavailable"
    return "not_enforced"


def parse(directory):
    import resource
    import socket

    def no_network(*_args, **_kwargs):
        raise OSError("parser_network_disabled")

    socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = no_network
    socket.create_connection = no_network
    request = json.loads((directory / "request.json").read_text())
    limits = request["limits"]
    # A hard address-space limit is useful on Linux. macOS uses the parent's
    # observed RSS watchdog: RLIMIT_AS is not represented as a reliable hard cap.
    mode = "rss_watchdog" if sys.platform == "darwin" else "rlimit_as_and_rss"
    if mode == "rlimit_as_and_rss":
        resource.setrlimit(resource.RLIMIT_AS, (limits["memory_bytes"], limits["memory_bytes"]))
    cpu = max(1, math.ceil(limits["wall_seconds"]) + 1)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    max_output = limits["max_text_chars"] * 6 + limits["max_pages"] * 512 + 16384
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_output, max_output))
    logging.disable(logging.CRITICAL)
    result = {
        "text": "",
        "pages": [],
        "page_count": None,
        "status": "failed",
        "error_code": "pdf_parse_failed",
        "memory_enforcement": mode,
    }
    try:
        import pypdf

        if pypdf.__version__ != "6.21.2":
            result["error_code"] = "pdf_dependency_version"
        else:
            decoded = limits["max_decoded_stream_bytes"]
            pypdf.overwrite_configuration(
                maximum_declared_stream_length=limits["max_bytes"],
                array_based_stream_maximum_output_length=decoded,
                jbig2_maximum_output_length=decoded,
                lzw_maximum_output_length=decoded,
                run_length_maximum_output_length=decoded,
                zlib_maximum_output_length=decoded,
                brotli_maximum_output_length=decoded,
                image_maximum_buffer_size=decoded,
                stream_decoding_work_maximum_length=decoded * 4,
                zlib_maximum_recovery_input_length=limits["max_bytes"],
                array_maximum_element_count=20_000,
                dictionary_maximum_element_count=20_000,
                page_tree_maximum_entries=limits["max_pages"] * 4 + 32,
                jbig2dec_binary=None,
            )
            reader = pypdf.PdfReader(directory / "input.pdf", strict=True)
            if reader.is_encrypted:
                result.update(status="encrypted", error_code="pdf_encrypted")
            else:
                count = len(reader.pages)
                result["page_count"] = count or None
                if not count:
                    result["error_code"] = "pdf_no_pages"
                elif count > limits["max_pages"]:
                    result["error_code"] = "pdf_page_limit"
                elif request["expected_page_count"] not in (None, count):
                    result["error_code"] = "pdf_expected_page_count_mismatch"
                else:
                    read_pages(reader, limits, result)
    except MemoryError:
        result["error_code"] = "pdf_memory_limit"
    except Exception:
        # Never return pypdf exception text, file contents, paths or passwords.
        result["error_code"] = "pdf_parse_failed"
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > max_output:
        raise ValueError("parser output limit")
    descriptor = os.open(directory / "response.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)


def read_pages(reader, limits, result):
    from pypdf.errors import LimitReachedError

    parts, spans, offset, failures = [], [], 0, False
    for number, page in enumerate(reader.pages, 1):
        error = None
        try:
            value = normalize(page.extract_text(extraction_mode="plain"))
            if len(value) > limits["max_page_chars"]:
                raise ValueError("page limit")
            addition = len(value) + (1 if value and parts else 0)
            if offset + addition > limits["max_text_chars"]:
                result.update(text="", pages=[], error_code="pdf_text_limit")
                return
        except MemoryError:
            raise
        except LimitReachedError:
            value, error, failures = "", "pdf_decode_limit", True
        except Exception:
            value, error, failures = "", "pdf_page_failed", True
        if value and parts:
            offset += 1
        start = offset
        if value:
            parts.append(value)
            offset += len(value)
        spans.append(
            {
                "page": number,
                "start": start,
                "end": offset,
                "text_sha256": hashlib.sha256(value.encode()).hexdigest(),
                "status": "failed" if error else "text" if value else "unrecognized",
                "error_code": error,
            }
        )
    result.update(text="\n".join(parts), pages=spans)
    if failures:
        result.update(status="failed", error_code="pdf_partial_pages")
    elif any(page["status"] != "text" for page in spans):
        result.update(status="scan_unrecognized", error_code="pdf_text_unrecognized")
    else:
        result.update(status="parsed", error_code=None)


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe-address-space"]:
        print(address_space_probe())
    else:
        parse(Path(sys.argv[1]))
