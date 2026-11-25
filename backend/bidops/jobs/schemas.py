"""Durable business records reuse the frozen screening execution contract."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints

from bidops.models import StrictModel
from bidops.screening.schemas import ExecutionStatus, ReviewEvent, TaskExecution

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class JobModel(StrictModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class JobState(JobModel):
    execution: TaskExecution
    state_version: int = Field(ge=0, strict=True)
    progress: dict = Field(default_factory=dict)
    result_id: Identifier | None = None
    updated_at: float
    quarantined: bool = False
    purge_state: Literal["live", "purged"] = "live"


class AttemptStart(JobModel):
    run_id: Identifier
    seq: int = Field(ge=1, strict=True)
    ledger_id: str | None = None
    request_sha256: Digest


__all__ = ["AttemptStart", "ExecutionStatus", "JobState", "ReviewEvent", "TaskExecution"]
