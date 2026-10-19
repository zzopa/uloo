# AI生成
"""P0-3: Review / rework / handoff closed-loop automation tests.

Covers the验收门 from docs/NEXT_AGENT_HANDOFF.md §4 P0-3:

  - Review member receives the previous member's structured handoff info.
  - Missing sources, missing members, empty results or unsatisfied completion
    conditions must not be marked as success.
  - Rework rounds have an upper bound; reaching it produces a clear failure
    state with the reason queryable.
  - Review feedback, member responses, final verdict and token usage are
    persisted as events and queryable.

These tests use the existing test_tasks.create_enabled_team helper so the
team/agent are real DB rows; execute_agent is monkey-patched so no real
model call is made.  They exercise the real runs.py review loop and the
real evaluate_completion / build_review_feedback code paths.
"""

import json

from uloo.runtime.execution import AgentExecutionResult

from .test_tasks import unique

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _team_with_review(client, *, max_rounds: int, criteria=None, handoff: str = ""):
    """Build an approved task whose team carries the given review_policy."""
    from .test_tasks import create_enabled_team, create_task

    team = await create_enabled_team(client)
    patch_body = {
        "review_policy": {
            "max_rounds": max_rounds,
            "require_unanimous": False,
            "criteria": criteria or ["must cite sources"],
        },
        "expected_version": team["version"],
    }
    if handoff:
        patch_body["handoff_context"] = handoff
    patched = await client.patch(f"/api/v1/teams/{team['id']}", json=patch_body)
    assert patched.status_code == 200, patched.text
    team = patched.json()

    task = await create_task(client, team_id=team["id"])

    # Planner stub so the task gets an approved plan pointing at this team.
    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner-run",
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

    # The monkeypatch has to be set on the caller's pytest instance, but this
    # helper is invoked from inside the test where monkeypatch is available.
    # Return the materials; the test wires the monkeypatch.
    return task, team, fake_plan


# ---------------------------------------------------------------------------
# Test 0: Initial success records zero rework rounds and initial usage
# ---------------------------------------------------------------------------


async def test_initial_success_records_zero_rework_rounds(client, monkeypatch):
    """P1: When the first execution already satisfies completion conditions,
    no rework round executes; review_rounds=0 and usage is the initial run's
    usage only. Covers the 'initial success' case of the three-way audit."""
    task, team, fake_plan = await _team_with_review(client, max_rounds=3)

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)

    planned = await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    assert planned.status_code == 201, planned.text
    approved = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert approved.status_code == 200, approved.text

    call_count = 0

    async def sufficient_team_run(_team, _prompt, *, timeout_seconds):
        nonlocal call_count
        call_count += 1
        return AgentExecutionResult(
            run_id="single-round",
            output={"content": "Verified data from sources [1] [2] [3] with citations."},
            usage={"total_tokens": 25, "input_tokens": 10, "output_tokens": 15},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Researcher",
                    "run_id": "member-1",
                    "output": {"content": "Cited sources [1] [2] [3]."},
                    "usage": {"total_tokens": 25, "input_tokens": 10, "output_tokens": 15},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", sufficient_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "succeeded"
    assert call_count == 1

    event_types = [e["event_type"] for e in run["events"]]
    assert "review.round" not in event_types, "no rework should execute when initial pass satisfies"
    assert event_types == ["run.started", "agent.completed", "run.completed"]

    final = run["events"][-1]
    # P1: review_rounds=0 (no rework), usage is the initial run's only.
    assert final["payload"]["review_rounds"] == 0
    assert final["payload"]["completion_satisfied"] is True
    assert final["payload"]["usage"]["total_tokens"] == 25
    assert final["payload"]["usage"]["input_tokens"] == 10
    assert final["payload"]["usage"]["output_tokens"] == 15
    # The Run's top-level usage is also the initial usage (no rework to add).
    assert run["usage"]["total_tokens"] == 25


# ---------------------------------------------------------------------------
# Test 1: First round fails review → rework → second round succeeds
# ---------------------------------------------------------------------------


async def test_review_rework_recovers_on_second_round(client, monkeypatch):
    """P0-3: Insufficient first output triggers review.round, second pass
    with cited output succeeds; all events are queryable."""
    task, team, fake_plan = await _team_with_review(client, max_rounds=2)

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)

    planned = await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    assert planned.status_code == 201, planned.text
    approved = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert approved.status_code == 200, approved.text

    call_count = 0

    async def fake_team_run(_team, prompt, *, timeout_seconds):
        nonlocal call_count
        call_count += 1
        # Inspect the handoff payload the rework round receives.
        if call_count == 2:
            payload = json.loads(prompt)
            assert "previous_result" in payload, "rework round must receive previous_result"
            assert "review_feedback" in payload, "rework round must receive review_feedback"
            assert "Review round 1" in payload["review_feedback"]

        # Round 1: short, uncited → insufficient evidence.
        if call_count == 1:
            return AgentExecutionResult(
                run_id="round-1",
                output={"content": "Done."},
                usage={"total_tokens": 12},
                member_results=[
                    {
                        "agent_id": team["member_agent_ids"][0],
                        "name": "Researcher",
                        "run_id": "member-1",
                        "output": {"content": "Done."},
                        "usage": {"total_tokens": 12},
                    }
                ],
            )
        # Round 2: cited, long enough → satisfied.
        return AgentExecutionResult(
            run_id="round-2",
            output={"content": "Verified data from sources [1] [2] [3] and summarised."},
            usage={"total_tokens": 18},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Researcher",
                    "run_id": "member-2",
                    "output": {"content": "Verified data from sources [1] [2] [3]."},
                    "usage": {"total_tokens": 18},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "succeeded"
    assert call_count == 2

    event_types = [e["event_type"] for e in run["events"]]
    # Expected: run.started, agent.completed(round=initial), review.round,
    # agent.completed(round=review-1), run.completed.
    assert "review.round" in event_types
    assert event_types[0] == "run.started"
    assert event_types[-1] == "run.completed"

    review_events = [e for e in run["events"] if e["event_type"] == "review.round"]
    assert len(review_events) == 1
    assert review_events[0]["payload"]["round"] == 1
    assert "reasons" in review_events[0]["payload"]
    assert "feedback" in review_events[0]["payload"]

    member_events = [e for e in run["events"] if e["event_type"] == "agent.completed"]
    rounds = [e["payload"].get("round") for e in member_events]
    assert "initial" in rounds
    assert "review-1" in rounds

    # Token usage from both rounds must be recorded in the final event.
    final = run["events"][-1]
    # P1: the run.completed event's ``usage`` is cumulative across initial
    # + all rework rounds (12 + 18 = 30), not just the final round.
    assert final["payload"]["usage"]["total_tokens"] == 30
    # P1: review_rounds records the actual number of rework rounds that
    # executed (1), not the configured max_rounds (2).
    assert final["payload"]["review_rounds"] == 1
    assert final["payload"]["completion_satisfied"] is True
    # The Run's top-level usage field is also cumulative, so reviewers
    # reading GET /runs/{id} see the true total cost.
    assert run["usage"]["total_tokens"] == 30


# ---------------------------------------------------------------------------
# Test 2: Rework hits max_rounds → clear failure state, reason queryable
# ---------------------------------------------------------------------------


async def test_review_rework_hits_max_rounds_and_fails(client, monkeypatch):
    """P0-3: When max_rounds is exhausted without satisfying completion,
    the run ends in 'failed' with the review reasons queryable."""
    task, team, fake_plan = await _team_with_review(client, max_rounds=1)

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)

    planned = await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    assert planned.status_code == 201, planned.text
    approved = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert approved.status_code == 200, approved.text

    call_count = 0

    async def always_insufficient(_team, _prompt, *, timeout_seconds):
        nonlocal call_count
        call_count += 1
        return AgentExecutionResult(
            run_id=f"round-{call_count}",
            output={"content": "Done."},
            usage={"total_tokens": 8},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Researcher",
                    "run_id": f"member-{call_count}",
                    "output": {"content": "Done."},
                    "usage": {"total_tokens": 8},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", always_insufficient)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 502, response.text
    run_id = response.json()["details"]["run_id"]

    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["status"] == "failed"
    assert run["error_code"] == "MODEL_ERROR"
    assert run["error_message"] is not None

    event_types = [e["event_type"] for e in run["events"]]
    assert "review.round" in event_types
    assert event_types[-1] == "run.failed"

    # The review reason must be persisted in the review.round event payload.
    review_events = [e for e in run["events"] if e["event_type"] == "review.round"]
    assert len(review_events) == 1
    assert review_events[0]["payload"]["round"] == 1
    assert len(review_events[0]["payload"]["reasons"]) > 0

    # Token usage from every round must be observable in events.
    member_events = [e for e in run["events"] if e["event_type"] == "agent.completed"]
    assert len(member_events) == 2  # initial + review-1
    total_tokens = sum(e["payload"].get("usage", {}).get("total_tokens", 0) for e in member_events)
    assert total_tokens == 16  # 8 + 8


# ---------------------------------------------------------------------------
# Test 3: handoff_context is delivered to the coordinator at build time
# ---------------------------------------------------------------------------


async def test_handoff_context_is_injected_into_team_instructions(client, monkeypatch):
    """P0-3: handoff_context configured on the team is passed to build_team
    as part of the team definition, so the coordinator can forward it to
    members during transitions."""
    task, team, fake_plan = await _team_with_review(
        client, max_rounds=1, handoff="Previous session: market analysis incomplete"
    )

    monkeypatch.setattr("uloo.api.tasks.build_planner_agent", lambda: object())
    monkeypatch.setattr("uloo.api.tasks.execute_agent", fake_plan)

    planned = await client.post(f"/api/v1/tasks/{task['id']}/plan", json={})
    assert planned.status_code == 201, planned.text
    approved = await client.post(f"/api/v1/tasks/{task['id']}/approve")
    assert approved.status_code == 200, approved.text

    captured_team_definition = {}

    def fake_build_team(definition, members, **kwargs):
        captured_team_definition["definition"] = definition
        return object()

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="single-round",
            output={"content": "Result with sources [1] [2] and a full report."},
            usage={"total_tokens": 10},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Researcher",
                    "run_id": "member-1",
                    "output": {"content": "Result with sources [1] [2]."},
                    "usage": {"total_tokens": 10},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", fake_build_team)
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text

    definition = captured_team_definition["definition"]
    assert (definition.handoff_context or "").startswith("Previous session: market analysis")

    # The snapshot persisted on the run must also carry handoff_context so
    # later review of the historical run shows what the coordinator received.
    run = response.json()
    snapshot = run["configuration_snapshot"]["team"]
    assert snapshot["handoff_context"].startswith("Previous session: market analysis")


# ---------------------------------------------------------------------------
# Test 4: route mode allows only selected members to execute
# ---------------------------------------------------------------------------


async def test_route_mode_allows_subset_of_plan_members(client, monkeypatch):
    """P0-3: route mode lets the team pick only the necessary members; the
    execution is still considered consistent with the plan."""
    from .test_tasks import create_task

    # Build a team with two members in route mode.
    agent_a = await client.post(
        "/api/v1/agents",
        json={
            "key": unique("route-agent-a"),
            "name": "Agent A",
            "role": "research",
            "model_ref": "openai-compatible:test-model",
            "enabled": True,
        },
    )
    assert agent_a.status_code == 201, agent_a.text
    agent_a = agent_a.json()
    agent_b = await client.post(
        "/api/v1/agents",
        json={
            "key": unique("route-agent-b"),
            "name": "Agent B",
            "role": "summarise",
            "model_ref": "openai-compatible:test-model",
            "enabled": True,
        },
    )
    assert agent_b.status_code == 201, agent_b.text
    agent_b = agent_b.json()

    team_resp = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("route-team"),
            "name": "Route Team",
            "mode": "route",
            "leader_agent_id": agent_a["id"],
            "member_agent_ids": [agent_a["id"], agent_b["id"]],
            "enabled": True,
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    team = team_resp.json()

    task = await create_task(client, team_id=team["id"])

    async def fake_plan(_agent, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="planner-run",
            output={
                "summary": "route plan",
                "recommended_team_id": team["id"],
                # The plan names agent_a only; route may still call agent_b.
                "steps": [
                    {
                        "id": "step-1",
                        "title": "research",
                        "assigned_agent_id": agent_a["id"],
                        "expected_output": "data",
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

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="route-run",
            output={"content": "Final report with sources [1] and citations."},
            usage={"total_tokens": 15},
            # Only agent_a executed; agent_b was not needed by the router.
            member_results=[
                {
                    "agent_id": agent_a["id"],
                    "name": "Agent A",
                    "run_id": "member-a",
                    "output": {"content": "Found data [1]."},
                    "usage": {"total_tokens": 15},
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "succeeded"
    assert run["events"][-1]["event_type"] == "run.completed"
