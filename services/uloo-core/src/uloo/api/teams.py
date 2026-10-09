"""Team management API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import TEAM_MODES, TEAM_MODES_REQUIRING_LEADER
from ..db import transaction_session
from ..errors import ApiError
from ..logging import get_logger
from ..models.agent import AgentDefinition
from ..models.team import TeamDefinition, TeamMember
from ..runtime.resources import load_resources
from ..schemas.common import Page
from ..schemas.team import (
    TeamCreate,
    TeamResponse,
    TeamUpdate,
    TeamValidateResponse,
)
from ..security import RequestContext, require_service_context

router = APIRouter(prefix="/teams", tags=["teams"])
logger = get_logger(__name__)

VALID_MODES = frozenset(TEAM_MODES)


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamCreate,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Create a new Team definition."""
    if body.mode not in VALID_MODES:
        raise ApiError(
            422,
            "INVALID_TEAM_MODE",
            f"Invalid mode: {body.mode}. Must be one of {list(TEAM_MODES)}",
        )

    existing = await db.execute(
        select(TeamDefinition).where(
            TeamDefinition.workspace_id == context.workspace_id,
            TeamDefinition.key == body.key,
        )
    )
    if existing.scalar_one_or_none():
        raise ApiError(409, "TEAM_KEY_CONFLICT", f"Team key '{body.key}' already exists")

    # Verify all member agents exist
    member_ids = body.member_agent_ids
    agents = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.id.in_(member_ids),
            AgentDefinition.workspace_id == context.workspace_id,
            AgentDefinition.deleted_at.is_(None),
        )
    )
    found_ids = {a.id for a in agents.scalars().all()}
    missing = set(member_ids) - found_ids
    if missing:
        raise ApiError(
            404,
            "AGENT_NOT_FOUND",
            f"Agents not found: {sorted(str(item) for item in missing)}",
        )

    leader_id = body.leader_agent_id

    team = TeamDefinition(
        workspace_id=context.workspace_id,
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
        db.add(TeamMember(workspace_id=context.workspace_id, team_id=team.id, agent_id=agent_id))

    await db.flush()
    await db.refresh(team, ["members", "updated_at"])
    logger.info("team_created", team_id=str(team.id), key=team.key)
    return TeamResponse(**team.to_dict())


@router.get("", response_model=Page[TeamResponse])
async def list_teams(
    q: str | None = Query(None),
    mode: str | None = Query(None),
    enabled: bool | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """List and search teams."""
    filters = [
        TeamDefinition.workspace_id == context.workspace_id,
        TeamDefinition.deleted_at.is_(None),
    ]

    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(
                TeamDefinition.name.ilike(pattern),
                TeamDefinition.key.ilike(pattern),
            )
        )
    if mode:
        filters.append(TeamDefinition.mode == mode)
    if enabled is not None:
        filters.append(TeamDefinition.enabled == enabled)

    total = await db.scalar(select(func.count()).select_from(TeamDefinition).where(*filters))
    stmt = (
        select(TeamDefinition)
        .where(*filters)
        .order_by(TeamDefinition.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return Page[TeamResponse](
        items=[TeamResponse(**t.to_dict()) for t in result.scalars().all()],
        total=total or 0,
        offset=offset,
        limit=limit,
    )


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Get team details by ID."""
    team = await _get_team_or_404(team_id, context.workspace_id, db)
    return TeamResponse(**team.to_dict())


@router.get("/by-key/{team_key}", response_model=TeamResponse)
async def get_team_by_key(
    team_key: str,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Get team by stable key (used by plugin)."""
    result = await db.execute(
        select(TeamDefinition).where(
            TeamDefinition.key == team_key,
            TeamDefinition.workspace_id == context.workspace_id,
            TeamDefinition.deleted_at.is_(None),
        )
    )
    team = result.scalar_one_or_none()
    if not team:
        raise ApiError(404, "TEAM_NOT_FOUND", f"Team key '{team_key}' not found")
    return TeamResponse(**team.to_dict())


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Update team with optimistic locking."""
    team = await _get_team_or_404(team_id, context.workspace_id, db, for_update=True)

    if team.version != body.expected_version:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            f"Version mismatch: expected {body.expected_version}, got {team.version}",
            details={"expected_version": body.expected_version, "current_version": team.version},
        )

    patch = body.model_dump(exclude={"expected_version"}, exclude_unset=True)
    current = team.to_dict()
    candidate_data = {
        "key": team.key,
        "name": current["name"],
        "description": current["description"],
        "mode": current["mode"],
        "leader_agent_id": current["leader_agent_id"],
        "member_agent_ids": current["member_agent_ids"],
        "instructions": current["instructions"],
        "memory_policy": current["memory_policy"],
        "limits": current["limits"],
        "enabled": current["enabled"],
    }
    candidate_data.update(patch)
    try:
        candidate = TeamCreate.model_validate(candidate_data)
    except ValidationError as exc:
        raise ApiError(
            422,
            "INVALID_TEAM_CONFIGURATION",
            "The resulting Team configuration is invalid",
            details=exc.errors(include_context=False),
        ) from exc
    member_ids = candidate.member_agent_ids

    agents = await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.id.in_(member_ids),
            AgentDefinition.workspace_id == context.workspace_id,
            AgentDefinition.deleted_at.is_(None),
        )
    )
    found_ids = {agent.id for agent in agents.scalars().all()}
    missing = set(member_ids) - found_ids
    if missing:
        raise ApiError(
            404,
            "AGENT_NOT_FOUND",
            f"Agents not found: {sorted(str(item) for item in missing)}",
        )

    if "member_agent_ids" in patch:
        await db.execute(
            delete(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.workspace_id == context.workspace_id,
            )
        )
        for agent_id in member_ids:
            db.add(
                TeamMember(
                    workspace_id=context.workspace_id,
                    team_id=team.id,
                    agent_id=agent_id,
                )
            )

    scalar_values = candidate.model_dump(exclude={"key", "member_agent_ids"})
    scalar_values["memory_policy"] = candidate.memory_policy.model_dump()
    scalar_values["limits"] = candidate.limits.model_dump()
    for field, value in scalar_values.items():
        setattr(team, field, value)

    team.version += 1
    await db.flush()
    await db.refresh(team, ["members", "updated_at"])
    logger.info("team_updated", team_id=str(team.id), version=team.version)
    return TeamResponse(**team.to_dict())


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Soft delete team."""
    from datetime import UTC, datetime

    team = await _get_team_or_404(team_id, context.workspace_id, db, for_update=True)
    team.deleted_at = datetime.now(UTC)
    team.enabled = False
    await db.flush()
    logger.info("team_deleted", team_id=str(team.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{team_id}/validate", response_model=TeamValidateResponse)
async def validate_team(
    team_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Validate team: leader, members, model, tools, limits."""
    team = await _get_team_or_404(team_id, context.workspace_id, db)
    errors: list[str] = []

    if team.mode not in VALID_MODES:
        errors.append(
            f"mode '{team.mode}' is not supported by the Agno runtime "
            f"(allowed: {list(TEAM_MODES)})"
        )

    if team.mode in TEAM_MODES_REQUIRING_LEADER and not team.leader_agent_id:
        errors.append(f"mode '{team.mode}' requires leader_agent_id")

    if team.leader_agent_id:
        leader_in_members = any(
            m.agent_id == team.leader_agent_id for m in team.members
        )
        if not leader_in_members:
            errors.append("leader_agent_id must be in member_agent_ids")

    if not team.members:
        errors.append("team must have at least one member")

    from ..runtime.factory import validate_agent_runtime_config
    from ..runtime.models import RuntimeConfigurationError

    if not team.enabled:
        errors.append("Team is disabled")
    resources = await load_resources(db, context.workspace_id)
    # Check all member agents still exist and have executable definitions.
    for member in team.members:
        agent = await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.id == member.agent_id,
                AgentDefinition.workspace_id == context.workspace_id,
                AgentDefinition.deleted_at.is_(None),
            )
        )
        definition = agent.scalar_one_or_none()
        if not definition:
            errors.append(f"Agent {member.agent_id} not found or deleted")
        else:
            if not definition.enabled:
                errors.append(f"Agent {definition.name} is disabled")
            try:
                validate_agent_runtime_config(definition, tools=resources)
            except RuntimeConfigurationError as exc:
                errors.append(f"Agent {definition.name}: {exc.message}")

    limits = team.limits or {}
    if limits.get("max_iterations", 0) <= 0:
        errors.append("limits.max_iterations must be positive")
    if limits.get("timeout_seconds", 0) <= 0:
        errors.append("limits.timeout_seconds must be positive")

    return TeamValidateResponse(valid=len(errors) == 0, errors=errors)


async def _get_team_or_404(
    team_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession,
    *,
    for_update: bool = False,
) -> TeamDefinition:
    stmt = select(TeamDefinition).where(
        TeamDefinition.id == team_id,
        TeamDefinition.workspace_id == workspace_id,
        TeamDefinition.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    team = result.scalar_one_or_none()
    if not team:
        raise ApiError(404, "TEAM_NOT_FOUND", f"Team {team_id} not found")
    return team
