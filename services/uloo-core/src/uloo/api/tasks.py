"""Task API endpoints - the root of the ULOO main flow."""

import json
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import transaction_session
from ..errors import ApiError
from ..logging import get_logger
from ..models.agent import AgentDefinition
from ..models.task import TASK_STATUSES, Task, TaskPlan
from ..models.team import TeamDefinition
from ..runtime import build_planner_agent, execute_agent
from ..runtime.models import RuntimeConfigurationError
from ..runtime.resources import load_resources
from ..schemas.task import (
    TaskCreate,
    TaskDetailResponse,
    TaskListResponse,
    TaskPlanDraft,
    TaskPlanRequest,
    TaskPlanResponse,
    TaskResponse,
)
from ..security import RequestContext, require_service_context

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = get_logger(__name__)


@router.post("", response_model=TaskDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreate,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Create a task and persist the user's original request."""
    team_id: uuid.UUID | None = None
    if body.team_id:
        try:
            team_id = uuid.UUID(body.team_id)
        except ValueError as exc:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "team_id must be a UUID",
                details={"team_id": body.team_id},
            ) from exc
        team = await db.execute(
            select(TeamDefinition).where(
                TeamDefinition.id == team_id,
                TeamDefinition.workspace_id == context.workspace_id,
                TeamDefinition.deleted_at.is_(None),
                TeamDefinition.enabled == True,
            )
        )
        if not team.scalar_one_or_none():
            raise ApiError(404, "TEAM_NOT_FOUND", f"Team {body.team_id} not found or disabled in this workspace")

    task = Task(
        workspace_id=context.workspace_id,
        title=body.title,
        query=body.query,
        status="draft",
        team_id=team_id,
    )
    db.add(task)
    await db.flush()
    logger.info("task_created", task_id=str(task.id), title=task.title)
    return TaskDetailResponse(**task.to_dict(), plans=[])


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    q: str | None = Query(None, description="Search in title and query"),
    status_filter: str | None = Query(None, alias="status", description="Filter by lifecycle status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """List tasks in the current workspace, newest first."""
    filters = [Task.workspace_id == context.workspace_id]
    if q:
        pattern = f"%{q}%"
        filters.append(or_(Task.title.ilike(pattern), Task.query.ilike(pattern)))
    if status_filter:
        if status_filter not in TASK_STATUSES:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                f"status must be one of: {', '.join(TASK_STATUSES)}",
                details={"status": status_filter},
            )
        filters.append(Task.status == status_filter)

    total = await db.scalar(select(func.count()).select_from(Task).where(*filters))
    stmt = select(Task).where(*filters).order_by(Task.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return TaskListResponse(
        items=[TaskResponse(**task.to_dict()) for task in result.scalars().all()],
        total=total or 0,
        offset=offset,
        limit=limit,
    )


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(
    task_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Return a task with its plans (plans appear once the Planner has produced them)."""
    task = await _get_task_or_404(task_id, context.workspace_id, db)
    plans = await db.execute(select(TaskPlan).where(TaskPlan.task_id == task.id).order_by(TaskPlan.version.asc()))
    return TaskDetailResponse(
        **task.to_dict(),
        plans=[TaskPlanResponse.model_validate(plan.to_dict()) for plan in plans.scalars().all()],
    )


@router.post("/{task_id}/plan", response_model=TaskPlanResponse, status_code=status.HTTP_201_CREATED)
async def plan_task(
    task_id: uuid.UUID,
    body: TaskPlanRequest | None = None,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Use a real structured Agno Planner run to create the next plan version."""
    task = await _get_task_or_404(task_id, context.workspace_id, db)
    if task.status in {"running", "succeeded", "cancelled"}:
        raise ApiError(409, "INVALID_TASK_STATE", f"Task in status '{task.status}' cannot be replanned")

    team_filters = [
        TeamDefinition.workspace_id == context.workspace_id,
        TeamDefinition.deleted_at.is_(None),
        TeamDefinition.enabled == True,
    ]
    if task.team_id:
        team_filters.append(TeamDefinition.id == task.team_id)
    teams_result = await db.execute(select(TeamDefinition).where(*team_filters).order_by(TeamDefinition.created_at))
    teams = list(teams_result.scalars().all())
    if not teams:
        raise ApiError(409, "TEAM_NOT_FOUND", "No enabled Agent team is available for planning")

    member_ids = {membership.agent_id for team in teams for membership in team.members}
    agents_result = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.workspace_id == context.workspace_id,
            AgentDefinition.id.in_(member_ids),
            AgentDefinition.deleted_at.is_(None),
            AgentDefinition.enabled == True,
        )
    )
    agents = {agent.id: agent for agent in agents_result.scalars().all()}
    candidates: list[dict[str, object]] = []
    allowed_agents_by_team: dict[str, set[str]] = {}
    for team in teams:
        team_id = str(team.id)
        members = [
            {
                "id": str(agent.id),
                "name": agent.name,
                "role": agent.role,
                "tool_refs": agent.tool_refs,
                "skill_refs": agent.skill_refs,
            }
            for membership in team.members
            if (agent := agents.get(membership.agent_id)) is not None
        ]
        candidates.append(
            {
                "id": team_id,
                "name": team.name,
                "description": team.description,
                "mode": team.mode,
                "members": members,
            }
        )
        allowed_agents_by_team[team_id] = {str(member["id"]) for member in members}
    resources = await load_resources(db, context.workspace_id)
    prompt = json.dumps(
        {
            "task": {"title": task.title, "query": task.query},
            "feedback": body.feedback if body else None,
            "candidate_teams": candidates,
            "resource_catalog": resources.snapshot,
        },
        ensure_ascii=False,
    )

    try:
        planner = build_planner_agent()
        result = await execute_agent(planner, prompt, timeout_seconds=settings.model_timeout_seconds)
        draft = TaskPlanDraft.model_validate(result.output)
    except RuntimeConfigurationError as exc:
        raise ApiError(503, exc.code, exc.message) from exc
    except TimeoutError as exc:
        raise ApiError(504, "TIMEOUT", "Planner run timed out") from exc
    except Exception as exc:
        logger.exception("task_planning_failed", task_id=str(task.id))
        raise ApiError(502, "MODEL_ERROR", "Planner did not return a valid structured plan") from exc

    allowed_agents = allowed_agents_by_team.get(draft.recommended_team_id)
    if allowed_agents is None:
        raise ApiError(502, "MODEL_ERROR", "Planner selected a team outside the supplied candidates")
    invalid_agents = sorted(
        {
            step.assigned_agent_id
            for step in draft.steps
            if step.assigned_agent_id and step.assigned_agent_id not in allowed_agents
        }
    )
    if invalid_agents:
        raise ApiError(502, "MODEL_ERROR", "Planner assigned Agents outside the selected team", details=invalid_agents)

    latest_version = await db.scalar(select(func.max(TaskPlan.version)).where(TaskPlan.task_id == task.id))
    plan = TaskPlan(
        task_id=task.id,
        version=(latest_version or 0) + 1,
        summary=draft.summary,
        assumptions=draft.assumptions,
        questions=draft.questions,
        recommended_team_id=uuid.UUID(draft.recommended_team_id),
        steps=[step.model_dump(mode="json") for step in draft.steps],
        approval_status="pending",
        runtime_type="agno",
        is_mock=False,
        planner_run_id=result.run_id,
        usage=result.usage,
    )
    db.add(plan)
    task.team_id = plan.recommended_team_id
    task.current_plan_version = plan.version
    task.status = "awaiting_approval"
    await db.flush()
    logger.info("task_plan_created", task_id=str(task.id), plan_id=str(plan.id), version=plan.version)
    return TaskPlanResponse.model_validate(plan.to_dict())


async def _get_task_or_404(
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession,
) -> Task:
    stmt = select(Task).where(Task.id == task_id, Task.workspace_id == workspace_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task:
        raise ApiError(404, "TASK_NOT_FOUND", f"Task {task_id} not found")
    return task
