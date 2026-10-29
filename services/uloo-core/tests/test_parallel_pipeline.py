# AI生成
"""Unit tests for DAG Parallel Pipeline Executor and concurrency grouping."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from uloo.config import settings
from uloo.models.agent import AgentDefinition
from uloo.models.task import TaskPlan
from uloo.models.team import TeamDefinition
from uloo.runtime.execution import AgentExecutionResult
from uloo.runtime.pipeline import (
    build_dag_stages,
    execute_dag_pipeline,
    should_execute_dag_pipeline,
)
from uloo.runtime.resources import ResourceRegistry


def test_build_dag_stages_parallel_waves():
    """Verify build_dag_stages groups independent steps into the same parallel wave."""
    steps = [
        {"id": "step-1", "title": "信息检索", "depends_on": []},
        {"id": "step-2", "title": "合规审查", "depends_on": []},
        {"id": "step-3", "title": "数据建模", "depends_on": ["step-1", "step-2"]},
        {"id": "step-4", "title": "终审统筹", "depends_on": ["step-3"]},
    ]

    stages = build_dag_stages(steps)
    assert len(stages) == 3

    # Stage 0: step-1 and step-2 run in parallel
    stage_0_ids = {s["id"] for s in stages[0]}
    assert stage_0_ids == {"step-1", "step-2"}

    # Stage 1: step-3
    stage_1_ids = {s["id"] for s in stages[1]}
    assert stage_1_ids == {"step-3"}

    # Stage 2: step-4
    stage_2_ids = {s["id"] for s in stages[2]}
    assert stage_2_ids == {"step-4"}


def test_build_dag_stages_cycle_safety():
    """Verify build_dag_stages breaks circular dependencies cleanly without deadlocks."""
    steps = [
        {"id": "step-a", "title": "A", "depends_on": ["step-b"]},
        {"id": "step-b", "title": "B", "depends_on": ["step-a"]},
    ]

    stages = build_dag_stages(steps)
    assert len(stages) >= 1
    all_scheduled = [s["id"] for st in stages for s in st]
    assert "step-a" in all_scheduled
    assert "step-b" in all_scheduled


def test_should_execute_dag_pipeline_decision():
    """Verify should_execute_dag_pipeline checks steps length and mode."""
    team_coord = TeamDefinition(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        key="team-c",
        name="Team Coord",
        mode="coordinate",
    )
    team_route = TeamDefinition(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        key="team-r",
        name="Team Route",
        mode="route",
    )

    plan_multi = TaskPlan(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        version=1,
        steps=[
            {"id": "s1", "title": "S1", "depends_on": []},
            {"id": "s2", "title": "S2", "depends_on": []},
        ],
    )
    plan_single = TaskPlan(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        version=1,
        steps=[{"id": "s1", "title": "S1", "depends_on": []}],
    )

    # Multi-step coordinate -> True
    assert should_execute_dag_pipeline(plan_multi, team_coord) is True

    # Single-step coordinate -> False
    assert should_execute_dag_pipeline(plan_single, team_coord) is False

    # Route mode -> False
    assert should_execute_dag_pipeline(plan_multi, team_route) is False

    # Disabled via settings -> False
    with patch.object(settings, "parallel_pipeline_enabled", False):
        assert should_execute_dag_pipeline(plan_multi, team_coord) is False


@pytest.mark.asyncio
async def test_execute_dag_pipeline_parallel_concurrency():
    """Verify execute_dag_pipeline executes parallel waves concurrently and aggregates outputs."""
    ws_id = str(uuid.uuid4())
    aid_1 = uuid.uuid4()
    aid_2 = uuid.uuid4()
    aid_3 = uuid.uuid4()

    agent_1 = AgentDefinition(
        id=aid_1,
        workspace_id=uuid.UUID(ws_id),
        key="agent-1",
        name="信息检索专员",
        role="网络检索",
        model_ref="default:test",
    )
    agent_2 = AgentDefinition(
        id=aid_2,
        workspace_id=uuid.UUID(ws_id),
        key="agent-2",
        name="合规核验专员",
        role="资质核验",
        model_ref="default:test",
    )
    agent_3 = AgentDefinition(
        id=aid_3,
        workspace_id=uuid.UUID(ws_id),
        key="agent-3",
        name="终审主编与研报统筹员",
        role="研报终审统筹",
        model_ref="default:test",
    )

    team = TeamDefinition(
        id=uuid.uuid4(),
        workspace_id=uuid.UUID(ws_id),
        key="team-speed",
        name="Speed Team",
        mode="coordinate",
        leader_agent_id=aid_3,
    )

    plan = TaskPlan(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        version=1,
        steps=[
            {"id": "step-1", "title": "信息检索", "assigned_agent_id": str(aid_1), "depends_on": []},
            {"id": "step-2", "title": "合规审查", "assigned_agent_id": str(aid_2), "depends_on": []},
            {"id": "step-3", "title": "终审统筹研报", "assigned_agent_id": str(aid_3), "depends_on": ["step-1", "step-2"]},
        ],
    )

    concurrent_active = 0
    max_concurrent_seen = 0

    async def mock_execute(agent, prompt, *, timeout_seconds):
        nonlocal concurrent_active, max_concurrent_seen
        concurrent_active += 1
        if concurrent_active > max_concurrent_seen:
            max_concurrent_seen = concurrent_active

        # Tiny sleep to ensure concurrent overlap can be observed
        await asyncio.sleep(0.05)
        concurrent_active -= 1

        agent_name = getattr(agent, "name", "Agent")
        return AgentExecutionResult(
            run_id=f"run-{agent_name}",
            output={"content": f"Output from {agent_name}: 详细深度研报内容测试"},
            usage={"total_tokens": 50, "input_tokens": 20, "output_tokens": 30},
            tool_summaries=[{"tool_name": "test_tool", "result_preview": "ok"}],
        )

    with patch("uloo.runtime.pipeline.execute_agent", side_effect=mock_execute), \
         patch("uloo.runtime.pipeline.build_agent", lambda m, **kw: type("DummyAgent", (), {"name": m.name, "agent_id": str(m.id)})()):

        res = await execute_dag_pipeline(
            plan=plan,
            team_definition=team,
            member_definitions=[agent_1, agent_2, agent_3],
            resources=ResourceRegistry(tools=[], skills=[], workspace_id=ws_id),
            workspace_id=ws_id,
            query_text="测试高并发多智能体协同",
            timeout_seconds=60.0,
        )

        # Stage 0 had 2 steps: step-1 and step-2. They MUST have executed concurrently!
        assert max_concurrent_seen >= 2, f"Expected concurrency >= 2, got {max_concurrent_seen}"

        # Verify all 3 members produced outputs
        assert len(res.member_results) == 3
        member_names = {m["name"] for m in res.member_results}
        assert "信息检索专员" in member_names
        assert "合规核验专员" in member_names
        assert "终审主编与研报统筹员" in member_names

        # Verify usage accumulated: 50 * 3 = 150 total tokens
        assert res.usage is not None
        assert res.usage["total_tokens"] == 150

        # Verify tool summaries collected
        assert len(res.tool_summaries) == 3
