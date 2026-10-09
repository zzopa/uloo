"""Closed resource configuration schemas; no arbitrary code or secrets."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SearchConfig(BaseModel):
    adapter: Literal["web-search"] = "web-search"
    query_prefix: str = Field(default="", max_length=200)
    max_results: int = Field(default=5, ge=1, le=8)
    model_config = {"extra": "forbid"}


class SkillConfig(BaseModel):
    instructions: list[Annotated[str, Field(min_length=1, max_length=10000, pattern=r"\S")]] = Field(
        min_length=1, max_length=100
    )
    tool_refs: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=30)
    model_config = {"extra": "forbid"}


class ResourceCreate(BaseModel):
    key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2000)
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    model_config = {"extra": "forbid"}


class ResourceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2000)
    config: dict
    enabled: bool
    expected_version: int = Field(ge=0)
    model_config = {"extra": "forbid"}


class ResourceResponse(ResourceCreate):
    version: int
