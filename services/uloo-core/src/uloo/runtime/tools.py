# AI生成
"""Explicit tool allow-list for Agno Agents."""

from collections.abc import Callable, Iterable
from typing import Any

from agno.tools.function import Function
from agno.tools.toolkit import Toolkit

from .code_interpreter import code_interpreter
from .models import RuntimeConfigurationError
from .pdf_parser import pdf_parser
from .web_search import web_search

AgnoTool = Toolkit | Callable[..., Any] | Function | dict[str, Any]

_DEFAULT_TOOLS: dict[str, AgnoTool] = {
    "web-search": web_search,
    "code-interpreter": code_interpreter,
    "pdf-parser": pdf_parser,
}


class ToolRegistry:
    """Resolve tool references without dynamic imports or arbitrary execution."""

    def __init__(self, tools: dict[str, AgnoTool] | None = None) -> None:
        self._tools = dict(tools) if tools is not None else dict(_DEFAULT_TOOLS)

    def resolve_many(self, refs: Iterable[str]) -> list[AgnoTool]:
        resolved: list[AgnoTool] = []
        for ref in refs:
            tool = self._tools.get(ref)
            if tool is None and ref == "code-interpreter":
                tool = _DEFAULT_TOOLS.get("code-interpreter")
            if tool is None and ref == "pdf-parser":
                tool = _DEFAULT_TOOLS.get("pdf-parser")
            if tool is None:
                raise RuntimeConfigurationError(
                    "TOOL_CONFIG_MISSING",
                    f"Tool '{ref}' is not registered in the server allow-list",
                )
            resolved.append(tool)
        return resolved

    def available_refs(self) -> list[str]:
        """Expose exactly the tools registered by this runtime."""
        return sorted(self._tools)

    def skill_config(self, refs: Iterable[str]) -> tuple[list[str], list[str]]:
        for ref in refs:
            raise RuntimeConfigurationError("SKILL_CONFIG_MISSING", f"Skill '{ref}' is not registered")
        return [], []
