"""Load one workspace catalog before constructing any Agent or Team."""

import hashlib
import json
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.resource import ResourceDefinition
from ..schemas.resource import SearchConfig
from .dify_tools import dify_tool
from .live_events import emit_live_event
from .mcp_adapter import MCPToolAdapter
from .models import RuntimeConfigurationError
from .tools import AgnoTool, ToolRegistry
from .web_search import web_search

BUILTIN_SEARCH = {
    "key": "web-search",
    "name": "互联网搜索",
    "description": "搜索公开网页，返回标题、链接和摘要。",
    "config": SearchConfig().model_dump(),
    "enabled": True,
    "version": 0,
}


async def definitions(db: AsyncSession, workspace_id, kind: str):
    result = await db.execute(
        select(ResourceDefinition)
        .where(ResourceDefinition.workspace_id == workspace_id, ResourceDefinition.kind == kind)
        .order_by(ResourceDefinition.key)
    )
    items = [item.to_dict() for item in result.scalars()]
    if kind == "tools" and not any(item["key"] == "web-search" for item in items):
        items.insert(0, dict(BUILTIN_SEARCH))
    return items


def configured_search(item):
    tool_key = item.get("key", "web-search")

    def search(query: str) -> str:
        prefix = item.get("config", {}).get("query_prefix", "")
        clean_query = (prefix + " " + query).strip() if prefix else query.strip()
        max_results = item.get("config", {}).get("max_results", 5)

        # 1. 触发工具调用前即时打点：在 SSE 管道中实时广播正在检索的关键词
        emit_live_event(
            event_type="tool.calling",
            source_type="tool",
            source_id=tool_key,
            payload={
                "tool_name": tool_key,
                "query": clean_query,
                "message": f"正在联网检索: {clean_query}",
            },
        )

        t0 = time.monotonic()
        res_str = web_search(clean_query, max_results)
        elapsed = round(time.monotonic() - t0, 2)

        # 2. 检索返回后即时打点：实时回传获取到的信源与条目数量
        try:
            res_obj = json.loads(res_str)
            sources = res_obj.get("results", [])
            status = res_obj.get("status", "ok")
            titles = [s.get("title", "") for s in sources[:3] if s.get("title")]
            preview = f"已检索到 {len(sources)} 条有效信源: " + " | ".join(titles) if sources else "未获取到直接网页结果"
        except Exception:
            sources = []
            status = "error"
            preview = "检索返回格式异常"

        emit_live_event(
            event_type="tool.summary",
            source_type="tool",
            source_id=tool_key,
            payload={
                "tool_name": tool_key,
                "query": clean_query,
                "count": len(sources),
                "status": status,
                "elapsed": elapsed,
                "result_preview": preview,
                "sources": sources[:5],
            },
        )
        return res_str

    search.__name__ = (
        "search_"
        + tool_key[:32].replace("-", "_").replace(".", "_")
        + "_"
        + hashlib.sha256(tool_key.encode()).hexdigest()[:8]
    )
    search.__doc__ = item.get("description") or "Search public internet pages; cite returned source URLs."
    return search


class ResourceRegistry(ToolRegistry):
    def __init__(self, tools, skills, workspace_id=None):
        super().__init__(self._build_tool_map(tools, workspace_id))
        self.skills = {item["key"]: item for item in skills}
        self.snapshot = {"tools": tools, "skills": skills}

    @staticmethod
    def _build_tool_map(tools: list[dict], workspace_id=None) -> dict[str, AgnoTool]:
        """Map each enabled tool resource key to its Agno-compatible callable."""
        mcp_adapter = MCPToolAdapter()
        registry: dict[str, AgnoTool] = {}
        for item in tools:
            if not item["enabled"]:
                continue
            tool_type = item["config"].get("tool_type", "builtin")
            if tool_type == "builtin":
                registry[item["key"]] = configured_search(item)
            elif tool_type in ("mcp_sse", "mcp_stdio"):
                registry[item["key"]] = mcp_adapter.create_tool(item["config"])
            elif tool_type == "dify":
                registry[item["key"]] = dify_tool(item, str(workspace_id))
        return registry

    def skill_config(self, refs):
        instructions, tools = [], []
        for ref in refs:
            skill = self.skills.get(ref)
            if not skill or not skill["enabled"]:
                raise RuntimeConfigurationError("SKILL_CONFIG_MISSING", f"Skill '{ref}' is missing or disabled")
            instructions.append(f"Skill: {skill['name']} ({ref})")
            instructions.extend(skill["config"]["instructions"])
            tools.extend(skill["config"]["tool_refs"])
        return instructions, tools


async def load_resources(db, workspace_id):
    return ResourceRegistry(
        await definitions(db, workspace_id, "tools"), await definitions(db, workspace_id, "skills"), workspace_id
    )
