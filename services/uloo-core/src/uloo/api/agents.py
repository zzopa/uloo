"""Agent Registry API endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..errors import ApiError
from ..logging import get_logger
from ..models.agent import AgentDefinition
from ..schemas.agent import (
    AgentCreate,
    AgentResponse,
    AgentTestRunResponse,
    AgentUpdate,
    AgentValidateResponse,
)

router = APIRouter(prefix="/agents", tags=["agents"])
logger = get_logger(__name__)


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, db: AsyncSession = Depends(get_db)):
    """Create a new Agent definition."""
    existing = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.key == body.key,
            AgentDefinition.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Agent key '{body.key}' already exists")

    agent = AgentDefinition(
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


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    q: str | None = Query(None, description="Search in name/key/role"),
    enabled: bool | None = Query(None),
    model_ref: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List and search agents."""
    stmt = select(AgentDefinition).where(AgentDefinition.deleted_at.is_(None))

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                AgentDefinition.name.ilike(pattern),
                AgentDefinition.key.ilike(pattern),
                AgentDefinition.role.ilike(pattern),
            )
        )
    if enabled is not None:
        stmt = stmt.where(AgentDefinition.enabled == enabled)
    if model_ref:
        stmt = stmt.where(AgentDefinition.model_ref == model_ref)

    stmt = stmt.order_by(AgentDefinition.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return [AgentResponse(**a.to_dict()) for a in result.scalars().all()]


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get agent details by ID."""
    agent = await _get_agent_or_404(agent_id, db)
    return AgentResponse(**agent.to_dict())


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update agent with optimistic locking."""
    agent = await _get_agent_or_404(agent_id, db)

    if agent.version != body.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"Version mismatch: expected {body.expected_version}, got {agent.version}",
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
async def delete_agent(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Soft delete agent. Fails if agent is used by an enabled team."""
    from ..models.team import TeamDefinition, TeamMember

    agent = await _get_agent_or_404(agent_id, db)

    # Check if used by enabled teams
    teams_using = await db.execute(
        select(TeamDefinition)
        .join(TeamMember, TeamMember.team_id == TeamDefinition.id)
        .where(
            TeamMember.agent_id == agent_id,
            TeamDefinition.deleted_at.is_(None),
            TeamDefinition.enabled == True,  # noqa: E712
        )
    )
    if teams_using.scalars().first():
        raise HTTPException(
            status_code=409,
            detail="Cannot delete agent: still referenced by enabled teams",
        )

    from datetime import UTC, datetime

    agent.deleted_at = datetime.now(UTC)
    agent.enabled = False
    await db.flush()
    logger.info("agent_deleted", agent_id=str(agent.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{agent_id}/validate", response_model=AgentValidateResponse)
async def validate_agent(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Validate agent model and tool configuration without creating a Run."""
    agent = await _get_agent_or_404(agent_id, db)
    errors: list[str] = []

    if not agent.model_ref:
        errors.append("model_ref is required")
    if not agent.role:
        errors.append("role is required")

    return AgentValidateResponse(valid=len(errors) == 0, errors=errors)


@router.post("/{agent_id}/test-runs", response_model=AgentTestRunResponse)
async def test_run_agent(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Execute a real single-agent run once the Stage 3 runtime is available."""
    await _get_agent_or_404(agent_id, db)
    raise ApiError(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        code="NOT_IMPLEMENTED",
        message="Real Agno agent test runs are not implemented yet",
        details={"required_stage": 3},
    )


async def _get_agent_or_404(agent_id: uuid.UUID, db: AsyncSession) -> AgentDefinition:
    result = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.id == agent_id,
            AgentDefinition.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return agent
