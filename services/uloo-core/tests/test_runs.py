# AI生成
"""Tests for the durable real Team Run boundary.

S5-补1: Execution ownership and duplicate start protection are verified
via the existing duplicate-start test case and the in-memory _active_runs set.
"""

import asyncio
import json

import pytest

from uloo.runtime.execution import AgentExecutionResult

from .test_tasks import create_enabled_team, create_task


@pytest.mark.parametrize('cancel', [False, True])
async def test_background_start_returns_before_execution_and_persists_terminal_state(
    client, monkeypatch, background_session, cancel,
):
    from uloo.api.runs import _active_runs

    task, team = await prepare_approved_task(client, monkeypatch)
    started = asyncio.Event()
    finish = asyncio.Event()
    interrupted = asyncio.Event()

    async def execute(_team, _prompt, *, timeout_seconds):
        started.set()
        try:
            await finish.wait()
        except asyncio.CancelledError:
            interrupted.set()
            raise
        return AgentExecutionResult(
            run_id='background-team', output={'content': 'verified result [1]'}, usage={'total_tokens': 20},
            member_results=[{
                'agent_id': team['member_agent_ids'][0], 'name': 'Member', 'run_id': 'background-member',
                'output': {'content': 'verified result [1]'}, 'usage': {'total_tokens': 10},
            }],
        )

    monkeypatch.setattr('uloo.api.runs.build_team', lambda *args, **kwargs: object())
    monkeypatch.setattr('uloo.api.runs.execute_agent', execute)
    response = await asyncio.wait_for(client.post(f"/api/v1/tasks/{task['id']}/runs?wait=false", json={}), 3)
    assert response.status_code == 202, response.text
    run_id = response.json()['id']
    owned = _active_runs[run_id]
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert response.json()['status'] == 'running'
        duplicate = await client.post(f"/api/v1/tasks/{task['id']}/runs?wait=false", json={})
        assert duplicate.status_code == 409
        if cancel:
            cancelled = await client.post(f'/api/v1/runs/{run_id}/cancel')
            assert cancelled.status_code == 200, cancelled.text
            assert interrupted.is_set()
        else:
            finish.set()
            await asyncio.wait_for(owned, 3)
        saved = (await client.get(f'/api/v1/runs/{run_id}')).json()
        assert saved['status'] == ('cancelled' if cancel else 'succeeded')
        assert saved['events'][-1]['event_type'] == ('run.cancelled' if cancel else 'run.completed')
        assert run_id not in _active_runs
        if not cancel:
            assert saved['output'] == {'content': 'verified result [1]'}
    finally:
        finish.set()
        if not owned.done():
            owned.cancel()
        await asyncio.gather(owned, return_exceptions=True)


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
    assert run["started_at"] is not None
    assert run["finished_at"] is not None
    assert run["usage"]["total_tokens"] == 20

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
    assert run["error_message"] is not None
    assert run["started_at"] is not None
    assert run["finished_at"] is not None
    assert run["configuration_snapshot"]["team"]["limits"]["timeout_seconds"] == 30
    assert [event["event_type"] for event in run["events"]] == ["run.started", "run.failed"]


async def test_token_budget_uses_total_tokens_not_sum_of_all(client, monkeypatch):
    """S3-补2-fix: _accumulate_usage must use total_tokens (or input+output),
    not input+output+total which double-counts."""
    task, team = await prepare_approved_task(client, monkeypatch)
    # Set a budget of 25.  usage has total_tokens=20, input=8, output=12.
    # Old buggy code: 8+12+20=40 > 25 → would fail.
    # Fixed code: total_tokens=20 ≤ 25 → should succeed.
    changed = await client.patch(
        f"/api/v1/teams/{team['id']}",
        json={"limits": {"total_token_budget": 25}, "expected_version": team["version"]},
    )
    assert changed.status_code == 200, changed.text

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "result within budget"},
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
    assert response.json()["status"] == "succeeded"


async def test_token_budget_exceeded_uses_total_tokens(client, monkeypatch):
    """S3-补2-fix: When total_tokens exceeds budget, the run must fail."""
    task, team = await prepare_approved_task(client, monkeypatch)
    changed = await client.patch(
        f"/api/v1/teams/{team['id']}",
        json={"limits": {"total_token_budget": 15}, "expected_version": team["version"]},
    )
    assert changed.status_code == 200, changed.text

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "exceeds budget"},
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
    assert response.status_code == 502
    run_id = response.json()["details"]["run_id"]
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "failed"


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
    assert run["error_code"] == "MODEL_ERROR"
    assert run["error_message"] is not None
    assert run["started_at"] is not None
    assert run["finished_at"] is not None
    assert run["events"][-1]["event_type"] == "run.failed"


async def test_duplicate_start_rejected_by_ownership(client, monkeypatch):
    """S5-补1: A task that is already running cannot start a second run."""
    from uloo.api.runs import _active_runs

    task, team = await prepare_approved_task(client, monkeypatch)

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "result with sources [1]"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-run-1",
                    "output": {"content": "Member result with citation [1]"},
                    "usage": {"total_tokens": 10},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    # Simulate an active run for this task
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201
    assert response.json()["status"] == "succeeded"

    # After completion, _active_runs should be empty (ownership released)
    assert len(_active_runs) == 0

    # Duplicate start should be rejected by task status check
    duplicate = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert duplicate.status_code == 409


# ---- S5-补2 tests: Cancel / Retry / Continue / Startup cleanup ----


async def test_cancel_running_run(client, monkeypatch):
    """S5-补2: A running run can be cancelled; a succeeded run cannot."""
    task, team = await prepare_approved_task(client, monkeypatch)

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "result with sources [1]"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-run-1",
                    "output": {"content": "Member result with citation [1]"},
                    "usage": {"total_tokens": 10},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201
    run_id = response.json()["id"]

    # Cannot cancel a succeeded run
    cancel_resp = await client.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancel_resp.status_code == 409
    assert cancel_resp.json()["code"] == "INVALID_RUN_STATE"


async def test_retry_failed_run(client, monkeypatch):
    """S5-补2: A failed run can be retried, creating a new successful run."""
    task, team = await prepare_approved_task(client, monkeypatch)

    call_count = 0

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TimeoutError()
        return AgentExecutionResult(
            run_id="agno-team-run-retry",
            output={"content": "retry result with sources [1]"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-run-retry",
                    "output": {"content": "Retry member result [1]"},
                    "usage": {"total_tokens": 10},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    # First run fails
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 504
    original_run_id = response.json()["details"]["run_id"]

    original = (await client.get(f"/api/v1/runs/{original_run_id}")).json()
    assert original["status"] == "failed"
    assert original["error_code"] == "TIMEOUT"

    # Retry the failed run
    retry_resp = await client.post(f"/api/v1/runs/{original_run_id}/retry", json={})
    assert retry_resp.status_code == 201, retry_resp.text
    retry_run = retry_resp.json()
    assert retry_run["status"] == "succeeded"
    assert retry_run["output"]["content"] == "retry result with sources [1]"
    assert retry_run["configuration_snapshot"]["retried_from"] == original_run_id
    assert retry_run["id"] != original_run_id

    # Original run should still be failed
    original_after = (await client.get(f"/api/v1/runs/{original_run_id}")).json()
    assert original_after["status"] == "failed"


async def test_retry_blocks_write_replay_without_confirmation(client, monkeypatch):
    """S5-补2/idempotency: retry of a run whose team binds a write-effect tool
    or Dify workflow must be blocked unless the caller confirms the replay risk.

    The default test team binds no tools (read-only), so the gate only fires
    when an agent has non-empty ``workflow_refs`` or a dify/mcp tool is bound.
    """
    import uuid as _uuid

    # Build a team whose only agent has workflow_refs (Dify workflow = write effect).
    agent_resp = await client.post(
        "/api/v1/agents",
        json={
            "key": f"wf-agent-{_uuid.uuid4().hex[:8]}",
            "name": "Workflow Agent",
            "role": "writer",
            "model_ref": "openai-compatible:test-model",
            "workflow_refs": ["dify-app-submit-001"],
            "enabled": True,
        },
    )
    assert agent_resp.status_code == 201, agent_resp.text
    wf_agent = agent_resp.json()
    team_resp = await client.post(
        "/api/v1/teams",
        json={
            "key": f"wf-team-{_uuid.uuid4().hex[:8]}",
            "name": "Workflow Team",
            "mode": "coordinate",
            "leader_agent_id": wf_agent["id"],
            "member_agent_ids": [wf_agent["id"]],
            "enabled": True,
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    wf_team = team_resp.json()

    task = await create_task(client, team_id=wf_team["id"])

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner-run",
            output={
                "summary": "提交一次工作流",
                "recommended_team_id": wf_team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "调用工作流",
                        "assigned_agent_id": wf_agent["id"],
                        "expected_output": "已提交",
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

    async def failing_team_run(_team, _prompt, *, timeout_seconds):
        raise TimeoutError()

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", failing_team_run)
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 504
    original_run_id = response.json()["details"]["run_id"]

    # Retry without confirming write replay -> 409 WRITE_REPLAY_NOT_CONFIRMED.
    blocked = await client.post(f"/api/v1/runs/{original_run_id}/retry", json={})
    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body["code"] == "WRITE_REPLAY_NOT_CONFIRMED"
    assert body["details"]["workflow_agents"] == [wf_agent["key"]]
    assert body["details"]["write_tool_keys"] == []

    # The original run is untouched (still failed), no new run was created.
    original = (await client.get(f"/api/v1/runs/{original_run_id}")).json()
    assert original["status"] == "failed"


async def test_retry_proceeds_with_write_replay_confirmation(client, monkeypatch):
    """S5-补2/idempotency: with ``confirm_write_replay=true`` the retry is allowed
    and the new run executes as expected (caller took responsibility for replays)."""
    import uuid as _uuid

    agent_resp = await client.post(
        "/api/v1/agents",
        json={
            "key": f"wf-agent-ok-{_uuid.uuid4().hex[:8]}",
            "name": "Workflow Agent OK",
            "role": "writer",
            "model_ref": "openai-compatible:test-model",
            "workflow_refs": ["dify-app-submit-002"],
            "enabled": True,
        },
    )
    wf_agent = agent_resp.json()
    team_resp = await client.post(
        "/api/v1/teams",
        json={
            "key": f"wf-team-ok-{_uuid.uuid4().hex[:8]}",
            "name": "Workflow Team OK",
            "mode": "coordinate",
            "leader_agent_id": wf_agent["id"],
            "member_agent_ids": [wf_agent["id"]],
            "enabled": True,
        },
    )
    wf_team = team_resp.json()
    task = await create_task(client, team_id=wf_team["id"])

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner-run",
            output={
                "summary": "提交一次工作流",
                "recommended_team_id": wf_team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "调用工作流",
                        "assigned_agent_id": wf_agent["id"],
                        "expected_output": "已提交",
                    }
                ],
            },
            usage=None,
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)
    await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    await client.post(f"/api/v1/tasks/{task['id']}/approve")

    call_count = 0

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TimeoutError()
        return AgentExecutionResult(
            run_id="agno-team-run-ok",
            output={"content": "retry ok"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": wf_agent["id"],
                    "name": "Workflow Agent OK",
                    "run_id": "member-run-ok",
                    "output": {"content": "ok"},
                    "usage": {"total_tokens": 10},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 504
    original_run_id = response.json()["details"]["run_id"]

    # Retry with confirmation -> succeeds.
    retry_resp = await client.post(
        f"/api/v1/runs/{original_run_id}/retry",
        json={"confirm_write_replay": True},
    )
    assert retry_resp.status_code == 201, retry_resp.text
    retry_run = retry_resp.json()
    assert retry_run["status"] == "succeeded"
    assert retry_run["output"]["content"] == "retry ok"
    assert retry_run["configuration_snapshot"]["retried_from"] == original_run_id


async def test_retry_non_failed_run_rejected(client, monkeypatch):
    """S5-补2: Retry is only allowed for failed or cancelled runs."""
    task, team = await prepare_approved_task(client, monkeypatch)

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "result with sources [1]"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-run-1",
                    "output": {"content": "Member result [1]"},
                    "usage": {"total_tokens": 10},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201
    run_id = response.json()["id"]

    retry_resp = await client.post(f"/api/v1/runs/{run_id}/retry", json={})
    assert retry_resp.status_code == 409
    assert retry_resp.json()["code"] == "INVALID_RUN_STATE"


async def test_continue_non_running_run_rejected(client, monkeypatch):
    """S5-补2: Continue is only allowed for running runs."""
    task, team = await prepare_approved_task(client, monkeypatch)

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "result with sources [1]"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-run-1",
                    "output": {"content": "Member result [1]"},
                    "usage": {"total_tokens": 10},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201
    run_id = response.json()["id"]

    continue_resp = await client.post(f"/api/v1/runs/{run_id}/continue")
    assert continue_resp.status_code == 409
    assert continue_resp.json()["code"] == "INVALID_RUN_STATE"


async def test_cleanup_leftover_runs(client, db_session, monkeypatch):
    """S5-补2: cleanup_leftover_runs marks running runs as failed on startup."""
    import uuid as _uuid

    from sqlalchemy import select

    from uloo.api.runs import _active_runs, cleanup_leftover_runs
    from uloo.models.run import Run

    task, team = await prepare_approved_task(client, monkeypatch)

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "result [1]"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-run-1",
                    "output": {"content": "Member [1]"},
                    "usage": {"total_tokens": 10},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201
    succeeded_run_id = response.json()["id"]

    # Manually set it back to running via the test session to simulate a crash.
    # Using db_session (savepoint-bound) instead of async_session_factory()
    # so the change is visible to cleanup_leftover_runs and the test client.
    run_obj = (await db_session.execute(
        select(Run).where(Run.id == _uuid.UUID(succeeded_run_id))
    )).scalar_one()
    run_obj.status = "running"
    run_obj.finished_at = None
    await db_session.flush()

    _active_runs[succeeded_run_id] = None  # type: ignore[assignment]

    # Cleanup using the test session so it can see savepoint data
    cleaned = await cleanup_leftover_runs(db=db_session)
    assert cleaned >= 1
    await db_session.flush()

    run = (await client.get(f"/api/v1/runs/{succeeded_run_id}")).json()
    assert run["status"] == "failed"
    assert run["error_code"] == "INTERRUPTED"
    assert len(_active_runs) == 0


# ---- S5-补3 tests: Intermediate event persistence & cursor replay ----


async def test_tool_summaries_persisted_during_execution(client, monkeypatch):
    """S5-补3: tool.summary events are persisted between run.started and agent.completed."""
    task, team = await prepare_approved_task(client, monkeypatch)

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "result with tool usage"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-run-1",
                    "output": {"content": "Member result"},
                    "usage": {"total_tokens": 10},
                }
            ],
            tool_summaries=[
                {
                    "tool_name": "web_search",
                    "tool_call_id": "call-1",
                    "error": False,
                    "result_preview": "search results...",
                },
                {
                    "tool_name": "code_exec",
                    "tool_call_id": "call-2",
                    "error": True,
                    "result_preview": None,
                },
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text
    run = response.json()
    event_types = [event["event_type"] for event in run["events"]]
    assert event_types == ["run.started", "tool.summary", "tool.summary", "agent.completed", "run.completed"]

    # Verify tool.summary payload content
    tool_events = [e for e in run["events"] if e["event_type"] == "tool.summary"]
    assert tool_events[0]["payload"]["tool_name"] == "web_search"
    assert tool_events[0]["payload"]["round"] == "initial"
    assert tool_events[0]["status"] == "succeeded"
    assert tool_events[1]["payload"]["tool_name"] == "code_exec"
    assert tool_events[1]["status"] == "failed"

    # Sequences must be monotonically increasing
    sequences = [e["sequence"] for e in run["events"]]
    assert sequences == sorted(sequences)
    assert sequences[0] == 1


async def test_cursor_replay_returns_events_in_order(client, monkeypatch):
    """S5-补3: Cursor-based polling returns events after a given sequence number."""
    task, team = await prepare_approved_task(client, monkeypatch)

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="agno-team-run-1",
            output={"content": "result"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-run-1",
                    "output": {"content": "Member result"},
                    "usage": {"total_tokens": 10},
                }
            ],
            tool_summaries=[
                {"tool_name": "tool_a", "tool_call_id": "c1", "error": False, "result_preview": "ok"},
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201
    run_id = response.json()["id"]

    # Full event list: run.started(1), tool.summary(2), agent.completed(3), run.completed(4)
    all_events = await client.get(f"/api/v1/runs/{run_id}/events")
    assert all_events.status_code == 200
    assert len(all_events.json()) == 4

    # Cursor after sequence 1 → should get 3 events
    after_1 = await client.get(f"/api/v1/runs/{run_id}/events", params={"after": 1})
    assert [e["sequence"] for e in after_1.json()] == [2, 3, 4]

    # Cursor after sequence 2 → should get 2 events
    after_2 = await client.get(f"/api/v1/runs/{run_id}/events", params={"after": 2})
    assert [e["sequence"] for e in after_2.json()] == [3, 4]

    # Cursor after the last event → should get empty list
    after_last = await client.get(f"/api/v1/runs/{run_id}/events", params={"after": 4})
    assert after_last.json() == []


async def test_retry_persists_intermediate_events(client, monkeypatch):
    """S5-补3: retry_run also persists tool.summary and agent.completed during execution."""
    task, team = await prepare_approved_task(client, monkeypatch)

    call_count = 0

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TimeoutError()
        return AgentExecutionResult(
            run_id="agno-retry-run",
            output={"content": "retry result"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Test Agent",
                    "run_id": "member-retry",
                    "output": {"content": "Retry member"},
                    "usage": {"total_tokens": 10},
                }
            ],
            tool_summaries=[
                {"tool_name": "search", "tool_call_id": "rc1", "error": False, "result_preview": "found"},
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    # First run fails
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 504
    original_run_id = response.json()["details"]["run_id"]

    # Retry
    retry_resp = await client.post(f"/api/v1/runs/{original_run_id}/retry", json={})
    assert retry_resp.status_code == 201, retry_resp.text
    retry_run = retry_resp.json()

    event_types = [e["event_type"] for e in retry_run["events"]]
    assert event_types == ["run.started", "tool.summary", "agent.completed", "run.completed"]
    tool_event = next(e for e in retry_run["events"] if e["event_type"] == "tool.summary")
    assert tool_event["payload"]["tool_name"] == "search"
    assert tool_event["payload"]["round"] == "initial"
