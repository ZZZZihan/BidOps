import pytest
from test_notice_versions import BODY, persist
from test_notice_versions import repository as _repository_fixture

repository = _repository_fixture


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("text/plain", BODY),
        ("text/html", "<div><p>" + BODY.replace("\n", "</p><p>") + "</p></div>"),
    ],
)
def test_notice_quote_reproduces_both_formats(repository, content_type, body):
    value = persist(repository, body, content_type=content_type)
    evidence = repository.make_evidence(value.version_id, 0, 8)
    assert evidence.quote == "项目编号：P-0"
    assert evidence.version == value.version_ref
    assert repository.resolve_evidence(evidence) == evidence


@pytest.mark.parametrize("start,end", [(-1, 3), (2, 2), (3, 2), (0, 9999), (True, 4), (0, 1.5)])
def test_out_of_bounds_and_noninteger_rejected(repository, start, end):
    value = persist(repository)
    with pytest.raises(ValueError, match="notice_evidence_out_of_bounds"):
        repository.make_evidence(value.version_id, start, end)


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote", "伪造正文123"),
        ("start", 1),
        ("text_sha256", "0" * 64),
        ("evidence_id", "invented"),
        ("locator", "page_text_v1"),
    ],
)
def test_mutated_evidence_never_passes(repository, field, value):
    version = persist(repository)
    evidence = repository.make_evidence(version.version_id, 0, 8).model_dump()
    evidence[field] = value
    with pytest.raises(ValueError, match="notice_evidence"):
        repository.resolve_evidence(evidence)


def test_version_reference_tamper_rejected(repository):
    version = persist(repository)
    evidence = repository.make_evidence(version.version_id, 0, 8).model_dump()
    evidence["version"]["object_id"] = "another_notice"
    with pytest.raises(ValueError, match="notice_evidence_integrity_mismatch"):
        repository.resolve_evidence(evidence)


def test_raw_tamper_and_symlink_rejected(repository, tmp_path):
    value = persist(repository)
    reference = repository.make_evidence(value.version_id, 0, 8)
    path = repository.raw / value.raw_sha256
    original = path.read_bytes()
    path.write_bytes(b"x" * len(original))
    with pytest.raises(ValueError, match="notice_blob_hash_mismatch"):
        repository.resolve_evidence(reference)
    outside = tmp_path / "outside"
    outside.write_bytes(original)
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="notice_blob_symlink"):
        repository.resolve_evidence(reference)


def test_old_reference_remains_valid_after_reparse_and_source_revision(repository):
    before = persist(repository)
    reference = repository.make_evidence(before.version_id, 0, 8)
    persist(repository, BODY + "\n新内容")
    snapshot, notice, normalized = persist(repository, capture=False)
    notice.parser_version = "future-parser"
    repository.capture(snapshot, notice, normalized)
    assert repository.resolve_evidence(reference) == reference


def test_db_text_tamper_rejected_even_with_original_raw(repository):
    value = persist(repository)
    repository.db.execute("DROP TRIGGER nd_versions_no_update")
    repository.db.execute("UPDATE nd_versions SET normalized_text='tampered'")
    with pytest.raises(ValueError, match="notice_version_integrity_mismatch"):
        repository.make_evidence(value.version_id, 0, 3)
