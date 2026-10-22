"""Generated PDFs exercise the real local parser; these are engineering fixtures."""

import json
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import timedelta
from io import BytesIO

import pytest
from bidops.notice_data import AttachmentInput
from bidops.notice_data import attachment_parser as parser
from bidops.notice_data.attachment_parser import (
    ParserLimits,
    prepare_attachment,
    probe_address_space_limit,
    verify_receipt,
)
from bidops.notice_data.schemas import PDFParseReceipt
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
from sqlalchemy.exc import IntegrityError
from test_notice_versions import AT, persist
from test_notice_versions import repository as _repository_fixture

repository = _repository_fixture


def pdf(*pages, encrypted=False, cjk=False, broken_page=None):
    """Create actual PDF bytes, including a real ToUnicode CMap for Chinese."""
    writer = PdfWriter()
    for number, value in enumerate(pages):
        page = writer.add_blank_page(612, 792)
        if value is None:
            continue
        if cjk:
            mapping = DecodedStreamObject()
            mapping.set_data(
                b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
                b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                b"/CMapName /Test def /CMapType 2 def\n"
                b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
                b"1 beginbfrange <0000> <FFFF> <0000> endbfrange\n"
                b"endcmap CMapName currentdict /CMap defineresource pop end end"
            )
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type0"),
                    NameObject("/BaseFont"): NameObject("/HeiseiMin-W3"),
                    NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
                    NameObject("/ToUnicode"): writer._add_object(mapping),
                }
            )
            encoded = b"<" + value.encode("utf-16-be").hex().encode() + b">"
        else:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            encoded = b"(" + value.encode("ascii") + b")"
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 700 Td " + encoded + b" Tj ET")
        if number == broken_page:
            # Bad Flate content creates an actual page extraction failure.
            stream[NameObject("/Filter")] = NameObject("/NotARealDecodeFilter")
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("not-supplied", algorithm="RC4-128")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def request(parent="parent", **changes):
    return AttachmentInput.model_validate(
        {
            "parent_version_id": getattr(parent, "version_id", parent),
            "attachment_key": "requirements",
            "filename": "synthetic.pdf",
            "observed_at": AT,
            "content_type": "application/pdf",
            "acquisition_status": "downloaded",
            "parse_status": "pending",
            **changes,
        }
    )


def test_real_two_page_pdf_has_fixed_fulltext_and_server_crosswalk(repository, monkeypatch):
    parent = persist(repository)
    prepared = prepare_attachment(
        request(parent), pdf("SYNTHETIC first", "SYNTHETIC second"), expected_page_count=2
    )
    assert prepared.input.parse_status == "parsed"
    assert prepared.normalized_text == "SYNTHETIC first\nSYNTHETIC second"
    assert [(p.start, p.end) for p in prepared.receipt.pages] == [(0, 15), (16, 32)]
    record = repository.record_attachment(prepared)
    assert record == repository.get_attachment(record.record_id)
    assert repository.get_attachment_receipt(record.record_id) == prepared.receipt.model_dump(
        mode="json"
    )
    assert repository.missing_attachments(parent.version_id) == []
    evidence = repository.make_evidence(record.record_id, 10, 26, object_kind="attachment")
    assert evidence.page is None and evidence.locator == "normalized_text_v2"
    location = repository.evidence_location(evidence)
    assert location["location"] == {
        "kind": "pdf_pages",
        "pages": [1, 2],
        "parser_receipt_sha256": record.parser_receipt_sha256,
    }
    monkeypatch.setattr(
        parser, "_run_pdf", lambda *_: pytest.fail("old references must not reparse")
    )
    assert repository.resolve_evidence(evidence) == evidence
    assert repository.record_attachment(prepared) == record


def test_real_chinese_pdf_extraction(repository):
    parent = persist(repository)
    prepared = prepare_attachment(request(parent), pdf("合成资质要求", "第二包条件", cjk=True))
    assert prepared.input.parse_status == "parsed"
    assert prepared.normalized_text == "合成资质要求\n第二包条件"
    record = repository.record_attachment(prepared)
    evidence = repository.make_evidence(record.record_id, 7, 12, object_kind="attachment")
    assert evidence.quote == "第二包条件"
    assert repository.evidence_location(evidence)["location"]["pages"] == [2]


@pytest.mark.parametrize(
    "data,kwargs,status,code",
    [
        (pdf("synthetic", encrypted=True), {}, "encrypted", "pdf_encrypted"),
        (pdf(None), {}, "scan_unrecognized", "pdf_text_unrecognized"),
        (pdf("text", None), {}, "scan_unrecognized", "pdf_text_unrecognized"),
        (pdf("first", "second", broken_page=1), {}, "failed", "pdf_partial_pages"),
        (b"%PDF-1.7\nprivate broken contents", {}, "failed", "pdf_parse_failed"),
        (pdf("text")[:-50], {}, "failed", "pdf_parse_failed"),
        (pdf("text"), {"expected_page_count": 2}, "failed", "pdf_expected_page_count_mismatch"),
        (pdf("one", "two"), {"limits": {"max_pages": 1}}, "failed", "pdf_page_limit"),
        (pdf("0123456789"), {"limits": {"max_page_chars": 5}}, "failed", "pdf_partial_pages"),
        (pdf("first", "second"), {"limits": {"max_text_chars": 5}}, "failed", "pdf_text_limit"),
        (pdf("text"), {"limits": {"max_bytes": 5}}, "failed", "pdf_byte_limit"),
        (pdf(), {}, "failed", "pdf_no_pages"),
    ],
)
def test_failures_are_explicit_persistable_and_never_evidence(
    repository, data, kwargs, status, code
):
    parent = persist(repository, source_complete=True)
    prepared = prepare_attachment(request(parent), data, **kwargs)
    assert prepared.input.parse_status == status
    assert prepared.input.error_code == code
    assert not prepared.receipt.complete
    record = repository.record_attachment(prepared)
    assert repository.get_attachment(record.record_id) == record
    assert repository.get_attachment_receipt(record.record_id)["error_code"] == code
    assert repository.missing_attachments(parent.version_id) == [record]
    assert repository.project_status(parent.project_id, AT).status == "unknown"
    with pytest.raises(ValueError, match="^attachment_text_unavailable$"):
        repository.make_evidence(record.record_id, 0, 1, object_kind="attachment")


@pytest.mark.parametrize("pages", [(None, "text"), ("one", None, "two"), ("one", None)])
def test_empty_pages_preserve_correct_global_offsets(pages):
    prepared = prepare_attachment(request(), pdf(*pages))
    verify_receipt(prepared.receipt, prepared.data, prepared.normalized_text)
    assert len(prepared.receipt.pages) == len(pages)
    assert prepared.input.parse_status == "scan_unrecognized"


@pytest.mark.parametrize("change", ["text", "raw", "page", "page_hash", "raw_hash", "options"])
def test_prepared_receipt_rejects_tampering(repository, change):
    parent = persist(repository)
    prepared = prepare_attachment(request(parent), pdf("synthetic"))
    receipt = prepared.receipt.model_dump()
    if change == "text":
        prepared = replace(prepared, normalized_text="forged")
    elif change == "raw":
        prepared = replace(prepared, data=b"forged")
    elif change == "page":
        receipt["pages"][0]["start"] += 1
        receipt["page_map_sha256"] = parser.sha(parser.canonical(receipt["pages"]))
    elif change == "page_hash":
        receipt["pages"][0]["text_sha256"] = "0" * 64
        receipt["page_map_sha256"] = parser.sha(parser.canonical(receipt["pages"]))
    else:
        receipt["raw_sha256" if change == "raw_hash" else "options_sha256"] = "0" * 64
    prepared = replace(prepared, receipt=receipt)
    with pytest.raises(ValueError, match="attachment_(receipt|page_map)_"):
        repository.record_attachment(prepared)
    assert repository.db.rows("SELECT * FROM nd_attachments") == []


@pytest.mark.parametrize("damage", ["text", "receipt", "raw", "missing_receipt"])
def test_persistent_tamper_blocks_evidence_and_source_complete(repository, damage):
    parent = persist(repository, source_complete=True)
    record = repository.record_attachment(prepare_attachment(request(parent), pdf("synthetic")))
    evidence = repository.make_evidence(record.record_id, 0, 5, object_kind="attachment")
    if damage == "raw":
        (repository.attachment_raw / record.raw_sha256).write_bytes(b"forged")
    elif damage == "text":
        repository.db.execute("DROP TRIGGER nd_attachments_no_update")
        repository.db.execute("UPDATE nd_attachments SET normalized_text='forged'")
    else:
        repository.db.execute("DROP TRIGGER nd_attachment_receipts_no_update")
        repository.db.execute("DROP TRIGGER nd_attachment_receipts_no_delete")
        repository.db.execute(
            "DELETE FROM nd_attachment_receipts"
            if damage == "missing_receipt"
            else "UPDATE nd_attachment_receipts SET receipt_json='{}'"
        )
    assert repository.get_attachment(record.record_id).integrity_status == "corrupt"
    assert repository.project_status(parent.project_id, AT).status == "unknown"
    with pytest.raises(ValueError, match="(receipt_integrity|blob_hash)_mismatch"):
        repository.resolve_evidence(evidence)


def test_receipt_immutable_and_atomic_with_caller_transaction(repository):
    parent = persist(repository)
    prepared = prepare_attachment(request(parent), pdf("synthetic"))
    with pytest.raises(RuntimeError, match="rollback"):
        with repository.db.engine.begin() as conn:
            repository.record_attachment(prepared, conn=conn)
            raise RuntimeError("rollback")
    assert repository.db.rows("SELECT * FROM nd_attachments") == []
    assert repository.db.rows("SELECT * FROM nd_attachment_receipts") == []
    repository.record_attachment(prepared)
    for sql in (
        "DELETE FROM nd_attachment_receipts",
        "UPDATE nd_attachment_receipts SET receipt_json='{}'",
    ):
        with pytest.raises(IntegrityError, match="nd_immutable_attachment_receipt"):
            repository.db.execute(sql)
    repository.db.migrate()  # The migration runner skips already applied files.


def test_reparse_new_receipt_is_new_record_and_old_evidence_survives(repository):
    parent = persist(repository)
    raw = pdf("synthetic")
    first = repository.record_attachment(prepare_attachment(request(parent), raw))
    old = repository.make_evidence(first.record_id, 0, 4, object_kind="attachment")
    second = repository.record_attachment(
        prepare_attachment(
            request(parent, observed_at=AT + timedelta(seconds=1)),
            raw,
            limits=ParserLimits(max_pages=5),
        )
    )
    assert first.attachment_id == second.attachment_id
    assert first.record_id != second.record_id
    assert first.parser_receipt_sha256 != second.parser_receipt_sha256
    assert repository.resolve_evidence(old) == old
    assert len(repository.list_attachments(parent.version_id)) == 2


@pytest.mark.parametrize(
    "kind,raw", [("text/plain", b"  original text  "), ("text/html", b"<p>original text</p>")]
)
def test_legacy_v1_id_and_input_dump_remain_identical(repository, kind, raw):
    parent = persist(repository)
    original = request(parent, content_type=kind, parse_status="parsed")
    metadata = original.model_dump(mode="json")
    metadata["observed_at"] = AT.isoformat()
    assert "parser_receipt_sha256" not in metadata
    record = repository.record_attachment(original, raw)
    expected = parser.sha(
        parser.canonical(
            [
                "attachment_record_v1",
                record.attachment_id,
                metadata,
                parser.sha(raw),
                record.text_sha256,
            ]
        )
    )
    assert record.record_id == expected
    assert "parser_receipt_sha256" not in json.loads(
        repository.db.rows("SELECT metadata_json FROM nd_attachments")[0]["metadata_json"]
    )
    prepared = prepare_attachment(original, raw)
    assert repository.record_attachment(prepared) == record
    evidence = repository.make_evidence(record.record_id, 0, 8, object_kind="attachment")
    assert repository.evidence_location(evidence)["location"]["kind"] == "normalized_text_v2"


def test_direct_pdf_parsed_claim_without_receipt_still_rejected():
    with pytest.raises(ValidationError):
        request(parse_status="parsed")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expected_page_count": True},
        {"expected_page_count": 0},
        {"expected_page_count": 2001},
        {"limits": {"max_pages": True}},
        {"limits": {"wall_seconds": float("inf")}},
    ],
)
def test_invalid_server_limits_rejected(kwargs):
    with pytest.raises(ValueError):
        prepare_attachment(request(), pdf("synthetic"), **kwargs)


def test_thread_and_cross_process_single_parser_lock(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = parser._run_pdf

    def delayed(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(parser, "_run_pdf", delayed)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(prepare_attachment(request(), pdf("one")))
    )
    worker.start()
    assert entered.wait(3)
    busy = prepare_attachment(request(), pdf("two"))
    assert busy.input.error_code == "pdf_parser_busy"
    # A separate interpreter observes the same flock, independent of threading.Lock.
    script = (
        "from bidops.notice_data.attachment_parser import _slot\n"
        "with _slot() as available: print(available)"
    )
    separate = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, timeout=3, check=True
    )
    assert separate.stdout.strip() == b"False"
    release.set()
    worker.join(3)
    assert not worker.is_alive() and result[0].input.parse_status == "parsed"
    with parser._slot() as available:
        assert available


@pytest.mark.parametrize(
    "mode,limits,code",
    [
        ("sleep", {"wall_seconds": 0.15}, "pdf_timeout"),
        ("memory", {"memory_bytes": 32 * 1024 * 1024}, "pdf_memory_limit"),
    ],
)
def test_parent_kills_actual_spawned_worker_at_resource_limit(
    tmp_path, monkeypatch, mode, limits, code
):
    script = tmp_path / "resource_worker.py"
    script.write_text(
        "import time\n"
        + ("time.sleep(5)\n" if mode == "sleep" else "x=bytearray(96*1024*1024)\ntime.sleep(5)\n")
    )
    monkeypatch.setattr(parser, "WORKER", script)
    started = time.monotonic()
    prepared = prepare_attachment(request(), pdf("synthetic"), limits=limits)
    assert prepared.input.error_code == code
    assert time.monotonic() - started < 3
    assert prepared.receipt.complete is False


def test_unavailable_rss_monitor_is_fail_closed(monkeypatch):
    def unavailable(_pid):
        raise OSError("secret exception text")

    monkeypatch.setattr(parser, "_rss", unavailable)
    prepared = prepare_attachment(request(), pdf("synthetic"))
    assert prepared.input.error_code == "pdf_resource_monitor_unavailable"
    assert "secret" not in prepared.receipt.model_dump_json()


def test_probe_reports_actual_platform_result():
    assert probe_address_space_limit() in {"enforced", "unavailable", "not_enforced"}


def test_receipt_revalidates_mutated_model_instances():
    prepared = prepare_attachment(request(), pdf("synthetic"))
    prepared.receipt.pages.clear()
    with pytest.raises(ValueError, match="attachment_receipt_invalid"):
        verify_receipt(prepared.receipt, prepared.data, prepared.normalized_text)


def test_malformed_page_map_model_rejected():
    prepared = prepare_attachment(request(), pdf("synthetic"))
    receipt = prepared.receipt.model_dump()
    receipt["pages"][0]["page"] = 2
    with pytest.raises(ValidationError):
        PDFParseReceipt.model_validate(receipt)


def test_actual_image_only_scan_is_unrecognized_without_ocr():
    writer = PdfWriter()
    page = writer.add_blank_page(100, 100)
    image = DecodedStreamObject()
    image.set_data(b"\x00\x00\x00")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(image)})}
    )
    content = DecodedStreamObject()
    content.set_data(b"q 100 0 0 100 0 0 cm /Im0 Do Q")
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = BytesIO()
    writer.write(buffer)
    prepared = prepare_attachment(request(), buffer.getvalue())
    assert prepared.input.parse_status == "scan_unrecognized"
    assert prepared.input.error_code == "pdf_text_unrecognized"
    assert prepared.normalized_text == ""
    assert prepared.receipt.pages[0].status == "unrecognized"


def test_huge_compressed_pdf_is_bounded_during_actual_pypdf_extraction():
    writer = PdfWriter()
    page = writer.add_blank_page(100, 100)
    stream = DecodedStreamObject()
    stream.set_data(b" " * (16 * 1024 * 1024))
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    buffer = BytesIO()
    writer.write(buffer)
    raw = buffer.getvalue()
    assert len(raw) < 100_000
    prepared = prepare_attachment(
        request(), raw, limits=ParserLimits(memory_bytes=64 * 1024 * 1024, wall_seconds=3)
    )
    assert prepared.input.parse_status == "failed"
    assert prepared.input.error_code == "pdf_partial_pages"
    assert prepared.receipt.pages[0].error_code == "pdf_decode_limit"
    assert prepared.receipt.complete is False


def test_spawn_does_not_inherit_server_secrets(tmp_path, monkeypatch):
    report = tmp_path / "environment.json"
    script = tmp_path / "environment_worker.py"
    script.write_text(
        "import os,json\nfrom pathlib import Path\n"
        f"Path({str(report)!r}).write_text(json.dumps('BIDOPS_PARSER_TEST_SECRET' in os.environ))\n"
    )
    monkeypatch.setenv("BIDOPS_PARSER_TEST_SECRET", "synthetic-value-not-a-real-key")
    monkeypatch.setattr(parser, "WORKER", script)
    prepared = prepare_attachment(request(), pdf("synthetic"))
    assert json.loads(report.read_text()) is False
    assert prepared.input.parse_status == "failed"
