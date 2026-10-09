"""Tests for the durable real Team Run boundary."""

import json

from uloo.runtime.execution import AgentExecutionResult

from .test_tasks import create_enabled_team, create_task


async def prepare_approved_task(client, monkeypatch) -> tuple[dict, dict]:
    team = await create_enabled_team(client)
    task = await create_task(client, team_id=team["id"])

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner-run",
            output={
                "summary": "执行一项可验证任务",
                "recommended_team_id": team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "完成任务",
                        "assigned_agent_id": team["member_agent_ids"][0],
                        "expected_output": "结果",
                    }
                ],
            },
            usage=None,
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)
    planned = await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    assert planned.status_code == 201, planned.text
    approved = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    return task, team


async def test_approved_task_runs_real_team_boundary_and_persists_events(client, monkeypatch):
    task, team = await prepare_approved_task(client, monkeypatch)

    async def fake_team_run(_team, prompt, *, timeout_seconds):
        execution = json.loads(prompt)
        assert execution["request"] == task["query"]
        assert execution["approved_plan"]["approval_status"] == "approved"
        assert execution["approved_plan"]["steps"][0]["assigned_agent_id"] == team["member_agent_ids"][0]
        assert timeout_seconds > 0
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "真实 Team 执行结果"},
            usage={"input_tokens": 8, "output_tokens": 12, "total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-run-1",
                    "output": {"content": "Member result"},
                    "usage": {"total_tokens": 10},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "succeeded"
    assert run["runtime_type"] == "agno"
    assert run["is_mock"] is False
    assert run["team_id"] == team["id"]
    assert run["agno_run_id"] == "agno-team-run-1"
    assert run["output"]["content"] == "真实 Team 执行结果"
    assert run["configuration_snapshot"]["team"]["id"] == team["id"]
    assert len(run["configuration_snapshot"]["agents"]) == len(team["member_agent_ids"])
    assert [event["event_type"] for event in run["events"]] == ["run.started", "agent.completed", "run.completed"]
    assert run["events"][1]["payload"]["output"] == {"content": "Member result"}

    detail = await client.get(f"/api/v1/runs/{run['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["events"]) == 3

    events = await client.get(f"/api/v1/runs/{run['id']}/events", params={"after": 1})
    assert events.status_code == 200
    assert [event["sequence"] for event in events.json()] == [2, 3]

    listed = await client.get("/api/v1/runs", params={"task_id": task["id"]})
    assert listed.status_code == 200
    assert any(item["id"] == run["id"] for item in listed.json()["items"])

    changed = await client.patch(
        f"/api/v1/teams/{team['id']}", json={"name": "Edited after run", "expected_version": team["version"]}
    )
    assert changed.status_code == 200, changed.text
    history = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert history["configuration_snapshot"]["team"]["name"] == team["name"]
    duplicate = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert duplicate.status_code == 409


async def test_team_timeout_is_enforced_and_failure_is_persisted(client, monkeypatch):
    task, team = await prepare_approved_task(client, monkeypatch)
    changed = await client.patch(
        f"/api/v1/teams/{team['id']}", json={"limits": {"timeout_seconds": 30}, "expected_version": team["version"]}
    )
    assert changed.status_code == 200, changed.text

    async def timed_out(_team, _prompt, *, timeout_seconds):
        assert timeout_seconds == 30
        raise TimeoutError()

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", timed_out)
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 504
    run_id = response.json()["details"]["run_id"]
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "failed"
    assert run["error_code"] == "TIMEOUT"
    assert run["finished_at"]
    assert run["configuration_snapshot"]["team"]["limits"]["timeout_seconds"] == 30
    assert [event["event_type"] for event in run["events"]] == ["run.started", "run.failed"]


async def test_run_requires_plan_approval(client, monkeypatch):
    team = await create_enabled_team(client)
    task = await create_task(client, team_id=team["id"])

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner-run",
            output={
                "summary": "pending plan",
                "recommended_team_id": team["id"],
                "steps": [{"id": "step-1", "title": "step"}],
            },
            usage=None,
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)
    assert (await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})).status_code == 201

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 409
    assert response.json()["code"] == "PLAN_APPROVAL_REQUIRED"


async def test_run_status_filter_validation(client):
    response = await client.get("/api/v1/runs", params={"status": "unknown"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_coordinator_answer_without_member_execution_is_not_success(client, monkeypatch):
    task, _team = await prepare_approved_task(client, monkeypatch)

    async def coordinator_only(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(run_id="coordinator-only", output={"content": "Claimed result"}, usage=None)

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", coordinator_only)
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 502
    run = (await client.get(f"/api/v1/runs/{response.json()['details']['run_id']}")).json()
    assert run["status"] == "failed"
    assert run["events"][-1]["event_type"] == "run.failed"
