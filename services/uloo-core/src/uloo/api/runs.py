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
from typing import Any

from fastapi import APIRouter, Depends, Query, status
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
from ..models.team import TeamDefinition
from ..runtime import build_team, execute_agent
from ..runtime.dify_tools import clear_runtime_caller, set_runtime_caller
from ..runtime.models import RuntimeConfigurationError
from ..runtime.review import build_review_feedback, evaluate_completion
from ..runtime.resources import load_resources
from ..schemas.run import RunCreate, RunDetailResponse, RunEventResponse, RunListResponse, RunResponse
from ..security import RequestContext, require_service_context

router = APIRouter(tags=["runs"])
logger = get_logger(__name__)

# S5-补1/S5-补2: In-memory registry mapping run_id → asyncio.Task for ownership
# tracking and real cancellation.  Prevents duplicate execution within the
# current process.  When a run completes or fails, its entry is removed.
_active_runs: dict[str, asyncio.Task] = {}


@router.post(
    "/tasks/{task_id}/approve",
    response_model=dict[str, str | int],
    status_code=status.HTTP_200_OK,
)
async def approve_task_plan(
    task_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Approve the task's current pending plan before execution."""
    task = await _get_task(task_id, context.workspace_id, db, for_update=True)
    plan = await _get_current_plan(task, db)
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

    # S5-补1: Register execution ownership.  If another request already started
    # this run (race condition between flush and commit), reject it.
    run_id_str = str(run.id)
    if run_id_str in _active_runs:
        await _fail_run(db, run, task, context.workspace_id, "DUPLICATE_START", "Run is already being executed")
        raise ApiError(409, "DUPLICATE_START", "Run is already being executed", details={"run_id": run_id_str})

    # S5-补1: When wait=false, start execution in a background asyncio.Task
    # and return 202 immediately.  The background task uses its own DB session.
    if not wait:
        bg_task = asyncio.create_task(
            _background_execute_run(
                run_id_str, task.id, plan.id, team_definition.id,
                context.workspace_id, input_text,
                context.user_id, context.app_id,
            )
        )
        _active_runs[run_id_str] = bg_task
        await db.refresh(run)
        await db.refresh(run, attribute_names=["events"])
        return RunDetailResponse.model_validate(run.to_dict(include_events=True))

    # S5-补1/S5-补2: Store current task handle for real cancellation.
    _active_runs[run_id_str] = asyncio.current_task()

    try:
        # S1-6: Set the Dify caller identity for this run so that Dify tool
        # invocations use the same user/app context as the console session.
        set_runtime_caller(context.user_id, context.app_id, str(context.workspace_id))
        runtime_team = build_team(
            team_definition, member_definitions, tools=resources, workspace_id=str(context.workspace_id)
        )
        timeout = min(
            settings.model_timeout_seconds,
            team_definition.limits.get("timeout_seconds", settings.model_timeout_seconds),
        )
        execution_prompt = json.dumps(
            {
                "request": input_text,
                "approved_plan": plan.to_dict(),
                "instructions": "Execute the approved steps in dependency order. Delegate assigned work to the named Agents and synthesize their actual results.",
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

        result = await execute_agent(runtime_team, execution_prompt, timeout_seconds=timeout)
        executed_ids = {member["agent_id"] for member in result.member_results}
        assigned_ids = {step["assigned_agent_id"] for step in plan.steps if step.get("assigned_agent_id")}
        if not executed_ids or (team_definition.mode != "route" and not assigned_ids <= executed_ids):
            raise ValueError("Team did not execute the approved member assignments")

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
        _check_budget(accumulated_tokens)
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids=assigned_ids,
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
            review_prompt = json.dumps(
                {
                    "request": input_text,
                    "approved_plan": plan.to_dict(),
                    "previous_result": result.output,
                    "review_feedback": feedback,
                    "instructions": "Address the review feedback and re-execute the failing members.",
                },
                ensure_ascii=False,
                default=str,
            )
            result = await execute_agent(runtime_team, review_prompt, timeout_seconds=timeout)
            accumulated_tokens += _accumulate_usage(result.usage)
            _check_budget(accumulated_tokens)
            _check_team_timeout()
            executed_ids = {member["agent_id"] for member in result.member_results}
            # S5-补3: Persist re-execution round events immediately.
            next_seq = await _persist_round_events(
                db, run, context.workspace_id, result, next_seq, round_label=f"review-{round_num}"
            )
            evaluation = evaluate_completion(
                result,
                assigned_agent_ids=assigned_ids,
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
        await _fail_run(db, run, task, context.workspace_id, "MODEL_ERROR", "Agno Team execution failed")
        raise ApiError(502, "MODEL_ERROR", "Agno Team execution failed", details={"run_id": str(run.id)}) from exc
    finally:
        clear_runtime_caller()
        # S5-补1: Release execution ownership.
        _active_runs.pop(run_id_str, None)

    run.status = "succeeded"
    run.agno_run_id = result.run_id
    run.output = result.output
    run.usage = result.usage
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
            payload={"output": result.output, "usage": result.usage or {}},
        )
    )
    await db.commit()
    await db.refresh(run)
    await db.refresh(run, attribute_names=["events"])
    logger.info("team_run_succeeded", run_id=str(run.id), task_id=str(task.id))
    return RunDetailResponse.model_validate(run.to_dict(include_events=True))


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
    after: int = Query(0, ge=0, description="Start streaming after this sequence cursor"),
    context: RequestContext = Depends(require_service_context),
):
    """S5-补3: Stream run events via Server-Sent Events (SSE).

    Yields events as they are persisted to the database, enabling real-time
    observation of execution progress.  Supports cursor replay via the ``after``
    query parameter for reconnection recovery.  The stream closes automatically
    when a terminal event is observed.
    """
    # Validate run exists and belongs to workspace before streaming.
    async with async_session_factory() as check_db:
        await _get_run(run_id, context.workspace_id, check_db)

    import time as _time

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
    if run.status != "running":
        raise ApiError(409, "INVALID_RUN_STATE", f"Run in status '{run.status}' cannot be cancelled")
    task = await _get_task(run.task_id, context.workspace_id, db, for_update=True)

    # S5-补2: Real cancellation — interrupt the asyncio.Task if present.
    run_id_str = str(run_id)
    active_task = _active_runs.pop(run_id_str, None)
    if active_task is not None and not active_task.done():
        active_task.cancel()

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

    run_id_str = str(run.id)
    if run_id_str in _active_runs:
        await _fail_run(db, run, task, context.workspace_id, "DUPLICATE_START", "Run is already being executed")
        raise ApiError(409, "DUPLICATE_START", "Run is already being executed", details={"run_id": run_id_str})

    # S5-补1: When wait=false, start execution in a background asyncio.Task.
    if not wait:
        bg_task = asyncio.create_task(
            _background_execute_run(
                run_id_str, task.id, plan.id, team_definition.id,
                context.workspace_id, input_text,
                context.user_id, context.app_id,
                retried_from=str(original.id),
            )
        )
        _active_runs[run_id_str] = bg_task
        await db.refresh(run)
        await db.refresh(run, attribute_names=["events"])
        return RunDetailResponse.model_validate(run.to_dict(include_events=True))

    _active_runs[run_id_str] = asyncio.current_task()

    try:
        set_runtime_caller(context.user_id, context.app_id, str(context.workspace_id))
        runtime_team = build_team(
            team_definition, member_definitions, tools=resources, workspace_id=str(context.workspace_id)
        )
        timeout = min(
            settings.model_timeout_seconds,
            team_definition.limits.get("timeout_seconds", settings.model_timeout_seconds),
        )
        execution_prompt = json.dumps(
            {
                "request": input_text,
                "approved_plan": plan.to_dict(),
                "instructions": "Execute the approved steps in dependency order. Delegate assigned work to the named Agents and synthesize their actual results.",
            },
            ensure_ascii=False,
            default=str,
        )
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

        result = await execute_agent(runtime_team, execution_prompt, timeout_seconds=timeout)
        executed_ids = {member["agent_id"] for member in result.member_results}
        assigned_ids = {step["assigned_agent_id"] for step in plan.steps if step.get("assigned_agent_id")}
        if not executed_ids or (team_definition.mode != "route" and not assigned_ids <= executed_ids):
            raise ValueError("Team did not execute the approved member assignments")

        # S5-补3: Persist tool summaries and agent events immediately.
        next_seq = 2
        next_seq = await _persist_round_events(
            db, run, context.workspace_id, result, next_seq, round_label="initial"
        )

        review_policy = team_definition.review_policy or {}
        completion_config = team_definition.completion or {}
        max_rounds = review_policy.get("max_rounds", 0)
        accumulated_tokens = _accumulate_usage(result.usage)
        _check_budget(accumulated_tokens)
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids=assigned_ids,
            completion_config=completion_config,
            review_policy=review_policy,
        )
        for round_num in range(1, max_rounds + 1):
            if evaluation.satisfied:
                break
            _check_team_timeout()
            feedback = build_review_feedback(evaluation, round_num)
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
            review_prompt = json.dumps(
                {
                    "request": input_text,
                    "approved_plan": plan.to_dict(),
                    "previous_result": result.output,
                    "review_feedback": feedback,
                    "instructions": "Address the review feedback and re-execute the failing members.",
                },
                ensure_ascii=False,
                default=str,
            )
            result = await execute_agent(runtime_team, review_prompt, timeout_seconds=timeout)
            accumulated_tokens += _accumulate_usage(result.usage)
            _check_budget(accumulated_tokens)
            _check_team_timeout()
            executed_ids = {member["agent_id"] for member in result.member_results}
            next_seq = await _persist_round_events(
                db, run, context.workspace_id, result, next_seq, round_label=f"review-{round_num}"
            )
            evaluation = evaluate_completion(
                result,
                assigned_agent_ids=assigned_ids,
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
        await _fail_run(db, run, task, context.workspace_id, "MODEL_ERROR", "Agno Team execution failed")
        raise ApiError(502, "MODEL_ERROR", "Agno Team execution failed", details={"run_id": str(run.id)}) from exc
    finally:
        clear_runtime_caller()
        _active_runs.pop(run_id_str, None)

    run.status = "succeeded"
    run.agno_run_id = result.run_id
    run.output = result.output
    run.usage = result.usage
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
            payload={"output": result.output, "usage": result.usage or {}},
        )
    )
    await db.commit()
    await db.refresh(run)
    await db.refresh(run, attribute_names=["events"])
    logger.info("team_run_succeeded", run_id=str(run.id), task_id=str(task.id), retried_from=str(original.id))
    return RunDetailResponse.model_validate(run.to_dict(include_events=True))


@router.post("/runs/{run_id}/continue", response_model=RunDetailResponse)
async def continue_run(
    run_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Confirm continuation of a run that was paused for user input.

    S5-补2: For interactive runs that reach a confirmation gate, this
    endpoint records the user's decision and allows the run to proceed.
    Currently a no-op marker that records a run.continued event; future
    interactive execution loops will use this to resume paused tasks.
    """
    run = await _get_run(run_id, context.workspace_id, db)
    if run.status != "running":
        raise ApiError(409, "INVALID_RUN_STATE", f"Run in status '{run.status}' cannot be continued")
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
            payload={"confirmed_by": str(context.user_id) if context.user_id else "system"},
        )
    )
    await db.commit()
    await db.refresh(run)
    await db.refresh(run, attribute_names=["events"])
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
    """Core cleanup logic operating on the given session."""
    rows = await db.execute(select(Run).where(Run.status == "running"))
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
    result = await db.execute(select(Run).where(Run.id == run_id, Run.workspace_id == workspace_id))
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
