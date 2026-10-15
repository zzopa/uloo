# AI生成
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


class ModelProviderInfo(BaseModel):
    """Selectability info for one configured model provider.

    `models` is the list of model ids the UI may offer for this provider.
    Credentials are never included; the UI only needs names to build a picker.
    """

    base_url: str
    models: list[str] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    """Capabilities currently available from this running Core version."""

    version: str
    modes: list[str]
    features: FeatureCapabilities
    models: dict[str, ModelProviderInfo]
    tools: list[str]
    agno_enabled: bool
    provider_aliases: dict[str, str] = Field(default_factory=dict)


class Page[ItemT](BaseModel):
    """Stable offset-based pagination envelope."""

    items: list[ItemT]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
