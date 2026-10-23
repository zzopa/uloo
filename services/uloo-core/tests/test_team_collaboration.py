# AI生成
"""Tests for S3 team collaboration fields: review_policy, completion, handoff_context, total_token_budget."""

import uuid

import pytest

from uloo.runtime.execution import AgentExecutionResult
from uloo.schemas.team import (
    CompletionConditions,
    ReviewPolicy,
    TeamCreate,
    TeamLimits,
    TeamUpdate,
)

VALID_AGENT_1 = uuid.uuid4()
VALID_AGENT_2 = uuid.uuid4()


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _create_agent(client, name: str) -> dict:
    resp = await client.post(
        "/api/v1/agents",
        json={
            "key": unique("agent"),
            "name": name,
            "role": "Researcher",
            "model_ref": "openai-compatible:test-model",
            "enabled": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestReviewPolicy:
    def test_defaults(self):
        rp = ReviewPolicy()
        assert rp.max_rounds == 3
        assert rp.require_unanimous is False
        assert rp.criteria == []

    def test_full_config(self):
        rp = ReviewPolicy(max_rounds=5, require_unanimous=True, criteria=["accuracy", "completeness"])
        assert rp.max_rounds == 5
        assert rp.require_unanimous is True
        assert rp.criteria == ["accuracy", "completeness"]

    def test_max_rounds_bounds(self):
        with pytest.raises(ValueError):
            ReviewPolicy(max_rounds=0)
        with pytest.raises(ValueError):
            ReviewPolicy(max_rounds=11)

    def test_criteria_max_20(self):
        with pytest.raises(ValueError):
            ReviewPolicy(criteria=[f"c{i}" for i in range(21)])

    def test_criteria_exactly_20_ok(self):
        rp = ReviewPolicy(criteria=[f"c{i}" for i in range(20)])
        assert len(rp.criteria) == 20


class TestCompletionConditions:
    def test_defaults(self):
        cc = CompletionConditions()
        assert cc.all_members_executed is True
        assert cc.no_unresolved_errors is True
        assert cc.custom == ""

    def test_full_config(self):
        cc = CompletionConditions(all_members_executed=False, no_unresolved_errors=False, custom="All reports submitted")
        assert cc.all_members_executed is False
        assert cc.no_unresolved_errors is False
        assert cc.custom == "All reports submitted"

    def test_custom_max_2000(self):
        with pytest.raises(ValueError):
            CompletionConditions(custom="x" * 2001)

    def test_custom_exactly_2000_ok(self):
        cc = CompletionConditions(custom="x" * 2000)
        assert len(cc.custom) == 2000


class TestTeamLimitsTokenBudget:
    def test_total_token_budget_default(self):
        limits = TeamLimits()
        assert limits.total_token_budget == 200000

    def test_total_token_budget_set(self):
        limits = TeamLimits(total_token_budget=500000)
        assert limits.total_token_budget == 500000

    def test_total_token_budget_min_1(self):
        with pytest.raises(ValueError):
            TeamLimits(total_token_budget=0)

    def test_total_token_budget_max(self):
        with pytest.raises(ValueError):
            TeamLimits(total_token_budget=10_000_001)


class TestTeamCreateWithCollaboration:
    def test_create_with_collaboration_fields(self):
        team = TeamCreate(
            key="research-team",
            name="Research Team",
            mode="coordinate",
            member_agent_ids=[VALID_AGENT_1, VALID_AGENT_2],
            leader_agent_id=VALID_AGENT_1,
            review_policy=ReviewPolicy(max_rounds=5, require_unanimous=True, criteria=["accuracy"]),
            completion=CompletionConditions(custom="All deliverables reviewed"),
            handoff_context="Previous session context: market analysis completed",
        )
        assert team.review_policy.max_rounds == 5
        assert team.review_policy.require_unanimous is True
        assert team.completion.custom == "All deliverables reviewed"
        assert team.handoff_context == "Previous session context: market analysis completed"

    def test_create_defaults(self):
        team = TeamCreate(
            key="simple-team",
            name="Simple Team",
            mode="coordinate",
            member_agent_ids=[VALID_AGENT_1],
            leader_agent_id=VALID_AGENT_1,
        )
        assert team.review_policy.max_rounds == 3
        assert team.completion.all_members_executed is True
        assert team.handoff_context == ""

    def test_handoff_context_max_2000(self):
        with pytest.raises(ValueError):
            TeamCreate(
                key="t",
                name="T",
                mode="coordinate",
                member_agent_ids=[VALID_AGENT_1],
                leader_agent_id=VALID_AGENT_1,
                handoff_context="x" * 2001,
            )


class TestTeamUpdateWithCollaboration:
    def test_update_partial_collaboration(self):
        update = TeamUpdate(
            expected_version=1,
            review_policy=ReviewPolicy(max_rounds=7),
        )
        assert update.review_policy is not None
        assert update.review_policy.max_rounds == 7
        assert update.completion is None
        assert update.handoff_context is None

    def test_update_all_collaboration_fields(self):
        update = TeamUpdate(
            expected_version=2,
            review_policy=ReviewPolicy(max_rounds=2, require_unanimous=True),
            completion=CompletionConditions(all_members_executed=False),
            handoff_context="Updated context",
        )
        assert update.review_policy.max_rounds == 2
        assert update.completion.all_members_executed is False
        assert update.handoff_context == "Updated context"


# ---------------------------------------------------------------------------
# S3: End-to-end API & DB execution tests for Team Collaboration & Budgets
# ---------------------------------------------------------------------------


async def test_team_run_exceeds_token_budget_terminates_with_budget_error(client, monkeypatch):
    """S3: When total tokens exceed limits.total_token_budget, the run terminates
    in failed status with a clear budget exceeded message."""
    agent = await _create_agent(client, "Budget Tester")
    team_resp = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("budget-team"),
            "name": "Budget Team",
            "mode": "coordinate",
            "leader_agent_id": agent["id"],
            "member_agent_ids": [agent["id"]],
            "limits": {
                "max_iterations": 5,
                "timeout_seconds": 60,
                "max_tokens": 1000,
                "total_token_budget": 50,
            },
            "enabled": True,
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    team = team_resp.json()

    task_resp = await client.post(
        "/api/v1/tasks",
        json={"title": "Test Budget Task", "query": "Run a test query", "team_id": team["id"]},
    )
    assert task_resp.status_code == 201, task_resp.text
    task = task_resp.json()

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="plan-run",
            output={
                "summary": "Step 1",
                "recommended_team_id": team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "work",
                        "assigned_agent_id": agent["id"],
                        "expected_output": "data",
                    }
                ],
            },
            usage=None,
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)

    plan_resp = await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    assert plan_resp.status_code == 201, plan_resp.text
    app_resp = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert app_resp.status_code == 200, app_resp.text

    async def overbudget_execution(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="overbudget-run",
            output={"content": "Completed work with verified sources [1]."},
            usage={"total_tokens": 120},
            member_results=[
                {
                    "agent_id": agent["id"],
                    "name": "Budget Tester",
                    "run_id": "member-run-1",
                    "output": {"content": "Data with sources [1]."},
                    "usage": {"total_tokens": 120},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", overbudget_execution)

    run_resp = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert run_resp.status_code == 502, run_resp.text
    run_id = run_resp.json()["details"]["run_id"]

    saved_run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert saved_run["status"] == "failed"
    assert saved_run["error_code"] == "MODEL_ERROR"
    assert "Team exceeded total token budget: 120 > 50" in saved_run["error_message"]
    assert saved_run["events"][-1]["event_type"] == "run.failed"


async def test_team_coordinate_mode_missing_assigned_member_fails(client, monkeypatch):
    """S3: In coordinate mode, if an assigned member does not execute, execution fails."""
    agent1 = await _create_agent(client, "Worker 1")
    agent2 = await _create_agent(client, "Worker 2")
    team_resp = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("coord-team"),
            "name": "Coord Team",
            "mode": "coordinate",
            "leader_agent_id": agent1["id"],
            "member_agent_ids": [agent1["id"], agent2["id"]],
            "enabled": True,
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    team = team_resp.json()

    task_resp = await client.post(
        "/api/v1/tasks",
        json={"title": "Multi-Agent Task", "query": "Coordinate two agents", "team_id": team["id"]},
    )
    assert task_resp.status_code == 201, task_resp.text
    task = task_resp.json()

    async def multi_member_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="multi-plan",
            output={
                "summary": "Two steps",
                "recommended_team_id": team["id"],
                "steps": [
                    {"id": "step-1", "title": "step1", "assigned_agent_id": agent1["id"], "expected_output": "out1"},
                    {"id": "step-2", "title": "step2", "assigned_agent_id": agent2["id"], "expected_output": "out2"},
                ],
            },
            usage=None,
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", multi_member_plan)

    plan_resp = await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    assert plan_resp.status_code == 201, plan_resp.text
    app_resp = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert app_resp.status_code == 200, app_resp.text

    # Member 2 is missing from execution results!
    async def partial_execution(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="partial-run",
            output={"content": "Only worker 1 finished."},
            usage={"total_tokens": 30},
            member_results=[
                {
                    "agent_id": agent1["id"],
                    "name": "Worker 1",
                    "run_id": "w1-run",
                    "output": {"content": "out1"},
                    "usage": {"total_tokens": 30},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", partial_execution)

    run_resp = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert run_resp.status_code == 502, run_resp.text
    run_id = run_resp.json()["details"]["run_id"]

    saved_run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert saved_run["status"] == "failed"
    assert "Team did not execute the approved member assignments" in saved_run["error_message"]


async def test_team_rework_accumulated_tokens_hits_budget(client, monkeypatch):
    """S3: Cumulative token usage across initial + rework rounds triggers budget limit."""
    agent = await _create_agent(client, "Rework Budget Agent")
    team_resp = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("rework-budget-team"),
            "name": "Rework Budget Team",
            "mode": "coordinate",
            "leader_agent_id": agent["id"],
            "member_agent_ids": [agent["id"]],
            "review_policy": {"max_rounds": 2, "criteria": ["must cite sources"]},
            "limits": {
                "max_iterations": 5,
                "timeout_seconds": 60,
                "max_tokens": 1000,
                "total_token_budget": 50,
            },
            "enabled": True,
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    team = team_resp.json()

    task_resp = await client.post(
        "/api/v1/tasks",
        json={"title": "Rework Task", "query": "Need rework", "team_id": team["id"]},
    )
    assert task_resp.status_code == 201, task_resp.text
    task = task_resp.json()

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="plan-run",
            output={
                "summary": "Step 1",
                "recommended_team_id": team["id"],
                "steps": [
                    {"id": "step-1", "title": "work", "assigned_agent_id": agent["id"], "expected_output": "data"}
                ],
            },
            usage=None,
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)

    plan_resp = await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    assert plan_resp.status_code == 201, plan_resp.text
    app_resp = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert app_resp.status_code == 200, app_resp.text

    call_count = 0

    async def two_round_execution(_team, _prompt, *, timeout_seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Round 1: short uncited output (fails completion) with 30 tokens <= 50 budget
            return AgentExecutionResult(
                run_id="r1",
                output={"content": "Short uncited."},
                usage={"total_tokens": 30},
                member_results=[
                    {"agent_id": agent["id"], "name": "Agent", "run_id": "m1", "output": {"content": "short"}, "usage": {"total_tokens": 30}}
                ],
            )
        # Round 2: another 30 tokens -> accumulated 60 tokens > 50 budget!
        return AgentExecutionResult(
            run_id="r2",
            output={"content": "Reworked output."},
            usage={"total_tokens": 30},
            member_results=[
                {"agent_id": agent["id"], "name": "Agent", "run_id": "m2", "output": {"content": "reworked"}, "usage": {"total_tokens": 30}}
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", two_round_execution)

    run_resp = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert run_resp.status_code == 502, run_resp.text
    run_id = run_resp.json()["details"]["run_id"]

    saved_run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert saved_run["status"] == "failed"
    assert "Team exceeded total token budget: 60 > 50" in saved_run["error_message"]
    # Check that review.round event was emitted before overbudget failure
    event_types = [e["event_type"] for e in saved_run["events"]]
    assert "review.round" in event_types
    assert event_types[-1] == "run.failed"
