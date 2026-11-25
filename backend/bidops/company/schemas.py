from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints, model_validator

from bidops.models import StrictModel
from bidops.screening.schemas import EvidenceRef

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Action = Literal["read", "write", "grant", "delete"]


class CompanyModel(StrictModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always", allow_inf_nan=False)


class CompanyFact(CompanyModel):
    fact_id: Name
    kind: Literal["industry", "region", "qualification", "other"]
    label: Name
    value: Annotated[str, Field(max_length=4000)] | None = None
    status: Literal["self_reported", "document_supported", "unknown", "conflicting"]
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    reason: Name | None = None

    @model_validator(mode="after")
    def provenance(self):
        if any(e.side != "company" or e.object_kind != "company_material" for e in self.evidence):
            raise ValueError("company_fact_requires_material_evidence")
        if self.status == "document_supported" and (not self.evidence or not self.value):
            raise ValueError("document_supported_requires_value_and_evidence")
        if self.status == "self_reported" and self.evidence:
            raise ValueError("self_reported_cannot_claim_document_support")
        if self.status in {"unknown", "conflicting"} and not self.reason:
            raise ValueError("unknown_or_conflicting_requires_reason")
        if self.status == "conflicting" and len({e.evidence_id for e in self.evidence}) < 2:
            raise ValueError("conflict_requires_distinct_evidence")
        return self


class ProfilePayload(CompanyModel):
    display_name: Name
    facts: list[CompanyFact] = Field(default_factory=list, max_length=200)
    provenance_kind: Literal["engineering_fixture", "authorized_real"] = "engineering_fixture"
    source_attestation: Name | None = None

    @model_validator(mode="after")
    def profile_shape(self):
        if len({fact.fact_id for fact in self.facts}) != len(self.facts):
            raise ValueError("duplicate_fact_id")
        if self.provenance_kind == "authorized_real" and not self.source_attestation:
            raise ValueError("real_profile_requires_source_attestation")
        return self


class ProfileWrite(CompanyModel):
    payload: ProfilePayload
    expected_version: Name | None = None
    retention_until: AwareDatetime | None = None


class GrantCommand(CompanyModel):
    subject_id: Name
    actions: list[Action] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_actions(self):
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("duplicate_action")
        return self


class CitationRequest(CompanyModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ValidityQuery(CompanyModel):
    required_at: AwareDatetime
