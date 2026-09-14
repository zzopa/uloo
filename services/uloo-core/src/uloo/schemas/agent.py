"""Agent Pydantic schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentBase(BaseModel):
    key: str = Field(..., max_length=128, description="Unique stable key")
    name: str = Field(..., max_length=256)
    description: str | None = None
    role: str = Field(..., max_length=256)
    instructions: list[str] = Field(default_factory=list)
    model_ref: str = Field(..., description="e.g. openai-compatible:gpt-5-mini")
    tool_refs: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None


class AgentCreate(AgentBase):
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: str | None = Field(None, max_length=256)
    description: str | None = None
    role: str | None = Field(None, max_length=256)
    instructions: list[str] | None = None
    model_ref: str | None = None
    tool_refs: list[str] | None = None
    knowledge_refs: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    enabled: bool | None = None
    expected_version: int = Field(..., description="Optimistic lock version")


class AgentResponse(AgentBase):
    id: str
    enabled: bool
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AgentValidateResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


class AgentTestRunResponse(BaseModel):
    status: str
    runtime_type: str = "agno"
    is_mock: bool = False
    run_id: str | None = None
    trace_id: str | None = None
    output: dict[str, Any] | None = None
    usage: dict[str, int] | None = None
    error: str | None = None
