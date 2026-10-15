# AI生成
"""Tests for Dify knowledge retrieval integration (S1-7)."""

import uuid

import pytest

from uloo.config import Settings
from uloo.models.agent import AgentDefinition
from uloo.runtime.factory import validate_agent_runtime_config
from uloo.runtime.models import ModelRegistry


def _configured_models() -> ModelRegistry:
    settings = Settings(
        _env_file=None,
        model_providers={"openai-compatible": "https://models.example.test/v1"},
        model_provider_api_keys={"openai-compatible": "server-secret"},
    )
    return ModelRegistry(settings)


def _agent_with_knowledge(dataset_ids: list[str]) -> AgentDefinition:
    return AgentDefinition(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        key="researcher",
        name="Researcher",
        role="Research role",
        instructions=["Act as the researcher."],
        model_ref="openai-compatible:test-model",
        tool_refs=[],
        knowledge_refs=dataset_ids,
        enabled=True,
        version=1,
    )


class TestKnowledgeToolCreation:
    """validate_agent_runtime_config creates Dify knowledge tools when workspace_id is provided."""

    def test_knowledge_refs_with_workspace_creates_tools(self):
        """Knowledge refs + workspace_id → tools are created, no error raised."""
        definition = _agent_with_knowledge(["ds-001", "ds-002"])
        model, tools = validate_agent_runtime_config(
            definition,
            models=_configured_models(),
            workspace_id="ws-123",
        )
        # Two knowledge tools should be added
        knowledge_tools = [t for t in tools if "knowledge" in t.name]
        assert len(knowledge_tools) == 2

    def test_knowledge_refs_without_workspace_skips_tool_creation(self):
        """Knowledge refs but no workspace_id → no tools created, no error raised."""
        definition = _agent_with_knowledge(["ds-001"])
        model, tools = validate_agent_runtime_config(
            definition,
            models=_configured_models(),
        )
        knowledge_tools = [t for t in tools if "knowledge" in t.name]
        assert len(knowledge_tools) == 0

    def test_empty_knowledge_refs_creates_no_tools(self):
        """Empty knowledge_refs → no knowledge tools, no error."""
        definition = _agent_with_knowledge([])
        model, tools = validate_agent_runtime_config(
            definition,
            models=_configured_models(),
            workspace_id="ws-123",
        )
        knowledge_tools = [t for t in tools if "knowledge" in t.name]
        assert len(knowledge_tools) == 0


class TestDifyKnowledgeTool:
    """dify_knowledge_tool creates a properly configured Agno Function."""

    def test_tool_has_correct_name_and_schema(self):
        from uloo.runtime.dify_knowledge import dify_knowledge_tool

        tool = dify_knowledge_tool("ds-001", "knowledge:ds-001", "ws-123")
        assert tool.name.startswith("dify_knowledge_")
        assert "knowledge" in tool.description.lower() or "ds-001" in tool.description
        # Schema should require a query parameter
        assert "query" in tool.parameters["properties"]
        assert "query" in tool.parameters["required"]

    def test_tool_entrypoint_is_async(self):
        import inspect

        from uloo.runtime.dify_knowledge import dify_knowledge_tool

        tool = dify_knowledge_tool("ds-001", "knowledge:ds-001", "ws-123")
        assert inspect.iscoroutinefunction(tool.entrypoint)
