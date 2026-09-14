"""Team Pydantic schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MemoryPolicy(BaseModel):
    read_scopes: list[str] = Field(default_factory=list)
    write_scope: str = "team"


class TeamLimits(BaseModel):
    max_iterations: int = 8
    timeout_seconds: int = 120
    max_tokens: int = 20000


class TeamBase(BaseModel):
    key: str = Field(..., max_length=128, description="Unique stable key")
    name: str = Field(..., max_length=256)
    description: str | None = None
    mode: str = Field(..., description="coordinate, tasks, or collaborate")
    leader_agent_id: str | None = Field(None, description="Required for coordinate/tasks")
    member_agent_ids: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    limits: TeamLimits = Field(default_factory=TeamLimits)

    @model_validator(mode="after")
    def validate_mode_leader(self):
        if self.mode in ("coordinate", "tasks") and not self.leader_agent_id:
            raise ValueError(f"mode '{self.mode}' requires leader_agent_id")
        if self.leader_agent_id and self.leader_agent_id not in self.member_agent_ids:
            raise ValueError("leader_agent_id must be in member_agent_ids")
        if not self.member_agent_ids:
            raise ValueError("team must have at least one member")
        return self


class TeamCreate(TeamBase):
    enabled: bool = True


class TeamUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    mode: str | None = None
    leader_agent_id: str | None = None
    member_agent_ids: list[str] | None = None
    instructions: list[str] | None = None
    memory_policy: MemoryPolicy | None = None
    limits: TeamLimits | None = None
    enabled: bool | None = None
    expected_version: int = Field(..., description="Optimistic lock version")


class TeamResponse(TeamBase):
    id: str
    enabled: bool
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class TeamValidateResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
