"""Editable execution plans retain history and cannot silently execute stale versions."""

import json
import uuid

import pytest

from uloo.models.task import Task
from uloo.runtime.execution import AgentExecutionResult

from .test_runs import prepare_approved_task
from .test_tasks import create_enabled_team, create_task, unique


@pytest.fixture
def direct_runtime(monkeypatch, background_session):
    monkeypatch.setattr("uloo.api.runs.build_team", lambda _team, members, **kwargs: members)

    async def execute(members, _prompt, *, timeout_seconds):
        return AgentExecutionResult(run_id="direct-runtime", output={"answer": 19}, usage=None, member_results=[
            {"agent_id": str(member.id), "name": member.name, "output": {"answer": 19}, "run_id": str(member.id)}
            for member in members
        ])

    monkeypatch.setattr("uloo.api.runs.execute_agent", execute)


async def execute_direct(client, task_id):
    from uloo.api.runs import _active_runs

    response = await client.post(f"/api/v1/tasks/{task_id}/execute", json={})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["status"] == "running"
    active = _active_runs.get(run["id"])
    if active:
        await active
    persisted = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert persisted["status"] == "succeeded"
    return (await client.get(f"/api/v1/tasks/{task_id}")).json()


async def plan_body(client, task_id):
    detail = (await client.get(f"/api/v1/tasks/{task_id}")).json()
    plan = detail["plans"][-1]
    return {key: plan[key] for key in ("summary", "assumptions", "questions", "recommended_team_id", "steps")} | {
        "expected_version": plan["version"],
    }


async def test_edit_saves_new_pending_version_and_rejects_stale_execution(client, monkeypatch):
    task, _ = await prepare_approved_task(client, monkeypatch)
    body = await plan_body(client, task["id"])
    body["steps"][0]["title"] = "Reviewed responsibility"
    body["steps"][0]["expected_output"] = "Verified arithmetic"
    saved = await client.patch(f"/api/v1/tasks/{task['id']}/plan", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    assert saved.json()["approval_status"] == "pending"
    detail = (await client.get(f"/api/v1/tasks/{task['id']}")).json()
    assert detail["plans"][0]["steps"][0]["title"] == "完成任务"
    assert detail["plans"][0]["approval_status"] == "approved"
    assert detail["current_plan_version"] == 2
    stale = await client.patch(f"/api/v1/tasks/{task['id']}/plan", json=body)
    assert stale.status_code == 409
    for suffix, request_body in [("approve", {"expected_version": 1}), ("runs", {"expected_plan_version": 1}), ("execute", {"expected_plan_version": 1})]:
        response = await client.post(f"/api/v1/tasks/{task['id']}/{suffix}", json=request_body)
        assert response.status_code == 409
        assert response.json()["code"] == "VERSION_CONFLICT"
    unapproved = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={"expected_plan_version": 2})
    assert unapproved.status_code == 409
    assert unapproved.json()["code"] == "PLAN_APPROVAL_REQUIRED"


@pytest.mark.parametrize("invalid", ["agent", "unassigned", "team", "cycle", "missing", "duplicate", "empty"])
async def test_plan_edit_validates_team_members_and_dependency_graph(client, monkeypatch, invalid):
    task, _ = await prepare_approved_task(client, monkeypatch)
    body = await plan_body(client, task["id"])
    if invalid == "agent":
        other = await create_enabled_team(client)
        body["steps"][0]["assigned_agent_id"] = other["member_agent_ids"][0]
    elif invalid == "unassigned":
        body["steps"][0]["assigned_agent_id"] = None
    elif invalid == "team":
        body["recommended_team_id"] = str(uuid.uuid4())
    elif invalid == "cycle":
        body["steps"][0]["depends_on"] = [body["steps"][0]["id"]]
    elif invalid == "missing":
        body["steps"][0]["depends_on"] = ["not-a-step"]
    elif invalid == "duplicate":
        body["steps"].append(body["steps"][0])
    else:
        body["steps"] = []
    response = await client.patch(f"/api/v1/tasks/{task['id']}/plan", json=body)
    assert response.status_code == (404 if invalid == "team" else 422), response.text
    detail = (await client.get(f"/api/v1/tasks/{task['id']}")).json()
    assert detail["current_plan_version"] == 1
    assert len(detail["plans"]) == 1


@pytest.mark.parametrize("state", ["running", "succeeded", "cancelled"])
async def test_active_or_terminal_task_cannot_edit_plan(client, monkeypatch, db_session, state):
    task, _ = await prepare_approved_task(client, monkeypatch)
    body = await plan_body(client, task["id"])
    row = await db_session.get(Task, uuid.UUID(task["id"]))
    row.status = state
    await db_session.commit()
    response = await client.patch(f"/api/v1/tasks/{task['id']}/plan", json=body)
    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TASK_STATE"


async def test_execution_uses_edited_version_in_prompt_and_snapshot(client, monkeypatch):
    task, team = await prepare_approved_task(client, monkeypatch)
    body = await plan_body(client, task["id"])
    body["steps"][0]["expected_output"] = "Answer with independent check"
    saved = (await client.patch(f"/api/v1/tasks/{task['id']}/plan", json=body)).json()
    await client.post(f"/api/v1/tasks/{task['id']}/approve", json={"expected_version": 2})

    async def execute(_team, prompt, *, timeout_seconds):
        execution = json.loads(prompt)
        assert execution["approved_plan"]["id"] == saved["id"]
        assert execution["approved_plan"]["steps"][0]["expected_output"] == "Answer with independent check"
        return AgentExecutionResult(run_id="edited-plan-run", output={"answer": 19}, usage=None, member_results=[{
            "agent_id": team["member_agent_ids"][0], "name": "Member", "output": {"answer": 19}, "run_id": "member",
        }])

    monkeypatch.setattr("uloo.api.runs.build_team", lambda *args, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", execute)
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={"expected_plan_version": 2})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "succeeded"
    assert response.json()["configuration_snapshot"]["plan"]["version"] == 2
    assert response.json()["plan_id"] == saved["id"]


@pytest.mark.parametrize("mode", ["coordinate", "route"])
async def test_direct_execution_uses_all_members_or_team_routing(client, mode, direct_runtime):
    team = await create_enabled_team(client)
    agent = (await client.post("/api/v1/agents", json={
        "key": unique("reviewer"), "name": "Reviewer", "role": "Verify the answer",
        "model_ref": "openai-compatible:test-model", "enabled": True,
    })).json()
    members = [*team["member_agent_ids"], agent["id"]]
    updated = await client.patch(f"/api/v1/teams/{team['id']}", json={"member_agent_ids": members, "mode": mode, "expected_version": team["version"]})
    assert updated.status_code == 200, updated.text
    task = await create_task(client, team_id=team["id"])
    detail = await execute_direct(client, task["id"])
    plan = detail["plans"][0]
    assert plan["approval_status"] == "approved"
    if mode == "route":
        assert plan["steps"][0]["assigned_agent_id"] is None
    else:
        assert [step["assigned_agent_id"] for step in plan["steps"]] == members
    again = await client.post(f"/api/v1/tasks/{task['id']}/execute", json={})
    assert again.status_code == 409
    assert len((await client.get(f"/api/v1/tasks/{task['id']}")).json()["plans"]) == 1


async def test_direct_execution_auto_selects_team_via_planner(client, monkeypatch, direct_runtime):
    team = await create_enabled_team(client)
    task = await create_task(client)

    async def execute(_agent, prompt, *, timeout_seconds):
        assert team["id"] in prompt
        return AgentExecutionResult(run_id="auto-planner", usage=None, output={
            "summary": "Automatically selected team", "recommended_team_id": team["id"],
            "steps": [{"id": "first", "title": "Analyse", "assigned_agent_id": team["member_agent_ids"][0]}],
        })

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", execute)
    detail = await execute_direct(client, task["id"])
    assert detail["team_id"] == team["id"]
    assert detail["plans"][0]["planner_run_id"] == "auto-planner"
    assert detail["plans"][0]["approval_status"] == "approved"


async def test_direct_execution_keeps_reviewed_plan(client, monkeypatch, direct_runtime):
    task, _ = await prepare_approved_task(client, monkeypatch)
    body = await plan_body(client, task["id"])
    body["steps"][0]["title"] = "User-edited step"
    await client.patch(f"/api/v1/tasks/{task['id']}/plan", json=body)
    detail = await execute_direct(client, task["id"])
    assert detail["current_plan_version"] == 2
    assert detail["plans"][-1]["steps"][0]["title"] == "User-edited step"
    assert len(detail["plans"]) == 2
