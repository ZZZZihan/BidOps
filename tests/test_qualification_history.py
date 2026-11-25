"""Immutable derived history, source revocation, retention and offline restoration."""

import hashlib
import json
import time
from datetime import timedelta
from pathlib import Path

import pytest
from bidops.company import CompanyService
from bidops.company.authorization import CompanyError, ToolContext
from bidops.company.retention import prune_company_materials
from bidops.db import Database
from bidops.enterprise.backup import backup, restore
from bidops.notice_data import NoticeRepository
from bidops.qualification.repository import QualificationRepository
from bidops.qualification.sources import SourceAdapter
from sqlalchemy.exc import IntegrityError
from test_company_access import ADMIN, ALICE, BOB, payload
from test_notice_versions import BODY, persist
from test_qualification_sources import domain as _domain_fixture
from test_qualification_sources import sources as _sources_fixture

domain, sources = _domain_fixture, _sources_fixture


def save(sources, **kwargs):
    adapter, bundle, _, _, fact, _ = sources
    repo = QualificationRepository(adapter.company.db, adapter)
    record = repo.save(
        ALICE,
        bundle.manifest,
        {
            "kind": "checklist",
            "evidence": fact["evidence"],
            "status": "information_insufficient",
            "human_review_state": "unreviewed",
        },
        raw_model_outputs=[{"output": "SYNTHETIC model-shaped output; fixture only"}],
        provenance={"execution_kind": "scorer_fixture", "model_calls": 0},
        **kwargs,
    )
    return repo, record


def test_history_keeps_exact_versions_output_and_provenance_append_only(sources):
    repo, record = save(sources)
    adapter, bundle, _, _, _, _ = sources
    loaded = repo.get(ALICE, record["result_id"])
    assert loaded == record
    assert loaded["manifest"] == bundle.manifest.model_dump(mode="json")
    assert loaded["raw_model_outputs"][0]["output"].startswith("SYNTHETIC")
    assert loaded["provenance"]["model_calls"] == 0
    for table in (
        "qualification_results",
        "qualification_result_bodies",
        "qualification_result_sources",
    ):
        with pytest.raises(IntegrityError, match="immutable"):
            column = (
                "body_json"
                if table.endswith("bodies")
                else "owner_id"
                if table.endswith("results")
                else "sha256"
            )
            adapter.company.db.execute(f"UPDATE {table} SET {column}='tampered'")
    with pytest.raises(CompanyError, match="already_exists"):
        save(sources, result_id=record["result_id"])
    assert repo.list(ALICE)["total"] == 1


@pytest.mark.parametrize("actor", [BOB, ADMIN, ToolContext("anonymous")])
def test_other_owner_and_admin_have_no_result_read_or_list(sources, actor):
    repo, record = save(sources)
    adapter, _, doc, profile, _, _ = sources
    if actor != ToolContext("anonymous"):
        for oid in (doc["object_id"], profile["object_id"]):
            adapter.company.auth.grant(ALICE, oid, actor.owner_id, ["read"])
    with pytest.raises(CompanyError):
        repo.get(actor, record["result_id"])
    if actor != ToolContext("anonymous"):
        assert repo.list(actor)["total"] == 0


def test_revocation_of_any_source_hides_existing_history_and_counts(sources):
    adapter, bundle, doc, profile, _, _ = sources
    for oid in (doc["object_id"], profile["object_id"]):
        adapter.company.auth.grant(ALICE, oid, BOB.owner_id, ["read"])
    repo = QualificationRepository(adapter.company.db, adapter)
    record = repo.save(BOB, bundle.manifest, {"kind": "candidate_batch", "items": []})
    adapter.company.auth.revoke(ALICE, doc["object_id"], BOB.owner_id)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        repo.get(BOB, record["result_id"])
    assert repo.list(BOB) == {"items": [], "total": 0, "offset": 0, "limit": 20}


def test_final_release_rechecks_all_sources_atomically(sources, monkeypatch):
    adapter, bundle, doc, profile, _, _ = sources
    for oid in (doc["object_id"], profile["object_id"]):
        adapter.company.auth.grant(ALICE, oid, BOB.owner_id, ["read"])
    repo = QualificationRepository(adapter.company.db, adapter)
    record = repo.save(BOB, bundle.manifest, {"kind": "checklist", "private": "SYNTHETIC"})
    original = adapter.recheck

    def revoke_after_read(context, value, **kwargs):
        original(context, value, **kwargs)
        adapter.company.auth.revoke(ALICE, doc["object_id"], BOB.owner_id)

    monkeypatch.setattr(adapter, "recheck", revoke_after_read)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        repo.get(BOB, record["result_id"])


def test_source_expiry_immediate_deny_and_purges_derived_body_even_unlink_fails(
    sources, monkeypatch
):
    repo, record = save(sources)
    adapter, _, doc, _, _, _ = sources
    adapter.company.db.execute(
        "UPDATE company_objects SET retention_until=:now WHERE object_id=:id",
        {"id": doc["object_id"], "now": time.time() - 1},
    )
    with pytest.raises(CompanyError):
        repo.get(ALICE, record["result_id"])
    original = Path.unlink

    def fail_private(path, *args, **kwargs):
        if "private/company/blob" in str(path):
            raise OSError("synthetic unlink failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_private)
    result = prune_company_materials(adapter.company)
    assert result["status"] == "FAIL"
    assert record["result_id"] in result["purged_result_ids"]
    assert not adapter.company.db.rows("SELECT * FROM qualification_result_bodies")
    assert adapter.company.db.rows("SELECT * FROM qualification_results")
    assert repo.list(ALICE)["total"] == 0
    monkeypatch.setattr(Path, "unlink", original)
    assert prune_company_materials(adapter.company)["status"] == "PASS"
    assert repo.prune()["purged_ids"] == []


def test_invalid_evidence_and_nonfinite_json_never_persist(sources):
    adapter, bundle, _, _, fact, _ = sources
    repo = QualificationRepository(adapter.company.db, adapter)
    ref = dict(fact["evidence"][0], evidence_id="invented")
    with pytest.raises(CompanyError, match="evidence_invalid"):
        repo.save(ALICE, bundle.manifest, {"kind": "checklist", "refs": [ref]})
    with pytest.raises(CompanyError, match="not_json"):
        repo.save(ALICE, bundle.manifest, {"kind": "checklist", "score": float("nan")})
    assert not adapter.company.db.rows("SELECT * FROM qualification_results")


def test_old_history_remains_readable_after_new_profile_notice_and_observation(sources):
    repo, record = save(sources)
    adapter, bundle, _, profile, _, original = sources
    later = bundle.manifest.as_of + timedelta(seconds=1)
    persist(adapter.notices, at=later)
    persist(adapter.notices, BODY.replace("招标公告", "更正公告"), path="correction", at=later)
    adapter.company.repo.put(
        ALICE,
        payload(display_name="changed"),
        object_id=profile["object_id"],
        expected_version=profile["version_id"],
    )
    adapter.company.auth.clock = lambda: later.timestamp() + 1
    loaded = repo.get(ALICE, record["result_id"])
    assert loaded["result"] == record["result"]
    assert loaded["manifest"] == record["manifest"]
    assert loaded["raw_model_outputs"] == record["raw_model_outputs"]
    assert not loaded["source_state"]["current"]
    assert {"profile_head_changed", "notice_versions_changed", "notice_observation_updated"} <= set(
        loaded["source_state"]["stale_reasons"]
    )
    assert loaded["manifest"]["notice_revisions"] == [original.version_ref.model_dump(mode="json")]


def test_execution_rejects_backfilled_closure_but_old_history_is_still_available(sources):
    repo, record = save(sources)
    adapter, bundle, _, _, _, original = sources
    persist(
        adapter.notices,
        BODY.replace("招标公告", "更正公告"),
        path="backfilled",
        at=bundle.manifest.as_of - timedelta(seconds=1),
    )
    with pytest.raises(CompanyError, match="screening_source_closure_changed"):
        adapter.recheck(ALICE, bundle, for_execution=True)
    adapter.recheck(ALICE, bundle, for_execution=False)
    assert repo.get(ALICE, record["result_id"])["result"] == record["result"]
    assert original.version_ref.model_dump(mode="json") in record["manifest"]["notice_revisions"]


def test_real_backup_closure_restore_quarantine_and_explicit_result_reauthorization(
    sources, settings, tmp_path
):
    repo, record = save(sources)
    adapter, _, doc, profile, _, _ = sources
    destination = tmp_path / "backup"
    backup(settings, destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    assert (
        manifest["files"]["bidops.sqlite3"]
        == hashlib.sha256((destination / "bidops.sqlite3").read_bytes()).hexdigest()
    )
    restored_path = tmp_path / "restored"
    restored = restore(destination, restored_path)
    assert restored["quarantined_results"] == 1
    db = Database(f"sqlite:///{restored_path}/bidops.sqlite3")
    try:
        company = CompanyService(db, restored_path)
        restored_repo = QualificationRepository(
            db, SourceAdapter(company, NoticeRepository(db, restored_path / "raw"))
        )
        assert (
            db.rows("SELECT quarantined FROM qualification_result_lifecycle")[0]["quarantined"] == 1
        )
        assert not db.rows("SELECT * FROM company_grants")
        with pytest.raises(CompanyError, match="current_identity_required"):
            restored_repo.get(ALICE, record["result_id"])
        db.execute(
            "UPDATE enterprise_subjects SET fresh_until=:future", {"future": time.time() + 1000}
        )
        with pytest.raises(CompanyError):
            restored_repo.reauthorize(ALICE, record["result_id"])
        # Current admin independently reauthorizes every object; old result still quarantined.
        for oid in (doc["object_id"], profile["object_id"]):
            company.auth.reauthorize(ADMIN, oid, ALICE.owner_id, ["read"])
        with pytest.raises(CompanyError, match="screening_result_not_found"):
            restored_repo.get(ALICE, record["result_id"])
        restored_repo.reauthorize(ALICE, record["result_id"])
        assert restored_repo.get(ALICE, record["result_id"])["result"] == record["result"]
        assert (
            restored_repo.get(ALICE, record["result_id"])["raw_model_outputs"]
            == record["raw_model_outputs"]
        )
    finally:
        db.engine.dispose()


def test_offline_restore_prunes_already_expired_result_without_body_revival(
    sources, settings, tmp_path
):
    _, record = save(sources)
    adapter, _, doc, _, _, _ = sources
    expire = time.time() - 1
    adapter.company.db.execute(
        "UPDATE company_objects SET retention_until=:at WHERE object_id=:id",
        {"at": expire, "id": doc["object_id"]},
    )
    destination = tmp_path / "backup"
    backup(settings, destination)
    restored = tmp_path / "restored"
    restore(destination, restored)
    db = Database(f"sqlite:///{restored}/bidops.sqlite3")
    try:
        assert not db.rows(
            "SELECT * FROM qualification_result_bodies WHERE result_id=:id",
            {"id": record["result_id"]},
        )
        assert (
            db.rows("SELECT purge_state FROM qualification_result_lifecycle")[0]["purge_state"]
            == "purged"
        )
    finally:
        db.engine.dispose()


def test_missing_quarantined_body_fails_closed_with_typed_not_found(sources):
    repo, record = save(sources)
    db = sources[0].company.db
    db.execute("UPDATE qualification_result_lifecycle SET quarantined=1")
    db.execute("DELETE FROM qualification_result_bodies")
    with pytest.raises(CompanyError, match="screening_result_not_found"):
        repo.reauthorize(ALICE, record["result_id"])
