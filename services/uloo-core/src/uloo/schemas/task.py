"""Task and TaskPlan Pydantic schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..models.task import PLAN_APPROVAL_STATUSES, TASK_STATUSES

TaskStatus = Field(..., description="Task lifecycle status", pattern="|".join(TASK_STATUSES))


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=512, description="Short task title")
    query: str = Field(..., min_length=1, max_length=100_000, description="User's original request")
    team_id: str | None = Field(None, description="Optional explicit team selection")


class TaskResponse(BaseModel):
    id: str
    title: str
    query: str
    status: str
    team_id: str | None = None
    session_id: str
    current_plan_version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskPlanStep(BaseModel):
    """One step of an execution plan."""

    id: str = Field(..., description="Stable step id within the plan")
    title: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    assigned_agent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str | None = None
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"


class TaskPlanDraft(BaseModel):
    """Structured Planner output before it is persisted as a versioned plan."""

    summary: str = Field(min_length=1, max_length=4000)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    questions: list[str] = Field(default_factory=list, max_length=20)
    recommended_team_id: str
    steps: list[TaskPlanStep] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_dependencies(self) -> "TaskPlanDraft":
        pending = {step.id: set(step.depends_on) for step in self.steps}
        if len(pending) != len(self.steps):
            raise ValueError("Plan step ids must be unique")
        if any(dependency not in pending for dependencies in pending.values() for dependency in dependencies):
            raise ValueError("Plan dependencies must refer to existing steps")
        completed: set[str] = set()
        while pending:
            ready = {step_id for step_id, dependencies in pending.items() if dependencies <= completed}
            if not ready:
                raise ValueError("Plan dependencies must not contain a cycle")
            completed.update(ready)
            pending = {step_id: dependencies for step_id, dependencies in pending.items() if step_id not in ready}
        return self


class TaskPlanRequest(BaseModel):
    feedback: str | None = Field(default=None, max_length=4000)


class TaskPlanResponse(BaseModel):
    id: str
    task_id: str
    version: int
    summary: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    recommended_team_id: str | None = None
    steps: list[TaskPlanStep] = Field(default_factory=list)
    approval_status: str = Field(default="pending", pattern="|".join(PLAN_APPROVAL_STATUSES))
    rejection_reason: str | None = None
    runtime_type: str = "agno"
    is_mock: bool = False
    planner_run_id: str | None = None
    usage: dict[str, int] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskDetailResponse(TaskResponse):
    """Task plus its current plan and latest run summary."""

    plans: list[TaskPlanResponse] = Field(default_factory=list)


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
