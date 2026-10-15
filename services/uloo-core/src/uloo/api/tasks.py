# AI生成
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
from ..models.run import Run
from ..models.task import TASK_STATUSES, Task, TaskPlan
from ..models.team import TeamDefinition
from ..runtime import build_planner_agent, execute_agent
from ..runtime.models import RuntimeConfigurationError
from ..runtime.resources import load_resources
from ..schemas.task import (
    TaskCreate,
    TaskDetailResponse,
    TaskDirectExecuteRequest,
    TaskExportResponse,
    TaskListResponse,
    TaskPlanDraft,
    TaskPlanRequest,
    TaskPlanResponse,
    TaskPreCheckResponse,
    PreCheckIssue,
    TaskSupplementRequest,
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
        constraints=body.constraints,
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
            "task": {"title": task.title, "query": task.query, "constraints": task.constraints},
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


@router.get("/{task_id}/precheck", response_model=TaskPreCheckResponse)
async def precheck_task(
    task_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Check team/model/tool/knowledge dependencies before planning."""
    task = await _get_task_or_404(task_id, context.workspace_id, db)
    issues: list[PreCheckIssue] = []

    # Check team availability
    team_filters = [
        TeamDefinition.workspace_id == context.workspace_id,
        TeamDefinition.deleted_at.is_(None),
        TeamDefinition.enabled == True,
    ]
    if task.team_id:
        team_filters.append(TeamDefinition.id == task.team_id)
    teams_result = await db.execute(select(TeamDefinition).where(*team_filters))
    teams = list(teams_result.scalars().all())
    if not teams:
        issues.append(PreCheckIssue(
            category="team",
            status="missing",
            detail="No enabled team available for this task",
            fix_link="/uloo/teams",
        ))
    else:
        issues.append(PreCheckIssue(
            category="team",
            status="ok",
            detail=f"{len(teams)} team(s) available",
        ))

    # Check agent availability for the team(s)
    member_ids = {membership.agent_id for team in teams for membership in team.members}
    if member_ids:
        agents_result = await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.workspace_id == context.workspace_id,
                AgentDefinition.id.in_(member_ids),
                AgentDefinition.deleted_at.is_(None),
                AgentDefinition.enabled == True,
            )
        )
        agents = list(agents_result.scalars().all())
        if len(agents) < len(member_ids):
            issues.append(PreCheckIssue(
                category="model",
                status="misconfigured",
                detail=f"{len(member_ids) - len(agents)} agent(s) missing or disabled",
                fix_link="/uloo/agents",
            ))
        else:
            issues.append(PreCheckIssue(
                category="model",
                status="ok",
                detail=f"{len(agents)} agent(s) ready",
            ))

        # Check tool refs
        all_tool_refs = {ref for agent in agents for ref in (agent.tool_refs or [])}
        if all_tool_refs:
            issues.append(PreCheckIssue(
                category="tool",
                status="ok",
                detail=f"{len(all_tool_refs)} tool reference(s) configured",
            ))
        else:
            issues.append(PreCheckIssue(
                category="tool",
                status="missing",
                detail="No tools configured on team agents",
                fix_link="/uloo/agents",
            ))
    else:
        issues.append(PreCheckIssue(
            category="model",
            status="missing",
            detail="No agents assigned to team",
            fix_link="/uloo/teams",
        ))

    # Check knowledge resources
    resources = await load_resources(db, context.workspace_id)
    knowledge_count = len(resources.snapshot.get("datasets", [])) if isinstance(resources.snapshot, dict) else 0
    if knowledge_count > 0:
        issues.append(PreCheckIssue(
            category="knowledge",
            status="ok",
            detail=f"{knowledge_count} knowledge resource(s) available",
        ))
    else:
        issues.append(PreCheckIssue(
            category="knowledge",
            status="missing",
            detail="No knowledge resources configured",
            fix_link="/uloo/settings",
        ))

    ready = all(issue.status == "ok" for issue in issues)
    return TaskPreCheckResponse(ready=ready, issues=issues)


@router.post("/{task_id}/supplement", response_model=TaskDetailResponse)
async def supplement_task(
    task_id: uuid.UUID,
    body: TaskSupplementRequest,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """User supplements conditions or answers planner questions."""
    task = await _get_task_or_404(task_id, context.workspace_id, db)
    if task.status in {"running", "succeeded", "cancelled"}:
        raise ApiError(409, "INVALID_TASK_STATE", f"Task in status '{task.status}' cannot be supplemented")

    existing = list(task.user_supplements or [])
    existing.extend(body.supplements)
    task.user_supplements = existing
    if body.constraints:
        current = task.constraints or ""
        task.constraints = (current + "\n" + body.constraints).strip() if current else body.constraints

    await db.flush()
    logger.info("task_supplemented", task_id=str(task.id), supplement_count=len(body.supplements))

    plans = await db.execute(select(TaskPlan).where(TaskPlan.task_id == task.id).order_by(TaskPlan.version.asc()))
    return TaskDetailResponse(
        **task.to_dict(),
        plans=[TaskPlanResponse.model_validate(plan.to_dict()) for plan in plans.scalars().all()],
    )


@router.post("/{task_id}/execute", response_model=TaskDetailResponse)
async def direct_execute_task(
    task_id: uuid.UUID,
    body: TaskDirectExecuteRequest | None = None,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Create a minimal plan, auto-approve it, and mark the task ready for direct execution.

    This bypasses the full Planner run for simple tasks. The frontend can then
    call POST /tasks/{task_id}/runs to start execution.
    """
    task = await _get_task_or_404(task_id, context.workspace_id, db)
    if task.status in {"running", "succeeded", "cancelled"}:
        raise ApiError(409, "INVALID_TASK_STATE", f"Task in status '{task.status}' cannot be executed")

    # If a plan already exists and is approved, just return
    if task.status == "awaiting_approval":
        plans_result = await db.execute(
            select(TaskPlan).where(TaskPlan.task_id == task.id).order_by(TaskPlan.version.desc())
        )
        latest_plan = plans_result.scalars().first()
        if latest_plan and latest_plan.approval_status == "approved":
            plans = await db.execute(
                select(TaskPlan).where(TaskPlan.task_id == task.id).order_by(TaskPlan.version.asc())
            )
            return TaskDetailResponse(
                **task.to_dict(),
                plans=[TaskPlanResponse.model_validate(p.to_dict()) for p in plans.scalars().all()],
            )

    # Find a team
    team_filters = [
        TeamDefinition.workspace_id == context.workspace_id,
        TeamDefinition.deleted_at.is_(None),
        TeamDefinition.enabled == True,
    ]
    if body and body.team_id:
        try:
            team_uuid = uuid.UUID(body.team_id)
        except ValueError as exc:
            raise ApiError(422, "VALIDATION_ERROR", "team_id must be a UUID") from exc
        team_filters.append(TeamDefinition.id == team_uuid)
    elif task.team_id:
        team_filters.append(TeamDefinition.id == task.team_id)

    teams_result = await db.execute(select(TeamDefinition).where(*team_filters).order_by(TeamDefinition.created_at))
    team = teams_result.scalars().first()
    if not team:
        raise ApiError(409, "TEAM_NOT_FOUND", "No enabled team available for direct execution")

    # Get first agent from team for the minimal plan
    member_ids = [m.agent_id for m in team.members]
    agents_result = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.workspace_id == context.workspace_id,
            AgentDefinition.id.in_(member_ids),
            AgentDefinition.deleted_at.is_(None),
            AgentDefinition.enabled == True,
        )
    )
    agents = list(agents_result.scalars().all())
    if not agents:
        raise ApiError(409, "INVALID_TEAM_CONFIGURATION", "Team has no enabled agents")

    # Create a minimal single-step plan
    latest_version = await db.scalar(select(func.max(TaskPlan.version)).where(TaskPlan.task_id == task.id))
    plan = TaskPlan(
        task_id=task.id,
        version=(latest_version or 0) + 1,
        summary=f"Direct execution: {task.title}",
        assumptions=[],
        questions=[],
        recommended_team_id=team.id,
        steps=[
            {
                "id": "direct-execute",
                "title": "Execute task directly",
                "description": task.query,
                "assigned_agent_id": str(agents[0].id),
                "depends_on": [],
                "expected_output": "Task result",
                "status": "pending",
            }
        ],
        approval_status="approved",
        runtime_type="agno",
        is_mock=False,
    )
    db.add(plan)
    task.team_id = team.id
    task.current_plan_version = plan.version
    task.status = "awaiting_approval"
    await db.flush()
    logger.info("task_direct_execute_plan", task_id=str(task.id), plan_id=str(plan.id))

    plans = await db.execute(select(TaskPlan).where(TaskPlan.task_id == task.id).order_by(TaskPlan.version.asc()))
    return TaskDetailResponse(
        **task.to_dict(),
        plans=[TaskPlanResponse.model_validate(p.to_dict()) for p in plans.scalars().all()],
    )


@router.get("/{task_id}/export", response_model=TaskExportResponse)
async def export_task(
    task_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Export task summary as Markdown for download."""
    task = await _get_task_or_404(task_id, context.workspace_id, db)
    plans_result = await db.execute(
        select(TaskPlan).where(TaskPlan.task_id == task.id).order_by(TaskPlan.version.asc())
    )
    plans = list(plans_result.scalars().all())
    runs_result = await db.execute(
        select(Run).where(Run.task_id == task.id).order_by(Run.created_at.desc())
    )
    runs = list(runs_result.scalars().all())

    lines: list[str] = []
    lines.append(f"# {task.title}\n")
    lines.append(f"**Task ID:** {task.id}  ")
    lines.append(f"**Status:** {task.status}  ")
    lines.append(f"**Created:** {task.created_at}  \n")

    lines.append("## Goal\n")
    lines.append(f"{task.query}\n")

    if task.constraints:
        lines.append("## Constraints\n")
        lines.append(f"{task.constraints}\n")

    if task.user_supplements:
        lines.append("## User Supplements\n")
        for s in task.user_supplements:
            lines.append(f"- {s}")
        lines.append("")

    if plans:
        lines.append("## Plans\n")
        for plan in plans:
            lines.append(f"### Plan v{plan.version}: {plan.summary or 'Untitled'}\n")
            if plan.assumptions:
                lines.append("**Assumptions:**")
                for a in plan.assumptions:
                    lines.append(f"- {a}")
                lines.append("")
            if plan.questions:
                lines.append("**Open questions:**")
                for q in plan.questions:
                    lines.append(f"- {q}")
                lines.append("")
            lines.append("**Steps:**")
            for i, step in enumerate(plan.steps, 1):
                step_title = step.get("title", "Untitled step")
                lines.append(f"{i}. {step_title}")
                if step.get("description"):
                    lines.append(f"   - {step['description']}")
            lines.append("")

    if runs:
        lines.append("## Runs\n")
        for run in runs:
            lines.append(f"### Run {run.id[:8]}...")
            lines.append(f"- **Status:** {run.status}")
            lines.append(f"- **Created:** {run.created_at}")
            if run.output:
                lines.append(f"- **Output:**\n\n```\n{run.output}\n```")
            if run.error_message:
                lines.append(f"- **Error:** {run.error_message}")
            lines.append("")

    content = "\n".join(lines)
    safe_title = task.title.replace(" ", "_").replace("/", "-")[:60]
    filename = f"task_{safe_title}_{str(task.id)[:8]}.md"

    return TaskExportResponse(format="markdown", filename=filename, content=content)


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
