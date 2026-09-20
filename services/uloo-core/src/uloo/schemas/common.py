"""Shared API response schemas."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Stable error envelope returned by every ULOO Core endpoint."""

    code: str = Field(description="Stable machine-readable error code")
    message: str = Field(description="Safe human-readable error message")
    request_id: str
    trace_id: str
    details: Any | None = None


class FeatureCapabilities(BaseModel):
    """Feature flags describe implemented behavior, not planned behavior."""

    agents: bool
    teams: bool
    agent_test_runs: bool
    team_runs: bool
    streaming: bool
    memory: bool


class CapabilitiesResponse(BaseModel):
    """Capabilities currently available from this running Core version."""

    version: str
    modes: list[str]
    features: FeatureCapabilities
    models: dict[str, dict[str, str]]
    tools: list[str]
    agno_enabled: bool
