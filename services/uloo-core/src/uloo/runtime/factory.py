"""Map persisted ULOO definitions to real Agno Agent and Team objects."""

import json
from collections.abc import Iterable
from typing import Literal, cast

from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from agno.team.team import Team

from ..config import settings
from ..models.agent import AgentDefinition
from ..models.team import TeamDefinition
from ..schemas.task import TaskPlanDraft
from .models import ModelRegistry, RuntimeConfigurationError
from .tools import ToolRegistry

TeamMode = Literal["route", "coordinate", "collaborate"]


def build_planner_agent(*, models: ModelRegistry | None = None) -> Agent:
    """Build the system Planner with a server-only model configuration."""
    if not settings.planner_model_ref.strip():
        raise RuntimeConfigurationError(
            "MODEL_CONFIG_MISSING",
            "ULOO_PLANNER_MODEL_REF is not configured",
        )
    model_registry = models or ModelRegistry()
    return Agent(
        agent_id="uloo-system-planner",
        name="ULOO Planner",
        add_datetime_to_instructions=True,
        role="Plan business tasks and assign validated Agent teams",
        model=model_registry.resolve(settings.planner_model_ref),
        response_model=TaskPlanDraft,
        parse_response=True,
        structured_outputs=False,
        use_json_mode=True,
        instructions=[
            "Choose exactly one team from the supplied candidates.",
            "Only assign Agents that belong to the chosen team.",
            "Produce concrete dependency-aware steps with observable outputs.",
            "Ask only questions that materially block execution.",
            "Never claim that execution has already happened.",
        ],
        telemetry=False,
        monitoring=False,
        markdown=False,
    )


def build_agent(
    definition: AgentDefinition,
    *,
    models: ModelRegistry | None = None,
    tools: ToolRegistry | None = None,
) -> Agent:
    """Build an executable Agno Agent without persisting any provider secret."""
    if not definition.enabled:
        raise RuntimeConfigurationError("AGENT_DISABLED", f"Agent '{definition.key}' is disabled")

    model_registry = models or ModelRegistry()
    tool_registry = tools or ToolRegistry()
    model, resolved_tools = validate_agent_runtime_config(
        definition,
        models=model_registry,
        tools=tool_registry,
    )
    instructions = list(definition.instructions or [])
    skill_instructions, _ = tool_registry.skill_config(definition.skill_refs or [])
    instructions.extend(skill_instructions)
    if definition.output_schema is not None:
        model.request_params = {"response_format": {"type": "json_object"}}
        instructions.append("Return only JSON matching this schema: " + json.dumps(definition.output_schema))
    return Agent(
        agent_id=str(definition.id),
        name=definition.name,
        add_datetime_to_instructions=True,
        role=definition.role,
        description=definition.description,
        instructions=instructions,
        extra_data={"uloo_output_schema": definition.output_schema},
        model=model,
        tools=resolved_tools,
        telemetry=False,
        monitoring=False,
        markdown=False,
    )


def validate_agent_runtime_config(
    definition: AgentDefinition,
    *,
    models: ModelRegistry | None = None,
    tools: ToolRegistry | None = None,
):
    """Resolve every executable reference without making a model request."""
    if definition.knowledge_refs:
        raise RuntimeConfigurationError(
            "KNOWLEDGE_CONFIG_MISSING",
            "Knowledge references are configured but no knowledge adapter is enabled",
        )
    model_registry = models or ModelRegistry()
    tool_registry = tools or ToolRegistry()
    model = model_registry.resolve(definition.model_ref)
    if definition.output_schema is not None:
        from jsonschema.validators import validator_for

        try:
            validator_for(definition.output_schema).check_schema(definition.output_schema)
        except Exception as exc:
            raise RuntimeConfigurationError(
                "OUTPUT_SCHEMA_INVALID", "Agent output_schema is not a valid JSON Schema"
            ) from exc
    _, skill_tools = tool_registry.skill_config(definition.skill_refs or [])
    resolved_tools = tool_registry.resolve_many(dict.fromkeys([*(definition.tool_refs or []), *skill_tools]))
    return model, resolved_tools


def build_team(
    definition: TeamDefinition,
    member_definitions: Iterable[AgentDefinition],
    *,
    models: ModelRegistry | None = None,
    tools: ToolRegistry | None = None,
) -> Team:
    """Build a real Agno Team using the declared leader as coordinator model."""
    if not definition.enabled:
        raise RuntimeConfigurationError("TEAM_DISABLED", f"Team '{definition.key}' is disabled")

    members_by_id = {member.id: member for member in member_definitions}
    expected_ids = [membership.agent_id for membership in definition.members]
    missing_ids = [str(member_id) for member_id in expected_ids if member_id not in members_by_id]
    if missing_ids:
        raise RuntimeConfigurationError(
            "AGENT_NOT_FOUND",
            f"Team references missing Agents: {', '.join(missing_ids)}",
        )

    model_registry = models or ModelRegistry()
    tool_registry = tools or ToolRegistry()
    runtime_members: list[Agent | Team] = [
        build_agent(members_by_id[member_id], models=model_registry, tools=tool_registry) for member_id in expected_ids
    ]
    if not runtime_members:
        raise RuntimeConfigurationError("AGENT_NOT_FOUND", "Team has no members")

    coordinator_definition = (
        members_by_id.get(definition.leader_agent_id) if definition.leader_agent_id else members_by_id[expected_ids[0]]
    )
    if coordinator_definition is None:
        raise RuntimeConfigurationError("AGENT_NOT_FOUND", "Team leader is not a member")

    instructions = list(definition.instructions or [])
    if definition.leader_agent_id:
        instructions.insert(
            0,
            f"Use {coordinator_definition.name} ({coordinator_definition.role}) as the declared coordination lead.",
        )

    coordinator_model = model_registry.resolve(coordinator_definition.model_ref)
    limits = definition.limits or {}
    # ponytail: max_tokens bounds each response, not total team usage; add a shared budget before billing enforcement.
    coordinator_model.max_tokens = limits.get("max_tokens", 20000)
    for member in runtime_members:
        cast(OpenAILike, member.model).max_tokens = coordinator_model.max_tokens
    return Team(
        team_id=str(definition.id),
        add_datetime_to_instructions=True,
        name=definition.name,
        description=definition.description,
        mode=cast(TeamMode, definition.mode),
        members=runtime_members,
        model=coordinator_model,
        tool_call_limit=limits.get("max_iterations", 8),
        share_member_interactions=True,
        instructions=instructions,
        extra_data={
            "uloo_team_key": definition.key,
            "uloo_team_version": definition.version,
            "uloo_limits": dict(definition.limits or {}),
        },
        telemetry=False,
        monitoring=False,
        markdown=False,
        stream_member_events=True,
    )
