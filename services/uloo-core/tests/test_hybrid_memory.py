# AI生成
"""Unit tests for pgvector hybrid memory retrieval and embedding integration."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from uloo.config import settings
from uloo.models.memory import MemoryItem
from uloo.runtime.embeddings import get_text_embedding_async, is_embedding_configured
from uloo.runtime.memory import recall_memories, wiki_memory

from .conftest import TEST_WORKSPACE_ID


def test_is_embedding_configured_behavior():
    """Verify is_embedding_configured checks both base_url and api_key."""
    with patch.object(settings, "embedding_api_key", ""), patch.object(settings, "embedding_base_url", ""):
        assert is_embedding_configured() is False

    with patch.object(settings, "embedding_api_key", "sk-test"), patch.object(settings, "embedding_base_url", ""):
        assert is_embedding_configured() is False

    with patch.object(settings, "embedding_api_key", "sk-test"), patch.object(settings, "embedding_base_url", "https://api.siliconflow.cn/v1"):
        assert is_embedding_configured() is True


@pytest.mark.asyncio
async def test_get_text_embedding_async_mocked():
    """Verify get_text_embedding_async parses SiliconFlow/OpenAI response format."""
    from unittest.mock import MagicMock
    fake_vector = [0.1, 0.2, 0.3, 0.4]

    with patch("uloo.runtime.embeddings.is_embedding_configured", return_value=True), \
         patch("httpx.AsyncClient.post") as mock_post:

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"embedding": fake_vector}]
        }
        mock_post.return_value = mock_resp

        vec = await get_text_embedding_async("测试文本")
        assert vec == fake_vector


@pytest.mark.asyncio
async def test_get_text_embedding_async_unconfigured_or_error():
    """Verify get_text_embedding_async returns None gracefully on unconfigured or network errors."""
    with patch("uloo.runtime.embeddings.is_embedding_configured", return_value=False):
        vec = await get_text_embedding_async("测试文本")
        assert vec is None

    with patch("uloo.runtime.embeddings.is_embedding_configured", return_value=True), \
         patch("httpx.AsyncClient.post", side_effect=Exception("Connection refused")):
        vec = await get_text_embedding_async("测试文本")
        assert vec is None


@pytest.mark.asyncio
async def test_recall_memories_keyword_fallback(db_session):
    """Verify recall_memories works cleanly with keyword fallback when no embedding is configured."""
    ws_id = uuid.UUID(TEST_WORKSPACE_ID)

    # Insert test memory
    item = MemoryItem(
        workspace_id=ws_id,
        category="fact",
        content="长江航运物流枢纽位于武汉阳逻港，具备千吨级集装箱通航保障能力。",
        keywords=["长江航运", "武汉阳逻港", "物流枢纽"],
        embedding=None,
    )
    db_session.add(item)
    await db_session.commit()

    # Query with keyword match
    with patch("uloo.runtime.memory.get_text_embedding_async", return_value=None):
        results = await recall_memories(db_session, ws_id, query="阳逻港 航运物流", limit=5)
        assert len(results) >= 1
        contents = [m.content for m in results]
        assert any("武汉阳逻港" in c for c in contents)


@pytest.mark.asyncio
async def test_recall_memories_hybrid_rrf_scoring(db_session):
    """Verify recall_memories uses RRF when embedding is available."""
    ws_id = uuid.UUID(TEST_WORKSPACE_ID)

    # Insert items with simple embeddings (3-dim dummy vectors)
    item_target = MemoryItem(
        workspace_id=ws_id,
        category="fact",
        content="硅基智能多模态神经推理引擎在复杂任务分解中表现出色。",
        keywords=["硅基智能", "推理引擎"],
        embedding=[0.9, 0.1, 0.0],
    )
    item_other = MemoryItem(
        workspace_id=ws_id,
        category="fact",
        content="传统手工表格录入存在大量人工时滞问题。",
        keywords=["手工表格", "时滞"],
        embedding=[0.0, 0.1, 0.9],
    )
    db_session.add_all([item_target, item_other])
    await db_session.commit()

    # Query with vector close to target: [0.85, 0.15, 0.0]
    with patch("uloo.runtime.memory.get_text_embedding_async", return_value=[0.85, 0.15, 0.0]):
        results = await recall_memories(db_session, ws_id, query="神经推理智能引擎", limit=5)
        assert len(results) >= 1
        # Target item should be ranked first due to vector similarity
        assert "硅基智能" in results[0].content


@pytest.mark.asyncio
async def test_memory_to_dict_has_embedding(db_session):
    """Verify MemoryItem.to_dict includes has_embedding indicator."""
    ws_id = uuid.UUID(TEST_WORKSPACE_ID)
    item_with_vec = MemoryItem(
        workspace_id=ws_id,
        category="guideline",
        content="所有导出的报表表头必须统一添加制表人与审核人时间戳。",
        embedding=[0.1, 0.2, 0.3],
    )
    item_without_vec = MemoryItem(
        workspace_id=ws_id,
        category="preference",
        content="偏好优先使用中文格式化时间输出。",
        embedding=None,
    )
    db_session.add_all([item_with_vec, item_without_vec])
    await db_session.commit()

    assert item_with_vec.to_dict()["has_embedding"] is True
    assert item_without_vec.to_dict()["has_embedding"] is False
