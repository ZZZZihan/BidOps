from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from bidops.models import StrictModel

Focus = Literal[
    "overview",
    "budget",
    "deadline",
    "packages",
    "buyer",
    "publication",
    "eligibility",
    "documents",
    "status",
]


class Query(StrictModel):
    keyword: str = Field(default="", max_length=120)
    min_budget_yuan: float | None = Field(default=None, ge=0, le=1e12)
    max_budget_yuan: float | None = Field(default=None, ge=0, le=1e12)
    min_budget_inclusive: bool = True
    max_budget_inclusive: bool = True
    deadline_from: datetime | None = None
    deadline_to: datetime | None = None
    future_deadline_only: bool = False
    sort_by: Literal["project_code", "budget", "deadline", "publication"] = "project_code"
    descending: bool = False
    offset: int = Field(default=0, ge=0, le=2_147_483_647)
    limit: int = Field(
        default=20,
        ge=1,
        le=20,
        description=(
            "Maximum returned rows. Use the user's requested count: one result = 1, top N = N."
        ),
    )

    @field_validator("deadline_from", "deadline_to")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("explicit_timezone_required")
        return value


class Citation(StrictModel):
    id: str
    project_code: str | None
    field: str
    source_url: str
    snapshot_id: str
    sha256: str
    quote: str
    start: int
    end: int
    locator: str


class Card(StrictModel):
    project_code: str | None
    title: str | None
    buyer_name: str | None
    budget_yuan: str | None
    bid_deadline_at: str | None
    published_at: str | None
    document_deadline_at: str | None
    notice_kind: str | None
    package_count: int
    packages: list[dict] = Field(default_factory=list)
    eligibility: list[str] = Field(default_factory=list)
    source_url: str
    snapshot_id: str
    source_observed_at: str
    current_status: Literal["unknown"] = "unknown"
    citations: list[Citation]


class Block(StrictModel):
    id: str
    operation: str
    total: int
    items: list[Card]
    metrics: dict[str, str] = Field(default_factory=dict)
    missing_project_codes: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int | None = None
    has_more: bool = False
    truncated: bool = False


class AnswerPlan(StrictModel):
    response_mode: Literal["results", "clarify", "unavailable", "review_required", "out_of_scope"]
    result_ids: list[str] = Field(default_factory=list, max_length=4)
    focus: Focus = "overview"
    reason: Literal[
        "none",
        "missing_information",
        "ambiguous_project",
        "not_in_corpus",
        "eligibility_requires_review",
        "current_status_unknown",
        "restricted_action",
        "off_topic",
    ] = "none"


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @field_validator("message")
    @classmethod
    def nonempty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("message_must_not_be_blank")
        return value


class PromptIdentity(StrictModel):
    name: str
    version: str
    owner: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ChatResponse(StrictModel):
    run_id: str
    session_id: str
    response_mode: str
    focus: Focus
    reason: str
    message: str
    blocks: list[Block]
    trace: list[dict]
    model: str
    # Old persisted responses have unknown provenance, never today's prompt identity.
    prompt: PromptIdentity | None = None
    provider_executed: bool
    usage: dict[str, int | float]
    elapsed_ms: int
    as_of: str
    warnings: list[str]
