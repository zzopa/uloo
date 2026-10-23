# AI生成
"""Durable real Agno Team Run APIs.

S5-补1: Run execution has execution ownership and duplicate start protection.
An in-memory registry (_active_runs) tracks which runs are currently being
executed, preventing concurrent duplicate executions within the same process.
When wait=false, execution starts in a background asyncio.Task and the API
returns 202 immediately; clients poll /runs/{id}/events or use the SSE
stream at /runs/{id}/stream for real-time progress.

S5-补2: Cancel calls task.cancel() on the asyncio.Task to actually interrupt
long-running model calls, not just flip a DB status flag.

S5-补3: SSE endpoint /runs/{id}/stream yields events as they are persisted.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import async_session_factory, transaction_session
from ..errors import ApiError
from ..logging import get_logger
from ..models.agent import AgentDefinition
from ..models.run import RUN_STATUSES, Run, RunEvent
from ..models.task import Task, TaskPlan
from ..models.team import TeamDefinition, TeamMember
from ..runtime import build_team, execute_agent
from ..runtime.dify_tools import clear_runtime_caller, set_runtime_caller
from ..runtime.memory import format_memories_for_prompt, recall_memories
from ..runtime.models import RuntimeConfigurationError
from ..runtime.resources import ResourceRegistry, load_resources
from ..runtime.review import build_review_feedback, evaluate_completion
from ..schemas.run import (
    RunContinueRequest,
    RunCreate,
    RunDetailResponse,
    RunEventResponse,
    RunListResponse,
    RunResponse,
)
from ..schemas.task import TaskPlanApprovalRequest
from ..security import RequestContext, require_service_context

router = APIRouter(tags=["runs"])
logger = get_logger(__name__)

# S5-补1/S5-补2: In-memory registry mapping run_id → asyncio.Task for ownership
# tracking and real cancellation.  Prevents duplicate execution within the
# current process.  When a run completes or fails, its entry is removed.
_active_runs: dict[str, asyncio.Task] = {}

# P0/S3-5: Real human-continue pause/resume registry.
# When a run's first review round fails and the team's review_policy has
# ``require_human_input=True``, the background executor sets the run status
# to ``awaiting_input`` and blocks on an asyncio.Event stored here.  The
# ``/continue`` endpoint stores the user's supplement in ``_paused_inputs``
# and sets the event so the executor resumes from the exact pause point,
# incorporating the supplement into the next rework round's prompt.
_paused_runs: dict[str, asyncio.Event] = {}
_paused_inputs: dict[str, dict[str, str]] = {}


@router.post(
    "/tasks/{task_id}/approve",
    response_model=dict[str, str | int],
    status_code=status.HTTP_200_OK,
)
async def approve_task_plan(
    task_id: uuid.UUID,
    body: TaskPlanApprovalRequest | None = None,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Approve the task's current pending plan before execution."""
    task = await _get_task(task_id, context.workspace_id, db, for_update=True)
    plan = await _get_current_plan(task, db)
    if body and body.expected_version is not None and plan.version != body.expected_version:
        raise ApiError(409, "VERSION_CONFLICT", "Plan changed; reload it before executing")
    if task.status in {"running", "succeeded", "cancelled"}:
        raise ApiError(409, "INVALID_TASK_STATE", "Task cannot approve another execution plan in its current state")
    if plan.approval_status == "rejected":
        raise ApiError(409, "PLAN_REJECTED", "Rejected plans must be regenerated before approval")
    plan.approval_status = "approved"
    await db.flush()
    return {"task_id": str(task.id), "plan_id": str(plan.id), "version": plan.version, "status": "approved"}


@router.post(
    "/tasks/{task_id}/runs",
    response_model=RunDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_task_run(
    task_id: uuid.UUID,
    response: Response,
    body: RunCreate | None = None,
    wait: bool = Query(True, description="If false, execute in background and return 202 immediately"),
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Execute the approved plan through a real Agno Team and persist safe events.

    S5-补1: Execution ownership is tracked via _active_runs to prevent
    duplicate concurrent executions.  The DB row lock (for_update=True)
    plus the in-memory check provide two-layer duplicate start protection.
    When wait=false, execution starts in a background asyncio.Task and
    the API returns 202 with run_id and status=running immediately.
    """
    task = await _get_task(task_id, context.workspace_id, db, for_update=True)
    plan = await _get_current_plan(task, db)
    if body and body.expected_plan_version is not None and plan.version != body.expected_plan_version:
        raise ApiError(409, "VERSION_CONFLICT", "Plan changed; reload it before executing")
    if plan.approval_status != "approved":
        raise ApiError(409, "PLAN_APPROVAL_REQUIRED", "Approve the current plan before starting a run")
    if task.status in {"running", "succeeded", "cancelled"}:
        raise ApiError(409, "INVALID_TASK_STATE", f"Task in status '{task.status}' cannot start a run")
    if not plan.recommended_team_id:
        raise ApiError(409, "TEAM_NOT_FOUND", "The approved plan has no recommended team")

    team_result = await db.execute(
        select(TeamDefinition).where(
            TeamDefinition.id == plan.recommended_team_id,
            TeamDefinition.workspace_id == context.workspace_id,
            TeamDefinition.deleted_at.is_(None),
            TeamDefinition.enabled == True,
        )
    )
    team_definition = team_result.scalar_one_or_none()
    if not team_definition:
        raise ApiError(409, "TEAM_NOT_FOUND", "The approved plan's team is missing or disabled")

    member_ids = [membership.agent_id for membership in team_definition.members]
    agents_result = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.workspace_id == context.workspace_id,
            AgentDefinition.id.in_(member_ids),
            AgentDefinition.deleted_at.is_(None),
        )
    )
    member_definitions = list(agents_result.scalars().all())
    if len(member_definitions) != len(member_ids) or any(not agent.enabled for agent in member_definitions):
        raise ApiError(409, "INVALID_TEAM_CONFIGURATION", "All team members must exist and be enabled")
    resources = await load_resources(db, context.workspace_id)
    skill_keys = {ref for agent in member_definitions for ref in (agent.skill_refs or [])}
    bound_skills = [skill for skill in resources.snapshot["skills"] if skill["key"] in skill_keys]
    tool_keys = {ref for agent in member_definitions for ref in (agent.tool_refs or [])}
    tool_keys.update(ref for skill in bound_skills for ref in skill["config"]["tool_refs"])
    resource_snapshot = {"skills": bound_skills, "tools": [tool for tool in resources.snapshot["tools"] if tool["key"] in tool_keys]}
    input_text = body.input if body and body.input else task.query
    run = Run(
        workspace_id=context.workspace_id,
        task_id=task.id,
        plan_id=plan.id,
        team_id=team_definition.id,
        status="running",
        input_text=input_text,
        started_at=datetime.now(UTC),
        configuration_snapshot={
            "team": team_definition.to_dict(),
            "agents": [agent.to_dict() for agent in sorted(member_definitions, key=lambda agent: str(agent.id))],
            "plan": plan.to_dict(),
            "resources": resource_snapshot,
        },
    )
    db.add(run)
    await db.flush()
    db.add(
        RunEvent(
            workspace_id=context.workspace_id,
            run_id=run.id,
            sequence=1,
            event_type="run.started",
            source_type="team",
            source_id=str(team_definition.id),
            status="running",
            payload={"task_id": str(task.id), "plan_id": str(plan.id), "team_name": team_definition.name},
        )
    )
    task.status = "running"
    await db.commit()

    await db.refresh(run, attribute_names=["events"])
    if not wait:
        result = RunDetailResponse.model_validate(run.to_dict(include_events=True))
        _active_runs[str(run.id)] = asyncio.create_task(_background_execute_run(run.id, context))
        response.status_code = status.HTTP_202_ACCEPTED
        return result
    return await _execute_owned_run(db, run, task, plan, team_definition, member_definitions, resources, context)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    task_id: uuid.UUID | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """List persisted runs in the current workspace."""
    filters = [Run.workspace_id == context.workspace_id]
    if task_id:
        filters.append(Run.task_id == task_id)
    if status_filter:
        if status_filter not in RUN_STATUSES:
            raise ApiError(422, "VALIDATION_ERROR", f"status must be one of: {', '.join(RUN_STATUSES)}")
        filters.append(Run.status == status_filter)
    total = await db.scalar(select(func.count()).select_from(Run).where(*filters))
    rows = await db.execute(select(Run).where(*filters).order_by(Run.created_at.desc()).offset(offset).limit(limit))
    return RunListResponse(
        items=[RunResponse.model_validate(run.to_dict()) for run in rows.scalars().all()],
        total=total or 0,
        offset=offset,
        limit=limit,
    )


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(
    run_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Return one run and its ordered observable events."""
    run = await _get_run(run_id, context.workspace_id, db)
    return RunDetailResponse.model_validate(run.to_dict(include_events=True))


@router.get("/runs/{run_id}/events", response_model=list[RunEventResponse])
async def get_run_events(
    run_id: uuid.UUID,
    after: int = Query(0, ge=0),
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Return ordered events after a sequence cursor for polling.

    S5-补3: For real-time streaming, use the SSE endpoint at
    /runs/{run_id}/stream instead.  This endpoint remains for
    cursor-based polling and reconnection recovery.
    """
    await _get_run(run_id, context.workspace_id, db)
    rows = await db.execute(
        select(RunEvent)
        .where(
            RunEvent.run_id == run_id,
            RunEvent.workspace_id == context.workspace_id,
            RunEvent.sequence > after,
        )
        .order_by(RunEvent.sequence)
    )
    return [RunEventResponse.model_validate(event.to_dict()) for event in rows.scalars().all()]


# ---- S5-补3: SSE real-time event streaming ----

_TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled"})
_SSE_POLL_INTERVAL = 0.5  # seconds between DB polls
_SSE_MAX_IDLE = 300  # seconds without any new event before closing


@router.get("/runs/{run_id}/stream")
async def stream_run_events(
    run_id: uuid.UUID,
    request: Request,
    after: int | None = Query(
        None,
        ge=0,
        description=(
            "Start streaming after this sequence cursor. If omitted, the "
            "`Last-Event-ID` request header is used so that browsers' native "
            "EventSource automatically resumes on reconnect without replaying "
            "events the client already observed."
        ),
    ),
    context: RequestContext = Depends(require_service_context),
):
    """S5-补3: Stream run events via Server-Sent Events (SSE).

    Yields events as they are persisted to the database, enabling real-time
    observation of execution progress.  Supports cursor replay via the ``after``
    query parameter for explicit reconnection, and the standard ``Last-Event-ID``
    request header for browser-native EventSource reconnection.  The stream
    closes automatically when a terminal event is observed.

    P0/S5: When ``after`` is omitted, the ``Last-Event-ID`` header (sent
    automatically by ``EventSource`` on reconnect) is parsed as the cursor.
    A reconnecting client therefore only receives events whose sequence is
    strictly greater than the last one it acknowledged — no duplicate
    delivery, no skipped events.
    """
    # Validate run exists and belongs to workspace before streaming.
    async with async_session_factory() as check_db:
        await _get_run(run_id, context.workspace_id, check_db)

    import time as _time

    # P0/S5: Standard SSE reconnection cursor resolution.
    if after is None:
        last_event_id = request.headers.get("last-event-id")
        if last_event_id:
            try:
                after = int(last_event_id)
            except (TypeError, ValueError):
                after = 0
        else:
            after = 0
    start_cursor = after

    async def _event_stream():
        nonlocal start_cursor
        cursor = start_cursor
        idle_start = _time.monotonic()
        while True:
            async with async_session_factory() as poll_db:
                rows = await poll_db.execute(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id,
                        RunEvent.workspace_id == context.workspace_id,
                        RunEvent.sequence > cursor,
                    )
                    .order_by(RunEvent.sequence)
                )
                events = list(rows.scalars().all())

            if events:
                idle_start = _time.monotonic()
                for event in events:
                    cursor = event.sequence
                    payload = json.dumps(event.to_dict(), ensure_ascii=False, default=str)
                    yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {payload}\n\n"
                    if event.event_type in _TERMINAL_EVENTS:
                        return
            else:
                # No new events — check if run reached terminal state in DB.
                async with async_session_factory() as poll_db:
                    run_row = await poll_db.execute(
                        select(Run.status).where(
                            Run.id == run_id,
                            Run.workspace_id == context.workspace_id,
                        )
                    )
                    current_status = run_row.scalar_one_or_none()

                if current_status in {"succeeded", "failed", "cancelled"}:
                    yield f"event: terminal\ndata: {{\"status\": \"{current_status}\"}}\n\n"
                    return

                if _time.monotonic() - idle_start > _SSE_MAX_IDLE:
                    yield "event: timeout\ndata: {\"reason\": \"no events for 300s\"}\n\n"
                    return

            await asyncio.sleep(_SSE_POLL_INTERVAL)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- S5-补2: Cancel / Retry / Confirm-continue ----


@router.post("/runs/{run_id}/cancel", response_model=RunDetailResponse)
async def cancel_run(
    run_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Cancel a running run.

    S5-补2: Marks the run as 'cancelled', releases execution ownership,
    and records a run.cancelled event.  If the run is being executed by
    an asyncio.Task in this process, task.cancel() is called to actually
    interrupt long-running model calls.  Only runs in 'running' status
    can be cancelled.
    """
    run = await _get_run(run_id, context.workspace_id, db)
    if run.status not in ("running", "awaiting_input"):
        raise ApiError(409, "INVALID_RUN_STATE", f"Run in status '{run.status}' cannot be cancelled")
    # S5-补2: Real cancellation — interrupt the asyncio.Task if present.
    run_id_str = str(run_id)
    # P0/S3-5: If the run is paused awaiting input, release the pause event
    # so the background task unblocks and observes the cancellation.  Without
    # this the executor would block forever on the Event.
    pause_event = _paused_runs.pop(run_id_str, None)
    if pause_event is not None and not pause_event.is_set():
        pause_event.set()
    active_task = _active_runs.get(run_id_str)
    if active_task is not None and not active_task.done():
        active_task.cancel()
        await asyncio.gather(active_task, return_exceptions=True)
    _paused_inputs.pop(run_id_str, None)
    task = await _get_task(run.task_id, context.workspace_id, db, for_update=True)
    run = await _get_run(run_id, context.workspace_id, db)
    if run.status not in ("running", "awaiting_input"):
        raise ApiError(409, "INVALID_RUN_STATE", f"Run in status '{run.status}' cannot be cancelled")

    run.status = "cancelled"
    run.finished_at = datetime.now(UTC)
    task.status = "cancelled"
    max_seq = await db.scalar(
        select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)
    )
    db.add(
        RunEvent(
            workspace_id=context.workspace_id,
            run_id=run.id,
            sequence=(max_seq or 0) + 1,
            event_type="run.cancelled",
            source_type="user",
            source_id=str(context.user_id) if context.user_id else None,
            status="cancelled",
            payload={"reason": "user_cancelled"},
        )
    )
    await db.commit()
    await db.refresh(run)
    await db.refresh(run, attribute_names=["events"])
    logger.info("run_cancelled", run_id=str(run.id))
    return RunDetailResponse.model_validate(run.to_dict(include_events=True))


@router.post("/runs/{run_id}/retry", response_model=RunDetailResponse, status_code=status.HTTP_201_CREATED)
async def retry_run(
    run_id: uuid.UUID,
    response: Response,
    body: RunCreate | None = None,
    wait: bool = Query(True, description="If false, execute in background and return 202 immediately"),
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Retry a failed or cancelled run by starting a new execution.

    S5-补2: Creates a fresh Run for the same task/plan/team and executes
    it.  The original run remains untouched for audit history.  Only runs
    in 'failed' or 'cancelled' status can be retried.
    """
    original = await _get_run(run_id, context.workspace_id, db)
    if original.status not in {"failed", "cancelled"}:
        raise ApiError(409, "INVALID_RUN_STATE", f"Run in status '{original.status}' cannot be retried")
    task = await _get_task(original.task_id, context.workspace_id, db, for_update=True)
    if task.status not in {"failed", "cancelled"}:
        raise ApiError(409, "INVALID_TASK_STATE", f"Task in status '{task.status}' cannot be retried")
    plan = await _get_current_plan(task, db)
    if plan.approval_status != "approved":
        raise ApiError(409, "PLAN_APPROVAL_REQUIRED", "The plan must be approved before retrying")

    team_result = await db.execute(
        select(TeamDefinition).where(
            TeamDefinition.id == original.team_id,
            TeamDefinition.workspace_id == context.workspace_id,
            TeamDefinition.deleted_at.is_(None),
            TeamDefinition.enabled == True,
        )
    )
    team_definition = team_result.scalar_one_or_none()
    if not team_definition:
        raise ApiError(409, "TEAM_NOT_FOUND", "The run's team is missing or disabled")

    member_ids = [membership.agent_id for membership in team_definition.members]
    agents_result = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.workspace_id == context.workspace_id,
            AgentDefinition.id.in_(member_ids),
            AgentDefinition.deleted_at.is_(None),
        )
    )
    member_definitions = list(agents_result.scalars().all())
    if len(member_definitions) != len(member_ids) or any(not agent.enabled for agent in member_definitions):
        raise ApiError(409, "INVALID_TEAM_CONFIGURATION", "All team members must exist and be enabled")
    resources = await load_resources(db, context.workspace_id)
    skill_keys = {ref for agent in member_definitions for ref in (agent.skill_refs or [])}
    bound_skills = [skill for skill in resources.snapshot["skills"] if skill["key"] in skill_keys]
    tool_keys = {ref for agent in member_definitions for ref in (agent.tool_refs or [])}
    tool_keys.update(ref for skill in bound_skills for ref in skill["config"]["tool_refs"])
    resource_snapshot = {"skills": bound_skills, "tools": [tool for tool in resources.snapshot["tools"] if tool["key"] in tool_keys]}

    # S5-补2: External tool idempotency gate. Retry creates a fresh Run with the
    # same prompt, so any bound write-effect tool (dify/mcp_sse/mcp_stdio) or
    # Dify workflow app could be replayed by the LLM and trigger duplicate
    # writes/submits. Read-only tools (builtin web-search) are safe to replay.
    # Block the retry unless the caller explicitly acknowledges the replay risk
    # by setting ``confirm_write_replay=True`` in the request body.
    write_tool_keys = [
        tool["key"]
        for tool in resource_snapshot["tools"]
        if tool.get("config", {}).get("tool_type") in ("dify", "mcp_sse", "mcp_stdio")
    ]
    workflow_agents = [
        agent.key for agent in member_definitions
        if agent.workflow_refs
    ]
    if (write_tool_keys or workflow_agents) and not (body and body.confirm_write_replay):
        raise ApiError(
            409,
            "WRITE_REPLAY_NOT_CONFIRMED",
            (
                "Retry would replay write-effect tools or Dify workflows on a fresh run. "
                "Set confirm_write_replay=true in the request body to acknowledge the "
                "duplicate write/submit risk and proceed."
            ),
            details={
                "write_tool_keys": write_tool_keys,
                "workflow_agents": workflow_agents,
            },
        )
    input_text = body.input if body and body.input else original.input_text
    run = Run(
        workspace_id=context.workspace_id,
        task_id=task.id,
        plan_id=plan.id,
        team_id=team_definition.id,
        status="running",
        input_text=input_text,
        started_at=datetime.now(UTC),
        configuration_snapshot={
            "team": team_definition.to_dict(),
            "agents": [agent.to_dict() for agent in sorted(member_definitions, key=lambda agent: str(agent.id))],
            "plan": plan.to_dict(),
            "resources": resource_snapshot,
            "retried_from": str(original.id),
        },
    )
    db.add(run)
    await db.flush()
    db.add(
        RunEvent(
            workspace_id=context.workspace_id,
            run_id=run.id,
            sequence=1,
            event_type="run.started",
            source_type="team",
            source_id=str(team_definition.id),
            status="running",
            payload={"task_id": str(task.id), "plan_id": str(plan.id), "team_name": team_definition.name, "retried_from": str(original.id)},
        )
    )
    task.status = "running"
    await db.commit()

    await db.refresh(run, attribute_names=["events"])
    if not wait:
        result = RunDetailResponse.model_validate(run.to_dict(include_events=True))
        _active_runs[str(run.id)] = asyncio.create_task(_background_execute_run(run.id, context))
        response.status_code = status.HTTP_202_ACCEPTED
        return result
    return await _execute_owned_run(db, run, task, plan, team_definition, member_definitions, resources, context)


@router.post("/runs/{run_id}/continue", response_model=RunDetailResponse)
async def continue_run(
    run_id: uuid.UUID,
    body: RunContinueRequest | None = None,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Resume a run that was paused awaiting user input.

    P0/S3-5: Real human-continue.  When a run's first review round fails
    and the team's review_policy has ``require_human_input=True``, the
    background executor sets the run status to ``awaiting_input`` and
    blocks on an asyncio.Event in ``_paused_runs``.  This endpoint stores
    the user's ``supplement`` (clarifying conditions, additional context)
    in ``_paused_inputs``, records a ``run.continued`` event carrying the
    supplement, and sets the event so the executor resumes from the exact
    pause point.  The supplement is incorporated into the next rework
    round's review_prompt so the team actually uses the human input
    instead of just recording an event marker.

    A run must be in ``awaiting_input`` status to be continued.  Runs in
    ``running`` status are not pause-gated and calling this endpoint on
    them returns 409.  The supplement is mandatory (min_length=1).
    """
    run = await _get_run(run_id, context.workspace_id, db)
    if run.status != "awaiting_input":
        raise ApiError(
            409,
            "INVALID_RUN_STATE",
            f"Run in status '{run.status}' cannot be continued; "
            "only 'awaiting_input' runs accept user supplement",
        )
    if body is None or not body.supplement.strip():
        raise ApiError(400, "SUPPLEMENT_REQUIRED", "A non-empty supplement is required to continue")
    run_id_str = str(run_id)
    supplement_text = body.supplement.strip()
    # Persist the supplement in the run.continued event BEFORE setting the
    # event so the executor observes a consistent (status=running, event
    # persisted) state on resume.
    max_seq = await db.scalar(
        select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)
    )
    db.add(
        RunEvent(
            workspace_id=context.workspace_id,
            run_id=run.id,
            sequence=(max_seq or 0) + 1,
            event_type="run.continued",
            source_type="user",
            source_id=str(context.user_id) if context.user_id else None,
            status="running",
            payload={
                "confirmed_by": str(context.user_id) if context.user_id else "system",
                "supplement": supplement_text,
            },
        )
    )
    run.status = "running"
    await db.commit()
    await db.refresh(run)
    await db.refresh(run, attribute_names=["events"])
    # Hand the supplement to the waiting background executor and release it.
    _paused_inputs[run_id_str] = {"supplement": supplement_text}
    pause_event = _paused_runs.get(run_id_str)
    if pause_event is not None:
        pause_event.set()
    return RunDetailResponse.model_validate(run.to_dict(include_events=True))


async def cleanup_leftover_runs(db: AsyncSession | None = None) -> int:
    """Mark all 'running' runs as 'failed' on startup.

    S5-补2: Called from the application lifespan on startup.  Any run left
    in 'running' status from a previous process crash or restart is marked
    as 'failed' with error_code='INTERRUPTED'.  Returns the count of
    cleaned-up runs.

    An optional ``db`` session can be passed for testing; in production the
    function creates its own session via ``async_session_factory``.
    """
    if db is not None:
        cleaned = await _do_cleanup(db)
    else:
        from ..db import async_session_factory
        async with async_session_factory() as session:
            cleaned = await _do_cleanup(session)
            if cleaned:
                await session.commit()
    _active_runs.clear()
    return cleaned


async def _do_cleanup(db: AsyncSession) -> int:
    """Core cleanup logic operating on the given session.

    P0/S3-5: Both ``running`` and ``awaiting_input`` runs are left over from
    a previous process crash or restart — the asyncio.Event the paused run
    was blocking on is gone, so the run can never be resumed.  Mark both
    as failed with error_code=INTERRUPTED so the UI shows a clear terminal
    state instead of a forever-paused run.
    """
    rows = await db.execute(select(Run).where(Run.status.in_(("running", "awaiting_input"))))
    leftover_runs = list(rows.scalars().all())
    for run in leftover_runs:
        run.status = "failed"
        run.error_code = "INTERRUPTED"
        run.error_message = "Run was interrupted by process restart"
        run.finished_at = datetime.now(UTC)
        task_result = await db.execute(select(Task).where(Task.id == run.task_id))
        task = task_result.scalar_one_or_none()
        if task and task.status == "running":
            task.status = "failed"
        max_seq = await db.scalar(
            select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)
        )
        db.add(
            RunEvent(
                workspace_id=run.workspace_id,
                run_id=run.id,
                sequence=(max_seq or 0) + 1,
                event_type="run.failed",
                source_type="system",
                status="failed",
                payload={"code": "INTERRUPTED", "message": "Run was interrupted by process restart"},
            )
        )
    if leftover_runs:
        logger.info("cleanup_leftover_runs", count=len(leftover_runs))
    return len(leftover_runs)


async def _persist_round_events(
    db: AsyncSession,
    run: Run,
    workspace_id: uuid.UUID,
    result,
    next_seq: int,
    *,
    round_label: str = "initial",
) -> int:
    """S5-补3: Persist tool.summary and agent.completed events for one execution round.

    Events are committed immediately so that clients polling the events
    endpoint with a cursor can observe progress in real time.
    Returns the next available sequence number.
    """
    seq = next_seq
    for tool_summary in result.tool_summaries:
        db.add(
            RunEvent(
                workspace_id=workspace_id,
                run_id=run.id,
                sequence=seq,
                event_type="tool.summary",
                source_type="tool",
                source_id=tool_summary.get("tool_name"),
                status="failed" if tool_summary.get("error") else "succeeded",
                payload={**tool_summary, "round": round_label},
            )
        )
        seq += 1
    for member_result in result.member_results:
        db.add(
            RunEvent(
                workspace_id=workspace_id,
                run_id=run.id,
                sequence=seq,
                event_type="agent.completed",
                source_type="agent",
                source_id=member_result["agent_id"],
                status="succeeded",
                payload={**member_result, "message": f"{member_result['name']} completed its assigned work", "round": round_label},
            )
        )
        seq += 1
    await db.commit()
    return seq


async def _get_task(task_id: uuid.UUID, workspace_id: uuid.UUID, db: AsyncSession, *, for_update: bool = False) -> Task:
    stmt = select(Task).where(Task.id == task_id, Task.workspace_id == workspace_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise ApiError(404, "TASK_NOT_FOUND", f"Task {task_id} not found")
    return task


async def _get_current_plan(task: Task, db: AsyncSession) -> TaskPlan:
    result = await db.execute(
        select(TaskPlan)
        .where(TaskPlan.task_id == task.id, TaskPlan.version == task.current_plan_version)
        .order_by(TaskPlan.created_at.desc())
    )
    plan = result.scalars().first()
    if not plan:
        raise ApiError(409, "PLAN_NOT_FOUND", "Create a plan before approval or execution")
    return plan


async def _get_run(run_id: uuid.UUID, workspace_id: uuid.UUID, db: AsyncSession) -> Run:
    result = await db.execute(
        select(Run).where(Run.id == run_id, Run.workspace_id == workspace_id).execution_options(populate_existing=True)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise ApiError(404, "RUN_NOT_FOUND", f"Run {run_id} not found")
    return run


async def _fail_run(
    db: AsyncSession,
    run: Run,
    task: Task,
    workspace_id: uuid.UUID,
    code: str,
    message: str,
) -> None:
    run.status = "failed"
    run.error_code = code
    run.error_message = message
    run.finished_at = datetime.now(UTC)
    task.status = "failed"
    max_seq = await db.scalar(
        select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)
    )
    db.add(
        RunEvent(
            workspace_id=workspace_id,
            run_id=run.id,
            sequence=(max_seq or 0) + 1,
            event_type="run.failed",
            source_type="system",
            status="failed",
            payload={"code": code, "message": message},
        )
    )
    await db.commit()


async def _background_execute_run(run_id: uuid.UUID, context: RequestContext) -> None:
    try:
        async with async_session_factory() as db:
            run = await _get_run(run_id, context.workspace_id, db)
            if run.status != "running":
                return
            task = await _get_task(run.task_id, context.workspace_id, db)
            # P0/S5: Rebuild execution inputs from the immutable snapshot
            # captured at Run creation. Do NOT re-read Team/Agent/Plan/Resource
            # from the database — they may have been edited between Run
            # creation and background execution, which would silently change
            # the review_policy, member list, plan steps or tool config that
            # the Run was authorised with. The snapshot is the source of truth.
            snapshot = run.configuration_snapshot or {}
            team = _rebuild_team_from_snapshot(snapshot.get("team") or {})
            member_definitions = _rebuild_agents_from_snapshot(snapshot.get("agents") or [])
            plan = _rebuild_plan_from_snapshot(snapshot.get("plan") or {})
            resources = _rebuild_resources_from_snapshot(
                snapshot.get("resources") or {}, context.workspace_id
            )
            if not member_definitions or not team.members:
                await _fail_run(db, run, task, context.workspace_id, "INVALID_TEAM_CONFIGURATION", "Run definition is missing")
                return
            await _execute_owned_run(db, run, task, plan, team, member_definitions, resources, context)
    except asyncio.CancelledError:
        raise
    except ApiError:
        # The execution boundary has already persisted the failure.
        logger.warning("background_run_failed", run_id=str(run_id))
    except Exception:
        logger.exception("background_run_failed", run_id=str(run_id))
        async with async_session_factory() as db:
            run = await _get_run(run_id, context.workspace_id, db)
            if run.status == "running":
                task = await _get_task(run.task_id, context.workspace_id, db)
                await _fail_run(db, run, task, context.workspace_id, "RUNTIME_ERROR", "Background execution failed")
    finally:
        _active_runs.pop(str(run_id), None)


def _rebuild_team_from_snapshot(snapshot: dict) -> TeamDefinition:
    """P0/S5: Rebuild a detached TeamDefinition from a configuration_snapshot.

    The returned object is not bound to any session; its fields reflect the
    state captured when the Run was created. Edits to the live Team row
    between Run creation and background execution do not affect this object,
    so the review_policy / limits / members / handoff_context used by the
    background executor are exactly what the Run was authorised with.
    """
    team = TeamDefinition()
    team.id = uuid.UUID(snapshot["id"]) if snapshot.get("id") else uuid.uuid4()
    team.key = snapshot.get("key") or ""
    team.name = snapshot.get("name") or ""
    team.description = snapshot.get("description")
    team.mode = snapshot.get("mode") or "coordinate"
    team.leader_agent_id = uuid.UUID(snapshot["leader_agent_id"]) if snapshot.get("leader_agent_id") else None
    team.instructions = snapshot.get("instructions") or []
    team.memory_policy = snapshot.get("memory_policy") or {"read_scopes": [], "write_scope": "team"}
    team.limits = snapshot.get("limits") or {}
    team.review_policy = snapshot.get("review_policy") or {}
    team.completion = snapshot.get("completion") or {}
    team.handoff_context = snapshot.get("handoff_context") or ""
    team.enabled = snapshot.get("enabled", True)
    team.version = snapshot.get("version", 1)
    team.members = [
        TeamMember(agent_id=uuid.UUID(agent_id))
        for agent_id in (snapshot.get("member_agent_ids") or [])
    ]
    return team


def _rebuild_agents_from_snapshot(snapshots: list[dict]) -> list[AgentDefinition]:
    """P0/S5: Rebuild detached AgentDefinitions from a configuration_snapshot."""
    agents: list[AgentDefinition] = []
    for snap in snapshots:
        agent = AgentDefinition()
        agent.id = uuid.UUID(snap["id"]) if snap.get("id") else uuid.uuid4()
        agent.key = snap.get("key") or ""
        agent.name = snap.get("name") or ""
        agent.description = snap.get("description")
        agent.role = snap.get("role") or ""
        agent.instructions = snap.get("instructions") or []
        agent.model_ref = snap.get("model_ref") or ""
        agent.tool_refs = snap.get("tool_refs") or []
        agent.skill_refs = snap.get("skill_refs") or []
        agent.knowledge_refs = snap.get("knowledge_refs") or []
        agent.workflow_refs = snap.get("workflow_refs") or []
        agent.output_schema = snap.get("output_schema")
        agent.enabled = snap.get("enabled", True)
        agent.version = snap.get("version", 1)
        agents.append(agent)
    return agents


def _rebuild_plan_from_snapshot(snapshot: dict) -> TaskPlan:
    """P0/S5: Rebuild a detached TaskPlan from a configuration_snapshot.

    The plan returned is not bound to the session; ``plan.steps`` mutations
    in _execute_run therefore do not silently overwrite the persisted plan
    row. Step completion status is already observable via RunEvent rows, so
    dropping the side-effect writeback is the correct immutable-input behaviour.
    """
    plan = TaskPlan()
    plan.id = uuid.UUID(snapshot["id"]) if snapshot.get("id") else uuid.uuid4()
    plan.task_id = uuid.UUID(snapshot["task_id"]) if snapshot.get("task_id") else uuid.uuid4()
    plan.version = snapshot.get("version", 1)
    plan.summary = snapshot.get("summary")
    plan.assumptions = snapshot.get("assumptions") or []
    plan.questions = snapshot.get("questions") or []
    plan.recommended_team_id = (
        uuid.UUID(snapshot["recommended_team_id"]) if snapshot.get("recommended_team_id") else None
    )
    plan.steps = snapshot.get("steps") or []
    plan.approval_status = snapshot.get("approval_status", "approved")
    plan.rejection_reason = snapshot.get("rejection_reason")
    plan.runtime_type = snapshot.get("runtime_type", "agno")
    plan.is_mock = snapshot.get("is_mock", False)
    plan.planner_run_id = snapshot.get("planner_run_id")
    plan.usage = snapshot.get("usage")
    return plan


def _rebuild_resources_from_snapshot(snapshot: dict, workspace_id) -> "ResourceRegistry":
    """P0/S5: Rebuild a ResourceRegistry from a configuration_snapshot.

    The tool/skill callables are reconstructed from the snapshot's tool
    configs, not from the live workspace catalog, so a tool edited after Run
    creation does not change the tools available to the background executor.
    """
    return ResourceRegistry(
        snapshot.get("tools") or [],
        snapshot.get("skills") or [],
        str(workspace_id),
    )


async def _execute_owned_run(
    db: AsyncSession, run: Run, task: Task, plan: TaskPlan, team_definition: TeamDefinition,
    member_definitions: list[AgentDefinition], resources: ResourceRegistry, context: RequestContext,
) -> RunDetailResponse:
    current = asyncio.current_task()
    assert current is not None
    _active_runs[str(run.id)] = current
    try:
        return await _execute_run(db, run, task, plan, team_definition, member_definitions, resources, context)
    finally:
        _active_runs.pop(str(run.id), None)
        # P0/S3-5: If the run was paused awaiting input and is now leaving
        # the executor (success, failure, or cancellation), release any
        # lingering pause event and clear the stored input so the memory
        # leak is bounded even on abnormal exit paths.
        run_id_str = str(run.id)
        ev = _paused_runs.pop(run_id_str, None)
        if ev is not None and not ev.is_set():
            ev.set()
        _paused_inputs.pop(run_id_str, None)


async def _execute_run(
    db: AsyncSession, run: Run, task: Task, plan: TaskPlan, team_definition: TeamDefinition,
    member_definitions: list[AgentDefinition], resources: ResourceRegistry, context: RequestContext,
) -> RunDetailResponse:
    input_text = run.input_text
    try:
        # S1-6: Set the Dify caller identity for this run so that Dify tool
        # invocations use the same user/app context as the console session.
        set_runtime_caller(context.user_id, context.app_id, str(context.workspace_id))
        query_text = input_text or task.query or ""
        recalled_memories = await recall_memories(db, context.workspace_id, query=query_text)
        recalled_context = format_memories_for_prompt(recalled_memories)
        runtime_team = build_team(
            team_definition,
            member_definitions,
            tools=resources,
            workspace_id=str(context.workspace_id),
            recalled_memories_context=recalled_context,
        )
        timeout = min(
            settings.model_timeout_seconds,
            team_definition.limits.get("timeout_seconds", settings.model_timeout_seconds),
        )
        execution_prompt = json.dumps(
            {
                "request": input_text,
                "approved_plan": plan.to_dict(),
                "long_term_memory": recalled_context if recalled_context else None,
                "instructions": (
                    "CRITICAL: Execute the approved steps in dependency order. "
                    "You are the coordination lead: you MUST delegate each assigned task to the named member Agent. "
                    "Do NOT answer on their behalf or skip member tool calls. "
                    "All assigned members must be invoked, and you must synthesize their actual findings into the final response. "
                    "SOURCE CITATION REQUIREMENT: Every fact, data point, quote, and finding MUST cite its real source using clickable Markdown links [Source Title](URL). "
                    "The URLs must come directly from search tool results. At the end of the report, you MUST provide a dedicated '### 🔗 数据出处与核验信源' section with clickable Markdown links."
                ),
            },
            ensure_ascii=False,
            default=str,
        )
        # S3-补2: Enforce team-level total token budget and wall-clock timeout
        # across all execution rounds (not just per execute_agent call).
        import time as _time

        limits = team_definition.limits or {}
        total_token_budget = limits.get("total_token_budget", 0)
        team_start = _time.monotonic()

        def _check_budget(accumulated_tokens: int) -> None:
            if total_token_budget > 0 and accumulated_tokens > total_token_budget:
                raise ValueError(
                    f"Team exceeded total token budget: {accumulated_tokens} > {total_token_budget}"
                )

        def _check_team_timeout() -> None:
            elapsed = _time.monotonic() - team_start
            if elapsed > timeout:
                raise TimeoutError(f"Team exceeded total time budget: {elapsed:.1f}s > {timeout}s")

        def _accumulate_usage(usage: dict | None) -> int:
            if not usage:
                return 0
            total = usage.get("total_tokens")
            if isinstance(total, int) and not isinstance(total, bool):
                return total
            return sum(
                usage.get(k, 0)
                for k in ("input_tokens", "output_tokens")
                if isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool)
            )

        def _merge_usage(accumulated: dict, usage: dict | None) -> None:
            """P0-3/P1: Merge one round's usage into the cumulative dict.

            ``_accumulate_usage`` returns an int for budget checks; this helper
            keeps the per-key breakdown (total/input/output) so the run's
            persisted ``usage`` reflects every round, not just the final one.
            """
            if not usage:
                return
            for key in ("total_tokens", "input_tokens", "output_tokens"):
                val = usage.get(key)
                if isinstance(val, int) and not isinstance(val, bool):
                    accumulated[key] = accumulated.get(key, 0) + val

        # P0-3/P1: Track cumulative usage across all rounds and the actual
        # number of rework rounds that executed (not the configured max).
        accumulated_usage: dict = {}
        actual_review_rounds = 0

        result = await execute_agent(runtime_team, execution_prompt, timeout_seconds=timeout)
        executed_ids = {member["agent_id"] for member in result.member_results}
        assigned_ids = {step["assigned_agent_id"] for step in plan.steps if step.get("assigned_agent_id")}
        if team_definition.mode != "route":
            if assigned_ids and not (assigned_ids <= executed_ids):
                raise ValueError("Team did not execute the approved member assignments")
            elif not assigned_ids and not executed_ids and not result.output:
                raise ValueError("Team did not execute the approved member assignments")
        else:
            if not executed_ids and not result.output:
                raise ValueError("Team did not produce any execution output")

        # S5-补3: Persist tool summaries and agent events immediately so that
        # clients polling /runs/{id}/events can observe progress in real time.
        next_seq = 2  # sequence 1 is run.started
        next_seq = await _persist_round_events(
            db, run, context.workspace_id, result, next_seq, round_label="initial"
        )

        # S3-补1: Evaluate completion conditions and re-execute with review feedback.
        review_policy = team_definition.review_policy or {}
        completion_config = team_definition.completion or {}
        max_rounds = review_policy.get("max_rounds", 0)
        accumulated_tokens = _accumulate_usage(result.usage)
        _merge_usage(accumulated_usage, result.usage)
        _check_budget(accumulated_tokens)
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids=executed_ids if team_definition.mode == "route" else assigned_ids,
            completion_config=completion_config,
            review_policy=review_policy,
        )
        for round_num in range(1, max_rounds + 1):
            if evaluation.satisfied:
                break
            _check_team_timeout()
            feedback = build_review_feedback(evaluation, round_num)
            # S5-补3: Persist review round event for real-time observation.
            db.add(
                RunEvent(
                    workspace_id=context.workspace_id,
                    run_id=run.id,
                    sequence=next_seq,
                    event_type="review.round",
                    source_type="system",
                    status="running",
                    payload={"round": round_num, "feedback": feedback, "reasons": list(evaluation.reasons)},
                )
            )
            next_seq += 1
            await db.commit()
            # P0/S3-5: Real human-continue pause point.  On the first review
            # round, if the team's review_policy has ``require_human_input``,
            # the run pauses for a human supplement instead of auto-reworking.
            # The background executor blocks here on an asyncio.Event until
            # the /continue endpoint stores a supplement and sets the event.
            # On resume, the supplement is merged into the rework prompt so
            # the team actually receives the human clarification.
            human_supplement: str | None = None
            if round_num == 1 and review_policy.get("require_human_input"):
                run.status = "awaiting_input"
                db.add(
                    RunEvent(
                        workspace_id=context.workspace_id,
                        run_id=run.id,
                        sequence=next_seq,
                        event_type="run.input_requested",
                        source_type="system",
                        status="awaiting_input",
                        payload={
                            "round": round_num,
                            "feedback": feedback,
                            "reasons": list(evaluation.reasons),
                            "prompt": "请补充关键条件（地区/材质/规格/含税与运费等），以便调研员重新执行并提交可核查来源。",
                        },
                    )
                )
                next_seq += 1
                await db.commit()
                await db.refresh(run)
                logger.info("run_awaiting_input", run_id=str(run.id), round=round_num)
                pause_event = asyncio.Event()
                _paused_runs[str(run.id)] = pause_event
                try:
                    await pause_event.wait()
                except asyncio.CancelledError:
                    # cancel_run set the event to unblock us; the run is
                    # being cancelled.  Re-raise so _execute_owned_run / the
                    # background wrapper observe the cancellation.
                    _paused_runs.pop(str(run.id), None)
                    _paused_inputs.pop(str(run.id), None)
                    raise
                user_input = _paused_inputs.pop(str(run.id), {})
                _paused_runs.pop(str(run.id), None)
                human_supplement = user_input.get("supplement")
                # The /continue endpoint already persisted run.continued and
                # set status back to running; we are now resuming.
                run.status = "running"
                await db.refresh(run)
                # P0/S3-5: Re-fetch the max sequence after the pause because
                # the /continue endpoint inserted a run.continued event with
                # a higher sequence while we were blocked.  Without this,
                # _persist_round_events would collide on the unique
                # (run_id, sequence) constraint.
                next_seq = (await db.scalar(
                    select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)
                ) or 0) + 1
            review_prompt = json.dumps(
                {
                    "request": input_text,
                    "approved_plan": plan.to_dict(),
                    "previous_result": result.output,
                    "review_feedback": feedback,
                    "instructions": "Address the review feedback and re-execute the failing members.",
                    **({"human_supplement": human_supplement} if human_supplement else {}),
                },
                ensure_ascii=False,
                default=str,
            )
            result = await execute_agent(runtime_team, review_prompt, timeout_seconds=timeout)
            # P1: This round actually executed — record it and merge usage.
            actual_review_rounds = round_num
            accumulated_tokens += _accumulate_usage(result.usage)
            _merge_usage(accumulated_usage, result.usage)
            _check_budget(accumulated_tokens)
            _check_team_timeout()
            executed_ids = {member["agent_id"] for member in result.member_results}
            # S5-补3: Persist re-execution round events immediately.
            next_seq = await _persist_round_events(
                db, run, context.workspace_id, result, next_seq, round_label=f"review-{round_num}"
            )
            evaluation = evaluate_completion(
                result,
                assigned_agent_ids=executed_ids if team_definition.mode == "route" else assigned_ids,
                completion_config=completion_config,
                review_policy=review_policy,
            )
        if not evaluation.satisfied:
            raise ValueError(
                f"Team execution did not meet completion conditions: {'; '.join(evaluation.reasons)}"
            )
    except RuntimeConfigurationError as exc:
        await _fail_run(db, run, task, context.workspace_id, exc.code, exc.message)
        raise ApiError(503, exc.code, exc.message, details={"run_id": str(run.id)}) from exc
    except TimeoutError as exc:
        await _fail_run(db, run, task, context.workspace_id, "TIMEOUT", "Team run timed out")
        raise ApiError(504, "TIMEOUT", "Team run timed out", details={"run_id": str(run.id)}) from exc
    except Exception as exc:
        logger.exception("team_run_failed", run_id=str(run.id), task_id=str(task.id))
        error_msg = str(exc).strip() or "Agno Team execution failed"
        await _fail_run(db, run, task, context.workspace_id, "MODEL_ERROR", error_msg)
        raise ApiError(502, "MODEL_ERROR", error_msg, details={"run_id": str(run.id)}) from exc
    finally:
        clear_runtime_caller()

    run.status = "succeeded"
    run.agno_run_id = result.run_id
    run.output = result.output
    # P1: run.usage is the cumulative usage across all rounds (initial +
    # every rework round), not just the final round's usage. Reviewers
    # reading GET /runs/{id} see the true total cost of the run.
    run.usage = accumulated_usage or result.usage
    run.finished_at = datetime.now(UTC)
    task.status = "succeeded"
    plan.steps = [
        {
            **step,
            "status": "completed"
            if not step.get("assigned_agent_id") or step["assigned_agent_id"] in executed_ids
            else "skipped",
        }
        for step in plan.steps
    ]
    # S5-补3: agent.completed events were already persisted during execution.
    # Only the final run.completed event is added here.
    max_seq = await db.scalar(
        select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)
    )
    db.add(
        RunEvent(
            workspace_id=context.workspace_id,
            run_id=run.id,
            sequence=(max_seq or 0) + 1,
            event_type="run.completed",
            source_type="team",
            source_id=str(team_definition.id),
            status="succeeded",
            payload={
                "output": result.output,
                # P1: cumulative usage across initial + all rework rounds.
                "usage": accumulated_usage or (result.usage or {}),
                # P1: actual rework rounds executed, not the configured max.
                "review_rounds": actual_review_rounds,
                "completion_satisfied": evaluation.satisfied,
            },
        )
    )
    await db.commit()
    await db.refresh(run)
    await db.refresh(run, attribute_names=["events"])
    logger.info("team_run_succeeded", run_id=str(run.id), task_id=str(task.id))
    return RunDetailResponse.model_validate(run.to_dict(include_events=True))
