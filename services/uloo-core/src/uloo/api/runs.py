"""Durable real Agno Team Run APIs."""

import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import transaction_session
from ..errors import ApiError
from ..logging import get_logger
from ..models.agent import AgentDefinition
from ..models.run import RUN_STATUSES, Run, RunEvent
from ..models.task import Task, TaskPlan
from ..models.team import TeamDefinition
from ..runtime import build_team, execute_agent
from ..runtime.models import RuntimeConfigurationError
from ..runtime.resources import load_resources
from ..schemas.run import RunCreate, RunDetailResponse, RunEventResponse, RunListResponse, RunResponse
from ..security import RequestContext, require_service_context

router = APIRouter(tags=["runs"])
logger = get_logger(__name__)


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
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Execute the approved plan through a real Agno Team and persist safe events."""
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

    try:
        runtime_team = build_team(team_definition, member_definitions, tools=resources)
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
        result = await execute_agent(runtime_team, execution_prompt, timeout_seconds=timeout)
        executed_ids = {member["agent_id"] for member in result.member_results}
        assigned_ids = {step["assigned_agent_id"] for step in plan.steps if step.get("assigned_agent_id")}
        if not executed_ids or (team_definition.mode != "route" and not assigned_ids <= executed_ids):
            raise ValueError("Team did not execute the approved member assignments")
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
    for sequence, member_result in enumerate(result.member_results, start=2):
        db.add(
            RunEvent(
                workspace_id=context.workspace_id,
                run_id=run.id,
                sequence=sequence,
                event_type="agent.completed",
                source_type="agent",
                source_id=member_result["agent_id"],
                status="succeeded",
                payload={**member_result, "message": f"{member_result['name']} completed its assigned work"},
            )
        )
    db.add(
        RunEvent(
            workspace_id=context.workspace_id,
            run_id=run.id,
            sequence=len(result.member_results) + 2,
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
    """Return ordered events after a sequence cursor; SSE is added in the next stage."""
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
    db.add(
        RunEvent(
            workspace_id=workspace_id,
            run_id=run.id,
            sequence=2,
            event_type="run.failed",
            source_type="system",
            status="failed",
            payload={"code": code, "message": message},
        )
    )
    await db.commit()
