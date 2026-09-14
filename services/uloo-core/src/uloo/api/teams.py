"""Team management API endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..logging import get_logger
from ..models.agent import AgentDefinition
from ..models.team import TeamDefinition, TeamMember
from ..schemas.team import (
    TeamCreate,
    TeamResponse,
    TeamUpdate,
    TeamValidateResponse,
)

router = APIRouter(prefix="/teams", tags=["teams"])
logger = get_logger(__name__)

VALID_MODES = {"coordinate", "tasks", "collaborate"}


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(body: TeamCreate, db: AsyncSession = Depends(get_db)):
    """Create a new Team definition."""
    if body.mode not in VALID_MODES:
        raise HTTPException(status_code=422, detail=f"Invalid mode: {body.mode}. Must be one of {VALID_MODES}")

    existing = await db.execute(
        select(TeamDefinition).where(
            TeamDefinition.key == body.key,
            TeamDefinition.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Team key '{body.key}' already exists")

    # Verify all member agents exist
    member_ids = [uuid.UUID(m) for m in body.member_agent_ids]
    agents = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.id.in_(member_ids),
            AgentDefinition.deleted_at.is_(None),
        )
    )
    found_ids = {a.id for a in agents.scalars().all()}
    missing = set(member_ids) - found_ids
    if missing:
        raise HTTPException(status_code=404, detail=f"Agents not found: {missing}")

    leader_id = uuid.UUID(body.leader_agent_id) if body.leader_agent_id else None

    team = TeamDefinition(
        key=body.key,
        name=body.name,
        description=body.description,
        mode=body.mode,
        leader_agent_id=leader_id,
        instructions=body.instructions,
        memory_policy=body.memory_policy.model_dump(),
        limits=body.limits.model_dump(),
        enabled=body.enabled,
    )
    db.add(team)
    await db.flush()

    # Add members
    for agent_id in member_ids:
        db.add(TeamMember(team_id=team.id, agent_id=agent_id))

    await db.flush()
    await db.refresh(team, ["members"])
    logger.info("team_created", team_id=str(team.id), key=team.key)
    return TeamResponse(**team.to_dict())


@router.get("", response_model=list[TeamResponse])
async def list_teams(
    q: str | None = Query(None),
    mode: str | None = Query(None),
    enabled: bool | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List and search teams."""
    stmt = select(TeamDefinition).where(TeamDefinition.deleted_at.is_(None))

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            func.or_(
                TeamDefinition.name.ilike(pattern),
                TeamDefinition.key.ilike(pattern),
            )
        )
    if mode:
        stmt = stmt.where(TeamDefinition.mode == mode)
    if enabled is not None:
        stmt = stmt.where(TeamDefinition.enabled == enabled)

    stmt = stmt.order_by(TeamDefinition.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return [TeamResponse(**t.to_dict()) for t in result.scalars().all()]


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(team_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get team details by ID."""
    team = await _get_team_or_404(team_id, db)
    return TeamResponse(**team.to_dict())


@router.get("/by-key/{team_key}", response_model=TeamResponse)
async def get_team_by_key(team_key: str, db: AsyncSession = Depends(get_db)):
    """Get team by stable key (used by plugin)."""
    result = await db.execute(
        select(TeamDefinition).where(
            TeamDefinition.key == team_key,
            TeamDefinition.deleted_at.is_(None),
        )
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail=f"Team key '{team_key}' not found")
    return TeamResponse(**team.to_dict())


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update team with optimistic locking."""
    team = await _get_team_or_404(team_id, db)

    if team.version != body.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"Version mismatch: expected {body.expected_version}, got {team.version}",
        )

    update_data = body.model_dump(exclude={"expected_version"}, exclude_none=True)

    if "mode" in update_data and update_data["mode"] not in VALID_MODES:
        raise HTTPException(status_code=422, detail=f"Invalid mode: {update_data['mode']}")

    # Handle member updates
    if "member_agent_ids" in update_data:
        member_ids = [uuid.UUID(m) for m in update_data.pop("member_agent_ids")]

        # Verify all agents exist
        agents = await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.id.in_(member_ids),
                AgentDefinition.deleted_at.is_(None),
            )
        )
        found_ids = {a.id for a in agents.scalars().all()}
        missing = set(member_ids) - found_ids
        if missing:
            raise HTTPException(status_code=404, detail=f"Agents not found: {missing}")

        # Delete old members and add new
        await db.execute(
            TeamMember.__table__.delete().where(TeamMember.team_id == team.id)
        )
        for agent_id in member_ids:
            db.add(TeamMember(team_id=team.id, agent_id=agent_id))

    if "leader_agent_id" in update_data:
        update_data["leader_agent_id"] = (
            uuid.UUID(update_data["leader_agent_id"]) if update_data["leader_agent_id"] else None
        )

    if "memory_policy" in update_data and hasattr(update_data["memory_policy"], "model_dump"):
        update_data["memory_policy"] = update_data["memory_policy"].model_dump()
    if "limits" in update_data and hasattr(update_data["limits"], "model_dump"):
        update_data["limits"] = update_data["limits"].model_dump()

    for field, value in update_data.items():
        setattr(team, field, value)

    team.version += 1
    await db.flush()
    await db.refresh(team, ["members"])
    logger.info("team_updated", team_id=str(team.id), version=team.version)
    return TeamResponse(**team.to_dict())


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(team_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Soft delete team."""
    from datetime import UTC, datetime

    team = await _get_team_or_404(team_id, db)
    team.deleted_at = datetime.now(UTC)
    team.enabled = False
    await db.flush()
    logger.info("team_deleted", team_id=str(team.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{team_id}/validate", response_model=TeamValidateResponse)
async def validate_team(team_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Validate team: leader, members, model, tools, limits."""
    team = await _get_team_or_404(team_id, db)
    errors: list[str] = []

    if team.mode in ("coordinate", "tasks") and not team.leader_agent_id:
        errors.append(f"mode '{team.mode}' requires leader_agent_id")

    if team.leader_agent_id:
        leader_in_members = any(
            m.agent_id == team.leader_agent_id for m in team.members
        )
        if not leader_in_members:
            errors.append("leader_agent_id must be in member_agent_ids")

    if not team.members:
        errors.append("team must have at least one member")

    # Check all member agents still exist
    for member in team.members:
        agent = await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.id == member.agent_id,
                AgentDefinition.deleted_at.is_(None),
            )
        )
        if not agent.scalar_one_or_none():
            errors.append(f"Agent {member.agent_id} not found or deleted")

    limits = team.limits or {}
    if limits.get("max_iterations", 0) <= 0:
        errors.append("limits.max_iterations must be positive")
    if limits.get("timeout_seconds", 0) <= 0:
        errors.append("limits.timeout_seconds must be positive")

    return TeamValidateResponse(valid=len(errors) == 0, errors=errors)


async def _get_team_or_404(team_id: uuid.UUID, db: AsyncSession) -> TeamDefinition:
    result = await db.execute(
        select(TeamDefinition).where(
            TeamDefinition.id == team_id,
            TeamDefinition.deleted_at.is_(None),
        )
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    return team
