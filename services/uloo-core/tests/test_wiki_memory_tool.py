# AI生成
"""Unit and functional tests for active wiki-memory tool and role binding."""

import uuid
import pytest
from sqlalchemy import select

from uloo.models.agent import AgentDefinition
from uloo.models.memory import MemoryItem
from uloo.runtime.factory import build_agent
from uloo.runtime.memory import recall_memories, wiki_memory
from uloo.runtime.resources import BUILTIN_WIKI_MEMORY, ResourceRegistry
from uloo.runtime.tools import ToolRegistry

from .conftest import TEST_WORKSPACE_ID


async def test_wiki_memory_tool_query_and_save(db_session):
    """Test wiki_memory save and query actions persist and recall records correctly."""
    ws_id = uuid.UUID(TEST_WORKSPACE_ID)

    # 1. Save action
    save_res = wiki_memory(
        action="save",
        content="武汉华信联创公司的法定代表人为龚杰，专注于智能政务协同系统研发与集成服务。",
        category="fact",
        keywords="武汉华信联创, 龚杰, 法定代表人",
        workspace_id=ws_id,
    )
    assert "持久化成功" in save_res
    assert "武汉华信联创" in save_res
    assert "龚杰" in save_res

    # Verify directly in DB
    saved_row = await db_session.scalar(
        select(MemoryItem).where(
            MemoryItem.workspace_id == ws_id,
            MemoryItem.content.ilike("%武汉华信联创%"),
        )
    )
    assert saved_row is not None
    assert saved_row.category == "fact"
    assert "龚杰" in saved_row.keywords

    # 2. Query action - matching
    query_res = wiki_memory(
        action="query",
        query="龚杰 法人",
        workspace_id=ws_id,
    )
    assert "检索结果" in query_res
    assert "武汉华信联创" in query_res
    assert "龚杰" in query_res

    # 3. Query action - non-matching
    nomatch_res = wiki_memory(
        action="query",
        query="火星探索航空器燃料动力配比",
        workspace_id=ws_id,
    )
    assert "未检索到" in nomatch_res

    # 4. Validation errors
    short_res = wiki_memory(action="save", content="短", workspace_id=ws_id)
    assert "保存失败" in short_res

    empty_q_res = wiki_memory(action="query", query="   ", workspace_id=ws_id)
    assert "提示: 请提供具体的查询关键词" in empty_q_res

    invalid_act = wiki_memory(action="delete_all", workspace_id=ws_id)
    assert "不支持的操作" in invalid_act


def test_tool_registry_resolves_wiki_memory():
    """Verify ToolRegistry resolves wiki-memory from server default allow-list."""
    registry = ToolRegistry()
    assert "wiki-memory" in registry.available_refs()
    resolved = registry.resolve_many(["wiki-memory"])
    assert len(resolved) == 1
    assert callable(resolved[0])


def test_resource_registry_maps_wiki_memory():
    """Verify ResourceRegistry maps custom and builtin wiki-memory configs."""
    ws_id = str(uuid.uuid4())
    tools = [
        dict(BUILTIN_WIKI_MEMORY),
    ]
    resource_reg = ResourceRegistry(tools=tools, skills=[], workspace_id=ws_id)
    assert "wiki-memory" in resource_reg.available_refs()
    resolved = resource_reg.resolve_many(["wiki-memory"])
    assert len(resolved) == 1
    fn = resolved[0]
    assert callable(fn)
    assert getattr(fn, "__name__", "") == "wiki_memory"


def test_build_agent_with_wiki_memory_instructions():
    """Verify build_agent injects LLM-Wiki cognitive alignment instructions."""
    agent_def = AgentDefinition(
        id=uuid.uuid4(),
        workspace_id=uuid.UUID(TEST_WORKSPACE_ID),
        key="deepseek-analyst-test",
        name="商业逻辑分析师",
        role="深度推演与事实穿透",
        model_ref="default:agnes-3.0-flash",
        tool_refs=["wiki-memory"],
        instructions=["严格遵守商业分析准则。"],
        enabled=True,
        version=1,
    )
    agent = build_agent(agent_def)
    assert agent is not None
    combined_instructions = "\n".join(agent.instructions)
    assert "wiki_memory" in combined_instructions
    assert "长期记忆中枢" in combined_instructions
    assert len(agent.tools) == 1
