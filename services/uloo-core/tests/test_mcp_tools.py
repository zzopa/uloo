"""MCP tool discovery, adapter, registry, and factory integration tests.

Uses ``unittest.mock.patch`` to simulate MCP Server responses for both
``tools/list`` discovery and ``tools/call`` execution without requiring a
real MCP Server process.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agno.tools.function import Function

from uloo.runtime.factory import build_agent, validate_agent_runtime_config
from uloo.runtime.mcp_adapter import MCPToolAdapter, _sanitize_name
from uloo.runtime.resources import ResourceRegistry
from uloo.schemas.resource import McpDiscoverRequest, McpDiscoverResponse, McpToolInfo

from .test_runtime_factory import agent_definition, configured_models

# ---------------------------------------------------------------------------
# Mock helpers for the MCP SDK
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Async context manager that yields a fixed value on enter."""

    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _mock_session(*, tools=None, call_result=None) -> AsyncMock:
    """Build a mock MCP ``ClientSession`` with configured responses."""
    session = AsyncMock()
    session.initialize = AsyncMock()

    list_result = MagicMock()
    list_result.tools = tools or []
    session.list_tools = AsyncMock(return_value=list_result)

    if call_result is not None:
        session.call_tool = AsyncMock(return_value=call_result)
    else:
        result = MagicMock()
        result.content = []
        result.isError = False
        session.call_tool = AsyncMock(return_value=result)
    return session


def _mock_tool(name="search_docs", description="Search docs", schema=None):
    """Build a mock MCP ``Tool`` object as returned by ``tools/list``."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = schema or {"type": "object", "properties": {}}
    return tool


def _mock_text_content(text: str = "result"):
    """Build a mock MCP ``TextContent`` object."""
    item = MagicMock()
    item.text = text
    item.type = "text"
    return item


def _sse_patches(session):
    """Return patch context managers for SSE transport + ClientSession."""
    read, write = AsyncMock(), AsyncMock()
    return (
        patch("mcp.client.sse.sse_client", return_value=_AsyncCM((read, write))),
        patch("mcp.ClientSession", return_value=_AsyncCM(session)),
    )


def _stdio_patches(session):
    """Return patch context managers for stdio transport + ClientSession."""
    read, write = AsyncMock(), AsyncMock()
    return (
        patch("mcp.client.stdio.stdio_client", return_value=_AsyncCM((read, write))),
        patch("mcp.client.stdio.StdioServerParameters", MagicMock()),
        patch("mcp.ClientSession", return_value=_AsyncCM(session)),
    )


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


async def test_mcp_sse_tool_resource_created_via_api(client):
    """Creating an MCP SSE tool via the API persists the config."""
    response = await client.post(
        "/api/v1/tools",
        json={
            "key": "mcp-sse-search",
            "name": "MCP SSE Search",
            "config": {
                "tool_type": "mcp_sse",
                "endpoint_url": "http://localhost:8080/sse",
                "headers": {"Authorization": "Bearer tok"},
                "tool_name": "search_docs",
                "tool_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
                "tool_description": "Search documentation",
            },
        },
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["config"]["tool_type"] == "mcp_sse"
    assert item["config"]["endpoint_url"] == "http://localhost:8080/sse"


async def test_mcp_stdio_tool_resource_created_via_api(client):
    """Creating an MCP stdio tool via the API persists the config."""
    response = await client.post(
        "/api/v1/tools",
        json={
            "key": "mcp-stdio-fs",
            "name": "MCP Stdio FS",
            "config": {
                "tool_type": "mcp_stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                "env": {"ROOT": "/tmp"},
                "tool_name": "read_file",
                "tool_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                "tool_description": "Read a file",
            },
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"]["tool_type"] == "mcp_stdio"


async def test_invalid_mcp_config_rejected(client):
    """Missing required fields for the declared tool_type are rejected."""
    response = await client.post(
        "/api/v1/tools",
        json={
            "key": "bad-mcp",
            "name": "Bad",
            "config": {"tool_type": "mcp_sse", "tool_name": "search"},
        },
    )
    assert response.status_code == 422


async def test_unknown_tool_type_rejected(client):
    """An unrecognised tool_type is rejected with a clear error."""
    response = await client.post(
        "/api/v1/tools",
        json={
            "key": "bad-type",
            "name": "Bad Type",
            "config": {"tool_type": "mcp_ws", "tool_name": "x"},
        },
    )
    assert response.status_code == 422


async def test_builtin_tool_still_works_with_explicit_tool_type(client):
    """Existing builtin tools can include ``tool_type: builtin`` explicitly."""
    response = await client.post(
        "/api/v1/tools",
        json={
            "key": "explicit-builtin",
            "name": "Explicit",
            "config": {"tool_type": "builtin", "query_prefix": "plywood", "max_results": 3},
        },
    )
    assert response.status_code == 201
    assert response.json()["config"]["tool_type"] == "builtin"


async def test_builtin_tool_implicit_tool_type(client):
    """Existing builtin tools without ``tool_type`` still validate."""
    response = await client.post(
        "/api/v1/tools",
        json={
            "key": "implicit-builtin",
            "name": "Implicit",
            "config": {"query_prefix": "plywood", "max_results": 3},
        },
    )
    assert response.status_code == 201
    assert response.json()["config"]["tool_type"] == "builtin"


# ---------------------------------------------------------------------------
# Discover endpoint tests (API layer)
# ---------------------------------------------------------------------------


async def test_discover_sse_via_api(client, monkeypatch):
    """The discover endpoint returns tools from a mocked MCP Server."""

    async def fake_discover(self, request):
        return McpDiscoverResponse(tools=[McpToolInfo(name="search", description="Search", input_schema={})])

    monkeypatch.setattr(MCPToolAdapter, "discover", fake_discover)

    response = await client.post(
        "/api/v1/tools/mcp/discover",
        json={"transport": "sse", "endpoint_url": "http://localhost:8080/sse"},
    )
    assert response.status_code == 200, response.text
    tools = response.json()["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "search"


async def test_discover_timeout_returns_504(client, monkeypatch):
    """A timeout during discovery produces a 504 error."""

    async def fail_discover(self, request):
        raise TimeoutError("connection timed out")

    monkeypatch.setattr(MCPToolAdapter, "discover", fail_discover)

    response = await client.post(
        "/api/v1/tools/mcp/discover",
        json={"transport": "sse", "endpoint_url": "http://slow:9999/sse"},
    )
    assert response.status_code == 504
    assert response.json()["code"] == "MCP_DISCOVERY_TIMEOUT"


async def test_discover_connection_error_returns_502(client, monkeypatch):
    """A connection failure during discovery produces a 502 error."""

    async def fail_discover(self, request):
        raise ConnectionError("refused")

    monkeypatch.setattr(MCPToolAdapter, "discover", fail_discover)

    response = await client.post(
        "/api/v1/tools/mcp/discover",
        json={"transport": "sse", "endpoint_url": "http://unreachable:9999/sse"},
    )
    assert response.status_code == 502
    assert response.json()["code"] == "MCP_DISCOVERY_FAILED"


async def test_discover_sse_missing_endpoint_url_rejected(client):
    """SSE transport without endpoint_url is a validation error."""
    response = await client.post(
        "/api/v1/tools/mcp/discover",
        json={"transport": "sse"},
    )
    assert response.status_code == 422


async def test_discover_stdio_missing_command_rejected(client):
    """Stdio transport without command is a validation error."""
    response = await client.post(
        "/api/v1/tools/mcp/discover",
        json={"transport": "stdio"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# MCPToolAdapter unit tests
# ---------------------------------------------------------------------------


async def test_discover_sse_returns_all_tools():
    """``discover`` connects via SSE and returns all tools from the server."""
    tools = [_mock_tool("search", "Search docs"), _mock_tool("read", "Read file")]
    session = _mock_session(tools=tools)
    p = _sse_patches(session)
    with p[0], p[1]:
        adapter = MCPToolAdapter()
        request = McpDiscoverRequest(transport="sse", endpoint_url="http://localhost:8080/sse")
        response = await adapter.discover(request)
    assert len(response.tools) == 2
    assert response.tools[0].name == "search"
    assert response.tools[1].name == "read"
    session.initialize.assert_awaited_once()
    session.list_tools.assert_awaited_once()


async def test_discover_sse_filters_enabled_tools():
    """``enabled_tools`` filter limits which tools are returned."""
    tools = [_mock_tool("search"), _mock_tool("read"), _mock_tool("write")]
    session = _mock_session(tools=tools)
    p = _sse_patches(session)
    with p[0], p[1]:
        adapter = MCPToolAdapter()
        request = McpDiscoverRequest(
            transport="sse",
            endpoint_url="http://localhost:8080/sse",
            enabled_tools=["search", "write"],
        )
        response = await adapter.discover(request)
    assert {t.name for t in response.tools} == {"search", "write"}


async def test_discover_stdio_returns_tools():
    """``discover`` connects via stdio and returns tools."""
    tools = [_mock_tool("list_files", "List files")]
    session = _mock_session(tools=tools)
    p = _stdio_patches(session)
    with p[0], p[1], p[2]:
        adapter = MCPToolAdapter()
        request = McpDiscoverRequest(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"],
        )
        response = await adapter.discover(request)
    assert len(response.tools) == 1
    assert response.tools[0].name == "list_files"


# ---------------------------------------------------------------------------
# create_tool tests
# ---------------------------------------------------------------------------


def test_create_sse_tool_produces_agno_function():
    """``create_tool`` returns a ``Function`` with the MCP tool's schema."""
    config = {
        "tool_type": "mcp_sse",
        "endpoint_url": "http://localhost:8080/sse",
        "headers": {"Authorization": "Bearer tok"},
        "tool_name": "search_docs",
        "tool_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        "tool_description": "Search documentation",
    }
    fn = MCPToolAdapter().create_tool(config)
    assert isinstance(fn, Function)
    assert fn.name.startswith("mcp_")
    assert fn.description == "Search documentation"
    assert fn.parameters == config["tool_schema"]
    assert fn.skip_entrypoint_processing is True
    assert fn.entrypoint is not None


def test_create_stdio_tool_produces_agno_function():
    """``create_tool`` for stdio transport produces a ``Function``."""
    config = {
        "tool_type": "mcp_stdio",
        "command": "npx",
        "args": ["server"],
        "env": {},
        "tool_name": "read_file",
        "tool_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "tool_description": "Read a file",
    }
    fn = MCPToolAdapter().create_tool(config)
    assert isinstance(fn, Function)
    assert fn.description == "Read a file"
    assert fn.parameters == config["tool_schema"]


def test_create_tool_rejects_builtin():
    """``create_tool`` raises for non-MCP tool types."""
    with pytest.raises(ValueError, match="tool_type"):
        MCPToolAdapter().create_tool({"tool_type": "builtin"})


def test_create_tool_uses_default_schema_when_empty():
    """Empty ``tool_schema`` defaults to a minimal object schema."""
    config = {
        "tool_type": "mcp_sse",
        "endpoint_url": "http://localhost:8080/sse",
        "tool_name": "noop",
        "tool_schema": {},
    }
    fn = MCPToolAdapter().create_tool(config)
    assert fn.parameters == {"type": "object", "properties": {}, "required": []}


def test_sanitize_name_is_stable_and_safe():
    """The name sanitizer produces deterministic, identifier-safe names."""
    name1 = _sanitize_name("search-docs")
    name2 = _sanitize_name("search-docs")
    assert name1 == name2
    assert name1.startswith("mcp_")
    assert "-" not in name1
    assert "." not in name1


# ---------------------------------------------------------------------------
# MCP tool callable execution tests
# ---------------------------------------------------------------------------


async def test_sse_tool_callable_invokes_mcp_session():
    """The entrypoint callable connects to the MCP Server and calls ``tools/call``."""
    call_result = MagicMock()
    call_result.content = [_mock_text_content("found: 3 items")]
    call_result.isError = False
    session = _mock_session(call_result=call_result)

    config = {
        "tool_type": "mcp_sse",
        "endpoint_url": "http://localhost:8080/sse",
        "tool_name": "search_docs",
        "tool_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "tool_description": "Search docs",
    }
    fn = MCPToolAdapter().create_tool(config)
    p = _sse_patches(session)
    with p[0], p[1]:
        result = await fn.entrypoint(q="plywood")
    session.call_tool.assert_awaited_once_with("search_docs", {"q": "plywood"})
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert "found: 3 items" in parsed["content"]


async def test_sse_tool_callable_formats_error_result():
    """When MCP returns ``isError=True``, the callable reports status=error."""
    call_result = MagicMock()
    call_result.content = [_mock_text_content("invalid arguments")]
    call_result.isError = True
    session = _mock_session(call_result=call_result)

    config = {
        "tool_type": "mcp_sse",
        "endpoint_url": "http://localhost:8080/sse",
        "tool_name": "search_docs",
        "tool_schema": {},
        "tool_description": "Search",
    }
    fn = MCPToolAdapter().create_tool(config)
    p = _sse_patches(session)
    with p[0], p[1]:
        result = await fn.entrypoint(q="test")
    parsed = json.loads(result)
    assert parsed["status"] == "error"


async def test_stdio_tool_callable_invokes_mcp_session():
    """The stdio entrypoint callable connects and calls ``tools/call``."""
    call_result = MagicMock()
    call_result.content = [_mock_text_content("file contents")]
    call_result.isError = False
    session = _mock_session(call_result=call_result)

    config = {
        "tool_type": "mcp_stdio",
        "command": "npx",
        "args": ["server"],
        "env": {"ROOT": "/tmp"},
        "tool_name": "read_file",
        "tool_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "tool_description": "Read a file",
    }
    fn = MCPToolAdapter().create_tool(config)
    p = _stdio_patches(session)
    with p[0], p[1], p[2]:
        result = await fn.entrypoint(path="/tmp/test.txt")
    session.call_tool.assert_awaited_once_with("read_file", {"path": "/tmp/test.txt"})
    assert "file contents" in json.loads(result)["content"]


# ---------------------------------------------------------------------------
# ResourceRegistry with MCP tools
# ---------------------------------------------------------------------------


def _mcp_sse_tool_item(key="mcp-search", tool_name="search_docs"):
    return {
        "key": key,
        "name": f"MCP {tool_name}",
        "description": f"MCP tool: {tool_name}",
        "config": {
            "tool_type": "mcp_sse",
            "endpoint_url": "http://localhost:8080/sse",
            "headers": {},
            "tool_name": tool_name,
            "tool_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            "tool_description": "Search documentation",
        },
        "enabled": True,
        "version": 1,
    }


def _builtin_tool_item(key="web-search"):
    return {
        "key": key,
        "name": "Web Search",
        "description": "Search the internet",
        "config": {"tool_type": "builtin", "adapter": "web-search", "query_prefix": "", "max_results": 5},
        "enabled": True,
        "version": 0,
    }


def test_resource_registry_contains_mcp_tool():
    """``ResourceRegistry`` creates an Agno Function for MCP tool items."""
    registry = ResourceRegistry([_mcp_sse_tool_item()], [])
    assert "mcp-search" in registry.available_refs()
    tools = registry.resolve_many(["mcp-search"])
    assert len(tools) == 1
    assert isinstance(tools[0], Function)


def test_resource_registry_mixed_builtin_and_mcp():
    """The registry handles both builtin and MCP tools simultaneously."""
    tools = [_builtin_tool_item(), _mcp_sse_tool_item()]
    registry = ResourceRegistry(tools, [])
    refs = registry.available_refs()
    assert "web-search" in refs
    assert "mcp-search" in refs
    resolved = registry.resolve_many(["web-search", "mcp-search"])
    assert len(resolved) == 2


def test_resource_registry_skips_disabled_mcp_tool():
    """Disabled MCP tools are not included in the registry."""
    item = _mcp_sse_tool_item()
    item["enabled"] = False
    registry = ResourceRegistry([item], [])
    assert "mcp-search" not in registry.available_refs()


# ---------------------------------------------------------------------------
# Factory integration: Agent with MCP tools
# ---------------------------------------------------------------------------


def test_agent_with_mcp_tool_builds_successfully():
    """An Agent with ``tool_refs`` pointing to an MCP tool builds correctly."""
    registry = ResourceRegistry([_mcp_sse_tool_item()], [])
    definition = agent_definition()
    definition.tool_refs = ["mcp-search"]

    agent = build_agent(definition, models=configured_models(), tools=registry)
    assert len(agent.tools) == 1
    assert isinstance(agent.tools[0], Function)


def test_validate_agent_config_with_mcp_tool():
    """``validate_agent_runtime_config`` resolves MCP tool refs."""
    registry = ResourceRegistry([_mcp_sse_tool_item()], [])
    definition = agent_definition()
    definition.tool_refs = ["mcp-search"]

    _model, resolved_tools = validate_agent_runtime_config(
        definition, models=configured_models(), tools=registry
    )
    assert len(resolved_tools) == 1
    assert isinstance(resolved_tools[0], Function)


def test_dedup_mcp_tool_referenced_by_both_agent_and_skill():
    """When an Agent and a Skill both reference the same MCP tool, it resolves once."""
    mcp_tool = _mcp_sse_tool_item()
    skill = {
        "key": "research-skill",
        "name": "Research",
        "description": "",
        "config": {"instructions": ["Use the search tool."], "tool_refs": ["mcp-search"]},
        "enabled": True,
        "version": 1,
    }
    registry = ResourceRegistry([mcp_tool], [skill])

    definition = agent_definition()
    definition.tool_refs = ["mcp-search"]
    definition.skill_refs = ["research-skill"]

    _, resolved_tools = validate_agent_runtime_config(
        definition, models=configured_models(), tools=registry
    )
    assert len(resolved_tools) == 1, "MCP tool must be deduplicated when referenced by both Agent and Skill"


def test_dedup_mcp_and_builtin_tools_with_skill():
    """Mixed builtin + MCP tools referenced via Agent and Skill deduplicate correctly."""
    builtin = _builtin_tool_item()
    mcp = _mcp_sse_tool_item()
    skill = {
        "key": "mixed-skill",
        "name": "Mixed",
        "description": "",
        "config": {"instructions": ["Use both tools."], "tool_refs": ["web-search", "mcp-search"]},
        "enabled": True,
        "version": 1,
    }
    registry = ResourceRegistry([builtin, mcp], [skill])

    definition = agent_definition()
    definition.tool_refs = ["mcp-search"]
    definition.skill_refs = ["mixed-skill"]

    _, resolved_tools = validate_agent_runtime_config(
        definition, models=configured_models(), tools=registry
    )
    assert len(resolved_tools) == 2


# ---------------------------------------------------------------------------
# Configuration snapshot compatibility
# ---------------------------------------------------------------------------


async def test_mcp_tool_appears_in_run_configuration_snapshot(client, monkeypatch):
    """MCP tools saved to the workspace are included in Run configuration snapshots."""
    from uloo.runtime.execution import AgentExecutionResult

    from .test_runs import prepare_approved_task

    await client.post(
        "/api/v1/tools",
        json={
            "key": "snapshot-mcp",
            "name": "Snapshot MCP",
            "config": {
                "tool_type": "mcp_sse",
                "endpoint_url": "http://localhost:8080/sse",
                "tool_name": "search",
                "tool_schema": {"type": "object", "properties": {}},
                "tool_description": "Search",
            },
        },
    )
    task, team = await prepare_approved_task(client, monkeypatch)
    agent_id = team["member_agent_ids"][0]
    assert (
        await client.patch(
            f"/api/v1/agents/{agent_id}",
            json={"expected_version": 1, "tool_refs": ["snapshot-mcp"]},
        )
    ).status_code == 200

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="snap",
            output={"content": "ok"},
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            member_results=[
                {
                    "agent_id": agent_id,
                    "name": "Test Agent",
                    "run_id": "member-snap",
                    "output": {"content": "ok"},
                    "usage": {"total_tokens": 1},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text
    snapshot_tools = response.json()["configuration_snapshot"]["resources"]["tools"]
    assert any(t["key"] == "snapshot-mcp" for t in snapshot_tools)
