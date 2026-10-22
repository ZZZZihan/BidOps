"""Actual SQLite/file source boundaries, with synthetic records and no model calls."""

import copy
from datetime import UTC, datetime, timedelta

import pytest
from bidops.company.authorization import CompanyError, ToolContext
from bidops.notice_data import NoticeRepository
from bidops.qualification.sources import ScreeningInputManifest, SourceAdapter, SourceBundle
from bidops.screening.schemas import VersionRef
from pydantic import ValidationError
from test_company_access import ADMIN, ALICE, BOB, material, payload, supported_profile
from test_company_access import domain as _domain_fixture
from test_notice_attachments import attachment
from test_notice_versions import AT, BODY, persist

domain = _domain_fixture


@pytest.fixture
def sources(domain, settings):
    raw = settings.data_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    notices = NoticeRepository(domain.db, raw)
    notice = persist(notices)
    doc, profile, fact = supported_profile(domain)
    adapter = SourceAdapter(domain, notices)
    bundle = adapter.resolve(ALICE, VersionRef(**profile), notice.project_id, datetime.now(UTC))
    return adapter, bundle, doc, profile, fact, notice


def test_fixed_profile_and_all_notice_attachment_versions_as_of(sources):
    adapter, before, _, profile, _, original = sources
    changed = persist(adapter.notices, BODY.replace("11月30", "12月30"), at=AT + timedelta(hours=1))
    correction = persist(
        adapter.notices,
        BODY.replace("招标公告", "更正公告"),
        path="correction",
        at=AT + timedelta(hours=2),
    )
    missing = adapter.notices.record_attachment(
        attachment(original, acquisition_status="not_downloaded", parse_status="pending")
    )
    parsed = adapter.notices.record_attachment(
        attachment(original, observed_at=AT + timedelta(minutes=30)), b"SYNTHETIC requirement"
    )
    frozen = adapter.resolve(
        ALICE, VersionRef(**profile), original.project_id, AT + timedelta(hours=1)
    )
    assert frozen.manifest.notice_revisions == [original.version_ref, changed.version_ref]
    assert correction.version_ref not in frozen.manifest.notice_revisions
    assert [r.record_id for r in frozen.manifest.attachment_records] == [
        missing.record_id,
        parsed.record_id,
    ]
    assert not frozen.manifest.missing_attachment_ids
    assert len(frozen.documents) == 3
    assert frozen.manifest.profile_revision == before.manifest.profile_revision
    new = adapter.company.repo.put(
        ALICE,
        payload(display_name="newer"),
        object_id=profile["object_id"],
        expected_version=profile["version_id"],
    )
    assert new["version_id"] != profile["version_id"]
    adapter.recheck(ALICE, frozen)
    assert frozen.profile["display_name"] == "Synthetic company"


def test_missing_attachment_is_explicit_not_false_absent_qualification(sources):
    adapter, _, _, profile, _, notice = sources
    missing = adapter.notices.record_attachment(
        attachment(notice, acquisition_status="not_downloaded", parse_status="pending")
    )
    bundle = adapter.resolve(ALICE, profile, notice.project_id, AT)
    assert bundle.manifest.missing_attachment_ids == [missing.attachment_id]
    assert bundle.manifest.attachment_records[0].version is None
    assert bundle.manifest.project_status["status"] == "unknown"
    assert len(bundle.documents) == 1


@pytest.mark.parametrize("actor", [BOB, ADMIN, ToolContext("anonymous")])
def test_every_entry_rejects_unauthorized_profile(sources, actor):
    adapter, bundle, _, _, _, _ = sources
    with pytest.raises(CompanyError):
        adapter.resolve_profile(actor, bundle.manifest.profile_revision, bundle.manifest.as_of)
    with pytest.raises(CompanyError):
        adapter.recheck(actor, bundle.manifest)


def test_old_tool_context_and_source_material_revocation_not_missing_fact(sources):
    adapter, bundle, doc, profile, _, _ = sources
    for oid in (profile["object_id"], doc["object_id"]):
        adapter.company.auth.grant(ALICE, oid, BOB.owner_id, ["read"])
    adapter.recheck(BOB, bundle)
    adapter.company.auth.revoke(ALICE, doc["object_id"], BOB.owner_id)
    with pytest.raises(CompanyError, match="company_object_not_found"):
        adapter.recheck(BOB, bundle)
    with pytest.raises(CompanyError):
        adapter.resolve_profile(BOB, bundle.manifest.profile_revision, bundle.manifest.as_of)


def test_subject_disabled_or_role_reduced_is_live(sources):
    adapter, bundle, _, _, _, _ = sources
    adapter.company.db.execute(
        "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id='alice'"
    )
    with pytest.raises(CompanyError, match="company_create_denied"):
        adapter.recheck(ALICE, bundle)
    adapter.recheck(ALICE, bundle, for_execution=False)
    adapter.company.db.execute(
        "UPDATE enterprise_subjects SET fresh_until=0 WHERE owner_id='alice'"
    )
    with pytest.raises(CompanyError, match="current_identity_required"):
        adapter.recheck(ALICE, bundle, for_execution=False)


def test_hash_version_and_runtime_bundle_tampering_is_rejected(sources):
    adapter, bundle, _, _, _, _ = sources
    forged = bundle.manifest.model_dump(mode="json")
    forged["project_id"] = "invented"
    with pytest.raises(ValidationError, match="manifest_hash_mismatch"):
        ScreeningInputManifest.model_validate(forged)
    for change in ("profile", "documents", "material_texts"):
        data = bundle.model_dump(mode="json")
        if change == "profile":
            data[change]["display_name"] = "forged"
        elif change == "documents":
            data[change][0]["text"] = "forged"
        else:
            data[change][next(iter(data[change]))] = "forged"
        with pytest.raises(ValidationError, match="screening_bundle"):
            SourceBundle.model_validate(data)
    wrong = bundle.manifest.profile_revision.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(CompanyError, match="company_input_version_mismatch"):
        adapter.resolve_profile(ALICE, wrong, bundle.manifest.as_of)
    data = bundle.model_dump(mode="json")
    data["documents"][0]["object_kind"] = "attachment"
    with pytest.raises(ValidationError, match="screening_bundle_document_mismatch"):
        SourceBundle.model_validate(data)


@pytest.mark.parametrize("change", ["quote", "evidence_id", "page", "version"])
def test_server_resolver_rejects_forged_notice_evidence(sources, change):
    adapter, bundle, _, _, _, _ = sources
    evidence = adapter.make_evidence(bundle, bundle.documents[0].document_id, 0, 4)
    assert adapter.resolve_evidence(ALICE, bundle, evidence) == evidence
    forged = evidence.model_dump(mode="json")
    if change == "quote":
        forged["quote"] = "伪造引用"
    elif change == "page":
        forged.update(locator="page_text_v1", page=999)
    elif change == "version":
        forged["version"]["version_id"] = "invented"
    else:
        forged["evidence_id"] = "invented"
    with pytest.raises((CompanyError, ValidationError)):
        adapter.resolve_evidence(ALICE, bundle, forged)


def test_material_reference_is_exact_and_cannot_escape_closure(sources):
    adapter, bundle, doc, _, fact, _ = sources
    evidence = fact["evidence"][0]
    assert adapter.resolve_evidence(ALICE, bundle, evidence).quote == "SYNTHETIC"
    forged = copy.deepcopy(evidence)
    forged["locator"], forged["page"] = "page_text_v1", 999
    with pytest.raises(CompanyError, match="screening_evidence_invalid"):
        adapter.resolve_evidence(ALICE, bundle, forged)
    forged = copy.deepcopy(evidence)
    forged["validity"] = {"kind": "perpetual", "reason": "invented by model"}
    with pytest.raises(CompanyError, match="validity_not_bound"):
        adapter.resolve_evidence(ALICE, bundle, forged)
    other = material(adapter.company, data=b"OTHER SYNTHETIC")
    outside = adapter.company.materials.citation(
        ALICE, other["object_id"], other["version_id"], 0, 5
    )
    with pytest.raises(CompanyError, match="outside_closure"):
        adapter.resolve_evidence(ALICE, bundle, outside)
    row = adapter.company.materials.revision(ALICE, doc["object_id"], doc["version_id"])
    adapter.company.materials.path(row["blob_path"]).unlink()
    with pytest.raises(CompanyError, match="material_source_unavailable"):
        adapter.recheck(ALICE, bundle)


def test_notice_blob_loss_and_attachment_change_stop_fixed_run(sources):
    adapter, bundle, _, profile, _, notice = sources
    record = adapter.notices.record_attachment(attachment(notice), b"requirements")
    updated = adapter.resolve(ALICE, profile, notice.project_id, bundle.manifest.as_of)
    (adapter.notices.attachment_raw / record.raw_sha256).unlink()
    with pytest.raises(CompanyError, match="screening_source_closure_changed"):
        adapter.recheck(ALICE, updated)
    (adapter.notices.raw / notice.raw_sha256).unlink()
    with pytest.raises(CompanyError, match="screening_source_unavailable"):
        adapter.recheck(ALICE, bundle)


def test_fixed_profile_validity_cannot_be_extended_by_model(sources):
    adapter, original, _, _, _, notice = sources
    _, profile, fact = supported_profile(
        adapter.company,
        validity={
            "kind": "interval",
            "valid_from": "2020-01-01T00:00:00Z",
            "valid_until": "2025-01-01T00:00:00Z",
        },
    )
    bundle = adapter.resolve(ALICE, profile, notice.project_id, original.manifest.as_of)
    reference = fact["evidence"][0]
    resolved = adapter.resolve_evidence(ALICE, bundle, reference)
    assert resolved.validity.valid_until.year == 2025
    for changed in (
        {"kind": "not_required"},
        {"kind": "perpetual", "reason": "model guessed"},
        {**reference["validity"], "valid_until": "2030-01-01T00:00:00Z"},
    ):
        proposed = {**reference, "validity": changed}
        with pytest.raises(CompanyError, match="validity_not_bound"):
            adapter.resolve_evidence(ALICE, bundle, proposed)


def test_extra_material_cannot_invent_validity_without_fixed_profile_assertion(sources):
    adapter, original, _, _, _, _ = sources
    extra = material(adapter.company, data=b"SYNTHETIC extra evidence")
    ref = VersionRef(**{k: extra[k] for k in ("object_id", "version_id", "sha256")})
    bundle = adapter.resolve_profile(
        ALICE, original.manifest.profile_revision, original.manifest.as_of, [ref]
    )
    citation = adapter.company.materials.citation(ALICE, ref.object_id, ref.version_id, 0, 9)
    assert adapter.resolve_evidence(ALICE, bundle, citation).quote == "SYNTHETIC"
    citation["validity"] = {"kind": "perpetual", "reason": "model guessed"}
    with pytest.raises(CompanyError, match="validity_not_bound"):
        adapter.resolve_evidence(ALICE, bundle, citation)


def test_explicit_extra_material_and_profile_only_closure(sources):
    adapter, bundle, _, _, _, _ = sources
    extra = material(adapter.company, data=b"additional SYNTHETIC")
    profile_only = adapter.resolve_profile(
        ALICE,
        bundle.manifest.profile_revision,
        bundle.manifest.as_of,
        [VersionRef(**{k: extra[k] for k in ("object_id", "version_id", "sha256")})],
    )
    assert len(profile_only.manifest.material_revisions) == 2
    assert not profile_only.documents
    assert profile_only.manifest.project_id is None
    assert not profile_only.manifest.notice_revisions
    adapter.recheck(ALICE, profile_only)
    with pytest.raises(CompanyError, match="requires_timezone"):
        adapter.resolve_profile(ALICE, bundle.manifest.profile_revision, datetime(2026, 1, 1))
