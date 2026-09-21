"""Team Pydantic schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..constants import TEAM_MODES, TEAM_MODES_REQUIRING_LEADER


class MemoryPolicy(BaseModel):
    read_scopes: list[Literal["project", "team", "agent"]] = Field(default_factory=list)
    write_scope: Literal["project", "team", "agent"] = "team"


class TeamLimits(BaseModel):
    max_iterations: int = Field(default=8, ge=1, le=100)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_tokens: int = Field(default=20000, ge=1, le=2_000_000)


class TeamBase(BaseModel):
    key: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="Unique stable key",
    )
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    mode: str = Field(..., description=f"One of: {', '.join(TEAM_MODES)}")
    leader_agent_id: uuid.UUID | None = Field(
        None, description=f"Required for {', '.join(sorted(TEAM_MODES_REQUIRING_LEADER))}"
    )
    member_agent_ids: list[uuid.UUID] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    limits: TeamLimits = Field(default_factory=TeamLimits)

    @model_validator(mode="after")
    def validate_mode_leader(self):
        if self.mode not in TEAM_MODES:
            raise ValueError(f"unsupported mode '{self.mode}', must be one of {list(TEAM_MODES)}")
        if self.mode in TEAM_MODES_REQUIRING_LEADER and not self.leader_agent_id:
            raise ValueError(f"mode '{self.mode}' requires leader_agent_id")
        if self.leader_agent_id and self.leader_agent_id not in self.member_agent_ids:
            raise ValueError("leader_agent_id must be in member_agent_ids")
        if not self.member_agent_ids:
            raise ValueError("team must have at least one member")
        if len(set(self.member_agent_ids)) != len(self.member_agent_ids):
            raise ValueError("member_agent_ids must not contain duplicates")
        return self


class TeamCreate(TeamBase):
    enabled: bool = True


class TeamUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=256)
    description: str | None = None
    mode: str | None = None
    leader_agent_id: uuid.UUID | None = None
    member_agent_ids: list[uuid.UUID] | None = None
    instructions: list[str] | None = None
    memory_policy: MemoryPolicy | None = None
    limits: TeamLimits | None = None
    enabled: bool | None = None
    expected_version: int = Field(..., ge=1, description="Optimistic lock version")


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
