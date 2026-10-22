"""Fixed source closures. Identity and every private source are checked on each use.

Full text belongs to the ephemeral SourceBundle, never to the input manifest.
The manifest is an integrity binding, not an authorization grant.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from bidops.company.authorization import CompanyError
from bidops.qualification.contracts import Digest, Name, QualificationModel, VerifiedDocument
from bidops.screening.schemas import EvidenceRef, VersionRef


def canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class NoticeInput(QualificationModel):
    version: VersionRef
    text_sha256: Digest
    parser_version: Name
    normalizer_version: Name
    available_at: AwareDatetime
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime
    package_ids: list[Name]


class AttachmentInput(QualificationModel):
    record_id: Name
    attachment_id: Name
    parent_notice_revision: VersionRef
    version: VersionRef | None
    observed_at: AwareDatetime
    acquisition_status: Name
    parse_status: Name
    integrity_status: Name
    text_sha256: Digest | None
    normalizer_version: str | None


class MaterialInput(QualificationModel):
    version: VersionRef
    text_sha256: Digest
    normalizer_version: Literal["normalized_text_v2"] = "normalized_text_v2"
    created_at: AwareDatetime
    provenance_kind: Name


class ScreeningInputManifest(QualificationModel):
    schema_version: Literal["screening_inputs_v1"] = "screening_inputs_v1"
    project_id: Name | None
    as_of: AwareDatetime
    profile_revision: VersionRef
    material_revisions: list[VersionRef] = Field(default_factory=list)
    material_versions: list[MaterialInput] = Field(default_factory=list)
    notice_revisions: list[VersionRef] = Field(default_factory=list)
    attachment_revisions: list[VersionRef] = Field(default_factory=list)
    notice_versions: list[NoticeInput] = Field(default_factory=list)
    attachment_records: list[AttachmentInput] = Field(default_factory=list)
    target_package_ids: list[Name] = Field(default_factory=list)
    missing_attachment_ids: list[Name] = Field(default_factory=list)
    project_status: dict = Field(default_factory=dict)
    closure_sha256: Digest

    @model_validator(mode="after")
    def closure(self):
        data = self.model_dump(mode="json", exclude={"closure_sha256"})
        if digest(data) != self.closure_sha256:
            raise ValueError("screening_manifest_hash_mismatch")
        for values in (self.material_revisions, self.notice_revisions, self.attachment_revisions):
            if len({v.key() for v in values}) != len(values):
                raise ValueError("screening_manifest_duplicate_revision")
        if self.notice_revisions != [row.version for row in self.notice_versions]:
            raise ValueError("screening_manifest_notice_closure_mismatch")
        if self.material_revisions != [row.version for row in self.material_versions]:
            raise ValueError("screening_manifest_material_closure_mismatch")
        if self.attachment_revisions != [
            row.version for row in self.attachment_records if row.version
        ]:
            raise ValueError("screening_manifest_attachment_closure_mismatch")
        if len(set(self.target_package_ids)) != len(self.target_package_ids):
            raise ValueError("screening_manifest_duplicate_package")
        if self.project_id is None and (self.notice_revisions or self.attachment_records):
            raise ValueError("profile_only_manifest_contains_public_project")
        return self

    @property
    def company_revisions(self):
        return [self.profile_revision, *self.material_revisions]


class SourceBundle(QualificationModel):
    manifest: ScreeningInputManifest
    profile: dict
    documents: list[VerifiedDocument] = Field(default_factory=list)
    material_texts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def verified_closure(self):
        if digest(self.profile) != self.manifest.profile_revision.sha256:
            raise ValueError("screening_bundle_profile_mismatch")
        expected = {v.version.version_id: v for v in self.manifest.notice_versions}
        expected.update(
            {
                a.record_id: a
                for a in self.manifest.attachment_records
                if a.parse_status == "parsed" and a.integrity_status == "verified"
            }
        )
        if len({d.document_id for d in self.documents}) != len(self.documents) or set(expected) != {
            d.document_id for d in self.documents
        }:
            raise ValueError("screening_bundle_document_closure_mismatch")
        for document in self.documents:
            row = expected[document.document_id]
            parent = row.version if isinstance(row, NoticeInput) else row.parent_notice_revision
            if (
                document.version != row.version
                or document.object_kind
                != ("notice" if isinstance(row, NoticeInput) else "attachment")
                or document.parent_notice_revision != parent
                or document.text_sha256 != row.text_sha256
                or hashlib.sha256(document.text.encode()).hexdigest() != row.text_sha256
            ):
                raise ValueError("screening_bundle_document_mismatch")
        if set(self.material_texts) != {v.version_id for v in self.manifest.material_revisions}:
            raise ValueError("screening_bundle_material_closure_mismatch")
        for row in self.manifest.material_versions:
            if (
                hashlib.sha256(self.material_texts[row.version.version_id].encode()).hexdigest()
                != row.text_sha256
            ):
                raise ValueError("screening_bundle_material_mismatch")
        return self


class SourceAdapter:
    def __init__(self, company_service, notice_repository):
        self.company, self.notices = company_service, notice_repository

    def _profile(self, context, profile_revision, additional_material_revisions):
        ref = VersionRef.model_validate(profile_revision)
        profile = self.company.repo.get(context, ref.object_id, ref.version_id)
        if profile["sha256"] != ref.sha256:
            raise CompanyError("company_input_version_mismatch", 409)
        materials = {}
        for value in [
            *(e["version"] for f in profile["payload"]["facts"] for e in f["evidence"]),
            *additional_material_revisions,
        ]:
            material = VersionRef.model_validate(value)
            materials[material.key()] = material
        refs, texts, versions = [], {}, []
        for key in sorted(materials):
            material = materials[key]
            row, _, normalized = self.company.materials.read(
                context, material.object_id, material.version_id
            )
            if row["sha256"] != material.sha256:
                raise CompanyError("company_input_version_mismatch", 409)
            refs.append(material)
            texts[material.version_id] = normalized
            versions.append(
                MaterialInput(
                    version=material,
                    text_sha256=row["text_sha256"],
                    created_at=datetime.fromtimestamp(row["created_at"], UTC),
                    provenance_kind=row["provenance_kind"],
                )
            )
        self.company.require_inputs(context, [ref, *refs])
        return ref, profile["payload"], refs, texts, versions

    def resolve_profile(self, context, profile_revision, as_of, additional_material_revisions=()):
        return self.resolve(context, profile_revision, None, as_of, additional_material_revisions)

    def resolve(
        self, context, profile_revision, project_id, as_of, additional_material_revisions=()
    ) -> SourceBundle:
        # Validate timezone even for an empty project closure.
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise CompanyError("screening_as_of_requires_timezone", 422)
        ref, profile, materials, material_texts, material_versions = self._profile(
            context, profile_revision, additional_material_revisions
        )
        notices, attachments, documents, missing = [], [], [], set()
        status = {}
        if project_id is not None:
            versions = self.notices.project_versions(project_id, as_of)
            if not versions:
                raise CompanyError("screening_project_not_observed", 404)
            for version in versions:
                try:
                    verified, normalized = self.notices.read_text(version.version_id)
                except ValueError:
                    raise CompanyError("screening_source_unavailable", 409) from None
                if verified != version.version_ref:
                    raise CompanyError("screening_source_version_mismatch", 409)
                notices.append(
                    NoticeInput(
                        version=verified,
                        text_sha256=version.text_sha256,
                        parser_version=version.parser_version,
                        normalizer_version=version.normalizer_version,
                        available_at=version.available_at,
                        first_observed_at=version.first_observed_at,
                        last_observed_at=version.last_observed_at,
                        package_ids=[p.package_id for p in version.packages],
                    )
                )
                documents.append(
                    VerifiedDocument(
                        document_id=version.version_id,
                        object_kind="notice",
                        version=verified,
                        text_sha256=version.text_sha256,
                        text=normalized,
                        parent_notice_revision=verified,
                        available_at=version.available_at,
                    )
                )
                latest = {}
                for attachment in self.notices.list_attachments(version.version_id, as_of):
                    attachments.append(
                        AttachmentInput(
                            record_id=attachment.record_id,
                            attachment_id=attachment.attachment_id,
                            parent_notice_revision=verified,
                            version=attachment.version_ref,
                            observed_at=attachment.observed_at,
                            acquisition_status=attachment.acquisition_status,
                            parse_status=attachment.parse_status,
                            integrity_status=attachment.integrity_status,
                            text_sha256=attachment.text_sha256,
                            normalizer_version=attachment.normalizer_version,
                        )
                    )
                    latest[attachment.attachment_id] = attachment
                    if (
                        attachment.parse_status == "parsed"
                        and attachment.integrity_status == "verified"
                    ):
                        aref, text = self.notices.read_text(
                            attachment.record_id, object_kind="attachment"
                        )
                        documents.append(
                            VerifiedDocument(
                                document_id=attachment.record_id,
                                object_kind="attachment",
                                version=aref,
                                text_sha256=attachment.text_sha256,
                                text=text,
                                parent_notice_revision=verified,
                                available_at=attachment.observed_at,
                            )
                        )
                missing.update(
                    a.attachment_id
                    for a in latest.values()
                    if a.parse_status != "parsed" or a.integrity_status != "verified"
                )
            status = self.notices.project_status(project_id, as_of).model_dump(mode="json")
        data = dict(
            schema_version="screening_inputs_v1",
            project_id=project_id,
            as_of=as_of,
            profile_revision=ref,
            material_revisions=materials,
            material_versions=material_versions,
            notice_revisions=[n.version for n in notices],
            attachment_revisions=[a.version for a in attachments if a.version],
            notice_versions=notices,
            attachment_records=attachments,
            target_package_ids=sorted({p for n in notices for p in n.package_ids}),
            missing_attachment_ids=sorted(missing),
            project_status=status,
        )
        # Use Pydantic's JSON datetime representation before hashing.
        from pydantic import TypeAdapter

        encoded = TypeAdapter(dict).dump_python(data, mode="json")
        manifest = ScreeningInputManifest(**data, closure_sha256=digest(encoded))
        self.company.require_inputs(context, manifest.company_revisions)
        return SourceBundle(
            manifest=manifest, profile=profile, documents=documents, material_texts=material_texts
        )

    @staticmethod
    def _manifest(value):
        if isinstance(value, SourceBundle):
            value = SourceBundle.model_validate(value).manifest
        return ScreeningInputManifest.model_validate(value)

    def recheck(self, context, bundle_or_manifest, for_execution=True):
        manifest = self._manifest(bundle_or_manifest)
        if for_execution:
            self.company.auth.require_creator(context)
        self.company.require_inputs(context, manifest.company_revisions)
        # New observations do not rewrite or hide an authorized historical result.
        if not for_execution:
            try:
                for row in manifest.notice_versions:
                    ref, normalized = self.notices.read_text(row.version.version_id)
                    if (
                        ref != row.version
                        or hashlib.sha256(normalized.encode()).hexdigest() != row.text_sha256
                    ):
                        raise ValueError("fixed_notice_changed")
                for row in manifest.attachment_records:
                    actual = self.notices.get_attachment(row.record_id)
                    if (
                        actual.version_ref != row.version
                        or actual.integrity_status != row.integrity_status
                    ):
                        raise ValueError("fixed_attachment_changed")
                    if row.parse_status == "parsed" and row.integrity_status == "verified":
                        ref, normalized = self.notices.read_text(
                            row.record_id, object_kind="attachment"
                        )
                        if (
                            ref != row.version
                            or hashlib.sha256(normalized.encode()).hexdigest() != row.text_sha256
                        ):
                            raise ValueError("fixed_attachment_text_changed")
            except ValueError:
                raise CompanyError("screening_source_unavailable", 409) from None
            self.company.require_inputs(context, manifest.company_revisions)
            return
        current = self.resolve(
            context,
            manifest.profile_revision,
            manifest.project_id,
            manifest.as_of,
            manifest.material_revisions,
        )
        if current.manifest != manifest:
            raise CompanyError("screening_source_closure_changed", 409)
        self.company.require_inputs(context, manifest.company_revisions)

    def changes(self, context, bundle_or_manifest):
        manifest = self._manifest(bundle_or_manifest)
        self.recheck(context, manifest, for_execution=False)
        reasons = []
        for ref in manifest.company_revisions:
            obj = self.company.auth.require(context, ref.object_id, "read")
            if obj["head_version"] != ref.version_id:
                reasons.append(
                    "profile_head_changed"
                    if ref == manifest.profile_revision
                    else "material_head_changed"
                )
        if manifest.project_id is not None:
            now = datetime.fromtimestamp(self.company.auth.clock(), UTC)
            versions = self.notices.project_versions(manifest.project_id, now)
            if [v.version_ref for v in versions] != manifest.notice_revisions:
                reasons.append("notice_versions_changed")
            current_by_id = {v.version_id: v for v in versions}
            if any(
                row.version.version_id in current_by_id
                and current_by_id[row.version.version_id].last_observed_at != row.last_observed_at
                for row in manifest.notice_versions
            ):
                reasons.append("notice_observation_updated")
            records = [
                a.record_id
                for v in versions
                for a in self.notices.list_attachments(v.version_id, now)
            ]
            if records != [a.record_id for a in manifest.attachment_records]:
                reasons.append("attachment_versions_changed")
            if not self.notices.project_status(manifest.project_id, now).is_fresh:
                reasons.append("public_sources_stale")
        self.company.require_inputs(context, manifest.company_revisions)
        return {"current": not reasons, "stale_reasons": sorted(set(reasons))}

    @staticmethod
    def read_documents(bundle):
        return SourceBundle.model_validate(bundle).documents

    def make_evidence(self, bundle, document_id, start, end):
        bundle = SourceBundle.model_validate(bundle)
        document = next((d for d in bundle.documents if d.document_id == document_id), None)
        if document is None:
            raise CompanyError("screening_evidence_outside_closure", 422)
        try:
            return self.notices.make_evidence(
                document.version.version_id, start, end, object_kind=document.object_kind
            )
        except ValueError:
            raise CompanyError("screening_evidence_invalid", 422) from None

    def resolve_evidence(self, context, bundle_or_manifest, evidence):
        manifest = self._manifest(bundle_or_manifest)
        self.recheck(context, manifest, for_execution=False)
        ref = EvidenceRef.model_validate(evidence)
        if ref.side == "notice":
            allowed = (
                manifest.notice_revisions
                if ref.object_kind == "notice"
                else manifest.attachment_revisions
            )
            if ref.version not in allowed:
                raise CompanyError("screening_evidence_outside_closure", 422)
            try:
                resolved = self.notices.resolve_evidence(ref)
            except ValueError:
                raise CompanyError("screening_evidence_invalid", 422) from None
            self.company.require_inputs(context, manifest.company_revisions)
            return resolved
        if ref.object_kind != "company_material" or ref.version not in manifest.material_revisions:
            raise CompanyError("screening_evidence_outside_closure", 422)
        expected = EvidenceRef.model_validate(
            self.company.materials.citation(
                context, ref.version.object_id, ref.version.version_id, ref.start, ref.end
            )
        )
        if ref.model_dump(exclude={"validity"}) != expected.model_dump(exclude={"validity"}):
            raise CompanyError("screening_evidence_invalid", 422)
        profile = self.company.repo.get(
            context, manifest.profile_revision.object_id, manifest.profile_revision.version_id
        )
        fixed = [
            EvidenceRef.model_validate(e)
            for fact in profile["payload"]["facts"]
            for e in fact["evidence"]
            if e["evidence_id"] == ref.evidence_id and e["version"] == ref.version.model_dump()
        ]
        permitted = [e.validity for e in fixed] or [expected.validity]
        if ref.validity not in permitted:
            raise CompanyError("screening_evidence_validity_not_bound", 422)
        self.company.require_inputs(context, manifest.company_revisions)
        return ref
