from datetime import timedelta

import pytest
from test_notice_versions import AT, BODY, persist
from test_notice_versions import repository as _repository_fixture

repository = _repository_fixture


def test_future_deadline_alone_is_unknown(repository):
    value = persist(repository)
    status = repository.project_status(value.project_id, AT)
    assert status.status == "unknown"
    assert status.reason == "source_incomplete"
    assert status.observed_at == AT
    assert status.basis_version_id is None


def test_as_of_excludes_not_observed_and_late_correction(repository):
    original = persist(repository)
    later = AT + timedelta(hours=4)
    correction = persist(
        repository, BODY.replace("招标公告", "更正公告"), path="correction", at=later
    )
    assert repository.project_versions(original.project_id, AT - timedelta(seconds=1)) == []
    assert repository.project_versions(original.project_id, later - timedelta(seconds=1)) == [
        original
    ]
    assert {v.version_id for v in repository.project_versions(original.project_id, later)} == {
        original.version_id,
        correction.version_id,
    }


def test_known_termination_wins_over_fresh_incomplete_original(repository):
    original = persist(repository)
    terminal = persist(
        repository,
        BODY.replace("招标公告", "终止公告"),
        path="termination",
        at=AT + timedelta(hours=1),
    )
    persist(repository, at=AT + timedelta(days=4))
    before = repository.project_status(original.project_id, AT)
    after = repository.project_status(original.project_id, AT + timedelta(days=20))
    assert before.status == "unknown"
    assert after.status == "terminated"
    assert after.basis_version_id == terminal.version_id
    assert not after.is_fresh


@pytest.mark.parametrize("offset,expected", [(0, "open"), (86400, "open"), (86401, "unknown")])
def test_explicit_completeness_and_freshness_boundary(repository, offset, expected):
    value = persist(repository, source_complete=True)
    result = repository.project_status(value.project_id, AT + timedelta(seconds=offset))
    assert result.status == expected
    assert result.is_fresh == (offset <= 86400)


def test_refresh_reuses_version_and_restarts_freshness(repository):
    value = persist(repository, source_complete=True)
    at = AT + timedelta(days=2)
    persist(repository, at=at, source_complete=True)
    result = repository.project_status(value.project_id, at)
    assert result.status == "open"
    assert result.observed_at == at
    assert result.age_seconds == 0
    assert result.version_ids == [value.version_id]


def test_refreshing_original_does_not_refresh_stale_correction_basis(repository):
    original_body = BODY + "\n发布时间：2026年10月20日11:00"
    correction_body = BODY.replace("招标公告", "更正公告") + "\n发布时间：2026年10月21日11:00"
    original = persist(repository, original_body, source_complete=True)
    correction = persist(repository, correction_body, path="correction", source_complete=True)
    later = AT + timedelta(days=2)
    persist(repository, original_body, at=later, source_complete=True)
    result = repository.project_status(original.project_id, later)
    assert result.status == "unknown"
    assert result.reason == "stale_observation"
    assert not result.is_fresh
    assert result.age_seconds == 172800
    assert result.source_observations[correction.version_id] == AT
    assert result.source_observations[original.version_id] == later


def test_pending_same_code_correction_blocks_claim_of_open(repository):
    value = persist(repository, source_complete=True)
    persist(repository, "项目编号：P-001\n公告类型：更正公告", path="unlinked", at=AT)
    result = repository.project_status(value.project_id, AT)
    assert result.status == "unknown"
    assert result.reason == "pending_association"


def test_unrelated_pending_notice_does_not_get_guessed_into_project(repository):
    value = persist(repository, source_complete=True)
    persist(repository, "项目编号：OTHER\n公告类型：更正公告", path="unlinked", at=AT)
    assert repository.project_status(value.project_id, AT).status == "open"


def test_correction_without_original_remains_unknown(repository):
    value = persist(repository, BODY.replace("招标公告", "更正公告"), source_complete=True)
    result = repository.project_status(value.project_id, AT)
    assert result.status == "unknown"
    assert result.reason == "missing_or_ambiguous_original"


def test_multiple_original_urls_are_ambiguous(repository):
    value = persist(repository, source_complete=True)
    persist(repository, path="different-original", source_complete=True)
    result = repository.project_status(value.project_id, AT)
    assert result.reason == "missing_or_ambiguous_original"


def test_late_old_original_cannot_override_published_newer_correction(repository):
    correction_body = BODY.replace("招标公告", "更正公告").replace("11月30", "10月23")
    correction = persist(
        repository,
        correction_body + "\n发布时间：2026年10月22日11:00",
        path="correction",
        source_complete=True,
    )
    persist(
        repository,
        BODY + "\n发布时间：2026年10月20日11:00",
        at=AT + timedelta(hours=1),
        source_complete=True,
    )
    result = repository.project_status(correction.project_id, AT + timedelta(hours=2))
    assert result.basis_version_id == correction.version_id
    assert result.status == "open"


def test_unknown_revision_order_never_chooses_latest_arrival(repository):
    original = persist(repository, source_complete=True)
    persist(
        repository,
        BODY.replace("招标公告", "更正公告"),
        path="correction",
        at=AT + timedelta(hours=1),
        source_complete=True,
    )
    result = repository.project_status(original.project_id, AT + timedelta(hours=2))
    assert result.status == "unknown"
    assert result.reason == "revision_order_unknown"


def test_complete_correction_does_not_attest_incomplete_original(repository):
    value = persist(repository)
    persist(
        repository,
        BODY.replace("招标公告", "更正公告"),
        path="correction",
        at=AT + timedelta(hours=1),
        source_complete=True,
    )
    result = repository.project_status(value.project_id, AT + timedelta(hours=2))
    assert result.reason == "source_incomplete"


def test_same_day_date_only_publication_cannot_determine_correction_order(repository):
    value = persist(repository, BODY + "\n发布时间：2026年10月22日", source_complete=True)
    persist(
        repository,
        BODY.replace("招标公告", "更正公告") + "\n发布时间：2026年10月22日11:00",
        path="correction",
        at=AT + timedelta(hours=1),
        source_complete=True,
    )
    result = repository.project_status(value.project_id, AT + timedelta(hours=2))
    assert result.reason == "revision_order_unknown"


def test_passed_deadline_is_closed_only_with_fresh_complete_basis(repository):
    value = persist(repository, BODY.replace("2026年11月30", "2026年10月21"), source_complete=True)
    result = repository.project_status(value.project_id, AT)
    assert result.status == "closed"
    assert result.reason == "known_deadline_passed"


def test_future_publication_never_implies_effective_open(repository):
    value = persist(repository, BODY + "\n发布时间：2026年10月23日11:00", source_complete=True)
    result = repository.project_status(value.project_id, AT)
    assert result.status == "unknown"
    assert result.reason == "publication_not_effective"


@pytest.mark.parametrize("damage", ["delete", "tamper"])
def test_missing_or_corrupt_notice_raw_never_allows_open(repository, damage):
    value = persist(repository, source_complete=True)
    path = repository.raw / value.raw_sha256
    if damage == "delete":
        path.unlink()
    else:
        path.write_bytes(b"damaged source")
    result = repository.project_status(value.project_id, AT)
    assert result.status == "unknown"
    assert result.reason == "notice_evidence_unavailable"
    assert value.version_id in result.integrity_issues


@pytest.mark.parametrize("max_age", [0, -1, float("nan"), float("inf"), True, "86400"])
def test_invalid_freshness_settings_rejected(repository, max_age):
    with pytest.raises(ValueError, match="notice_invalid_freshness_limit"):
        repository.project_status("unknown-project", AT, max_age)


def test_timezone_is_required(repository):
    with pytest.raises(ValueError, match="notice_timezone_required"):
        repository.project_status("project", AT.replace(tzinfo=None))
