# AI生成
"""P0/S5: Background Run snapshot consistency tests.

Covers the验收门 from docs/NEXT_AGENT_HANDOFF.md / TODO.md 2026-10-19
"P0 / S5: 修复后台 Run 的快照一致性":

  - `_background_execute_run` must not re-read Team/Agent/Plan/Resource that
    may have been edited between Run creation and background execution.
  - Execution inputs are rebuilt from `run.configuration_snapshot`, so edits
    to the live Team row after Run creation do not change what the background
    executor uses.

The test creates a Run with `wait=false`, pauses the background executor
inside the first `execute_agent` call (via an asyncio.Event gate), edits the
Team's review_policy while the executor is paused, then releases the gate.
Because the snapshot was captured at Run creation with max_rounds=2, the
executor runs 2 rework rounds (3 execute_agent calls total) before failing.
A live-driven executor would have observed the edited max_rounds=1 and run
only 1 rework round (2 calls total). The observed call count, the
review.round event count, and the max_rounds captured at build_team all
prove the snapshot was used.

Synchronisation note
--------------------
The polling approach `client.get("/runs/{id}")` in a loop raises
`ResourceClosedError` because `background_session` patches
`async_session_factory` to yield the same session the test client uses, so
concurrent DB access during background execution conflicts.  We follow the
pattern from `test_runs.py::test_background_start_returns_before_execution_and_persists_terminal_state`:
an `asyncio.Event` gates the first `execute_agent` call so the test can
edit the live Team while the background is paused, and `await owned` waits
for the background task to finish before any further DB reads.
"""

import asyncio

from uloo.runtime.execution import AgentExecutionResult

from .test_tasks import create_enabled_team, create_task


async def test_background_run_uses_snapshot_not_live_team_after_edit(
    client, monkeypatch, background_session,
):
    """P0/S5: After a Run is created, editing the Team's review_policy does
    not change what the background executor uses. The snapshot allows 2
    rework rounds; the live Team is edited to 1. The executor runs 2 review
    rounds (snapshot) before failing, proving it did not re-read the live
    Team's max_rounds=1."""
    from uloo.api.runs import _active_runs

    team = await create_enabled_team(client)
    # Configure review_policy with max_rounds=2 so two rework rounds are allowed.
    patch = await client.patch(
        f"/api/v1/teams/{team['id']}",
        json={
            "review_policy": {
                "max_rounds": 2,
                "require_unanimous": False,
                "criteria": ["must cite sources"],
            },
            "expected_version": team["version"],
        },
    )
    assert patch.status_code == 200, patch.text
    team = patch.json()

    task = await create_task(client, team_id=team["id"])

    # Planner stub — produce an approved plan pointing at this team.
    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner",
            output={
                "summary": "execute one step",
                "recommended_team_id": team["id"],
                "steps": [
                    {
                        "id": "step-1",
                        "title": "do work",
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
    assert planned.status_code == 201, planned.text
    approved = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert approved.status_code == 200, approved.text

    # Capture the team_definition that build_team receives so we can assert
    # it carries the snapshot's review_policy, not the edited live one.
    captured = {}
    started = asyncio.Event()
    finish = asyncio.Event()

    def fake_build_team(definition, members, **kwargs):
        captured["max_rounds"] = (definition.review_policy or {}).get("max_rounds")
        return object()

    call_count = 0

    async def always_insufficient(_team, _prompt, *, timeout_seconds):
        nonlocal call_count
        call_count += 1
        # On the first call, signal that the background executor has reached
        # execute_agent and pause. The snapshot rebuild and build_team call
        # have already happened, so the captured max_rounds reflects the
        # snapshot. While paused here, the test edits the live Team — this
        # must NOT change what subsequent rounds use because the executor
        # already holds the snapshot-rebuilt TeamDefinition.
        if call_count == 1:
            started.set()
            await finish.wait()
        return AgentExecutionResult(
            run_id=f"round-{call_count}",
            output={"content": "Done."},
            usage={"total_tokens": 5},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Researcher",
                    "run_id": f"member-{call_count}",
                    "output": {"content": "Done."},
                    "usage": {"total_tokens": 5},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", fake_build_team)
    monkeypatch.setattr("uloo.api.runs.execute_agent", always_insufficient)

    # Start the run in the background. The snapshot is captured here with
    # review_policy.max_rounds=2.
    response = await asyncio.wait_for(
        client.post(f"/api/v1/tasks/{task['id']}/runs?wait=false", json={}), 3,
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    owned = _active_runs[run_id]
    try:
        # Wait for the background executor to reach the first execute_agent
        # call. By this point the snapshot rebuild and build_team call have
        # both happened, so captured["max_rounds"] reflects the snapshot.
        await asyncio.wait_for(started.wait(), 3)

        # Edit the Team's review_policy to max_rounds=1 AFTER the Run was
        # created and the snapshot was captured. If the background executor
        # re-read the live Team, only 1 rework round would execute. The
        # snapshot has max_rounds=2, so the executor must run 2 rework
        # rounds before failing.
        edit = await client.patch(
            f"/api/v1/teams/{team['id']}",
            json={
                "review_policy": {
                    "max_rounds": 1,
                    "require_unanimous": False,
                    "criteria": ["must cite sources"],
                },
                "expected_version": team["version"],
            },
        )
        assert edit.status_code == 200, edit.text

        # Release the background executor. Rounds 2 and 3 will run with
        # finish.is_set() == True and return immediately.
        finish.set()

        # Wait for the background task to finish before any further DB reads.
        # Polling client.get during execution would share the patched session
        # and raise ResourceClosedError.
        await asyncio.wait_for(owned, 5)
    finally:
        finish.set()
        if not owned.done():
            owned.cancel()
        await asyncio.gather(owned, return_exceptions=True)

    # Now the background task is done, so we can safely query the run state.
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    # Max rounds exhausted without satisfaction → failed.
    assert run["status"] == "failed", (
        f"all rounds return insufficient output; expected failed, got {run['status']}"
    )
    # Snapshot max_rounds=2 → 3 execute_agent calls (1 initial + 2 rework).
    # Live max_rounds=1 would give 2 calls (1 initial + 1 rework).
    assert call_count == 3, (
        f"snapshot max_rounds=2 should run 2 rework rounds (3 calls total); "
        f"got {call_count} calls — executor may have used live max_rounds=1"
    )
    review_events = [e for e in run["events"] if e["event_type"] == "review.round"]
    assert len(review_events) == 2, (
        f"snapshot max_rounds=2 should produce 2 review.round events; "
        f"got {len(review_events)} — executor may have used live max_rounds=1"
    )
    assert [e["payload"]["round"] for e in review_events] == [1, 2]

    # The team_definition passed to build_team carried the snapshot's policy.
    assert captured["max_rounds"] == 2, (
        f"build_team received max_rounds={captured.get('max_rounds')}, "
        "expected 2 from snapshot (live was edited to 1)"
    )

    # The live Team was actually edited to max_rounds=1.
    live_team = (await client.get(f"/api/v1/teams/{team['id']}")).json()
    assert live_team["review_policy"]["max_rounds"] == 1, (
        "live Team should reflect the edit to max_rounds=1"
    )
