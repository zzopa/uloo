# AI生成
"""End-to-End Functional Tests for LLM-Wiki Evolving Memory & Multi-turn Deduction."""

import json
import uuid
import pytest
from unittest.mock import AsyncMock

from uloo.runtime.execution import AgentExecutionResult
from uloo.runtime.memory import DistilledMemoriesDraft, DistilledMemoryItem
from tests.test_runs import prepare_approved_task
from tests.test_tasks import create_task


@pytest.mark.asyncio
async def test_end_to_end_llm_wiki_distillation_and_event(client, monkeypatch):
    """Functional Test 1: Task run -> successful delivery -> auto memory distillation -> memory.consolidated event."""
    task, team = await prepare_approved_task(client, monkeypatch)

    deliverable_text = (
        "# 美国 EB-5 投资移民政策调研成果报告\n\n"
        "1. 核心投资门槛：根据 2022 年《EB-5 改革与诚信法案》（RIA），"
        "目标就业区（TEA）项目的最低投资金额为 80 万美元，非 TEA 地区投资额为 105 万美元。\n"
        "2. 签证预留配额：乡村地区预留 20%，高失业地区预留 10%，目前乡村项目暂无排期。"
    )

    async def fake_team_run(_team, prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-wiki-1",
            output={"content": deliverable_text},
            usage={"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Market Researcher",
                    "run_id": "member-run-1",
                    "output": {"content": deliverable_text},
                    "usage": {"total_tokens": 200},
                }
            ],
        )

    # Mock the LLM distiller agent
    mock_distiller_agent = AsyncMock()
    mock_distiller_agent.arun.return_value = AsyncMock(
        content=DistilledMemoriesDraft(
            memories=[
                DistilledMemoryItem(
                    category="fact",
                    content="EB-5 目标就业区(TEA)最低投资额为 80 万美元，乡村地区享 20% 签证配额且暂无排期",
                    keywords=["EB-5", "TEA", "80万美元", "乡村项目", "无排期"],
                ),
                DistilledMemoryItem(
                    category="preference",
                    content="客户优先关注无排期乡村投资移民项目及绿卡获批周期",
                    keywords=["投资移民", "乡村优先", "获批周期"],
                ),
            ]
        )
    )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)
    monkeypatch.setattr("uloo.runtime.memory.Agent", lambda **kwargs: mock_distiller_agent)

    # 1. Execute the run
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "succeeded"

    # 2. Check that memory.consolidated event exists in the run
    event_types = [e["event_type"] for e in run["events"]]
    assert "memory.consolidated" in event_types, f"Events were: {event_types}"

    distill_event = next(e for e in run["events"] if e["event_type"] == "memory.consolidated")
    assert distill_event["payload"]["count"] == 2
    assert len(distill_event["payload"]["items"]) == 2
    assert "提炼萃取 2 条 LLM-Wiki 实体记忆" in distill_event["payload"]["message"]

    # 3. Verify event is available via GET /api/v1/runs/{run_id}/events
    events_res = await client.get(f"/api/v1/runs/{run['id']}/events")
    assert events_res.status_code == 200
    events_list = events_res.json()
    assert any(e["event_type"] == "memory.consolidated" for e in events_list)

    # 4. Verify memories are persisted and queryable via GET /api/v1/memories
    memories_res = await client.get("/api/v1/memories?limit=10")
    assert memories_res.status_code == 200
    memories_data = memories_res.json()
    assert memories_data["total"] >= 2

    wiki_entries = [m for m in memories_data["items"] if m.get("source_run_id") == run["id"]]
    assert len(wiki_entries) == 2
    assert any("80 万美元" in m["content"] for m in wiki_entries)
    assert any("客户优先关注" in m["content"] for m in wiki_entries)


@pytest.mark.asyncio
async def test_multi_turn_followup_and_memory_recall(client, monkeypatch):
    """Functional Test 2: In-canvas follow-up query inherits previous deliverable, recalls memories and continues deduction."""
    team_res = await prepare_approved_task(client, monkeypatch)
    parent_task, team = team_res

    # 1. Seed an auto-distilled wiki memory from parent task
    parent_run_id = str(uuid.uuid4())
    await client.post(
        "/api/v1/memories",
        json={
            "category": "fact",
            "content": "EB-5 投资款合法来源需出具过去 5 年税单与资产交易完税凭证",
            "keywords": ["EB-5", "资金来源", "税单", "完税凭证", "合规"],
            "source_run_id": parent_run_id,
        },
    )

    # 2. Simulate in-canvas follow-up query
    followup_query = "【基于前序成果的深入追问】请详细分析 EB-5 资金来源证明的具体材料清单与个税要求"

    # 3. Test semantic recall endpoint matches the seed memory
    recall_res = await client.get(f"/api/v1/memories/recall?query={followup_query}")
    assert recall_res.status_code == 200
    recall_data = recall_res.json()
    assert len(recall_data["recalled"]) >= 1
    assert "完税凭证" in recall_data["recalled"][0]["content"]
    assert "### 长期经验与偏好记忆" in recall_data["formatted_context"]

    # 4. Create and approve the follow-up task
    followup_task = await create_task(client, team_id=team["id"], title="追问: 资金来源清单", query=followup_query)

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="followup-plan-run",
            output={
                "summary": "推进资金来源清单推演",
                "recommended_team_id": team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "梳理税务与银行流水材料",
                        "assigned_agent_id": team["member_agent_ids"][0],
                        "expected_output": "材料清单",
                    }
                ],
            },
            usage=None,
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)

    plan_res = await client.post(f"/api/v1/tasks/{followup_task['id']}/plan", json={})
    assert plan_res.status_code == 201
    appr_res = await client.post(f"/api/v1/tasks/{followup_task['id']}/approve")
    assert appr_res.status_code == 200

    # 5. Mock follow-up run execution and new distillation
    followup_deliverable = "EB-5 资金来源材料清单汇总：1. 过去五年个人所得税完税证明；2. 房产抵押借款合同与评估报告..."
    async def fake_followup_run(_team, prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="followup-run-2",
            output={"content": followup_deliverable},
            usage={"input_tokens": 150, "output_tokens": 250, "total_tokens": 400},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Legal Specialist",
                    "run_id": "member-run-2",
                    "output": {"content": followup_deliverable},
                    "usage": {"total_tokens": 250},
                }
            ],
        )

    mock_distiller_agent = AsyncMock()
    mock_distiller_agent.arun.return_value = AsyncMock(
        content=DistilledMemoriesDraft(
            memories=[
                DistilledMemoryItem(
                    category="fact",
                    content="房产抵押用于 EB-5 投资款必须证明购房款初始来源及抵押机构合规资质",
                    keywords=["房产抵押", "EB-5", "初始资金", "合规资质"],
                )
            ]
        )
    )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_followup_run)
    monkeypatch.setattr("uloo.runtime.memory.Agent", lambda **kwargs: mock_distiller_agent)

    run_res = await client.post(f"/api/v1/tasks/{followup_task['id']}/runs", json={})
    assert run_res.status_code == 201
    followup_run = run_res.json()
    assert followup_run["status"] == "succeeded"

    # Verify second-generation wiki memory was created and linked to followup_run
    memories_res = await client.get("/api/v1/memories?limit=20")
    assert memories_res.status_code == 200
    all_memories = memories_res.json()["items"]
    followup_mem = next((m for m in all_memories if m.get("source_run_id") == followup_run["id"]), None)
    assert followup_mem is not None
    assert "房产抵押" in followup_mem["content"]


@pytest.mark.asyncio
async def test_memory_cortex_manual_vs_wiki_distilled(client):
    """Functional Test 3: Distinguish manual vs auto-distilled wiki entries, filtering and deletion."""
    # 1. Manual entry (no source_run_id)
    manual_res = await client.post(
        "/api/v1/memories",
        json={
            "category": "preference",
            "content": "系统架构报告格式必须遵循 C4 Model 标准规范",
            "keywords": ["架构", "C4Model", "规范"],
        },
    )
    assert manual_res.status_code == 201
    manual_id = manual_res.json()["id"]

    # 2. Distilled wiki entry (with source_run_id)
    dummy_run_id = str(uuid.uuid4())
    distilled_res = await client.post(
        "/api/v1/memories",
        json={
            "category": "fact",
            "content": "Agno 多智能体框架默认采用事件总线传递分步产物",
            "keywords": ["Agno", "事件总线", "多智能体"],
            "source_run_id": dummy_run_id,
        },
    )
    assert distilled_res.status_code == 201
    distilled_id = distilled_res.json()["id"]

    # 3. Retrieve detail and verify source_run_id distinction
    m_detail = (await client.get(f"/api/v1/memories/{manual_id}")).json()
    d_detail = (await client.get(f"/api/v1/memories/{distilled_id}")).json()

    assert m_detail.get("source_run_id") is None
    assert d_detail.get("source_run_id") == dummy_run_id

    # 4. Search by keyword
    search_res = await client.get("/api/v1/memories?query=C4")
    assert search_res.status_code == 200
    matched = search_res.json()["items"]
    assert any("C4 Model" in m["content"] for m in matched)

    # 5. Clean up
    await client.delete(f"/api/v1/memories/{manual_id}")
    await client.delete(f"/api/v1/memories/{distilled_id}")
