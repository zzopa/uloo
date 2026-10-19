"""Run and observable event API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RunCreate(BaseModel):
    input: str | None = Field(default=None, min_length=1, max_length=100_000)
    expected_plan_version: int | None = Field(default=None, ge=1)
    confirm_write_replay: bool = Field(
        default=False,
        description=(
            "Idempotency gate for retry: must be true when the retried run's team "
            "binds any write-effect tool (dify, mcp_sse, mcp_stdio) or any agent "
            "with non-empty workflow_refs. Prevents accidental replay of write/submit "
            "side effects on a fresh run."
        ),
    )


class RunContinueRequest(BaseModel):
    """P0/S3-5: User supplement submitted to resume a paused run.

    When a run is in ``awaiting_input`` status (the first review round
    failed and the team's review_policy has ``require_human_input=True``),
    the caller must provide a ``supplement`` clarifying the ambiguous
    conditions.  The background executor incorporates this supplement into
    the next rework round's prompt and continues from the pause point.
    """

    supplement: str = Field(min_length=1, max_length=100_000)


class RunEventResponse(BaseModel):
    id: str
    run_id: str
    sequence: int = Field(ge=1)
    event_type: str
    source_type: str
    source_id: str | None = None
    status: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class RunResponse(BaseModel):
    id: str
    workspace_id: str
    task_id: str
    plan_id: str
    team_id: str
    status: str
    runtime_type: str
    is_mock: bool
    agno_run_id: str | None = None
    input_text: str
    configuration_snapshot: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    usage: dict[str, int] | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RunDetailResponse(RunResponse):
    events: list[RunEventResponse] = Field(default_factory=list)


class RunListResponse(BaseModel):
    items: list[RunResponse]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
