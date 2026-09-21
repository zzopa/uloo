"""Agent Registry API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..errors import ApiError
from ..logging import get_logger
from ..models.agent import AgentDefinition
from ..schemas.common import Page
from ..schemas.agent import (
    AgentCreate,
    AgentResponse,
    AgentTestRunResponse,
    AgentUpdate,
    AgentValidateResponse,
)
from ..security import RequestContext, require_service_context

router = APIRouter(prefix="/agents", tags=["agents"])
logger = get_logger(__name__)


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = Depends(get_db),
):
    """Create a new Agent definition."""
    existing = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.workspace_id == context.workspace_id,
            AgentDefinition.key == body.key,
        )
    )
    if existing.scalar_one_or_none():
        raise ApiError(409, "AGENT_KEY_CONFLICT", f"Agent key '{body.key}' already exists")

    agent = AgentDefinition(
        workspace_id=context.workspace_id,
        key=body.key,
        name=body.name,
        description=body.description,
        role=body.role,
        instructions=body.instructions,
        model_ref=body.model_ref,
        tool_refs=body.tool_refs,
        knowledge_refs=body.knowledge_refs,
        output_schema=body.output_schema,
        enabled=body.enabled,
    )
    db.add(agent)
    await db.flush()
    logger.info("agent_created", agent_id=str(agent.id), key=agent.key)
    return AgentResponse(**agent.to_dict())


@router.get("", response_model=Page[AgentResponse])
async def list_agents(
    q: str | None = Query(None, description="Search in name/key/role"),
    enabled: bool | None = Query(None),
    model_ref: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = Depends(get_db),
):
    """List and search agents."""
    filters = [
        AgentDefinition.workspace_id == context.workspace_id,
        AgentDefinition.deleted_at.is_(None),
    ]

    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(
                AgentDefinition.name.ilike(pattern),
                AgentDefinition.key.ilike(pattern),
                AgentDefinition.role.ilike(pattern),
            )
        )
    if enabled is not None:
        filters.append(AgentDefinition.enabled == enabled)
    if model_ref:
        filters.append(AgentDefinition.model_ref == model_ref)

    total = await db.scalar(select(func.count()).select_from(AgentDefinition).where(*filters))
    stmt = (
        select(AgentDefinition)
        .where(*filters)
        .order_by(AgentDefinition.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return Page[AgentResponse](
        items=[AgentResponse(**a.to_dict()) for a in result.scalars().all()],
        total=total or 0,
        offset=offset,
        limit=limit,
    )


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = Depends(get_db),
):
    """Get agent details by ID."""
    agent = await _get_agent_or_404(agent_id, context.workspace_id, db)
    return AgentResponse(**agent.to_dict())


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = Depends(get_db),
):
    """Update agent with optimistic locking."""
    agent = await _get_agent_or_404(agent_id, context.workspace_id, db, for_update=True)

    if agent.version != body.expected_version:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            f"Version mismatch: expected {body.expected_version}, got {agent.version}",
            details={"expected_version": body.expected_version, "current_version": agent.version},
        )

    update_fields = body.model_dump(exclude={"expected_version"}, exclude_none=True)
    for field, value in update_fields.items():
        setattr(agent, field, value)

    agent.version += 1
    await db.flush()
    await db.refresh(agent)
    logger.info("agent_updated", agent_id=str(agent.id), version=agent.version)
    return AgentResponse(**agent.to_dict())


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete agent. Fails if agent is used by an enabled team."""
    from ..models.team import TeamDefinition, TeamMember

    agent = await _get_agent_or_404(agent_id, context.workspace_id, db, for_update=True)

    # Check if used by enabled teams
    teams_using = await db.execute(
        select(TeamDefinition)
        .join(TeamMember, TeamMember.team_id == TeamDefinition.id)
        .where(
            TeamMember.agent_id == agent_id,
            TeamDefinition.workspace_id == context.workspace_id,
            TeamMember.workspace_id == context.workspace_id,
            TeamDefinition.deleted_at.is_(None),
            TeamDefinition.enabled == True,  # noqa: E712
        )
    )
    if teams_using.scalars().first():
        raise ApiError(
            409,
            "AGENT_IN_USE",
            "Cannot delete agent: still referenced by enabled teams",
        )

    from datetime import UTC, datetime

    agent.deleted_at = datetime.now(UTC)
    agent.enabled = False
    await db.flush()
    logger.info("agent_deleted", agent_id=str(agent.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{agent_id}/validate", response_model=AgentValidateResponse)
async def validate_agent(
    agent_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = Depends(get_db),
):
    """Validate agent model and tool configuration without creating a Run."""
    agent = await _get_agent_or_404(agent_id, context.workspace_id, db)
    errors: list[str] = []

    if not agent.model_ref:
        errors.append("model_ref is required")
    if not agent.role:
        errors.append("role is required")

    return AgentValidateResponse(valid=len(errors) == 0, errors=errors)


@router.post("/{agent_id}/test-runs", response_model=AgentTestRunResponse)
async def test_run_agent(
    agent_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = Depends(get_db),
):
    """Execute a real single-agent run once the Stage 3 runtime is available."""
    await _get_agent_or_404(agent_id, context.workspace_id, db)
    raise ApiError(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        code="NOT_IMPLEMENTED",
        message="Real Agno agent test runs are not implemented yet",
        details={"required_stage": 3},
    )


async def _get_agent_or_404(
    agent_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession,
    *,
    for_update: bool = False,
) -> AgentDefinition:
    stmt = select(AgentDefinition).where(
        AgentDefinition.id == agent_id,
        AgentDefinition.workspace_id == workspace_id,
        AgentDefinition.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    agent = result.scalar_one_or_none()
    if not agent:
        raise ApiError(404, "AGENT_NOT_FOUND", f"Agent {agent_id} not found")
    return agent
