"""Load one workspace catalog before constructing any Agent or Team."""

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.resource import ResourceDefinition
from ..schemas.resource import SearchConfig
from .dify_tools import dify_tool
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
    def search(query: str) -> str:
        return web_search((item["config"]["query_prefix"] + " " + query).strip(), item["config"]["max_results"])

    search.__name__ = (
        "search_"
        + item["key"][:32].replace("-", "_").replace(".", "_")
        + "_"
        + hashlib.sha256(item["key"].encode()).hexdigest()[:8]
    )
    search.__doc__ = item["description"] or "Search public internet pages; cite returned source URLs."
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
