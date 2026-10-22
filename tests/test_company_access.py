"""Synthetic company records and signed offline OIDC; no real enterprise acceptance."""

import copy
import io
import json
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx2
import pytest
from bidops.agent_demo.runtime import DemoSettings
from bidops.api import create_app
from bidops.company import CompanyService
from bidops.company.authorization import CompanyError, ToolContext
from bidops.company.retention import prune_company_materials, quarantine_after_restore
from bidops.company.schemas import CompanyFact, ProfilePayload
from bidops.parser import canonical_text
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from test_enterprise import ORIGIN, ProtocolProvider, enterprise

ALICE, BOB, ADMIN, VIEWER = (ToolContext(name) for name in ("alice", "bob", "admin", "viewer"))


def payload(**updates):
    return {"display_name": "Synthetic company", "facts": [], **updates}


def material(service, context=ALICE, **updates):
    return service.materials.put(
        context,
        **{
            "filename": "certificate.txt",
            "content_type": "text/plain",
            "data": b"SYNTHETIC certificate alpha",
            **updates,
        },
    )


@pytest.fixture
def domain(store, settings):
    for actor, roles in (
        ("alice", ["analyst"]),
        ("bob", ["analyst"]),
        ("admin", ["admin"]),
        ("viewer", ["viewer"]),
    ):
        store.db.execute(
            "INSERT INTO enterprise_subjects "
            "(owner_id,issuer,subject,status,roles_json,roles_checked_at,fresh_until,updated_at) "
            "VALUES (:owner,'https://unit.example',:owner,'active',:roles,:now,:until,:now)",
            {
                "owner": actor,
                "roles": json.dumps(roles),
                "now": time.time(),
                "until": time.time() + 100000,
            },
        )
    return CompanyService(store.db, settings.data_dir)


def supported_profile(service, *, validity=None):
    doc = material(service)
    evidence = service.materials.citation(ALICE, doc["object_id"], doc["version_id"], 0, 9)
    if validity is not None:
        evidence["validity"] = validity
    fact = {
        "fact_id": "cert",
        "kind": "qualification",
        "label": "Synthetic cert",
        "status": "document_supported",
        "value": "recorded",
        "evidence": [evidence],
    }
    profile = service.repo.put(ALICE, payload(facts=[fact]))
    return doc, profile, fact


def test_append_only_profiles_expected_version_and_no_silent_latest(domain):
    first = domain.repo.put(ALICE, payload(display_name="first"))
    second = domain.repo.put(
        ALICE,
        payload(display_name="second"),
        object_id=first["object_id"],
        expected_version=first["version_id"],
    )
    assert first["version_id"] != second["version_id"]
    assert (
        domain.repo.get(ALICE, first["object_id"], first["version_id"])["payload"]["display_name"]
        == "first"
    )
    assert domain.repo.get(ALICE, first["object_id"])["payload"]["display_name"] == "second"
    with pytest.raises(CompanyError, match="version_conflict"):
        domain.repo.put(
            ALICE, payload(), object_id=first["object_id"], expected_version=first["version_id"]
        )
    with pytest.raises(CompanyError, match="version_conflict"):
        domain.repo.put(ALICE, payload(), object_id=first["object_id"])
    assert len(domain.db.rows("SELECT * FROM company_profile_revisions")) == 2
    with pytest.raises(IntegrityError):
        domain.db.execute("UPDATE company_profile_revisions SET payload_json='{}'")


@pytest.mark.parametrize(
    "status,evidence,reason",
    [
        ("document_supported", [], None),
        ("unknown", [], None),
        ("conflicting", [], "conflict"),
        ("self_reported", [{"malformed": True}], None),
    ],
)
def test_fact_provenance_cannot_claim_missing_support(status, evidence, reason):
    with pytest.raises(ValidationError):
        CompanyFact(
            fact_id="x",
            kind="qualification",
            label="x",
            value="x",
            status=status,
            evidence=evidence,
            reason=reason,
        )


def test_unknown_self_reported_and_real_provenance_are_explicit(domain):
    for status in ("unknown", "self_reported"):
        fact = {
            "fact_id": "x",
            "kind": "qualification",
            "label": "x",
            "value": None,
            "status": status,
            "reason": "source not supplied",
        }
        profile = domain.repo.put(ALICE, payload(facts=[fact]))
        result = domain.repo.qualification_at(
            ALICE, profile["object_id"], profile["version_id"], "x", datetime.now(UTC)
        )
        assert result["status"] == "information_insufficient"
    with pytest.raises(ValidationError):
        ProfilePayload(**payload(provenance_kind="authorized_real"))


def test_no_implicit_admin_read_and_old_context_observes_revocation(domain):
    doc = material(domain)
    for who in (BOB, ADMIN, VIEWER):
        with pytest.raises(CompanyError):
            domain.materials.read(who, doc["object_id"])
    domain.auth.grant(ALICE, doc["object_id"], BOB.owner_id, ["read"])
    assert domain.materials.read(BOB, doc["object_id"])[1]
    domain.auth.revoke(ALICE, doc["object_id"], BOB.owner_id)
    with pytest.raises(CompanyError):
        domain.materials.read(BOB, doc["object_id"])
    assert not domain.auth.visible(BOB, "material")["items"]
    assert domain.materials.search(BOB, "alpha")["total"] == 0


@pytest.mark.parametrize("updates", [{"status": "disabled"}, {"fresh_until": 0}])
def test_fixed_tool_context_checks_current_subject_on_every_call(domain, updates):
    doc = material(domain)
    for key, value in updates.items():
        domain.db.execute(
            f"UPDATE enterprise_subjects SET {key}=:value WHERE owner_id='alice'", {"value": value}
        )
    with pytest.raises(CompanyError) as error:
        domain.materials.read(ALICE, doc["object_id"])
    assert error.value.status_code == 401


def test_viewer_cannot_create_and_write_does_not_imply_read(domain):
    with pytest.raises(CompanyError) as error:
        material(domain, VIEWER)
    assert error.value.status_code == 403
    doc = material(domain)
    domain.auth.grant(ALICE, doc["object_id"], BOB.owner_id, ["write"])
    changed = material(
        domain,
        BOB,
        object_id=doc["object_id"],
        expected_version=doc["version_id"],
        data=b"new text",
    )
    assert changed["version_id"] != doc["version_id"]
    with pytest.raises(CompanyError):
        domain.materials.read(BOB, doc["object_id"])


def test_profile_derivations_inherit_source_acl_and_integrity(domain):
    doc, profile, fact = supported_profile(domain)
    domain.auth.grant(ALICE, profile["object_id"], BOB.owner_id, ["read"])
    with pytest.raises(CompanyError):
        domain.repo.get(BOB, profile["object_id"])
    domain.auth.grant(ALICE, doc["object_id"], BOB.owner_id, ["read"])
    assert domain.repo.get(BOB, profile["object_id"])["payload"]["facts"][0]["value"] == "recorded"
    domain.auth.revoke(ALICE, doc["object_id"], BOB.owner_id)
    with pytest.raises(CompanyError):
        domain.repo.get(BOB, profile["object_id"])
    forged = copy.deepcopy(fact)
    forged["evidence"][0]["quote"] = "FORGED!!!"
    with pytest.raises(CompanyError, match="company_evidence_mismatch"):
        domain.repo.put(ALICE, payload(facts=[forged]))


@pytest.mark.parametrize(
    "updates",
    [
        {"locator": "page_text_v1", "page": 999},
        {"evidence_id": "invented-evidence-id"},
    ],
)
def test_profile_cannot_persist_fictitious_page_or_evidence_identity(domain, updates):
    _, _, fact = supported_profile(domain)
    forged = copy.deepcopy(fact)
    forged["evidence"][0].update(updates)
    with pytest.raises(CompanyError, match="company_evidence_mismatch"):
        domain.repo.put(ALICE, payload(facts=[forged]))


def test_same_material_span_with_invented_id_cannot_manufacture_conflict(domain):
    _, _, fact = supported_profile(domain)
    forged = copy.deepcopy(fact)
    forged.update(status="conflicting", reason="synthetic fake conflict")
    duplicate = copy.deepcopy(forged["evidence"][0])
    duplicate["evidence_id"] += "-invented-duplicate"
    forged["evidence"].append(duplicate)
    with pytest.raises(CompanyError, match="company_evidence_mismatch"):
        domain.repo.put(ALICE, payload(facts=[forged]))


@pytest.mark.parametrize("entry", ["profile", "search"])
def test_derived_result_rechecks_all_sources_after_later_source_read(domain, monkeypatch, entry):
    first, second = material(domain), material(domain, data=b"SYNTHETIC second alpha")
    refs = [
        domain.materials.citation(ALICE, item["object_id"], item["version_id"], 0, 9)
        for item in (first, second)
    ]
    profile = domain.repo.put(
        ALICE,
        payload(
            facts=[
                {
                    "fact_id": "two_sources",
                    "kind": "qualification",
                    "label": "two synthetic sources",
                    "status": "document_supported",
                    "value": "supported",
                    "evidence": refs,
                }
            ]
        ),
    )
    for item in (first, second, profile):
        domain.auth.grant(ALICE, item["object_id"], BOB.owner_id, ["read"])
    original = domain.materials.read

    def revoke_earlier_source_after_later_read(context, object_id, version_id=None):
        result = original(context, object_id, version_id)
        if context == BOB and object_id == second["object_id"]:
            domain.auth.revoke(ALICE, first["object_id"], BOB.owner_id)
        return result

    monkeypatch.setattr(domain.materials, "read", revoke_earlier_source_after_later_read)
    with pytest.raises(CompanyError):
        if entry == "profile":
            domain.repo.get(BOB, profile["object_id"])
        else:
            domain.materials.search(BOB, "alpha")


def test_current_role_downgrade_revokes_old_context_mutations(domain):
    doc = material(domain)
    domain.db.execute(
        "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE owner_id='alice'"
    )
    assert domain.materials.read(ALICE, doc["object_id"])[1]
    for mutation in (
        lambda: material(domain, object_id=doc["object_id"], expected_version=doc["version_id"]),
        lambda: domain.auth.grant(ALICE, doc["object_id"], BOB.owner_id, ["read"]),
        lambda: domain.auth.revoke(ALICE, doc["object_id"], BOB.owner_id),
        lambda: domain.repo.delete(ALICE, doc["object_id"]),
    ):
        with pytest.raises(CompanyError):
            mutation()


def test_fixed_tool_inputs_bind_version_hash_and_recheck_at_result_release(domain):
    doc, profile, _ = supported_profile(domain)
    refs = [
        {key: obj[key] for key in ("object_id", "version_id", "sha256")} for obj in (doc, profile)
    ]
    for obj in (doc, profile):
        domain.auth.grant(ALICE, obj["object_id"], BOB.owner_id, ["read"])
    domain.require_inputs(BOB, refs)  # Tool execution gate.
    material(
        domain,
        object_id=doc["object_id"],
        expected_version=doc["version_id"],
        data=b"new material must not replace the fixed task input",
    )
    domain.require_inputs(BOB, refs)
    forged = copy.deepcopy(refs)
    forged[0]["sha256"] = "0" * 64
    with pytest.raises(CompanyError, match="company_input_version_mismatch"):
        domain.require_inputs(BOB, forged)
    domain.auth.revoke(ALICE, doc["object_id"], BOB.owner_id)
    with pytest.raises(CompanyError):
        domain.require_inputs(BOB, refs)  # The same context cannot release a stale result.


@pytest.mark.parametrize(
    "required_day,status", [(1, "satisfied"), (2, "satisfied"), (3, "not_satisfied")]
)
def test_qualification_dates_use_required_instant_not_retention(domain, required_day, status):
    validity = {
        "kind": "interval",
        "valid_from": "2026-01-01T00:00:00+08:00",
        "valid_until": "2026-01-02T12:00:00+08:00",
    }
    _, profile, _ = supported_profile(domain, validity=validity)
    required = datetime(2026, 1, required_day, 4, tzinfo=UTC)
    answer = domain.repo.qualification_at(
        ALICE, profile["object_id"], profile["version_id"], "cert", required
    )
    assert answer["status"] == status
    assert domain.repo.get(ALICE, profile["object_id"])


def test_missing_dates_or_missing_files_never_satisfy(domain):
    doc, profile, _ = supported_profile(domain)
    args = (ALICE, profile["object_id"], profile["version_id"], "cert", datetime.now(UTC))
    assert domain.repo.qualification_at(*args)["status"] == "information_insufficient"
    row = domain.materials.revision(ALICE, doc["object_id"])
    domain.materials.path(row["blob_path"]).unlink()
    assert domain.repo.qualification_at(*args)["reasons"] == ["material_source_unavailable"]


def test_material_versions_and_hashes_are_immutable(domain):
    first = material(domain)
    second = material(
        domain, object_id=first["object_id"], expected_version=first["version_id"], data=b"second"
    )
    assert domain.materials.read(ALICE, first["object_id"], first["version_id"])[1].startswith(
        b"SYNTHETIC"
    )
    assert domain.materials.read(ALICE, first["object_id"])[1] == b"second"
    with pytest.raises(CompanyError, match="version_conflict"):
        material(domain, object_id=first["object_id"], expected_version=first["version_id"])
    assert len(domain.db.rows("SELECT * FROM company_material_revisions")) == 2
    assert len(domain.backup_files()) == 4
    with pytest.raises(IntegrityError):
        domain.db.execute("UPDATE company_material_revisions SET sha256='corrupt'")
    row = domain.materials.revision(ALICE, first["object_id"], second["version_id"])
    domain.materials.path(row["text_path"]).write_text("tampered")
    with pytest.raises(CompanyError, match="material_integrity_failed"):
        domain.materials.read(ALICE, first["object_id"])
    with pytest.raises(CompanyError):
        domain.backup_files()


@pytest.mark.parametrize(
    "filename,ctype,data,code",
    [
        ("../escape.txt", "text/plain", b"x", "unsafe_filename"),
        ("a\\b.txt", "text/plain", b"x", "unsafe_filename"),
        ("fake.pdf", "text/plain", b"x", "unsupported_material_format"),
        ("fake.txt", "text/plain", b"%PDF-1.4", "material_format_mismatch"),
        ("fake.txt", "text/plain", b"PK\x03\x04", "material_format_mismatch"),
        ("binary.txt", "text/plain", b"x\x00y", "binary_material_rejected"),
        ("invalid.txt", "text/plain", b"\xff", "material_requires_utf8"),
        ("fake.html", "text/html", b"plain text", "material_format_mismatch"),
        (
            "file.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK",
            "unsupported_material_format",
        ),
    ],
)
def test_file_names_formats_and_binary_spoofing_are_rejected(domain, filename, ctype, data, code):
    with pytest.raises(CompanyError, match=code):
        material(domain, filename=filename, content_type=ctype, data=data)
    assert not domain.db.rows("SELECT * FROM company_objects")
    assert not list(domain.materials.root.rglob("*.*"))


def test_bounded_html_normalization_never_serves_executable_html(domain):
    doc = material(
        domain,
        filename="certificate.html",
        content_type="text/html",
        data=b"<html><body><script>SECRET_SCRIPT</script><p>certificate alpha</p></body></html>",
    )
    assert domain.materials.read(ALICE, doc["object_id"])[2] == "certificate alpha"
    with pytest.raises(CompanyError, match="html_resource_limit"):
        material(
            domain,
            filename="deep.html",
            content_type="text/html",
            data=b"<div>" * 65 + b"x" + b"</div>" * 65,
        )
    with pytest.raises(CompanyError, match="html_resource_limit"):
        material(domain, filename="wide.html", content_type="text/html", data=b"<br>" * 10001)
    with pytest.raises(CompanyError, match="html_structure_invalid"):
        material(
            domain,
            filename="malformed.html",
            content_type="text/html",
            data=b"<div>" * 64 + b"</span><div>" * 500 + b"synthetic",
        )


@pytest.mark.parametrize(
    "filename,media,data",
    [
        ("unit.txt", "text/plain", "甲  乙\r\n\r\n丙\t丁".encode()),
        ("unit.html", "text/html", b"<p>alpha  beta</p><p>gamma</p><script>hidden</script>"),
    ],
)
def test_material_citations_use_shared_normalized_text_v2(domain, filename, media, data):
    doc = material(domain, filename=filename, content_type=media, data=data)
    expected = canonical_text(data, media, "utf-8-sig")
    assert domain.materials.read(ALICE, doc["object_id"])[2] == expected
    evidence = domain.materials.citation(
        ALICE, doc["object_id"], doc["version_id"], 0, len(expected)
    )
    assert evidence["locator"] == "normalized_text_v2" and evidence["quote"] == expected


def test_size_text_limits_and_zip_bomb_rejected_before_decompression(domain):
    domain.materials.max_bytes = 1000
    with pytest.raises(CompanyError, match="material_size_limit"):
        material(domain, data=b"a" * 1001)
    domain.materials.max_text_chars = 10
    with pytest.raises(CompanyError, match="material_text_limit"):
        material(domain, data=b"a" * 11)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("large.txt", b"a" * 100000)
    with pytest.raises(CompanyError, match="material_format_mismatch"):
        material(domain, data=buffer.getvalue())


def test_symlink_storage_escape_and_cross_object_version_are_denied(domain, tmp_path):
    first, second = material(domain), material(domain, data=b"second")
    with pytest.raises(CompanyError):
        domain.materials.read(ALICE, first["object_id"], second["version_id"])
    row = domain.materials.revision(ALICE, first["object_id"])
    target = domain.materials.path(row["blob_path"])
    target.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private outside")
    target.symlink_to(outside)
    with pytest.raises(CompanyError, match="unsafe_material_path"):
        domain.materials.read(ALICE, first["object_id"])


def test_retention_denies_immediately_then_purges_source_and_derived_quotes(domain):
    doc, profile, _ = supported_profile(domain)
    domain.db.execute(
        "UPDATE company_objects SET retention_until=0 WHERE object_id=:id", {"id": doc["object_id"]}
    )
    with pytest.raises(CompanyError):
        domain.materials.read(ALICE, doc["object_id"])
    with pytest.raises(CompanyError):
        domain.repo.get(ALICE, profile["object_id"])
    report = prune_company_materials(domain)
    assert report["status"] == "PASS"
    assert set(report["purged_ids"]) == {doc["object_id"], profile["object_id"]}
    assert not domain.db.rows("SELECT * FROM company_material_revisions")
    assert not domain.db.rows("SELECT * FROM company_profile_revisions")
    assert len(domain.db.rows("SELECT * FROM company_revision_tombstones")) == 2
    assert not list((domain.materials.root / "blob").iterdir())
    assert not list((domain.materials.root / "text").iterdir())


def test_cleanup_failure_is_closed_and_retryable(domain, monkeypatch):
    doc = material(domain)
    domain.repo.delete(ALICE, doc["object_id"])
    original = Path.unlink

    def cannot_unlink(path, *args, **kwargs):
        if path.parent.name == "blob":
            raise PermissionError("unit failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cannot_unlink)
    report = prune_company_materials(domain)
    assert report["failure_ids"] == [doc["object_id"]]
    with pytest.raises(CompanyError):
        domain.materials.read(ALICE, doc["object_id"])
    assert domain.db.rows("SELECT purge_state FROM company_objects")[0]["purge_state"] == "pending"
    monkeypatch.setattr(Path, "unlink", original)
    assert prune_company_materials(domain)["purged_ids"] == [doc["object_id"]]


def test_restore_quarantine_discards_all_old_acls_and_requires_object_regrant(domain):
    one, two = material(domain), material(domain, data=b"second")
    domain.auth.grant(ALICE, one["object_id"], BOB.owner_id, ["read"])
    assert quarantine_after_restore(domain.db)["quarantined_objects"] == 2
    assert not domain.db.rows("SELECT * FROM company_grants")
    for who in (ALICE, BOB, ADMIN):
        with pytest.raises(CompanyError):
            domain.materials.read(who, one["object_id"])
    with pytest.raises(CompanyError):
        domain.auth.reauthorize(ALICE, one["object_id"], "alice", ["read"])
    domain.auth.reauthorize(ADMIN, one["object_id"], "alice", ["read"])
    assert domain.materials.read(ALICE, one["object_id"])[1]
    for who, oid in ((BOB, one["object_id"]), (ADMIN, one["object_id"]), (ALICE, two["object_id"])):
        with pytest.raises(CompanyError):
            domain.materials.read(who, oid)


@pytest.fixture
def company_http(settings):
    app = create_app(settings, DemoSettings(_env_file=None), enterprise())
    provider = ProtocolProvider()
    cookies = {}
    with TestClient(app, base_url=ORIGIN) as client:
        app.state.identity.oauth.enterprise.client_kwargs["transport"] = httpx2.MockTransport(
            provider.handle
        )
        for actor, role in (
            ("alice", "analyst"),
            ("bob", "analyst"),
            ("admin", "admin"),
            ("viewer", "viewer"),
        ):
            client.cookies.clear()
            provider.subject, provider.roles = actor, [role]
            assert provider.login(client).headers["location"] == "/"
            cookies[actor] = client.cookies.get("bidops_session")

        def login(actor):
            client.cookies.clear()
            if actor is not None:
                client.cookies.set("bidops_session", cookies[actor], domain="bidops.test", path="/")
            me = client.get("/api/auth/me").json()
            return {"Origin": ORIGIN, "X-CSRF-Token": me["csrf_token"] or ""}

        yield app, client, login


def http_material(case):
    _, client, login = case
    headers = {**login("alice"), "X-Filename": "unit.txt", "Content-Type": "text/plain"}
    result = client.post(
        "/api/company/materials", content=b"PRIVATE_COMPANY alpha", headers=headers
    )
    assert result.status_code == 201, result.text
    return result.json()


@pytest.mark.parametrize(
    "actor,expected", [(None, 401), ("bob", 404), ("admin", 404), ("viewer", 404)]
)
def test_all_real_material_read_paths_deny_unauthorized_and_no_private_body(
    company_http, actor, expected
):
    doc = http_material(company_http)
    _, client, login = company_http
    headers = login(actor)
    for suffix in ("", "/download", "/export"):
        response = client.get(f"/api/company/materials/{doc['object_id']}{suffix}")
        assert response.status_code == expected
        assert "PRIVATE_COMPANY" not in response.text
    cited = client.post(
        f"/api/company/materials/{doc['object_id']}/versions/{doc['version_id']}/citations",
        json={"start": 0, "end": 15},
        headers=headers,
    )
    assert cited.status_code == expected and "PRIVATE_COMPANY" not in cited.text
    for path in ("/api/company/materials", "/api/company/materials/search?q=alpha"):
        response = client.get(path)
        if actor is None:
            assert response.status_code == 401
        else:
            assert response.json()["total"] == 0 and response.json()["items"] == []


def test_http_profiles_versions_csrf_and_all_mutations(company_http):
    app, client, login = company_http
    headers = login("alice")
    response = client.post("/api/company/profiles", json={"payload": payload()}, headers=headers)
    assert response.status_code == 201, response.text
    ref = response.json()
    assert (
        client.get(f"/api/company/profiles/{ref['object_id']}").json()["version_id"]
        == ref["version_id"]
    )
    denied = client.put(f"/api/company/profiles/{ref['object_id']}", json={"payload": payload()})
    assert denied.status_code == 403
    changed = client.put(
        f"/api/company/profiles/{ref['object_id']}",
        json={"payload": payload(display_name="new"), "expected_version": ref["version_id"]},
        headers=headers,
    )
    assert changed.status_code == 200
    assert (
        client.get(f"/api/company/profiles/{ref['object_id']}?version={ref['version_id']}").json()[
            "payload"
        ]["display_name"]
        == "Synthetic company"
    )
    alice = app.state.store.db.rows(
        "SELECT owner_id FROM enterprise_subjects WHERE subject='alice'"
    )[0]["owner_id"]
    login("admin")
    assert client.get(f"/api/company/profiles/{ref['object_id']}").status_code == 404
    assert client.get("/api/company/profiles").json()["total"] == 0
    login("alice")
    assert app.state.company.auth.require_subject(ToolContext(alice))


def test_http_grants_revoke_old_cookie_context_and_local_disable(company_http):
    doc = http_material(company_http)
    app, client, login = company_http
    bob = app.state.store.db.rows("SELECT owner_id FROM enterprise_subjects WHERE subject='bob'")[
        0
    ]["owner_id"]
    granted = client.post(
        f"/api/company/objects/{doc['object_id']}/grants",
        json={"subject_id": bob, "actions": ["read"]},
        headers=login("alice"),
    )
    assert granted.status_code == 200
    login("bob")
    assert (
        client.get(f"/api/company/materials/{doc['object_id']}/download").content
        == b"PRIVATE_COMPANY alpha"
    )
    revoked = client.delete(
        f"/api/company/objects/{doc['object_id']}/grants/{bob}", headers=login("alice")
    )
    assert revoked.status_code == 200
    login("bob")
    assert client.get(f"/api/company/materials/{doc['object_id']}/export").status_code == 404
    with pytest.raises(CompanyError):
        app.state.company.materials.read(ToolContext(bob), doc["object_id"])
    login("alice")
    app.state.store.db.execute(
        "UPDATE enterprise_subjects SET status='disabled' WHERE subject='alice'"
    )
    assert client.get(f"/api/company/materials/{doc['object_id']}").status_code == 401


def test_http_current_role_downgrade_blocks_all_old_owner_mutations(company_http):
    doc = http_material(company_http)
    app, client, login = company_http
    headers = login("alice")
    app.state.store.db.execute(
        "UPDATE enterprise_subjects SET roles_json='[\"viewer\"]' WHERE subject='alice'"
    )
    # The old session's analyst role no longer intersects the current viewer role.
    assert client.get(f"/api/company/materials/{doc['object_id']}/download").status_code == 401
    assert (
        client.put(
            f"/api/company/materials/{doc['object_id']}",
            content=b"new text",
            headers={**headers, "X-Filename": "unit.txt", "Content-Type": "text/plain"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"/api/company/objects/{doc['object_id']}/grants",
            json={"subject_id": "unused-target", "actions": ["read"]},
            headers=headers,
        ).status_code
        == 401
    )
    assert (
        client.delete(f"/api/company/objects/{doc['object_id']}", headers=headers).status_code
        == 401
    )
    assert (
        client.post(
            "/api/company/profiles", json={"payload": payload()}, headers=headers
        ).status_code
        == 401
    )


def test_http_streaming_limits_spoofing_csrf_and_download_headers(company_http):
    app, client, login = company_http
    headers = {**login("alice"), "X-Filename": "unit.txt", "Content-Type": "text/plain"}
    app.state.company.materials.max_bytes = 100
    assert (
        client.post("/api/company/materials", content=b"a" * 101, headers=headers).status_code
        == 413
    )
    assert (
        client.post("/api/company/materials", content=b"PK\x03\x04", headers=headers).status_code
        == 415
    )
    assert (
        client.post(
            "/api/company/materials",
            content=b"text",
            headers={**headers, "X-Filename": "../outside.txt"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/company/materials",
            content=b"text",
            headers={"X-Filename": "unit.txt", "Content-Type": "text/plain"},
        ).status_code
        == 403
    )
    doc = client.post("/api/company/materials", content=b"text", headers=headers).json()
    download = client.get(f"/api/company/materials/{doc['object_id']}/download")
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "no-store" in download.headers["cache-control"].split(", ")
    assert not app.state.store.db.rows("SELECT * FROM snapshots")


def test_http_encoded_chinese_filename_and_version_updates(company_http):
    _, client, login = company_http
    headers = {
        **login("alice"),
        "X-Filename-Encoded": quote("合成资质证明.txt"),
        "X-Provenance-Kind": "authorized_real",
        "X-Source-Attestation-Encoded": quote("测试用授权说明，非真实企业材料"),
        "Content-Type": "text/plain",
    }
    response = client.post("/api/company/materials", content=b"first version", headers=headers)
    assert response.status_code == 201, response.text
    ref = response.json()
    metadata = client.get(f"/api/company/materials/{ref['object_id']}").json()
    assert metadata["filename"] == "合成资质证明.txt"
    assert metadata["source_attestation"] == "测试用授权说明，非真实企业材料"
    retained_until = metadata["retention_until"]
    path = f"/api/company/materials/{ref['object_id']}"
    assert client.put(path, content=b"second", headers=headers).status_code == 409
    update_headers = {**headers, "If-Match": ref["version_id"]}
    assert client.put(path, content=b"second", headers=update_headers).status_code == 200
    assert client.put(path, content=b"third", headers=update_headers).status_code == 409
    assert client.get(f"{path}/download?version={ref['version_id']}").content == b"first version"
    assert client.get(path).json()["retention_until"] == retained_until


def test_profile_history_returns_current_retention_and_update_without_policy_preserves_it(
    company_http,
):
    _, client, login = company_http
    headers = login("alice")
    first_until = datetime.fromtimestamp(time.time() + 3 * 86400, UTC)
    current_until = datetime.fromtimestamp(time.time() + 7 * 86400, UTC).replace(microsecond=123456)
    created = client.post(
        "/api/company/profiles",
        json={"payload": payload(), "retention_until": first_until.isoformat()},
        headers=headers,
    )
    assert created.status_code == 201
    first = created.json()
    path = f"/api/company/profiles/{first['object_id']}"
    assert client.get(path).json()["retention_until"] == first_until.timestamp()
    changed = client.put(
        path,
        json={
            "payload": payload(display_name="Updated synthetic company"),
            "expected_version": first["version_id"],
            "retention_until": current_until.isoformat(),
        },
        headers=headers,
    )
    assert changed.status_code == 200
    old = client.get(f"{path}?version={first['version_id']}").json()
    assert old["retention_until"] == current_until.timestamp()
    assert old["sha256"] == first["sha256"]
    assert old["payload"]["display_name"] == payload()["display_name"]
    assert (
        client.put(
            path,
            json={"payload": payload(), "expected_version": changed.json()["version_id"]},
            headers=headers,
        ).status_code
        == 200
    )
    assert client.get(path).json()["retention_until"] == current_until.timestamp()
    login("bob")
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "encoded,detail",
    [("..%2Foutside.txt", "unsafe_filename"), ("%FF.txt", "invalid_filename_encoding")],
)
def test_http_encoded_filename_uses_same_validation(company_http, encoded, detail):
    _, client, login = company_http
    response = client.post(
        "/api/company/materials",
        content=b"text",
        headers={
            **login("alice"),
            "X-Filename-Encoded": encoded,
            "Content-Type": "text/plain",
        },
    )
    assert response.status_code == 422 and response.json()["detail"] == detail


def test_company_private_routes_never_fall_back_to_anonymous_local_mode(settings):
    app = create_app(settings, DemoSettings(_env_file=None))
    with TestClient(app) as client:
        assert client.get("/api/company/profiles").status_code == 404
        assert client.post("/api/company/materials", content=b"x").status_code == 404
