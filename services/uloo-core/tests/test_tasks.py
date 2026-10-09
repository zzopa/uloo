"""Tests for the ULOO main-flow Task APIs."""

import uuid

from uloo.runtime.execution import AgentExecutionResult


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def create_task(client, **overrides) -> dict:
    payload = {
        "title": "调研竞品方案",
        "query": "请帮我调研竞品 A/B/C 的核心能力、价格和定位，并输出对比报告。",
    }
    payload.update(overrides)
    resp = await client.post("/api/v1/tasks", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_enabled_team(client, **overrides) -> dict:
    """Build a valid team (with one agent) for task team references."""
    agent_resp = await client.post(
        "/api/v1/agents",
        json={
            "key": unique("task-agent"),
            "name": "Task Agent",
            "role": "test",
            "model_ref": "openai-compatible:test-model",
            "enabled": True,
        },
    )
    assert agent_resp.status_code == 201, agent_resp.text
    agent = agent_resp.json()

    team_resp = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("task-team"),
            "name": "Task Team",
            "mode": "coordinate",
            "leader_agent_id": agent["id"],
            "member_agent_ids": [agent["id"]],
            "enabled": True,
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    return team_resp.json()


async def test_create_and_get_task(client):
    """Create a task, then retrieve it with its plan list."""
    created = await create_task(client)
    assert created["status"] == "draft"
    assert created["current_plan_version"] == 1
    assert created["plans"] == []
    assert created["session_id"]

    resp = await client.get(f"/api/v1/tasks/{created['id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "调研竞品方案"
    assert body["query"].startswith("请帮我调研竞品")
    assert body["plans"] == []


async def test_create_task_with_team(client):
    """An explicit enabled team is accepted and persisted."""
    team = await create_enabled_team(client)
    created = await create_task(client, team_id=team["id"])
    assert created["team_id"] == team["id"]


async def test_create_task_unknown_team_fails(client):
    """Referencing a team outside the workspace fails cleanly."""
    resp = await client.post(
        "/api/v1/tasks",
        json={"title": "t", "query": "q", "team_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "TEAM_NOT_FOUND"


async def test_create_task_invalid_team_id(client):
    """A non-UUID team_id is a validation error, not a 500."""
    resp = await client.post(
        "/api/v1/tasks",
        json={"title": "t", "query": "q", "team_id": "not-a-uuid"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_list_tasks_pagination_and_search(client):
    """List returns only this workspace's tasks with keyword search."""
    key = unique("listable")
    await create_task(client, title=f"搜索 {key}")
    await create_task(client, title="另一任务")

    resp = await client.get("/api/v1/tasks", params={"q": key})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert all(key in item["title"] for item in body["items"])

    resp = await client.get("/api/v1/tasks", params={"limit": 1, "offset": 0})
    body = resp.json()
    assert len(body["items"]) <= 1
    assert body["limit"] == 1


async def test_list_tasks_status_filter(client):
    """Invalid status is rejected; valid filter returns matching tasks."""
    resp = await client.get("/api/v1/tasks", params={"status": "not-a-status"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"

    await create_task(client, title="进行中的任务")
    # created tasks are 'draft'; filter for them
    resp = await client.get("/api/v1/tasks", params={"status": "draft"})
    assert resp.status_code == 200
    assert all(item["status"] == "draft" for item in resp.json()["items"])


async def test_task_workspace_isolation(client, db_session):
    """A task created under one workspace is invisible under another."""
    created = await create_task(client)
    other_workspace = "20000000-0000-0000-0000-000000000002"

    from httpx import ASGITransport, AsyncClient

    from uloo.db import get_db
    from uloo.main import app

    from .conftest import TEST_API_TOKEN

    async def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={
                "Authorization": f"Bearer {TEST_API_TOKEN}",
                "X-ULOO-Workspace": other_workspace,
            },
        ) as other:
            resp = await other.get(f"/api/v1/tasks/{created['id']}")
            assert resp.status_code == 404
            assert resp.json()["code"] == "TASK_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_empty_input_rejected(client):
    """Empty title/query cannot create a task."""
    resp = await client.post("/api/v1/tasks", json={"title": "", "query": "q"})
    assert resp.status_code == 422

    resp = await client.post("/api/v1/tasks", json={"title": "t", "query": ""})
    assert resp.status_code == 422


async def test_plan_task_with_real_runtime_boundary(client, monkeypatch):
    """A validated structured Planner result is persisted and advances task state."""
    team = await create_enabled_team(client)
    created = await create_task(client, team_id=team["id"])

    async def fake_execute(_agent, prompt, *, timeout_seconds):
        assert "candidate_teams" in prompt
        assert timeout_seconds > 0
        return AgentExecutionResult(
            run_id="planner-run-1",
            output={
                "summary": "先分析需求，再形成可验收方案。",
                "assumptions": ["用户需要中文输出"],
                "questions": [],
                "recommended_team_id": team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "需求分析",
                        "description": "梳理目标和范围",
                        "assigned_agent_id": team["member_agent_ids"][0],
                        "depends_on": [],
                        "expected_output": "需求清单",
                        "status": "pending",
                    }
                ],
            },
            usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_execute)

    response = await client.post(f"/api/v1/tasks/{created['id']}/plan", json={})
    assert response.status_code == 201, response.text
    plan = response.json()
    assert plan["runtime_type"] == "agno"
    assert plan["is_mock"] is False
    assert plan["planner_run_id"] == "planner-run-1"
    assert plan["recommended_team_id"] == team["id"]
    assert plan["steps"][0]["assigned_agent_id"] == team["member_agent_ids"][0]

    detail = (await client.get(f"/api/v1/tasks/{created['id']}")).json()
    assert detail["status"] == "awaiting_approval"
    assert detail["current_plan_version"] == 1
    assert len(detail["plans"]) == 1


async def test_plan_rejects_agent_outside_selected_team(client, monkeypatch):
    team = await create_enabled_team(client)
    created = await create_task(client, team_id=team["id"])

    async def fake_execute(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner-run-invalid",
            output={
                "summary": "invalid assignment",
                "recommended_team_id": team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "Invalid",
                        "assigned_agent_id": str(uuid.uuid4()),
                    }
                ],
            },
            usage=None,
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_execute)

    response = await client.post(f"/api/v1/tasks/{created['id']}/plan", json={})
    assert response.status_code == 502
    assert response.json()["code"] == "MODEL_ERROR"
