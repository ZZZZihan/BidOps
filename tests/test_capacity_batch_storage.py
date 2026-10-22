"""Real SQLite large batches and index upgrades; synthetic, no model/source calls."""

import copy
import hashlib
from datetime import timedelta

import pytest
from bidops.company.authorization import CompanyError
from bidops.db import Database
from bidops.enterprise.backup import backup, restore
from bidops.qualification.candidates import CandidateFilters, build_batch, page_batch
from bidops.qualification.config import ScreeningSettings
from bidops.qualification.repository import QualificationRepository
from bidops.qualification.service import ScreeningService
from bidops.qualification.sources import canonical_json, digest
from bidops.storage import Store
from sqlalchemy.exc import OperationalError
from test_company_access import ALICE
from test_notice_versions import persist
from test_qualification_candidates import body
from test_qualification_candidates import candidates as candidates
from test_qualification_candidates import domain as domain

MIGRATION = "012_package_versions_index.sql"
INDEX = "nd_package_versions_version_package"


def bind(result, manifest):
    result["manifest_sha256"] = digest(
        {
            "private": manifest.closure_sha256,
            **{key: value for key, value in result.items() if key != "manifest_sha256"},
        }
    )
    return result


@pytest.fixture
def batch(candidates):
    adapter, bundle = candidates
    persist(adapter.notices, body())
    result = build_batch(adapter, bundle, CandidateFilters())
    return QualificationRepository(adapter.company.db, adapter), bundle.manifest, result


def body_size(manifest, result, provenance=None):
    return len(
        canonical_json(
            {
                "manifest": manifest.model_dump(mode="json"),
                "result": result,
                "provenance": provenance or {},
                "raw_model_outputs": [],
            }
        ).encode()
    )


def sized_result(manifest, result, size):
    result = copy.deepcopy(result)
    old_title = result["items"][0]["title"]
    result["items"][0]["title"] = "X"
    result["items"][0]["title"] = "X" * (size - body_size(manifest, result) + 1)
    bind(result, manifest)
    assert body_size(manifest, result) == size
    assert old_title
    return result


def test_complete_exact_fifty_mb_envelope_persists_and_one_byte_more_is_rejected(batch):
    repo, manifest, result = batch
    # The byte count includes the manifest, provenance and raw-output envelope.
    maximum = sized_result(manifest, result, 50_000_000)
    saved = repo.save_candidate_batch(ALICE, manifest, maximum)
    assert saved["result"]["manifest_sha256"] == maximum["manifest_sha256"]
    stored = repo.db.rows(
        "SELECT r.body_sha256,b.body_json FROM qualification_results r "
        "JOIN qualification_result_bodies b ON r.result_id=b.result_id"
    )[0]
    assert len(stored["body_json"].encode()) == 50_000_000
    assert hashlib.sha256(stored["body_json"].encode()).hexdigest() == stored["body_sha256"]
    del saved, stored, maximum
    oversized = sized_result(manifest, result, 50_000_001)
    with pytest.raises(CompanyError, match="screening_result_too_large") as error:
        repo.save_candidate_batch(ALICE, manifest, oversized)
    assert error.value.status_code == 413
    assert repo.db.rows("SELECT COUNT(*) AS n FROM qualification_results")[0]["n"] == 1


@pytest.mark.parametrize("channel", ["ordinary", "raw", "kind_only"])
def test_ordinary_results_raw_and_kind_only_candidate_keep_ten_mb_limit(batch, channel):
    repo, manifest, _ = batch
    result, raw = {"kind": "checklist"}, ()
    if channel == "raw":
        raw = ["X" * 10_000_000]
    else:
        result["body"] = "X" * 10_000_000
        if channel == "kind_only":
            result["kind"] = "candidate_batch"
    with pytest.raises(CompanyError, match="screening_result_too_large") as error:
        repo.save(ALICE, manifest, result, raw_model_outputs=raw)
    assert error.value.status_code == 413
    assert not repo.db.rows("SELECT * FROM qualification_results")


def test_legacy_small_candidate_save_stays_compatible_but_not_in_special_channel(batch):
    repo, manifest, _ = batch
    minimal = {"kind": "candidate_batch", "fixture": "legacy small internal caller"}
    assert repo.save(ALICE, manifest, minimal)["result"] == minimal
    with pytest.raises(CompanyError, match="screening_candidate_batch_invalid"):
        repo.save_candidate_batch(ALICE, manifest, minimal)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_body",
        "counts",
        "boolean_counts",
        "duplicate_unit",
        "wrong_unit",
        "unknown_revision",
        "duplicate_public",
        "missing_public",
        "wrong_profile",
        "wrong_as_of",
        "future_public",
        "qualified",
        "unknown_reason",
        "bad_money",
        "wrong_hash",
        "changed_filter",
        "stored_payload_hash",
        "stored_text_hash",
        "stored_raw_hash",
        "stored_available_at",
        "stored_project",
        "stored_package",
        "unrecorded_version",
    ],
)
def test_candidate_dedicated_channel_rejects_forged_shape_counts_and_binding(batch, mutation):
    repo, manifest, result = batch
    item = result["items"][0]
    if mutation == "extra_body":
        result["body"] = "arbitrary raw output is not a candidate field"
    elif mutation == "counts":
        result["counts"]["candidate"] += 1
    elif mutation == "boolean_counts":
        result["counts"]["total_units"] = True
    elif mutation == "duplicate_unit":
        result["items"].append(copy.deepcopy(item))
        result["counts"]["total_units"] += 1
        result["counts"][item["disposition"]] += 1
    elif mutation == "wrong_unit":
        item["unit_id"] = "0" * 64
    elif mutation == "unknown_revision":
        item["notice_revisions"][0]["version_id"] = "unknown"
    elif mutation == "duplicate_public":
        result["public_versions"].append(copy.deepcopy(result["public_versions"][0]))
    elif mutation == "missing_public":
        result["public_versions"] = []
    elif mutation == "wrong_profile":
        result["profile_revision"]["sha256"] = "0" * 64
    elif mutation == "wrong_as_of":
        result["as_of"] = (manifest.as_of + timedelta(seconds=1)).isoformat()
    elif mutation == "future_public":
        result["public_versions"][0]["available_at"] = (
            manifest.as_of + timedelta(seconds=1)
        ).isoformat()
    elif mutation == "qualified":
        result["qualification_checked"] = True
    elif mutation == "unknown_reason":
        item["reasons"] = ["arbitrary private body"]
    elif mutation == "bad_money":
        item["budget_yuan"] = "NaN"
    elif mutation == "changed_filter":
        result["filters"]["unregistered_field"] = "payload"
    elif mutation in {"stored_payload_hash", "stored_text_hash"}:
        field = "payload_sha256" if mutation == "stored_payload_hash" else "text_sha256"
        result["public_versions"][0][field] = "0" * 64
    elif mutation == "stored_raw_hash":
        result["public_versions"][0]["version"]["sha256"] = "0" * 64
        item["notice_revisions"][0]["sha256"] = "0" * 64
    elif mutation == "stored_available_at":
        result["public_versions"][0]["available_at"] = (
            manifest.as_of - timedelta(seconds=1)
        ).isoformat()
    elif mutation == "stored_project":
        item["project_id"] = "forged-project"
        item["unit_id"] = digest([item["project_id"], item["package_id"]])
    elif mutation == "stored_package":
        item["package_id"] = "forged-package"
        item["unit_id"] = digest([item["project_id"], item["package_id"]])
    elif mutation == "unrecorded_version":
        result["public_versions"][0]["version"]["version_id"] = "unrecorded"
        item["notice_revisions"][0]["version_id"] = "unrecorded"
    bind(result, manifest)  # Most forgeries have a consistent recomputed hash.
    if mutation == "wrong_hash":
        result["manifest_sha256"] = "0" * 64
    with pytest.raises(CompanyError, match="screening_candidate_batch_invalid") as error:
        repo.save_candidate_batch(ALICE, manifest, result)
    assert error.value.status_code == 422
    assert not repo.db.rows("SELECT * FROM qualification_results")


def test_dropped_package_with_recomputed_counts_and_hash_is_rejected(candidates):
    adapter, bundle = candidates
    persist(adapter.notices, body() + "\n第2包\n预算金额：20元")
    result = build_batch(adapter, bundle, {})
    assert len(result["items"]) == 2
    removed = result["items"].pop()
    result["counts"]["total_units"] -= 1
    result["counts"][removed["disposition"]] -= 1
    bind(result, bundle.manifest)
    repo = QualificationRepository(adapter.company.db, adapter)
    with pytest.raises(CompanyError, match="screening_candidate_batch_invalid"):
        repo.save_candidate_batch(ALICE, bundle.manifest, result)
    assert not repo.db.rows("SELECT * FROM qualification_results")


def test_fixed_batch_save_preserves_snapshot_when_new_notice_arrives(candidates):
    adapter, bundle = candidates
    persist(adapter.notices, body())
    result = build_batch(adapter, bundle, {})
    before = copy.deepcopy(result)
    persist(adapter.notices, body(code="AFTER-BUILD"), path="after-build")
    repo = QualificationRepository(adapter.company.db, adapter)
    saved = repo.save_candidate_batch(ALICE, bundle.manifest, result)
    assert saved["result"] == before
    assert page_batch(saved)["total"] == 1
    assert build_batch(adapter, bundle, {})["counts"]["total_units"] == 2


def test_larger_candidate_channel_cannot_store_large_provenance_or_raw_output(batch):
    repo, manifest, result = batch
    with pytest.raises(CompanyError, match="screening_result_too_large"):
        repo.save_candidate_batch(ALICE, manifest, result, provenance={"raw": "X" * 10_000_000})
    with pytest.raises(TypeError):
        repo.save_candidate_batch(ALICE, manifest, result, raw_model_outputs=["untrusted"])
    assert not repo.db.rows("SELECT * FROM qualification_results")


def test_real_over_ten_mb_service_batch_keeps_complete_fixed_pages_and_private_acl(candidates):
    adapter, bundle = candidates
    for number in range(200):
        persist(
            adapter.notices,
            body(
                code=f"STORAGE-{number:04}",
                title=f"SYNTHETIC-{number:04}-" + "X" * 6_000,
                packages=False,
            )
            + "\n"
            + "\n".join(f"第{n}包" for n in range(1, 11)),
            path=f"storage-{number:04}",
        )
    service = ScreeningService(
        adapter.company, adapter.notices, ScreeningSettings(enabled=True, _env_file=None)
    )
    first = service.candidates(ALICE, bundle.manifest.profile_revision, {}, bundle.manifest.as_of)
    assert first["total"] == 2_000 and len(first["items"]) == 20
    saved = service.repository.get(ALICE, first["batch_id"])
    assert (
        10_000_000 < body_size(bundle.manifest, saved["result"], saved["provenance"]) < 50_000_000
    )
    assert saved["provenance"]["model_calls"] == 0
    assert saved["raw_model_outputs"] == []
    all_ids = []
    for offset in range(0, 2_000, 100):
        page = page_batch(saved, offset, 100)
        assert page["total"] == 2_000
        assert page["manifest_sha256"] == first["manifest_sha256"]
        all_ids.extend(item["unit_id"] for item in page["items"])
    assert len(all_ids) == len(set(all_ids)) == 2_000
    later = service.candidate_page(ALICE, first["batch_id"], offset=1_500, limit=100)
    assert [item["unit_id"] for item in later["items"]] == all_ids[1_500:1_600]
    final = service.candidate_page(ALICE, first["batch_id"], offset=1_980, limit=100)
    assert len(final["items"]) == 20 and not final["has_more"]
    assert (
        sum(
            saved["result"]["counts"][name]
            for name in ("candidate", "excluded", "needs_verification")
        )
        == 2_000
    )
    adapter.company.db.execute(
        "UPDATE enterprise_subjects SET status='disabled' WHERE owner_id='alice'"
    )
    with pytest.raises(CompanyError):
        service.candidate_page(ALICE, first["batch_id"], 1_500, 100)


def query_plan(db):
    return " ".join(
        row["detail"]
        for row in db.rows(
            "EXPLAIN QUERY PLAN SELECT package_id,label,normalized_label,"
            "evidence_start,evidence_end "
            "FROM nd_package_versions WHERE version_id=:id ORDER BY package_id",
            {"id": "fixed"},
        )
    )


def remove_index_upgrade(db):
    db.execute(f"DROP INDEX {INDEX}")
    db.execute("DELETE FROM schema_migrations WHERE version=:v", {"v": MIGRATION})


def test_pre012_old_database_upgrades_idempotently_and_uses_version_index(batch):
    repo, _, result = batch
    before = repo.db.rows("SELECT * FROM nd_package_versions ORDER BY version_id,package_id")
    remove_index_upgrade(repo.db)
    assert INDEX not in query_plan(repo.db)
    repo.db.migrate()
    repo.db.migrate()
    assert "SEARCH" in query_plan(repo.db) and INDEX in query_plan(repo.db)
    assert "SCAN" not in query_plan(repo.db)
    assert (
        repo.db.rows("SELECT * FROM nd_package_versions ORDER BY version_id,package_id") == before
    )
    assert (
        repo.db.rows(
            "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=:v", {"v": MIGRATION}
        )[0]["n"]
        == 1
    )
    assert result["public_versions"]
    # Even an interrupted migration with the index already created is repeatable.
    repo.db.execute("DELETE FROM schema_migrations WHERE version=:v", {"v": MIGRATION})
    repo.db.migrate()
    assert INDEX in query_plan(repo.db)


@pytest.mark.parametrize("old", [False, True])
def test_backup_restore_preserves_index_or_upgrades_old_copy_and_fixed_versions(
    batch, settings, tmp_path, old
):
    repo, _, result = batch
    before = repo.db.rows("SELECT * FROM nd_package_versions ORDER BY version_id,package_id")
    if old:
        remove_index_upgrade(repo.db)
    archive, destination = tmp_path / "backup", tmp_path / "restore"
    backup(settings, archive)
    assert restore(archive, destination)["logins_revoked"]
    restored_settings = settings.model_copy(
        update={"data_dir": destination, "database_url": f"sqlite:///{destination}/bidops.sqlite3"}
    )
    restored = Store(restored_settings)
    try:
        assert INDEX in query_plan(restored.db) and "SEARCH" in query_plan(restored.db)
        assert (
            restored.db.rows("SELECT * FROM nd_package_versions ORDER BY version_id,package_id")
            == before
        )
        version = result["public_versions"][0]["version"]
        assert (
            restored.notice_data.get_version(version["version_id"]).version_ref.model_dump(
                mode="json"
            )
            == version
        )
        assert (
            restored.db.rows(
                "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=:v", {"v": MIGRATION}
            )[0]["n"]
            == 1
        )
        assert restored.db.rows("SELECT quarantined FROM company_objects")[0]["quarantined"] == 1
    finally:
        restored.db.engine.dispose()


def test_corrupted_backup_is_rejected_before_destination_creation(batch, settings, tmp_path):
    archive, destination = tmp_path / "backup", tmp_path / "restore"
    backup(settings, archive)
    path = archive / "bidops.sqlite3"
    with path.open("ab") as stream:
        stream.write(b"CORRUPT-SYNTHETIC")
    with pytest.raises(ValueError):
        restore(archive, destination)
    assert not destination.exists()


def test_damaged_pre012_schema_cannot_claim_successful_index_upgrade(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/broken.sqlite3")
    try:
        db.migrate()
        db.execute("DROP TABLE nd_package_versions")
        db.execute("DELETE FROM schema_migrations WHERE version=:v", {"v": MIGRATION})
        with pytest.raises(OperationalError, match="no such table"):
            db.migrate()
        assert not db.rows("SELECT 1 FROM schema_migrations WHERE version=:v", {"v": MIGRATION})
    finally:
        db.engine.dispose()
