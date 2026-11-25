import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from bidops.db import Database
from bidops.models import Snapshot
from bidops.notice_data import NoticeRepository
from bidops.parser import canonical_text, parse_notice
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

AT = datetime(2026, 10, 22, 3, tzinfo=UTC)
BODY = (
    "项目编号：P-001\n采购人名称：示例采购中心\n公告类型：招标公告\n"
    "投标截止时间：2026年11月30日10:00\n第1包\n第2包"
)


@pytest.fixture
def repository(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/versions.sqlite3")
    db.migrate()
    raw = tmp_path / "raw"
    raw.mkdir()
    repo = NoticeRepository(db, raw)
    yield repo
    db.engine.dispose()


def persist(
    repo, body=BODY, *, path="original", at=AT, content_type="text/plain", capture=True, **kwargs
):
    data = body.encode()
    sha = hashlib.sha256(data).hexdigest()
    url = "https://procurement.example/" + path
    snapshot = Snapshot(
        snapshot_id=hashlib.sha256((url + "\n" + sha).encode()).hexdigest(),
        source_url=url,
        sha256=sha,
        byte_size=len(data),
        fetched_at=at,
        content_type=content_type,
        encoding="utf-8",
        access_method="manual_import",
        manual_attestation=True,
    )
    normalized = canonical_text(data, content_type, "utf-8")
    notice = parse_notice(normalized, snapshot)
    (repo.raw / sha).write_bytes(data)
    with repo.db.engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO urls(source_url) VALUES (:url)"), {"url": url})
        conn.execute(
            text("INSERT OR IGNORE INTO snapshots VALUES (:id,:url,:sha,:meta)"),
            {
                "id": snapshot.snapshot_id,
                "url": url,
                "sha": sha,
                "meta": snapshot.model_dump_json(),
            },
        )
        conn.execute(
            text("INSERT OR IGNORE INTO notices VALUES (:id,:id,:code,:kind,:payload)"),
            {
                "id": snapshot.snapshot_id,
                "code": notice.project_code,
                "kind": notice.notice_kind,
                "payload": notice.model_dump_json(),
            },
        )
        if capture:
            return repo.capture(snapshot, notice, normalized, conn=conn, **kwargs)
    return snapshot, notice, normalized


def count(repo, table):
    return repo.db.rows(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


def test_original_two_corrections_termination_packages_and_repeat(repository):
    original = persist(repository)
    correction1 = persist(
        repository,
        BODY.replace("招标公告", "更正公告"),
        path="correction-1",
        at=AT + timedelta(hours=1),
    )
    correction2 = persist(
        repository,
        BODY.replace("招标公告", "更正公告").replace("11月30", "12月30"),
        path="correction-2",
        at=AT + timedelta(hours=2),
    )
    terminal = persist(
        repository,
        BODY.replace("招标公告", "终止公告"),
        path="termination",
        at=AT + timedelta(hours=3),
    )
    assert len({v.project_id for v in [original, correction1, correction2, terminal]}) == 1
    assert original.project_id is not None
    assert len(original.packages) == 2
    assert {p.package_id for p in original.packages} == {p.package_id for p in correction1.packages}
    assert persist(repository).version_id == original.version_id
    assert count(repository, "nd_projects") == 1
    assert count(repository, "nd_versions") == 4
    assert count(repository, "nd_logical_notices") == 4


def test_same_url_new_bytes_preserves_logical_and_legacy_history(repository):
    before = persist(repository)
    after = persist(repository, BODY + "\n补充内容", at=AT + timedelta(hours=1))
    assert before.version_id != after.version_id
    assert before.snapshot_id != after.snapshot_id
    assert before.logical_notice_id == after.logical_notice_id
    assert repository.get_version(before.version_id) == before
    assert len(repository.versions_for_notice(before.snapshot_id)) == 2


@pytest.mark.parametrize(
    "body",
    [
        "项目编号：P-001\n公告类型：更正公告",
        "采购人名称：示例采购中心\n公告类型：更正公告",
        "标题：与P-001看起来一样的项目",
    ],
)
def test_no_fuzzy_association(repository, body):
    persist(repository)
    value = persist(repository, body, path="ambiguous")
    assert value.project_id is None
    assert value.association_state == "pending_review"


def test_same_code_different_buyer_is_separate_project(repository):
    original = persist(repository)
    other = persist(repository, BODY.replace("示例采购中心", "另一采购中心"), path="other")
    assert original.project_id != other.project_id


def test_new_observation_reuses_version_and_original_snapshot_time(repository):
    before = persist(repository)
    after = persist(repository, at=AT + timedelta(hours=1))
    assert before.version_id == after.version_id
    assert after.first_observed_at == AT
    assert after.last_observed_at == AT + timedelta(hours=1)
    assert after.notice.source.fetched_at == AT
    assert count(repository, "nd_observations") == 2


def test_reparse_payload_change_with_same_parser_tag_gets_new_version_and_time(repository):
    old = persist(repository)
    snapshot, notice, normalized = persist(repository, capture=False)
    notice.parse_warnings.append("new_interpretation")
    new = repository.capture(snapshot, notice, normalized, interpreted_at=AT + timedelta(days=1))
    assert old.version_id != new.version_id
    assert new.parser_version == old.parser_version
    assert repository.project_versions(old.project_id, AT + timedelta(hours=1)) == [old]
    assert len(repository.project_versions(old.project_id, AT + timedelta(days=1))) == 2
    assert repository.get_version(old.version_id).notice.parse_warnings != new.notice.parse_warnings


def test_backfill_idempotent_and_exact_payload_bytes_retained(repository):
    snapshot, notice, _ = persist(repository, capture=False)
    pretty = notice.model_dump_json(indent=4)
    repository.db.execute(
        "UPDATE notices SET payload_json=:p WHERE notice_id=:id",
        {"p": pretty, "id": snapshot.snapshot_id},
    )
    assert repository.backfill_legacy() == {"created": 1, "existing": 0}
    assert repository.backfill_legacy() == {"created": 0, "existing": 1}
    assert (
        repository.db.rows("SELECT legacy_payload_json FROM nd_versions")[0]["legacy_payload_json"]
        == pretty
    )
    assert repository.db.rows("SELECT payload_json FROM notices")[0]["payload_json"] == pretty


def test_backfill_failure_rolls_back_all_rows(repository):
    persist(repository, capture=False)
    bad, _, _ = persist(repository, path="bad", capture=False)
    (repository.raw / bad.sha256).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="notice_blob_hash_mismatch"):
        repository.backfill_legacy()
    assert count(repository, "nd_versions") == 0
    assert count(repository, "nd_observations") == 0


@pytest.mark.parametrize(
    "table,column",
    [
        ("nd_versions", "parser_version"),
        ("nd_observations", "source_complete"),
        ("nd_package_versions", "label"),
    ],
)
def test_history_update_and_delete_rejected(repository, table, column):
    persist(repository)
    with pytest.raises(IntegrityError, match="nd_immutable"):
        repository.db.execute(f"UPDATE {table} SET {column}={column}")
    with pytest.raises(IntegrityError, match="nd_immutable"):
        repository.db.execute(f"DELETE FROM {table}")


def test_capture_rolls_back_with_callers_transaction(repository):
    snapshot, notice, normalized = persist(repository, capture=False)
    with pytest.raises(RuntimeError), repository.db.engine.begin() as conn:
        repository.capture(snapshot, notice, normalized, conn=conn)
        raise RuntimeError("abort")
    assert count(repository, "nd_versions") == 0


def test_old_locator_is_not_relabelled_as_current_normalizer(repository):
    snapshot, notice, normalized = persist(repository, capture=False)
    notice.field_evidence["project_code"][0].locator = "normalized_text_v1"
    with pytest.raises(ValueError, match="notice_unsupported_legacy_locator"):
        repository.capture(snapshot, notice, normalized)


def test_normalized_text_and_identity_must_match_raw(repository):
    snapshot, notice, normalized = persist(repository, capture=False)
    with pytest.raises(ValueError, match="notice_normalized_text_mismatch"):
        repository.capture(snapshot, notice, normalized + "tamper")
    snapshot.snapshot_id = "invalid"
    with pytest.raises(ValueError, match="notice_snapshot_mismatch"):
        repository.capture(snapshot, notice, normalized)


def test_observation_cannot_be_backdated_before_original_capture(repository):
    snapshot, notice, normalized = persist(repository, capture=False)
    with pytest.raises(ValueError, match="notice_.*before_capture"):
        repository.capture(snapshot, notice, normalized, observed_at=AT - timedelta(seconds=1))


def test_reparse_never_refreshes_source_or_overwrites_completeness(repository):
    old = persist(repository, source_complete=True)
    snapshot, notice, normalized = persist(repository, capture=False)
    notice.parser_version = "next-parser"
    new = repository.capture(
        snapshot,
        notice,
        normalized,
        interpreted_at=AT + timedelta(days=1),
        record_observation=False,
    )
    assert new.available_at == AT + timedelta(days=1)
    assert new.last_observed_at == old.last_observed_at == AT
    assert count(repository, "nd_observations") == 1
    assert (
        repository.db.rows("SELECT source_complete FROM nd_observations")[0]["source_complete"] == 1
    )
