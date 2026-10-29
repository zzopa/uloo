"""Load one workspace catalog before constructing any Agent or Team."""

import hashlib
import json
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.resource import ResourceDefinition
from ..schemas.resource import SearchConfig
from .code_interpreter import code_interpreter
from .dify_tools import dify_tool
from .live_events import emit_live_event
from .mcp_adapter import MCPToolAdapter
from .memory import wiki_memory
from .models import RuntimeConfigurationError
from .pdf_parser import pdf_parser
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

BUILTIN_PDF_PARSER = {
    "key": "pdf-parser",
    "name": "PDF 文档解析",
    "description": "解析 PDF 文档，提取目录大纲、正文与结构化表格。",
    "config": {"tool_type": "pdf_parser"},
    "enabled": True,
    "version": 0,
}

BUILTIN_CODE_INTERPRETER = {
    "key": "code-interpreter",
    "name": "代码解释器",
    "description": "在沙箱中执行 Python 代码进行数据计算与分析。",
    "config": {"tool_type": "code_interpreter"},
    "enabled": True,
    "version": 0,
}

BUILTIN_WIKI_MEMORY = {
    "key": "wiki-memory",
    "name": "长期记忆中枢",
    "description": "跨任务检索历史实体事实与用户偏好 (action='query')，或主动持久化确凿实体结论与规程 (action='save')。",
    "config": {"tool_type": "wiki_memory"},
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
        except Exception:  # noqa: BLE001
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


def configured_code_interpreter(item):
    tool_key = item.get("key", "code-interpreter")

    def run_python_code(code: str) -> str:
        emit_live_event(
            event_type="tool.calling",
            source_type="tool",
            source_id=tool_key,
            payload={
                "tool_name": tool_key,
                "query": (code[:120] + "...") if len(code) > 120 else code,
                "message": f"正在运行代码解释器 ({tool_key})...",
            },
        )
        t0 = time.monotonic()
        res_str = code_interpreter(code)
        elapsed = round(time.monotonic() - t0, 2)
        try:
            res_obj = json.loads(res_str)
            status = res_obj.get("status", "ok")
            preview = res_obj.get("stdout") or str(res_obj.get("result", "")) or "代码执行完毕"
        except Exception:  # noqa: BLE001
            status = "unknown"
            preview = res_str[:200]

        emit_live_event(
            event_type="tool.summary",
            source_type="tool",
            source_id=tool_key,
            payload={
                "tool_name": tool_key,
                "query": "python_eval",
                "count": 1,
                "status": status,
                "elapsed": elapsed,
                "result_preview": str(preview)[:200],
            },
        )
        return res_str

    run_python_code.__name__ = (
        "interpreter_"
        + tool_key[:32].replace("-", "_").replace(".", "_")
        + "_"
        + hashlib.sha256(tool_key.encode()).hexdigest()[:8]
    )
    run_python_code.__doc__ = (
        item.get("description") or "Execute Python code safely in a sandbox to compute, analyze, or process data."
    )
    return run_python_code


def configured_pdf_parser(item):
    tool_key = item.get("key", "pdf-parser")

    def parse_pdf(
        file_path: str,
        page_start: int = 1,
        page_end: int | None = None,
        extract_tables: bool = True,
        query: str = "",
        mode: str = "all",
    ) -> str:
        file_name = file_path.replace("\\", "/").split("/")[-1]
        msg = f"正在解析 PDF 文档: {file_name}"
        if query:
            msg += f" (检索关键词: {query})"
        elif page_end:
            msg += f" (第 {page_start} - {page_end} 页)"
        else:
            msg += f" (从第 {page_start} 页开始)"

        emit_live_event(
            event_type="tool.calling",
            source_type="tool",
            source_id=tool_key,
            payload={
                "tool_name": tool_key,
                "file_path": file_path,
                "query": query or file_name,
                "message": msg,
            },
        )
        t0 = time.monotonic()
        res_str = pdf_parser(
            file_path=file_path,
            page_start=page_start,
            page_end=page_end,
            extract_tables=extract_tables,
            query=query,
            mode=mode,
        )
        elapsed = round(time.monotonic() - t0, 2)
        try:
            res_obj = json.loads(res_str)
            status = res_obj.get("status", "ok")
            if status == "ok":
                tables_cnt = res_obj.get("tables_count", 0)
                pages_range = res_obj.get("pages_analyzed", [])
                pages_str = f"{pages_range[0]}-{pages_range[1]}" if pages_range else "全部"
                preview = f"成功解析 {file_name} (页码 {pages_str}): 提取 {res_obj.get('total_characters', 0)} 字，{tables_cnt} 个表格"
            else:
                preview = res_obj.get("message", "解析失败")
        except Exception:  # noqa: BLE001
            status = "unknown"
            preview = res_str[:200]

        emit_live_event(
            event_type="tool.summary",
            source_type="tool",
            source_id=tool_key,
            payload={
                "tool_name": tool_key,
                "query": query or file_name,
                "status": status,
                "elapsed": elapsed,
                "result_preview": str(preview)[:200],
            },
        )
        return res_str

    parse_pdf.__name__ = (
        "pdf_parser_"
        + tool_key[:32].replace("-", "_").replace(".", "_")
        + "_"
        + hashlib.sha256(tool_key.encode()).hexdigest()[:8]
    )
    parse_pdf.__doc__ = (
        item.get("description")
        or "Parse PDF documents to extract metadata, TOC outlines, page text, and structured tables."
    )
    return parse_pdf


def configured_wiki_memory(item, workspace_id=None):
    tool_key = item.get("key", "wiki-memory")

    def wiki_memory_tool(
        action: str,
        query: str = "",
        content: str = "",
        category: str = "fact",
        keywords: str = "",
    ) -> str:
        return wiki_memory(
            action=action,
            query=query,
            content=content,
            category=category,
            keywords=keywords,
            workspace_id=workspace_id,
        )

    wiki_memory_tool.__name__ = "wiki_memory"
    wiki_memory_tool.__doc__ = (
        item.get("description")
        or "Query (action='query') or persist (action='save') entity facts, user preferences, and guidelines in LLM-Wiki long-term memory."
    )
    return wiki_memory_tool


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
            if item["key"] == "code-interpreter" or tool_type == "code_interpreter":
                registry[item["key"]] = configured_code_interpreter(item)
            elif item["key"] == "pdf-parser" or tool_type == "pdf_parser":
                registry[item["key"]] = configured_pdf_parser(item)
            elif item["key"] == "wiki-memory" or tool_type == "wiki_memory":
                registry[item["key"]] = configured_wiki_memory(item, workspace_id)
            elif tool_type == "builtin":
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
