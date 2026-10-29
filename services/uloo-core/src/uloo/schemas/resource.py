# AI生成
"""Closed resource configuration schemas; no arbitrary code or secrets."""

from typing import Annotated, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, Field, model_validator


class SearchConfig(BaseModel):
    tool_type: Literal["builtin"] = "builtin"
    adapter: Literal["web-search"] = "web-search"
    query_prefix: str = Field(default="", max_length=200)
    max_results: int = Field(default=5, ge=1, le=8)
    model_config = {"extra": "forbid"}


class WikiMemoryConfig(BaseModel):
    tool_type: Literal["wiki_memory"] = "wiki_memory"
    adapter: Literal["wiki-memory"] = "wiki-memory"
    model_config = {"extra": "forbid"}


class PdfParserConfig(BaseModel):
    tool_type: Literal["pdf_parser"] = "pdf_parser"
    adapter: Literal["pdf-parser"] = "pdf-parser"
    model_config = {"extra": "forbid"}


class CodeInterpreterConfig(BaseModel):
    tool_type: Literal["code_interpreter"] = "code_interpreter"
    adapter: Literal["code-interpreter"] = "code-interpreter"
    model_config = {"extra": "forbid"}


class McpSseToolConfig(BaseModel):
    """MCP tool accessible via SSE transport."""

    tool_type: Literal["mcp_sse"] = "mcp_sse"
    endpoint_url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)
    tool_name: str = Field(min_length=1, max_length=256)
    tool_schema: dict = Field(default_factory=dict)
    tool_description: str = Field(default="", max_length=2000)
    model_config = {"extra": "forbid"}


class McpStdioToolConfig(BaseModel):
    """MCP tool accessible via stdio transport."""

    tool_type: Literal["mcp_stdio"] = "mcp_stdio"
    command: str = Field(min_length=1, max_length=512)
    args: list[str] = Field(default_factory=list, max_length=50)
    env: dict[str, str] = Field(default_factory=dict, max_length=50)
    tool_name: str = Field(min_length=1, max_length=256)
    tool_schema: dict = Field(default_factory=dict)
    tool_description: str = Field(default="", max_length=2000)
    model_config = {"extra": "forbid"}


class DifyToolConfig(BaseModel):
    """Reference a Dify-owned tool; credentials stay in Dify."""

    tool_type: Literal["dify"] = "dify"
    provider_type: Literal["plugin", "builtin", "api", "workflow", "mcp"]
    provider_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    credential_id: str | None = None
    tool_schema: dict
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_schema(self):
        if self.tool_schema.get("type") != "object":
            raise ValueError("Tool schema must describe an object")
        try:
            Draft202012Validator.check_schema(self.tool_schema)
        except SchemaError as exc:
            raise ValueError("Invalid tool JSON schema") from exc
        return self


class SkillConfig(BaseModel):
    instructions: list[Annotated[str, Field(min_length=1, max_length=10000, pattern=r"\S")]] = Field(
        min_length=1, max_length=100
    )
    tool_refs: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=30)
    scenarios: str = Field(default="", max_length=2000)
    steps: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=20)
    result_format: str = Field(default="", max_length=2000)
    quality_checks: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=20)
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


class McpDiscoverRequest(BaseModel):
    """Request to discover tools from an MCP Server."""

    transport: Literal["sse", "stdio"]
    endpoint_url: str = Field(default="", max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)
    command: str = Field(default="", max_length=512)
    args: list[str] = Field(default_factory=list, max_length=50)
    env: dict[str, str] = Field(default_factory=dict, max_length=50)
    enabled_tools: list[str] = Field(default_factory=list, max_length=100)
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_transport_fields(self) -> "McpDiscoverRequest":
        if self.transport == "sse" and not self.endpoint_url.strip():
            raise ValueError("endpoint_url is required for SSE transport")
        if self.transport == "stdio" and not self.command.strip():
            raise ValueError("command is required for stdio transport")
        return self


class McpToolInfo(BaseModel):
    """Single tool discovered from an MCP Server."""

    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2000)
    input_schema: dict = Field(default_factory=dict)
    model_config = {"extra": "forbid"}


class McpDiscoverResponse(BaseModel):
    """Response containing all tools discovered from an MCP Server."""

    tools: list[McpToolInfo]
    model_config = {"extra": "forbid"}
