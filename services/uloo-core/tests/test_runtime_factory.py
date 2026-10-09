"""Unit contract for the ULOO-to-Agno runtime mapping."""

import uuid

import pytest
from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from agno.team.team import Team

from uloo.config import Settings
from uloo.models.agent import AgentDefinition
from uloo.models.team import TeamDefinition, TeamMember
from uloo.runtime import (
    ModelRegistry,
    RuntimeConfigurationError,
    ToolRegistry,
    build_agent,
    build_team,
)
from uloo.schemas.task import TaskPlanDraft


def configured_models() -> ModelRegistry:
    settings = Settings(
        _env_file=None,
        model_providers={"openai-compatible": "https://models.example.test/v1"},
        model_provider_api_keys={"openai-compatible": "server-secret"},
    )
    return ModelRegistry(settings)


def agent_definition(*, key: str = "researcher", enabled: bool = True) -> AgentDefinition:
    return AgentDefinition(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        key=key,
        name=key.title(),
        role=f"{key} role",
        instructions=[f"Act as the {key}."],
        model_ref="openai-compatible:test-model",
        tool_refs=[],
        knowledge_refs=[],
        enabled=enabled,
        version=1,
    )


def test_model_registry_resolves_only_server_configured_provider():
    registry = configured_models()

    model = registry.resolve("openai-compatible:test-model")

    assert isinstance(model, OpenAILike)
    assert model.id == "test-model"
    assert registry.configured_refs() == ["openai-compatible"]


@pytest.mark.parametrize("model_ref", ["test-model", ":model", "provider:"])
def test_model_registry_rejects_invalid_reference(model_ref: str):
    with pytest.raises(RuntimeConfigurationError, match="provider:model-id") as error:
        configured_models().resolve(model_ref)
    assert error.value.code == "MODEL_CONFIG_MISSING"


def test_model_registry_rejects_unconfigured_provider_without_fallback():
    with pytest.raises(RuntimeConfigurationError, match="not configured") as error:
        configured_models().resolve("unknown:test-model")
    assert error.value.code == "MODEL_CONFIG_MISSING"


def test_tool_registry_is_an_explicit_allow_list():
    with pytest.raises(RuntimeConfigurationError, match="not registered") as error:
        ToolRegistry().resolve_many(["arbitrary.module:function"])
    assert error.value.code == "TOOL_CONFIG_MISSING"


def test_build_agent_creates_real_agno_agent():
    definition = agent_definition()

    runtime = build_agent(definition, models=configured_models())

    assert isinstance(runtime, Agent)
    assert runtime.agent_id == str(definition.id)
    assert runtime.name == definition.name
    assert runtime.role == definition.role
    assert runtime.telemetry is False


def test_build_agent_rejects_disabled_definition():
    with pytest.raises(RuntimeConfigurationError, match="disabled") as error:
        build_agent(agent_definition(enabled=False), models=configured_models())
    assert error.value.code == "AGENT_DISABLED"


def test_build_team_uses_declared_leader_model_and_instructions():
    researcher = agent_definition(key="researcher")
    writer = agent_definition(key="writer")
    team_id = uuid.uuid4()
    team = TeamDefinition(
        id=team_id,
        workspace_id=researcher.workspace_id,
        key="research-team",
        name="Research team",
        mode="coordinate",
        leader_agent_id=researcher.id,
        instructions=["Produce one reviewed answer."],
        enabled=True,
        version=3,
        limits={"max_iterations": 8, "timeout_seconds": 120, "max_tokens": 20000},
    )
    team.members = [
        TeamMember(team_id=team_id, workspace_id=team.workspace_id, agent_id=researcher.id),
        TeamMember(team_id=team_id, workspace_id=team.workspace_id, agent_id=writer.id),
    ]

    runtime = build_team(team, [researcher, writer], models=configured_models())

    assert isinstance(runtime, Team)
    assert runtime.team_id == str(team_id)
    assert runtime.mode == "coordinate"
    assert [member.agent_id for member in runtime.members] == [str(researcher.id), str(writer.id)]
    assert runtime.instructions[0].startswith("Use Researcher")
    assert runtime.extra_data["uloo_team_version"] == 3
    assert runtime.tool_call_limit == 8
    assert runtime.share_member_interactions is True
    assert runtime.model.max_tokens == 20000


def test_declared_output_schema_configures_json_mode():
    definition = agent_definition()
    definition.output_schema = {"type": "object", "properties": {"answer": {"type": "integer"}}}
    runtime = build_agent(definition, models=configured_models())
    assert runtime.model.request_params == {"response_format": {"type": "json_object"}}
    assert runtime.extra_data["uloo_output_schema"] == definition.output_schema
    assert "Return only JSON" in runtime.instructions[-1]


def test_invalid_output_schema_cannot_execute():
    definition = agent_definition()
    definition.output_schema = {"type": "invalid-type"}
    with pytest.raises(RuntimeConfigurationError) as error:
        build_agent(definition, models=configured_models())
    assert error.value.code == "OUTPUT_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "steps",
    [
        [{"id": "a", "title": "A"}, {"id": "a", "title": "Duplicate"}],
        [{"id": "a", "title": "A", "depends_on": ["missing"]}],
        [{"id": "a", "title": "A", "depends_on": ["b"]}, {"id": "b", "title": "B", "depends_on": ["a"]}],
    ],
)
def test_planner_rejects_unexecutable_dependencies(steps):
    with pytest.raises(ValueError):
        TaskPlanDraft(summary="Plan", recommended_team_id=str(uuid.uuid4()), steps=steps)


def test_search_agent_has_current_time_context():
    definition = agent_definition()
    definition.tool_refs = ["web-search"]
    agent = build_agent(definition, models=configured_models())
    assert agent.add_datetime_to_instructions
    assert agent.tools == ToolRegistry().resolve_many(["web-search"])


