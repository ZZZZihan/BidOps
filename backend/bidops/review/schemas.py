"""Human commands carry targets and concurrency tokens, never actor claims."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from bidops.screening.schemas import ReviewCommand

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Version = Annotated[int, Field(strict=True, ge=0)]


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class CreateCase(ReviewModel):
    run_id: Identifier
    candidate_batch_id: Identifier
    idempotency_key: Identifier
    reviewer_ids: list[Identifier] = Field(default_factory=list, max_length=100)


class CaseCommand(ReviewModel):
    expected_version: Version
    idempotency_key: Identifier
    reason: Reason


class AssignmentCommand(CaseCommand):
    reviewer_ids: list[Identifier] = Field(max_length=100)


class ItemDecision(ReviewModel):
    revision_id: Identifier
    assessment_id: Identifier
    condition_id: Identifier
    expected_version: Version
    command: ReviewCommand
    supersedes_event_id: Identifier | None = None


class SuccessorCommand(CaseCommand):
    revision_id: Identifier
    trigger_event_id: Identifier


class AttachRevision(CaseCommand):
    run_id: Identifier
