"""MCP (Model Context Protocol) tool adapter for Agno runtime.

Discovers tools from an MCP Server and wraps them as Agno-native ``Function``
objects with proper JSON Schema, timeout control, and error handling.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from agno.tools.function import Function

from ..schemas.resource import (
    McpDiscoverRequest,
    McpDiscoverResponse,
    McpToolInfo,
)

DEFAULT_TIMEOUT_SECONDS: int = 30


def _sanitize_name(tool_name: str) -> str:
    """Build a short, identifier-safe function name from the MCP tool name."""
    safe = tool_name[:48].replace("-", "_").replace(".", "_").replace("/", "_")
    return f"mcp_{safe}_{hashlib.sha256(tool_name.encode()).hexdigest()[:8]}"


class MCPToolAdapter:
    """Discover MCP tools and create Agno-compatible ``Function`` wrappers."""

    def __init__(self, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def discover(self, request: McpDiscoverRequest) -> McpDiscoverResponse:
        """Connect to an MCP Server and return all exposed tools."""
        raw_tools = await self._list_tools(request)
        if request.enabled_tools:
            raw_tools = [t for t in raw_tools if t.name in request.enabled_tools]
        return McpDiscoverResponse(tools=raw_tools)

    async def _list_tools(self, request: McpDiscoverRequest) -> list[McpToolInfo]:
        if request.transport == "sse":
            return await self._list_sse(request.endpoint_url, request.headers)
        return await self._list_stdio(request.command, request.args, request.env)

    async def _list_sse(self, url: str, headers: dict[str, str]) -> list[McpToolInfo]:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(url, headers=headers) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(session.list_tools(), timeout=self._timeout)
            return [self._to_tool_info(t) for t in result.tools]

    async def _list_stdio(
        self, command: str, args: list[str], env: dict[str, str]
    ) -> list[McpToolInfo]:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=command, args=args, env=env or None)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(session.list_tools(), timeout=self._timeout)
            return [self._to_tool_info(t) for t in result.tools]

    @staticmethod
    def _to_tool_info(tool: Any) -> McpToolInfo:
        return McpToolInfo(
            name=tool.name,
            description=tool.description or "",
            input_schema=tool.inputSchema or {},
        )

    # ------------------------------------------------------------------
    # Tool factory
    # ------------------------------------------------------------------

    def create_tool(self, config: dict[str, Any]) -> Function:
        """Create an Agno ``Function`` wrapping a single MCP tool.

        The returned ``Function`` carries the MCP tool's JSON Schema via
        ``parameters`` and uses ``skip_entrypoint_processing=True`` so Agno
        preserves the schema instead of introspecting the async entrypoint.
        """
        tool_type = config.get("tool_type", "builtin")
        if tool_type == "mcp_sse":
            return self._create_sse_tool(config)
        if tool_type == "mcp_stdio":
            return self._create_stdio_tool(config)
        raise ValueError(f"Cannot create MCP tool for tool_type: {tool_type}")

    def _create_sse_tool(self, config: dict[str, Any]) -> Function:
        endpoint_url = config["endpoint_url"]
        headers = config.get("headers", {})
        tool_name = config["tool_name"]
        description = config.get("tool_description", "") or f"MCP tool: {tool_name}"
        tool_schema = config.get("tool_schema", {})

        async def entrypoint(**kwargs: Any) -> str:
            return await self._call_sse(endpoint_url, headers, tool_name, kwargs)

        return Function(
            name=_sanitize_name(tool_name),
            description=description,
            parameters=tool_schema or {"type": "object", "properties": {}, "required": []},
            entrypoint=entrypoint,
            skip_entrypoint_processing=True,
        )

    def _create_stdio_tool(self, config: dict[str, Any]) -> Function:
        command = config["command"]
        args = config.get("args", [])
        env = config.get("env", {})
        tool_name = config["tool_name"]
        description = config.get("tool_description", "") or f"MCP tool: {tool_name}"
        tool_schema = config.get("tool_schema", {})

        async def entrypoint(**kwargs: Any) -> str:
            return await self._call_stdio(command, args, env, tool_name, kwargs)

        return Function(
            name=_sanitize_name(tool_name),
            description=description,
            parameters=tool_schema or {"type": "object", "properties": {}, "required": []},
            entrypoint=entrypoint,
            skip_entrypoint_processing=True,
        )

    # ------------------------------------------------------------------
    # Tool invocation
    # ------------------------------------------------------------------

    async def _call_sse(
        self, url: str, headers: dict[str, str], tool_name: str, arguments: dict[str, Any]
    ) -> str:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async with sse_client(url, headers=headers) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments), timeout=self._timeout
            )
            return self._format_result(result)

    async def _call_stdio(
        self,
        command: str,
        args: list[str],
        env: dict[str, str],
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=command, args=args, env=env or None)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments), timeout=self._timeout
            )
            return self._format_result(result)

    @staticmethod
    def _format_result(result: Any) -> str:
        """Serialize an MCP ``CallToolResult`` into a JSON string for Agno."""
        content_parts: list[str] = []
        for item in getattr(result, "content", None) or []:
            text = getattr(item, "text", None)
            content_parts.append(text if text is not None else str(item))
        is_error = bool(getattr(result, "isError", False))
        return json.dumps(
            {"status": "error" if is_error else "ok", "content": content_parts},
            ensure_ascii=False,
        )
