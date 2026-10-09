"""Manage tools and skills using the existing workspace boundary."""

from fastapi import APIRouter, Depends, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import transaction_session
from ..errors import ApiError
from ..models.resource import ResourceDefinition
from ..runtime.resources import definitions
from ..schemas.resource import ResourceCreate, ResourceResponse, ResourceUpdate, SearchConfig, SkillConfig
from ..security import RequestContext, require_service_context

router = APIRouter(tags=["resources"])


def validate_config(kind: str, config: dict) -> dict:
    try:
        return (SearchConfig if kind == "tools" else SkillConfig).model_validate(config).model_dump()
    except ValidationError as exc:
        raise ApiError(
            422,
            "INVALID_RESOURCE_CONFIG",
            "Resource configuration is invalid",
            details=exc.errors(include_context=False),
        ) from exc


def register(kind: str):
    async def listing(
        context: RequestContext = Depends(require_service_context), db: AsyncSession = transaction_session
    ):
        return await definitions(db, context.workspace_id, kind)

    async def create(
        body: ResourceCreate,
        context: RequestContext = Depends(require_service_context),
        db: AsyncSession = transaction_session,
    ):
        existing = await db.scalar(
            select(ResourceDefinition)
            .where(
                ResourceDefinition.workspace_id == context.workspace_id,
                ResourceDefinition.kind == kind,
                ResourceDefinition.key == body.key,
            )
            .with_for_update()
        )
        if existing or (kind == "tools" and body.key == "web-search"):
            raise ApiError(409, "RESOURCE_KEY_CONFLICT", "Resource key already exists")
        resource = ResourceDefinition(
            workspace_id=context.workspace_id,
            kind=kind,
            **{**body.model_dump(), "config": validate_config(kind, body.config)},
        )
        db.add(resource)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ApiError(409, "RESOURCE_KEY_CONFLICT", "Resource changed concurrently; refresh before saving") from exc
        return resource.to_dict()

    async def update(
        key: str,
        body: ResourceUpdate,
        context: RequestContext = Depends(require_service_context),
        db: AsyncSession = transaction_session,
    ):
        resource = await db.scalar(
            select(ResourceDefinition)
            .where(
                ResourceDefinition.workspace_id == context.workspace_id,
                ResourceDefinition.kind == kind,
                ResourceDefinition.key == key,
            )
            .with_for_update()
        )
        if resource is None:
            if kind != "tools" or key != "web-search" or body.expected_version != 0:
                raise ApiError(404, "RESOURCE_NOT_FOUND", "Resource not found")
            resource = ResourceDefinition(workspace_id=context.workspace_id, kind=kind, key=key, version=0)
            db.add(resource)
        if resource.version != body.expected_version:
            raise ApiError(409, "VERSION_CONFLICT", "Resource was edited; refresh before saving")
        for field, value in body.model_dump(exclude={"expected_version"}).items():
            setattr(resource, field, validate_config(kind, value) if field == "config" else value)
        resource.version += 1
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ApiError(409, "RESOURCE_KEY_CONFLICT", "Resource changed concurrently; refresh before saving") from exc
        return resource.to_dict()

    async def delete(
        key: str, context: RequestContext = Depends(require_service_context), db: AsyncSession = transaction_session
    ):
        from ..models.agent import AgentDefinition

        if kind == "tools" and key == "web-search":
            raise ApiError(409, "BUILTIN_RESOURCE", "Disable the built-in search instead of deleting it")
        resource = await db.scalar(
            select(ResourceDefinition)
            .where(
                ResourceDefinition.workspace_id == context.workspace_id,
                ResourceDefinition.kind == kind,
                ResourceDefinition.key == key,
            )
            .with_for_update()
        )
        if resource is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "Resource not found")
        agents = (
            await db.scalars(
                select(AgentDefinition).where(
                    AgentDefinition.workspace_id == context.workspace_id, AgentDefinition.deleted_at.is_(None)
                )
            )
        ).all()
        if any(key in (agent.tool_refs if kind == "tools" else agent.skill_refs) for agent in agents):
            raise ApiError(409, "RESOURCE_IN_USE", "Unbind this resource from Agents before deleting")
        if kind == "tools":
            skills = await definitions(db, context.workspace_id, "skills")
            if any(key in skill["config"].get("tool_refs", []) for skill in skills):
                raise ApiError(409, "RESOURCE_IN_USE", "Unbind this tool from skills before deleting")
        await db.delete(resource)
        return Response(status_code=204)

    router.add_api_route(
        f"/{kind}", listing, methods=["GET"], response_model=list[ResourceResponse], name=f"list_{kind}"
    )
    router.add_api_route(
        f"/{kind}", create, methods=["POST"], response_model=ResourceResponse, status_code=201, name=f"create_{kind}"
    )
    router.add_api_route(
        f"/{kind}/{{key}}", update, methods=["PATCH"], response_model=ResourceResponse, name=f"update_{kind}"
    )
    router.add_api_route(f"/{kind}/{{key}}", delete, methods=["DELETE"], status_code=204, name=f"delete_{kind}")


register("tools")
register("skills")
