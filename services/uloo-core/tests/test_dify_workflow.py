# AI生成
"""Tests for Dify workflow execution integration (S1-8)."""

import uuid

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


def _agent_with_workflows(workflow_ids: list[str]) -> AgentDefinition:
    return AgentDefinition(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        key="orchestrator",
        name="Orchestrator",
        role="Orchestration role",
        instructions=["Act as the orchestrator."],
        model_ref="openai-compatible:test-model",
        tool_refs=[],
        knowledge_refs=[],
        workflow_refs=workflow_ids,
        enabled=True,
        version=1,
    )


class TestWorkflowToolCreation:
    """validate_agent_runtime_config creates Dify workflow tools when workspace_id is provided."""

    def test_workflow_refs_with_workspace_creates_tools(self):
        """Workflow refs + workspace_id → tools are created, no error raised."""
        definition = _agent_with_workflows(["wf-001", "wf-002"])
        _model, tools = validate_agent_runtime_config(
            definition,
            models=_configured_models(),
            workspace_id="ws-123",
        )
        workflow_tools = [t for t in tools if "workflow" in t.name]
        assert len(workflow_tools) == 2

    def test_workflow_refs_without_workspace_skips_tool_creation(self):
        """Workflow refs but no workspace_id → no tools created, no error raised."""
        definition = _agent_with_workflows(["wf-001"])
        _model, tools = validate_agent_runtime_config(
            definition,
            models=_configured_models(),
        )
        workflow_tools = [t for t in tools if "workflow" in t.name]
        assert len(workflow_tools) == 0

    def test_empty_workflow_refs_creates_no_tools(self):
        """Empty workflow_refs → no workflow tools, no error."""
        definition = _agent_with_workflows([])
        _model, tools = validate_agent_runtime_config(
            definition,
            models=_configured_models(),
            workspace_id="ws-123",
        )
        workflow_tools = [t for t in tools if "workflow" in t.name]
        assert len(workflow_tools) == 0

    def test_combined_knowledge_and_workflow_refs(self):
        """Both knowledge_refs and workflow_refs → both types of tools created."""
        definition = AgentDefinition(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            key="full-agent",
            name="Full Agent",
            role="Full role",
            instructions=["Act as the full agent."],
            model_ref="openai-compatible:test-model",
            tool_refs=[],
            knowledge_refs=["ds-001"],
            workflow_refs=["wf-001"],
            enabled=True,
            version=1,
        )
        _model, tools = validate_agent_runtime_config(
            definition,
            models=_configured_models(),
            workspace_id="ws-123",
        )
        knowledge_tools = [t for t in tools if "knowledge" in t.name]
        workflow_tools = [t for t in tools if "workflow" in t.name]
        assert len(knowledge_tools) == 1
        assert len(workflow_tools) == 1


class TestDifyWorkflowTool:
    """dify_workflow_tool creates a properly configured Agno Function."""

    def test_tool_has_correct_name_and_schema(self):
        from uloo.runtime.dify_workflow import dify_workflow_tool

        tool = dify_workflow_tool("wf-001", "workflow:wf-001", "ws-123")
        assert tool.name.startswith("dify_workflow_")
        assert "workflow" in tool.description.lower() or "wf-001" in tool.description
        # Schema should require an inputs parameter
        assert "inputs" in tool.parameters["properties"]
        assert "inputs" in tool.parameters["required"]

    def test_tool_entrypoint_is_async(self):
        import inspect

        from uloo.runtime.dify_workflow import dify_workflow_tool

        tool = dify_workflow_tool("wf-001", "workflow:wf-001", "ws-123")
        assert inspect.iscoroutinefunction(tool.entrypoint)
