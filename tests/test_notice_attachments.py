from datetime import timedelta

import pytest
from bidops.notice_data import AttachmentInput
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from test_notice_versions import AT, persist
from test_notice_versions import repository as _repository_fixture

repository = _repository_fixture


def attachment(parent, **changes):
    return AttachmentInput.model_validate(
        {
            "parent_version_id": parent.version_id,
            "attachment_key": "requirements",
            "filename": "采购文件.txt",
            "observed_at": AT,
            "content_type": "text/plain",
            "acquisition_status": "downloaded",
            "parse_status": "parsed",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "content_type,data",
    [("text/plain", "有效资质要求".encode()), ("text/html", "<p>有效资质要求</p>".encode())],
)
def test_supported_attachment_quotes_are_verified(repository, content_type, data):
    parent = persist(repository)
    record = repository.record_attachment(attachment(parent, content_type=content_type), data)
    assert record.text_sha256 is not None
    assert record.normalizer_version == "normalized_text_v2"
    evidence = repository.make_evidence(record.record_id, 0, 4, object_kind="attachment")
    assert evidence.quote == "有效资质"
    assert repository.resolve_evidence(evidence) == evidence
    assert repository.missing_attachments(parent.version_id) == []
    assert record.raw_sha256 in repository.blob_hashes()
    assert (repository.attachment_raw / record.raw_sha256).read_bytes() == data
    assert "normalized_text" not in record.model_dump()


@pytest.mark.parametrize(
    "changes,data",
    [
        ({"acquisition_status": "not_downloaded", "parse_status": "pending"}, None),
        (
            {
                "acquisition_status": "failed",
                "parse_status": "pending",
                "error_code": "access_denied",
            },
            None,
        ),
        ({"parse_status": "failed", "error_code": "encoding_failed"}, b"not parsed"),
        (
            {
                "content_type": "application/pdf",
                "parse_status": "encrypted",
                "diagnostic_origin": "imported_metadata",
            },
            b"%PDF-imported",
        ),
        (
            {
                "content_type": "application/pdf",
                "parse_status": "scan_unrecognized",
                "diagnostic_origin": "imported_metadata",
            },
            b"%PDF-imported",
        ),
        ({"content_type": "application/pdf", "parse_status": "unsupported"}, b"%PDF-imported"),
    ],
)
def test_incomplete_attachment_state_matrix_is_explicit(repository, changes, data):
    parent = persist(repository)
    record = repository.record_attachment(attachment(parent, **changes), data)
    assert repository.get_attachment(record.record_id) == record
    assert repository.missing_attachments(parent.version_id) == [record]
    assert record.text_sha256 is None
    with pytest.raises(ValueError, match="attachment_text_unavailable"):
        repository.make_evidence(record.record_id, 0, 2, object_kind="attachment")


@pytest.mark.parametrize(
    "changes",
    [
        {"content_type": "application/pdf"},
        {"parse_status": "encrypted"},
        {"parse_status": "scan_unrecognized"},
        {"parse_status": "failed"},
        {"acquisition_status": "failed"},
        {"acquisition_status": "not_downloaded"},
        {"parse_status": "unsupported"},
    ],
)
def test_invalid_state_combinations_rejected(repository, changes):
    parent = persist(repository)
    with pytest.raises(ValidationError):
        attachment(parent, **changes)


def test_duplicate_and_retry_preserve_history_with_as_of_missing_list(repository):
    parent = persist(repository)
    missing = repository.record_attachment(
        attachment(parent, acquisition_status="not_downloaded", parse_status="pending")
    )
    later = AT + timedelta(hours=1)
    done = repository.record_attachment(attachment(parent, observed_at=later), b"requirements")
    assert done == repository.record_attachment(
        attachment(parent, observed_at=later), b"requirements"
    )
    assert done.attachment_id == missing.attachment_id
    assert len(repository.list_attachments(parent.version_id)) == 2
    assert repository.missing_attachments(parent.version_id, AT) == [missing]
    assert repository.missing_attachments(parent.version_id, later) == []
    assert repository.get_attachment(missing.record_id).parse_status == "pending"


@pytest.mark.parametrize("supplied", [None, b""])
def test_downloaded_requires_nonempty_actual_bytes(repository, supplied):
    parent = persist(repository)
    with pytest.raises(ValueError, match="attachment_.*mismatch"):
        repository.record_attachment(attachment(parent), supplied)


def test_not_downloaded_cannot_have_hidden_bytes(repository):
    parent = persist(repository)
    value = attachment(parent, acquisition_status="not_downloaded", parse_status="pending")
    with pytest.raises(ValueError, match="attachment_acquisition_bytes_mismatch"):
        repository.record_attachment(value, b"hidden")


def test_declared_hash_and_actual_parser_failure_rejected_without_body_leak(repository):
    parent = persist(repository)
    with pytest.raises(ValueError, match="^attachment_declared_hash_mismatch$"):
        repository.record_attachment(
            attachment(parent, declared_sha256="0" * 64), b"private content"
        )
    with pytest.raises(ValueError, match="^attachment_parse_failed$"):
        repository.record_attachment(attachment(parent), b"\xffprivate content")


def test_filename_cannot_escape_content_addressed_directory(repository, tmp_path):
    parent = persist(repository)
    record = repository.record_attachment(
        attachment(parent, filename="../../escape.txt"), b"content"
    )
    assert (repository.attachment_raw / record.raw_sha256).is_file()
    assert not (tmp_path / "escape.txt").exists()


def test_same_time_conflict_is_not_arbitrarily_resolved_by_hash(repository):
    parent = persist(repository)
    repository.record_attachment(attachment(parent), b"one")
    with pytest.raises(ValueError, match="attachment_observation_conflict"):
        repository.record_attachment(attachment(parent), b"two")


def test_attachment_tamper_and_append_only_guards(repository):
    parent = persist(repository)
    record = repository.record_attachment(attachment(parent), b"original")
    evidence = repository.make_evidence(record.record_id, 0, 4, object_kind="attachment")
    (repository.attachment_raw / record.raw_sha256).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="notice_blob_hash_mismatch"):
        repository.resolve_evidence(evidence)
    with pytest.raises(IntegrityError, match="nd_immutable_attachment"):
        repository.db.execute("UPDATE nd_attachments SET parse_status='parsed'")
    with pytest.raises(IntegrityError, match="nd_immutable_attachment"):
        repository.db.execute("DELETE FROM nd_attachments")


def test_incomplete_attachment_blocks_complete_source_status(repository):
    parent = persist(repository, source_complete=True)
    repository.record_attachment(
        attachment(parent, acquisition_status="not_downloaded", parse_status="pending")
    )
    result = repository.project_status(parent.project_id, AT)
    assert result.status == "unknown"
    assert result.reason == "attachments_incomplete"
    assert len(result.missing_attachment_ids) == 1


def test_missing_list_is_visible_even_when_other_status_reason_wins(repository):
    parent = persist(repository)
    missing = repository.record_attachment(
        attachment(parent, acquisition_status="not_downloaded", parse_status="pending")
    )
    result = repository.project_status(parent.project_id, AT)
    assert result.reason == "source_incomplete"
    assert result.missing_attachment_ids == [missing.record_id]


@pytest.mark.parametrize("damage,expected", [("delete", "unavailable"), ("tamper", "corrupt")])
def test_parsed_history_does_not_hide_current_missing_or_corrupt_blob(repository, damage, expected):
    parent = persist(repository, source_complete=True)
    record = repository.record_attachment(attachment(parent), b"required tender terms")
    path = repository.attachment_raw / record.raw_sha256
    if damage == "delete":
        path.unlink()
    else:
        path.write_bytes(b"forged tender content")
    current = repository.get_attachment(record.record_id)
    assert current.parse_status == "parsed"  # Historical acquisition is immutable.
    assert current.integrity_status == expected
    assert current.integrity_error is not None
    assert repository.missing_attachments(parent.version_id) == [current]
    result = repository.project_status(parent.project_id, AT)
    assert result.status == "unknown"
    assert result.reason == "attachments_incomplete"
    assert result.missing_attachment_ids == [record.record_id]
    saved = repository.db.rows("SELECT metadata_json FROM nd_attachments")[0]["metadata_json"]
    assert "integrity_status" not in saved


def test_parsed_history_does_not_hide_corrupt_normalized_text(repository):
    parent = persist(repository, source_complete=True)
    record = repository.record_attachment(attachment(parent), b"required tender terms")
    repository.db.execute("DROP TRIGGER nd_attachments_no_update")
    repository.db.execute("UPDATE nd_attachments SET normalized_text='forged normalized text'")
    assert repository.get_attachment(record.record_id).integrity_status == "corrupt"
    assert repository.project_status(parent.project_id, AT).status == "unknown"
