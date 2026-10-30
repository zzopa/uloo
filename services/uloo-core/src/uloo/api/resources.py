"""Manage tools and skills using the existing workspace boundary."""

from fastapi import APIRouter, Depends, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import transaction_session
from ..errors import ERROR_RESPONSES, ApiError
from ..models.resource import ResourceDefinition
from ..runtime.mcp_adapter import MCPToolAdapter
from ..runtime.resources import definitions
from ..schemas.resource import (
    CodeInterpreterConfig,
    DifyToolConfig,
    McpDiscoverRequest,
    McpDiscoverResponse,
    McpSseToolConfig,
    McpStdioToolConfig,
    PdfParserConfig,
    ResourceCreate,
    ResourceResponse,
    ResourceUpdate,
    SearchConfig,
    SkillConfig,
    WikiMemoryConfig,
)
from ..security import RequestContext, require_service_context

router = APIRouter(tags=["resources"])

_TOOL_CONFIG_MODELS: dict[str, type] = {
    "dify": DifyToolConfig,
    "builtin": SearchConfig,
    "mcp_sse": McpSseToolConfig,
    "mcp_stdio": McpStdioToolConfig,
    "wiki_memory": WikiMemoryConfig,
    "pdf_parser": PdfParserConfig,
    "code_interpreter": CodeInterpreterConfig,
}


def validate_config(kind: str, config: dict) -> dict:
    try:
        if kind == "skills":
            model: type = SkillConfig
        else:
            tool_type = config.get("tool_type", "builtin")
            model = _TOOL_CONFIG_MODELS.get(tool_type)  # type: ignore[assignment]
            if model is None:
                raise ApiError(
                    422,
                    "INVALID_TOOL_TYPE",
                    f"Unknown tool_type: {tool_type}",
                )
        return model.model_validate(config).model_dump()  # type: ignore[attr-defined]
    except ValidationError as exc:
        raise ApiError(
            422,
            "INVALID_RESOURCE_CONFIG",
            "Resource configuration is invalid",
            details=exc.errors(include_context=False),
        ) from exc


@router.post(
    "/tools/mcp/discover",
    response_model=McpDiscoverResponse,
    responses=ERROR_RESPONSES,
    name="discover_mcp_tools",
)
async def discover_mcp_tools(
    body: McpDiscoverRequest,
    context: RequestContext = Depends(require_service_context),
) -> McpDiscoverResponse:
    """Connect to an MCP Server and discover its exposed tools."""
    adapter = MCPToolAdapter()
    try:
        return await adapter.discover(body)
    except TimeoutError as exc:
        raise ApiError(504, "MCP_DISCOVERY_TIMEOUT", f"MCP Server did not respond in time: {exc}") from exc
    except Exception as exc:
        raise ApiError(502, "MCP_DISCOVERY_FAILED", f"MCP Server discovery failed: {exc}") from exc


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
        if existing or (kind == "tools" and body.key in ("web-search", "pdf-parser", "code-interpreter", "wiki-memory")):
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

        if kind == "tools" and key in ("web-search", "pdf-parser", "code-interpreter", "wiki-memory"):
            raise ApiError(409, "BUILTIN_RESOURCE", f"Disable the built-in {key} instead of deleting it")
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
