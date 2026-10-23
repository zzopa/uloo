# AI生成
"""Unit and API tests for Long-Term Memory (P2-1)."""


import pytest

from uloo.models.memory import MemoryItem
from uloo.runtime.memory import format_memories_for_prompt


@pytest.mark.asyncio
async def test_memory_crud_api(client):
    """Verify memory creation, detail retrieval, listing, and deletion."""
    # 1. Create a fact memory
    payload = {
        "category": "fact",
        "content": "华东地区木板标准厚度为 18mm，规格 1220x2440mm，市场通常以张计价",
        "keywords": ["华东", "木板", "厚度", "规格", "张"],
    }
    create_res = await client.post("/api/v1/memories", json=payload)
    assert create_res.status_code == 201
    created = create_res.json()
    assert created["category"] == "fact"
    assert "18mm" in created["content"]
    assert "木板" in created["keywords"]
    memory_id = created["id"]

    # 2. Retrieve detail
    get_res = await client.get(f"/api/v1/memories/{memory_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == memory_id

    # 3. Create a user preference
    pref_payload = {
        "category": "preference",
        "content": "用户要求所有木板调研报告均需注明是否含税及是否含运费",
        "keywords": ["木板", "含税", "运费", "报告规范"],
    }
    await client.post("/api/v1/memories", json=pref_payload)

    # 4. List memories by category
    list_res = await client.get("/api/v1/memories?category=preference")
    assert list_res.status_code == 200
    pref_items = list_res.json()["items"]
    assert any("含税" in m["content"] for m in pref_items)

    # 5. Delete memory
    del_res = await client.delete(f"/api/v1/memories/{memory_id}")
    assert del_res.status_code == 204

    # 6. Verify 404 after deletion
    get_deleted = await client.get(f"/api/v1/memories/{memory_id}")
    assert get_deleted.status_code == 404


@pytest.mark.asyncio
async def test_memory_recall_endpoint(client):
    """Verify semantic recall endpoint matches relevant facts based on query keywords."""
    await client.post(
        "/api/v1/memories",
        json={
            "category": "fact",
            "content": "华为问界刹车踏板采用工程塑料包裹金属骨架设计，发生极端受力时有预设吸能溃缩槽",
            "keywords": ["华为", "刹车踏板", "断裂", "问界", "官方说明"],
        },
    )
    await client.post(
        "/api/v1/memories",
        json={
            "category": "fact",
            "content": "江苏常州与山东临沂是华东人造板材主要集散交易中心",
            "keywords": ["人造板", "华东", "集散中心", "临沂", "常州"],
        },
    )

    # Query matching the brake pedal topic
    recall_res = await client.get("/api/v1/memories/recall?query=调研华为刹车踏板事件")
    assert recall_res.status_code == 200
    data = recall_res.json()
    assert len(data["recalled"]) >= 1
    top_match = data["recalled"][0]
    assert "刹车踏板" in top_match["content"]
    assert "长期经验与偏好记忆" in data["formatted_context"]


def test_format_memories_for_prompt():
    """Verify prompt formatting outputs expected Markdown structure."""
    mem1 = MemoryItem(
        category="fact",
        content="板材单位通常区分元/张与元/立方米",
        keywords=["板材", "单位"],
    )
    mem2 = MemoryItem(
        category="preference",
        content="结论中必须包含官方公告链接",
        keywords=["官方", "公告"],
    )
    text = format_memories_for_prompt([mem1, mem2])
    assert "### 长期经验与偏好记忆" in text
    assert "[行业事实] 板材单位通常区分" in text
    assert "[用户偏好] 结论中必须包含" in text
