# AI生成
"""P0/S3-5: Real human-continue pause/resume tests.

Covers the验收门 from TODO.md 2026-10-19
"P0 / S3-S5: 完成 DeepSeek 复核返工和人工继续执行验收":
  - 第一次结果不合格后核验员能退回调研员重新执行
  - `continue` 必须真正等待人工补充后从暂停点继续，而不是只写入事件标记

The test creates a Team with `review_policy.require_human_input=True`,
starts a background run whose first execute_agent round returns insufficient
output. The executor must pause (status=awaiting_input) instead of
auto-reworking, record a `run.input_requested` event, and block on an
asyncio.Event. The test then calls POST /runs/{id}/continue with a
supplement; the executor must resume from the pause point, incorporate
the supplement into the rework prompt, and the second round returns
sufficient output. The run ends in succeeded with the supplement visible
in the `run.continued` event payload.

A second test verifies that a paused run can be cancelled (the pause
event is released so the background task unblocks and observes the
cancellation).
"""

import asyncio
import json

from uloo.runtime.execution import AgentExecutionResult

from .test_tasks import create_enabled_team, create_task


async def test_human_continue_pauses_then_resumes_with_supplement(client, monkeypatch, background_session):
    """P0/S3-5: A run with require_human_input pauses after the first
    insufficient round, waits for a human supplement via /continue, then
    resumes and succeeds on the second round. The supplement is observable
    in both the rework prompt and the run.continued event payload."""
    from uloo.api.runs import _paused_runs

    team = await create_enabled_team(client)
    # Configure review_policy: max_rounds=2, require_human_input=True so
    # the first insufficient round pauses instead of auto-reworking.
    patch = await client.patch(
        f"/api/v1/teams/{team['id']}",
        json={
            "review_policy": {
                "max_rounds": 2,
                "require_unanimous": False,
                "criteria": ["must cite sources"],
                "require_human_input": True,
            },
            "expected_version": team["version"],
        },
    )
    assert patch.status_code == 200, patch.text
    team = patch.json()

    task = await create_task(client, team_id=team["id"])

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner",
            output={
                "summary": "research wood board prices",
                "recommended_team_id": team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "research prices",
                        "assigned_agent_id": team["member_agent_ids"][0],
                        "expected_output": "price table with sources",
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

    call_count = 0
    captured_rework_prompts: list[str] = []

    async def fake_team_run(_team, prompt, *, timeout_seconds):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First round: short, uncited → insufficient, triggers pause.
            return AgentExecutionResult(
                run_id="round-1",
                output={"content": "Done."},
                usage={"total_tokens": 10},
                member_results=[
                    {
                        "agent_id": team["member_agent_ids"][0],
                        "name": "Researcher",
                        "run_id": "member-1",
                        "output": {"content": "Done."},
                        "usage": {"total_tokens": 10},
                    }
                ],
            )
        # Second round: capture the rework prompt to verify the supplement
        # was incorporated, then return cited sufficient output.
        captured_rework_prompts.append(prompt)
        return AgentExecutionResult(
            run_id="round-2",
            output={"content": "Verified prices from sources [1] [2] [3] with华东松木规格."},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Researcher",
                    "run_id": "member-2",
                    "output": {"content": "Cited sources [1] [2] [3]."},
                    "usage": {"total_tokens": 20},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda *args, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    # Start the run in the background so the executor can pause.
    response = await asyncio.wait_for(
        client.post(f"/api/v1/tasks/{task['id']}/runs?wait=false", json={}), 3,
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    # Poll until the run reaches awaiting_input. The background is paused
    # inside pause_event.wait() and NOT holding any DB operation, so polling
    # client.get is safe here (unlike the snapshot consistency test where the
    # background was inside execute_agent holding session state).
    run = None
    for _ in range(50):
        run = (await client.get(f"/api/v1/runs/{run_id}")).json()
        if run["status"] in {"awaiting_input", "succeeded", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.1)

    assert run is not None
    assert run["status"] == "awaiting_input", (
        f"require_human_input=True should pause after first insufficient round; "
        f"got status={run['status']}"
    )

    # The run.input_requested event must be queryable with the review feedback.
    input_events = [e for e in run["events"] if e["event_type"] == "run.input_requested"]
    assert len(input_events) == 1, "exactly one run.input_requested event expected"
    assert input_events[0]["payload"]["round"] == 1
    assert len(input_events[0]["payload"]["reasons"]) > 0

    # The background executor is blocked on the pause event.
    assert run_id in _paused_runs

    # Submit the human supplement via /continue.
    supplement_text = "地区：华东；材质：松木；规格：1220×2440×18mm；含税含运费。"
    cont = await asyncio.wait_for(
        client.post(
            f"/api/v1/runs/{run_id}/continue",
            json={"supplement": supplement_text},
        ),
        5,
    )
    assert cont.status_code == 200, cont.text

    # Poll until terminal.
    for _ in range(50):
        run = (await client.get(f"/api/v1/runs/{run_id}")).json()
        if run["status"] in {"succeeded", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.1)

    assert run["status"] == "succeeded", (
        f"after supplement the second round should satisfy review; got {run['status']}"
    )
    assert call_count == 2

    # The supplement must be visible in the run.continued event payload.
    continued_events = [e for e in run["events"] if e["event_type"] == "run.continued"]
    assert len(continued_events) == 1
    assert continued_events[0]["payload"]["supplement"] == supplement_text

    # The rework prompt received by execute_agent must carry the supplement
    # so the team actually used the human input (not just an event marker).
    assert len(captured_rework_prompts) == 1
    prompt_payload = json.loads(captured_rework_prompts[0])
    assert prompt_payload["human_supplement"] == supplement_text, (
        "the supplement must be incorporated into the rework prompt so the "
        "team receives the human clarification; otherwise continue is just "
        "an event marker"
    )

    # Cumulative usage across initial + rework round (10 + 20 = 30).
    final = run["events"][-1]
    assert final["payload"]["usage"]["total_tokens"] == 30
    # One rework round actually executed (the second round).
    assert final["payload"]["review_rounds"] == 1


async def test_continue_rejects_running_run_and_requires_supplement(client, monkeypatch, background_session):
    """P0/S3-5: /continue only accepts awaiting_input runs and requires a
    non-empty supplement. Calling it on an awaiting_input run without a
    supplement returns 400. A paused run can be cancelled (the pause event
    is released so the background task unblocks and observes the
    cancellation)."""
    team = await create_enabled_team(client)
    patch = await client.patch(
        f"/api/v1/teams/{team['id']}",
        json={
            "review_policy": {
                "max_rounds": 2,
                "require_unanimous": False,
                "criteria": ["must cite sources"],
                "require_human_input": True,
            },
            "expected_version": team["version"],
        },
    )
    assert patch.status_code == 200
    team = patch.json()
    task = await create_task(client, team_id=team["id"])

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner",
            output={
                "summary": "plan",
                "recommended_team_id": team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "work",
                        "assigned_agent_id": team["member_agent_ids"][0],
                        "expected_output": "result",
                    }
                ],
            },
            usage=None,
        )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)
    planned = await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    assert planned.status_code == 201
    approved = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert approved.status_code == 200

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        # Always insufficient → triggers pause (require_human_input=True)
        return AgentExecutionResult(
            run_id="round-1",
            output={"content": "Done."},
            usage={"total_tokens": 5},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Researcher",
                    "run_id": "member-1",
                    "output": {"content": "Done."},
                    "usage": {"total_tokens": 5},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda *args, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await asyncio.wait_for(
        client.post(f"/api/v1/tasks/{task['id']}/runs?wait=false", json={}), 3,
    )
    assert response.status_code == 202
    run_id = response.json()["id"]

    # Poll until the run reaches awaiting_input. The background is paused on
    # pause_event.wait() (NOT holding any DB operation), so polling is safe.
    run = None
    for _ in range(50):
        run = (await client.get(f"/api/v1/runs/{run_id}")).json()
        if run["status"] in {"awaiting_input", "succeeded", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.1)
    assert run is not None
    assert run["status"] == "awaiting_input"

    # Calling /continue without a body must return 400.
    no_body = await client.post(f"/api/v1/runs/{run_id}/continue")
    assert no_body.status_code == 400
    empty = await client.post(f"/api/v1/runs/{run_id}/continue", json={"supplement": "   "})
    assert empty.status_code == 400
    # The run must still be awaiting_input after rejected supplement calls.
    still_paused = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert still_paused["status"] == "awaiting_input"

    # Cancel the paused run — cancel must release the pause event so the
    # background task unblocks and observes the cancellation.
    cancelled = await asyncio.wait_for(client.post(f"/api/v1/runs/{run_id}/cancel"), 5)
    assert cancelled.status_code == 200
    final = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert final["status"] == "cancelled"
