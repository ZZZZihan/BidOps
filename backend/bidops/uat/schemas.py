"""Non-secret inputs for B10's six acceptance records.

Claims are supplied by operators. A valid schema or artifact hash does not prove
that an account, signature, permission, delivery receipt or environment is real.
"""

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from bidops.screening.schemas import VersionRef

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Sha = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
CodeSha = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{40}$")]
Outcome = Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN"]
Role = Literal["viewer", "analyst", "reviewer", "auditor", "admin"]
Seconds = Annotated[StrictInt, Field(ge=0)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class Artifact(Contract):
    artifact_id: Id
    path: str = Field(min_length=1, max_length=512)
    sha256: Sha
    size_bytes: StrictInt = Field(ge=0, le=50_000_000)
    media_type: Literal[
        "application/json", "text/plain", "video/mp4", "image/png", "application/pdf"
    ]
    sensitivity: Literal["private"] = "private"

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        if (
            value.startswith("/")
            or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(ord(char) < 32 for char in value)
            or ":" in value
        ):
            raise ValueError("artifact_path_must_be_relative")
        return value


class Account(Contract):
    account_id: Id
    operator_id: Id
    subject_binding_sha256: Sha
    roles: list[Role] = Field(min_length=1, max_length=5)
    actual_human: StrictBool = False
    independent: StrictBool = False
    developer_or_tuner: StrictBool = True
    attestation_artifact_id: Id | None = None


class Inventory(Contract):
    deployment_id: Id
    origin: str | None = None
    issuer: str | None = None
    client_id: Id | None = None
    callback_uri: str | None = None
    backchannel_uri: str | None = None
    keycloak_version: Id | None = None
    role_claim: Literal["bidops_roles"] = "bidops_roles"
    mapped_roles: list[Role] = Field(default_factory=list, max_length=5)
    target_host_id: Id | None = None
    operations_owner_id: Id | None = None
    single_host_single_worker: StrictBool = False
    clock_synchronized: StrictBool = False
    accounts: list[Account] = Field(default_factory=list, max_length=20)
    profile_revisions: list[VersionRef] = Field(default_factory=list, max_length=100)
    material_revisions: list[VersionRef] = Field(default_factory=list, max_length=100)
    notice_revisions: list[VersionRef] = Field(default_factory=list, max_length=100)
    materials_authorized: StrictBool = False
    source_permission_artifact_ids: list[Id] = Field(default_factory=list, max_length=100)
    alert_receiver_id: Id | None = None
    rpo_target_seconds: Seconds | None = None
    rto_target_seconds: Seconds | None = None

    @field_validator("origin", "issuer", "callback_uri", "backchannel_uri")
    @classmethod
    def safe_https(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or len(value) > 1024
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("uat_url_requires_https_without_credentials")
        return value.rstrip("/")


class SessionObservation(Contract):
    account_id: Id
    login_at: AwareDatetime
    reauthentication_at: AwareDatetime
    logout_at: AwareDatetime
    login_succeeded: StrictBool = False
    reauthentication_succeeded: StrictBool = False
    logout_replay_rejected: StrictBool = False
    cookie_flags_verified: StrictBool = False
    csrf_rejected: StrictBool = False


class AccessObservation(Contract):
    actor_account_id: Id | None = None
    target_account_id: Id | None = None
    resource: Literal["material", "session", "job", "role", "anonymous"]
    observed_at: AwareDatetime
    expected_status: Literal[401, 403, 404]
    observed_status: StrictInt = Field(ge=100, le=599)


class IdentityChange(Contract):
    account_id: Id
    kind: Literal["disable", "role_change"]
    idp_event_at: AwareDatetime
    protected_denied_at: AwareDatetime
    new_task_denied_at: AwareDatetime
    tool_denied_at: AwareDatetime
    result_denied_at: AwareDatetime
    queued_job_id: Id
    waiting_job_id: Id
    old_session_retained: StrictBool = False


class BusinessFlow(Contract):
    owner_account_id: Id
    reviewer_account_id: Id
    profile_revision: VersionRef
    notice_revision: VersionRef
    material_revisions: list[VersionRef] = Field(min_length=1, max_length=100)
    batch_id: Id
    run_id: Id
    case_id: Id
    successor_run_id: Id
    export_artifact_id: Id
    independent_review_artifact_id: Id
    all_evidence_opened: StrictBool = False
    individually_reviewed: StrictBool = False
    successor_recomputed: StrictBool = False
    agent_result_unchanged: StrictBool = False
    reviewer_assignment_verified: StrictBool = False


class RecoveryObservation(Contract):
    job_id: Id
    interrupted_at: AwareDatetime
    restarted_at: AwareDatetime
    recovered_at: AwareDatetime
    model_calls_before: Seconds
    model_calls_after: Seconds
    same_key_same_id: StrictBool = False
    different_input_409: StrictBool = False
    native_steps_verified: StrictBool = False
    backup_artifact_id: Id
    restore_artifact_id: Id
    ledger_reconciliation_artifact_id: Id
    restore_epoch_changed: StrictBool = False
    old_session_rejected: StrictBool = False
    source_quarantine_verified: StrictBool = False
    job_quarantine_verified: StrictBool = False
    review_quarantine_verified: StrictBool = False
    explicit_reauthorization_verified: StrictBool = False
    old_decisions_ineffective: StrictBool = False
    alert_sent_at: AwareDatetime
    alert_received_at: AwareDatetime
    rpo_observed_seconds: Seconds
    # Includes restart/recovery and operator steps, not just an HTTP response.
    rto_observed_seconds: Seconds


class Prerequisite(Contract):
    ac_id: Annotated[str, StringConstraints(pattern=r"^B0[1-9]-[0-9]{2}$")]
    outcome: Outcome = "NOT_RUN"
    artifact_ids: list[Id] = Field(default_factory=list, max_length=100)


class CommonRecord(Contract):
    environment_kind: Literal["real_enterprise", "engineering_fixture", "historical"]
    deployment_id: Id
    code_sha: CodeSha
    operator_id: Id | None = None
    reviewer_id: Id | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    artifact_ids: list[Id] = Field(default_factory=list, max_length=100)
    claimed_outcome: Outcome = "NOT_RUN"

    @model_validator(mode="after")
    def ordered_times(self):
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("record_end_precedes_start")
        return self


class EnvironmentRecord(CommonRecord):
    ac_id: Literal["B10-01"]
    configuration_artifact_id: Id | None = None


class LoginRecord(CommonRecord):
    ac_id: Literal["B10-02"]
    sessions: list[SessionObservation] = Field(default_factory=list, max_length=20)
    access_checks: list[AccessObservation] = Field(default_factory=list, max_length=100)


class RevocationRecord(CommonRecord):
    ac_id: Literal["B10-03"]
    changes: list[IdentityChange] = Field(default_factory=list, max_length=20)
    idp_outage_expiry_denied: StrictBool = False
    clock_skew_bounded: StrictBool = False


class BusinessRecord(CommonRecord):
    ac_id: Literal["B10-04"]
    flow: BusinessFlow | None = None


class RecoveryRecord(CommonRecord):
    ac_id: Literal["B10-05"]
    recovery: RecoveryObservation | None = None


class ReleaseRecord(CommonRecord):
    ac_id: Literal["B10-06"]
    prerequisites: list[Prerequisite] = Field(default_factory=list, max_length=200)
    human_claimed_decision: Literal["GO", "NO_GO", "NOT_REVIEWED"] = "NOT_REVIEWED"
    release_review_artifact_id: Id | None = None
    trial_scale_artifact_id: Id | None = None
    production_e3_claimed: StrictBool = False


AcRecord = Annotated[
    EnvironmentRecord
    | LoginRecord
    | RevocationRecord
    | BusinessRecord
    | RecoveryRecord
    | ReleaseRecord,
    Field(discriminator="ac_id"),
]


class UatBundle(Contract):
    schema_version: Literal["bidops-uat-v1"] = "bidops-uat-v1"
    code_sha: CodeSha
    lock_hashes: dict[Literal["base", "durable", "attachments"], Sha]
    plan_sha256: Sha
    inventory: Inventory
    artifacts: list[Artifact] = Field(default_factory=list, max_length=500)
    records: list[AcRecord] = Field(default_factory=list, max_length=6)
