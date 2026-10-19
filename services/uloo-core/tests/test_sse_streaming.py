# AI生成
"""P0-4: SSE real-time event streaming reliability tests.

Covers the验收门 from docs/NEXT_AGENT_HANDOFF.md §4 P0-4:

  - SSE supports authentication, cursor replay, reconnection recovery,
    terminal close and concurrent event ordering.

The SSE endpoint /runs/{id}/stream is implemented in uloo.api.runs. These
tests exercise it against a real PostgreSQL-backed run that has already
reached a terminal state, so the stream must close cleanly.
"""

import json

import pytest

from uloo.runtime.execution import AgentExecutionResult

from .test_runs import prepare_approved_task


def _parse_sse_event(lines: list[str]) -> dict | None:
    """Combine consecutive SSE 'id:'/'event:'/'data:' lines into one event."""
    if not lines:
        return None
    event = {"id": None, "event": None, "data": None}
    for line in lines:
        if line.startswith("id:"):
            event["id"] = line[3:].strip()
        elif line.startswith("event:"):
            event["event"] = line[6:].strip()
        elif line.startswith("data:"):
            event["data"] = line[5:].strip()
    if event["data"]:
        try:
            event["data"] = json.loads(event["data"])
        except json.JSONDecodeError:
            pass
    return event if event["event"] or event["data"] else None


async def _collect_sse_stream(response) -> list[dict]:
    """Drain an SSE StreamingResponse into a list of parsed events."""
    events: list[dict] = []
    buffer: list[str] = []
    async for raw_line in response.aiter_lines():
        if raw_line == "":
            if buffer:
                parsed = _parse_sse_event(buffer)
                if parsed:
                    events.append(parsed)
                buffer = []
            continue
        buffer.append(raw_line)
    if buffer:
        parsed = _parse_sse_event(buffer)
        if parsed:
            events.append(parsed)
    return events


@pytest.fixture
async def completed_run(client, monkeypatch, background_session):
    """A run that has already reached 'succeeded' so the SSE stream closes.

    ``background_session`` patches ``uloo.api.runs.async_session_factory`` so
    the SSE endpoint's internal polling sessions see the same savepoint-bound
    session as the test client; without it, the stream opens a fresh session
    that cannot see the uncommitted run and returns 404.
    """
    task, team = await prepare_approved_task(client, monkeypatch)

    async def fake_team_run(_team, _prompt, *, timeout_seconds):
        return AgentExecutionResult(
            run_id="sse-run",
            output={"content": "result with sources [1] [2]"},
            usage={"total_tokens": 20},
            member_results=[
                {
                    "agent_id": team["member_agent_ids"][0],
                    "name": "Member",
                    "run_id": "member-run",
                    "output": {"content": "Member result with citations [1]"},
                    "usage": {"total_tokens": 10},
                }
            ],
            tool_summaries=[
                {"tool_name": "web_search", "tool_call_id": "c1", "error": False, "result_preview": "ok"},
            ],
        )

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text
    return response.json()


async def test_sse_requires_authentication(client, completed_run):
    """P0-4: An unauthenticated SSE request must be rejected with 401."""
    run_id = completed_run["id"]
    response = await client.get(
        f"/api/v1/runs/{run_id}/stream",
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_sse_rejects_unknown_run(client, completed_run):
    """P0-4: An SSE stream for an unknown run id returns 404, not an empty
    stream that never closes."""
    import uuid as _uuid

    response = await client.get(f"/api/v1/runs/{_uuid.uuid4()}/stream")
    assert response.status_code == 404
    assert response.json()["code"] == "RUN_NOT_FOUND"


async def test_sse_streams_all_events_in_sequence_and_closes_on_terminal(client, completed_run):
    """P0-4: A stream from cursor=0 yields every persisted event in
    monotonic sequence order and closes after the terminal event."""
    run_id = completed_run["id"]
    async with client.stream("GET", f"/api/v1/runs/{run_id}/stream") as response:
        assert response.status_code == 200
        events = await _collect_sse_stream(response)

    assert len(events) >= 4  # run.started, tool.summary, agent.completed, run.completed
    event_types = [e["event"] for e in events if e["event"]]
    assert "run.started" in event_types
    assert "run.completed" in event_types

    # The terminal event must be the last one observed.
    assert events[-1]["event"] == "run.completed"

    # Sequences must be strictly increasing across the whole stream.
    sequences = [e["id"] for e in events if e["id"] is not None]
    assert sequences == sorted(sequences, key=int)
    assert len(sequences) >= 4
    assert sequences[0] == "1"


async def test_sse_cursor_replay_skips_events_before_cursor(client, completed_run):
    """P0-4: Starting the stream after a given cursor replays only the
    events whose sequence is greater than the cursor. This is the basis for
    reconnection recovery: a client that lost the connection can resume."""
    run_id = completed_run["id"]
    async with client.stream("GET", f"/api/v1/runs/{run_id}/stream?after=1") as response:
        assert response.status_code == 200
        events = await _collect_sse_stream(response)

    # Cursor=1 means sequence > 1; the run.started event (seq=1) is skipped.
    sequences = [int(e["id"]) for e in events if e["id"] is not None]
    assert sequences
    assert all(seq > 1 for seq in sequences)
    assert sequences == sorted(sequences)
    # The stream still terminates at the same terminal event.
    assert events[-1]["event"] == "run.completed"


async def test_sse_terminal_closes_for_failed_run(client, monkeypatch, background_session):
    """P0-4: A failed run's stream must also close cleanly via run.failed."""
    task, _team = await prepare_approved_task(client, monkeypatch)

    async def failing_team_run(_team, _prompt, *, timeout_seconds):
        raise TimeoutError()

    monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
    monkeypatch.setattr("uloo.api.runs.execute_agent", failing_team_run)

    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 504
    run_id = response.json()["details"]["run_id"]

    async with client.stream("GET", f"/api/v1/runs/{run_id}/stream") as stream_response:
        assert stream_response.status_code == 200
        events = await _collect_sse_stream(stream_response)

    event_types = [e["event"] for e in events if e["event"]]
    assert "run.failed" in event_types
    assert events[-1]["event"] == "run.failed"


async def test_sse_workspace_isolation_rejects_other_workspace_run(client, completed_run, monkeypatch):
    """P0-4: A stream request from a different workspace cannot observe a
    run that does not belong to it."""
    run_id = completed_run["id"]
    other_workspace = "20000000-0000-0000-0000-000000000002"
    response = await client.get(
        f"/api/v1/runs/{run_id}/stream",
        headers={
            "Authorization": client.headers["Authorization"],
            "X-ULOO-Workspace": other_workspace,
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RUN_NOT_FOUND"


async def test_sse_last_event_id_header_resumes_from_cursor(client, completed_run):
    """P0/S5: When `after` is omitted, the `Last-Event-ID` request header
    (sent automatically by browsers' native EventSource on reconnect) is
    used as the cursor. A reconnecting client only receives events with a
    strictly greater sequence than the last one it acknowledged."""
    run_id = completed_run["id"]
    async with client.stream(
        "GET",
        f"/api/v1/runs/{run_id}/stream",
        headers={"Last-Event-ID": "1"},
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse_stream(response)

    # Cursor=1 from Last-Event-ID; only events with sequence > 1 are sent.
    sequences = [int(e["id"]) for e in events if e["id"] is not None]
    assert sequences
    assert all(seq > 1 for seq in sequences), (
        "Last-Event-ID cursor must skip events with sequence <= cursor"
    )
    assert sequences == sorted(sequences)
    # The stream still terminates at the terminal event.
    assert events[-1]["event"] == "run.completed"


async def test_sse_explicit_after_overrides_last_event_id(client, completed_run):
    """P0/S5: When both `after` and `Last-Event-ID` are present, the explicit
    query parameter wins. This lets a client force a different replay point
    even if its EventSource library auto-sent a stale Last-Event-ID."""
    run_id = completed_run["id"]
    # First, find the max sequence so we can pick an after cursor that
    # excludes everything except the terminal event.
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    max_seq_before_terminal = max(
        e["sequence"] for e in run["events"] if e["event_type"] != "run.completed"
    )

    async with client.stream(
        "GET",
        f"/api/v1/runs/{run_id}/stream?after={max_seq_before_terminal - 1}",
        headers={"Last-Event-ID": "1"},
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse_stream(response)

    sequences = [int(e["id"]) for e in events if e["id"] is not None]
    assert sequences
    # Explicit after takes precedence over Last-Event-ID=1; the first
    # event must be at max_seq_before_terminal (the one after the cursor).
    assert sequences[0] == max_seq_before_terminal
    assert all(seq >= max_seq_before_terminal for seq in sequences)


async def test_sse_no_duplicate_events_on_reconnect(client, completed_run):
    """P0/S5: A reconnecting client that already observed the terminal
    event (Last-Event-ID = terminal sequence) receives no further events;
    the stream closes immediately. This prevents duplicate terminal
    delivery on repeated reconnects."""
    run_id = completed_run["id"]
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    terminal_seq = max(e["sequence"] for e in run["events"])

    async with client.stream(
        "GET",
        f"/api/v1/runs/{run_id}/stream",
        headers={"Last-Event-ID": str(terminal_seq)},
    ) as response:
        assert response.status_code == 200
        events = await _collect_sse_stream(response)

    # No events with sequence > terminal_seq exist; the stream closes
    # without re-delivering the terminal event.
    sequences = [int(e["id"]) for e in events if e["id"] is not None]
    assert all(seq > terminal_seq for seq in sequences)
    # The terminal event itself must not be re-sent.
    event_types = [e["event"] for e in events if e["event"]]
    assert "run.completed" not in event_types


async def test_sse_multi_stream_ordering_isolated(client, monkeypatch, background_session):
    """P0-4/S5-补3 browser SSE 验收: Multiple SSE streams (one per run) each
    observe their own run's events in strictly increasing sequence order and
    close cleanly on the terminal event. No stream delivers events from a
    different run, and per-stream terminal events are not duplicated.

    This models the browser scenario where multiple tabs/processes watch
    different runs. Streams are drained one at a time because the test
    fixture's savepoint-bound session cannot be shared by concurrent polling
    sessions; in production each SSE stream opens its own DB session and the
    endpoint supports true concurrency.
    """
    from .test_runs import prepare_approved_task

    async def make_run(idx: int) -> dict:
        task, team = await prepare_approved_task(client, monkeypatch)

        async def fake_team_run(_team, _prompt, *, timeout_seconds):
            return AgentExecutionResult(
                run_id=f"multi-sse-run-{idx}",
                output={"content": f"result {idx} with sources [1]"},
                usage={"total_tokens": 20},
                member_results=[
                    {
                        "agent_id": team["member_agent_ids"][0],
                        "name": "Member",
                        "run_id": f"multi-member-{idx}",
                        "output": {"content": f"member {idx}"},
                        "usage": {"total_tokens": 10},
                    }
                ],
                tool_summaries=[
                    {"tool_name": "web_search", "tool_call_id": f"multi-c-{idx}", "error": False, "result_preview": "ok"},
                ],
            )

        monkeypatch.setattr("uloo.api.runs.build_team", lambda definition, members, **kwargs: object())
        monkeypatch.setattr("uloo.api.runs.execute_agent", fake_team_run)
        response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
        assert response.status_code == 201, response.text
        return response.json()

    runs = [await make_run(i) for i in range(3)]

    async def stream_one(run: dict) -> tuple[str, list[dict]]:
        async with client.stream("GET", f"/api/v1/runs/{run['id']}/stream") as response:
            assert response.status_code == 200
            events = await _collect_sse_stream(response)
        return run["id"], events

    results = [await stream_one(r) for r in runs]

    # Per-stream assertions: every stream saw its own events, in order, and
    # closed on its own terminal event exactly once.
    seen_run_ids: set[str] = set()
    for run_id, events in results:
        seen_run_ids.add(run_id)
        sequences = [int(e["id"]) for e in events if e["id"] is not None]
        assert sequences, f"stream for run {run_id} produced no events"
        # Strictly increasing and starts at 1.
        assert sequences == sorted(sequences), f"out-of-order events for run {run_id}"
        assert sequences[0] == 1, f"first event for run {run_id} was not seq=1"
        # Exactly one terminal event, at the end.
        terminal = [e for e in events if e["event"] == "run.completed"]
        assert len(terminal) == 1, f"run {run_id}: expected one terminal event, got {len(terminal)}"
        assert events[-1]["event"] == "run.completed", f"run {run_id}: terminal event was not last"
        # No event payload carries a different run_id.
        for e in events:
            if e["data"] and isinstance(e["data"], dict):
                assert e["data"].get("run_id") == run_id, (
                    f"stream for run {run_id} observed event from {e['data'].get('run_id')}"
                )

    # All three runs were observed, no cross-stream contamination.
    assert len(seen_run_ids) == 3
