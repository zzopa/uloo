# AI生成
"""Map persisted ULOO definitions to real Agno Agent and Team objects."""

import inspect
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
from .live_events import emit_live_event
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
    workspace_id: str | None = None,
) -> Agent:
    """Build an executable Agno Agent without persisting any provider secret."""
    if not definition.enabled:
        raise RuntimeConfigurationError("AGENT_DISABLED", f"Agent '{definition.key}' is disabled")

    model_registry = models or ModelRegistry(workspace_id=workspace_id)
    tool_registry = tools or ToolRegistry()
    model, resolved_tools = validate_agent_runtime_config(
        definition,
        models=model_registry,
        tools=tool_registry,
        workspace_id=workspace_id,
    )
    instructions = list(definition.instructions or [])
    instructions.append(
        "CITATION AND SOURCE MANDATE: Every factual assertion, data point, and quote in the final report MUST cite its real source with a clickable Markdown hyperlink [Source Title](URL). Provide a dedicated '### 🔗 数据出处与核验信源' section listing all real source URLs at the end."
    )
    skill_instructions, _ = tool_registry.skill_config(definition.skill_refs or [])
    instructions.extend(skill_instructions)
    if "web-search" in (definition.tool_refs or []):
        instructions.append(
            "CITATION MANDATE: When providing findings from web search, ALWAYS cite the real source with a Markdown hyperlink [Source Title](URL) using the actual URL from search results."
        )
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
    workspace_id: str | None = None,
):
    """Resolve every executable reference without making a model request."""
    model_registry = models or ModelRegistry(workspace_id=workspace_id)
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

    # S1-7: Create Dify knowledge retrieval tools for each referenced dataset.
    if definition.knowledge_refs and workspace_id:
        from .dify_knowledge import dify_knowledge_tool

        for dataset_id in definition.knowledge_refs:
            tool_name = f"knowledge:{dataset_id}"
            resolved_tools.append(
                dify_knowledge_tool(dataset_id, tool_name, workspace_id)
            )

    # S1-8: Create Dify workflow execution tools for each referenced workflow app.
    if definition.workflow_refs and workspace_id:
        from .dify_workflow import dify_workflow_tool

        for app_id in definition.workflow_refs:
            tool_name = f"workflow:{app_id}"
            resolved_tools.append(
                dify_workflow_tool(app_id, tool_name, workspace_id)
            )

    return model, resolved_tools


def _wrap_team_transfer_function(team: Team) -> None:
    """Wrap Agno Team's get_transfer_task_function to emit observable real-time delegation events."""
    orig_get_transfer_func = getattr(team, "get_transfer_task_function", None)
    if not orig_get_transfer_func:
        return

    def wrapped_get_transfer_func(*args, **kwargs):
        func = orig_get_transfer_func(*args, **kwargs)
        orig_entrypoint = func.entrypoint

        if inspect.iscoroutinefunction(orig_entrypoint):
            async def wrapped_entrypoint(
                member_id: str, task_description: str, expected_output: str | None = None
            ):
                member_name = member_id
                for m in getattr(team, "members", []):
                    if getattr(m, "agent_id", None) == member_id:
                        member_name = getattr(m, "name", member_id)
                        break
                emit_live_event(
                    event_type="agent.started",
                    source_type="agent",
                    source_id=member_id,
                    payload={
                        "member_id": member_id,
                        "name": member_name,
                        "task": task_description,
                        "message": f"协调员向【{member_name}】派发任务: {task_description[:100]}",
                    },
                )
                try:
                    res = await orig_entrypoint(member_id, task_description, expected_output=expected_output)
                    emit_live_event(
                        event_type="agent.completed",
                        source_type="agent",
                        source_id=member_id,
                        payload={
                            "member_id": member_id,
                            "name": member_name,
                            "message": f"【{member_name}】完成分配阶段推演",
                        },
                    )
                    return res
                except Exception as exc:
                    emit_live_event(
                        event_type="agent.failed",
                        source_type="agent",
                        source_id=member_id,
                        status="failed",
                        payload={"member_id": member_id, "name": member_name, "error": str(exc)},
                    )
                    raise

            func.entrypoint = wrapped_entrypoint
            if hasattr(func, "process_entrypoint"):
                func.process_entrypoint()
        else:
            def wrapped_entrypoint(
                member_id: str, task_description: str, expected_output: str | None = None
            ):
                member_name = member_id
                for m in getattr(team, "members", []):
                    if getattr(m, "agent_id", None) == member_id:
                        member_name = getattr(m, "name", member_id)
                        break
                emit_live_event(
                    event_type="agent.started",
                    source_type="agent",
                    source_id=member_id,
                    payload={
                        "member_id": member_id,
                        "name": member_name,
                        "task": task_description,
                        "message": f"协调员向【{member_name}】派发任务: {task_description[:100]}",
                    },
                )
                try:
                    res = orig_entrypoint(member_id, task_description, expected_output=expected_output)
                    emit_live_event(
                        event_type="agent.completed",
                        source_type="agent",
                        source_id=member_id,
                        payload={
                            "member_id": member_id,
                            "name": member_name,
                            "message": f"【{member_name}】完成分配阶段推演",
                        },
                    )
                    return res
                except Exception as exc:
                    emit_live_event(
                        event_type="agent.failed",
                        source_type="agent",
                        source_id=member_id,
                        status="failed",
                        payload={"member_id": member_id, "name": member_name, "error": str(exc)},
                    )
                    raise

            func.entrypoint = wrapped_entrypoint
            if hasattr(func, "process_entrypoint"):
                func.process_entrypoint()
        return func

    team.get_transfer_task_function = wrapped_get_transfer_func


def build_team(
    definition: TeamDefinition,
    member_definitions: Iterable[AgentDefinition],
    *,
    models: ModelRegistry | None = None,
    tools: ToolRegistry | None = None,
    workspace_id: str | None = None,
    recalled_memories_context: str | None = None,
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

    model_registry = models or ModelRegistry(workspace_id=workspace_id)
    tool_registry = tools or ToolRegistry()
    runtime_members: list[Agent | Team] = [
        build_agent(members_by_id[member_id], models=model_registry, tools=tool_registry, workspace_id=workspace_id)
        for member_id in expected_ids
    ]
    if not runtime_members:
        raise RuntimeConfigurationError("AGENT_NOT_FOUND", "Team has no members")

    coordinator_definition = (
        members_by_id.get(definition.leader_agent_id) if definition.leader_agent_id else members_by_id[expected_ids[0]]
    )
    if coordinator_definition is None:
        raise RuntimeConfigurationError("AGENT_NOT_FOUND", "Team leader is not a member")

    instructions = list(definition.instructions or [])
    if recalled_memories_context and recalled_memories_context.strip():
        instructions.insert(0, recalled_memories_context.strip())
    if getattr(definition, "mode", None) == "coordinate":
        instructions.append(
            "【协同调度规范 (Coordinated Multi-Agent Execution Protocol)】:\n"
            "1. 严禁一次性盲目并发派发所有任务。你必须按严密的流水线分工调度各成员智能体：\n"
            "   - 第一步（事实与民意调研）：首先向负责检索/调研的成员派发任务，收集互联网上公众/网民的真实讨论焦点、民间顾虑与一手信源；\n"
            "   - 第二步（移交与独立核验）：等待调研员完成返回后，提取调研员收集到的具体观点、网传争议与链接，作为任务输入明确移交给核验员，要求核验员对这些具体言论逐条进行真实性核验与科学标准比对；\n"
            "   - 第三步（综合研判与成果输出）：在各成员均完成实质性工作后，由你整合两方成果输出最终报告。\n"
            "2. 针对网络讨论类任务的交付标准：\n"
            "   - 必须设立专门章节【👥 网络舆情与公众讨论焦点】，真实反映民间关切（如辅食/糙米担忧、网传致癌流言、民间淘米降砷法）；\n"
            "   - 必须设立【⚖️ 核心争议深度对撞（民间网传关切 vs 权威科学事实）】；\n"
            "   - 必须设立【🔗 数据与信息来源清单】，罗列包含真实链接的 Markdown 引用 [来源标题](URL)。"
        )
    if definition.leader_agent_id:
        instructions.insert(
            0,
            f"Use {coordinator_definition.name} ({coordinator_definition.role}) as the declared coordination lead.",
        )

    # S3-补1: Inject review_policy, completion conditions, and handoff context
    # into the team instructions so the coordinator enforces them during execution.
    review_policy = definition.review_policy or {}
    completion = definition.completion or {}
    handoff_context = (definition.handoff_context or "").strip()

    if review_policy.get("criteria"):
        criteria_text = "; ".join(review_policy["criteria"])
        max_rounds = review_policy.get("max_rounds", 3)
        instructions.append(
            f"Review policy (max {max_rounds} rounds): before marking work complete, "
            f"verify: {criteria_text}. If criteria are not met, hand back to the "
            f"responsible member for rework. Do not claim completion without verification."
        )
    if review_policy.get("require_unanimous"):
        instructions.append(
            "All team members must agree on the final result before marking complete. "
            "Dissent must be recorded and addressed."
        )
    if handoff_context:
        instructions.append(
            f"Handoff context: {handoff_context}. Ensure this context is preserved "
            f"and communicated during member transitions."
        )
    if completion.get("all_members_executed"):
        instructions.append(
            "Every assigned member must produce output before the team result is "
            "considered complete. Missing member output means the task is incomplete."
        )
    if completion.get("no_unresolved_errors"):
        instructions.append(
            "No unresolved errors may remain. If a member reports a tool failure or "
            "insufficient evidence, the issue must be addressed before completion."
        )
    if completion.get("custom", "").strip():
        instructions.append(f"Additional completion requirement: {completion['custom']}")

    coordinator_model = model_registry.resolve(coordinator_definition.model_ref)
    limits = definition.limits or {}
    # ponytail: max_tokens bounds each response, not total team usage; add a shared budget before billing enforcement.
    coordinator_model.max_tokens = limits.get("max_tokens", 20000)
    for member in runtime_members:
        cast(OpenAILike, member.model).max_tokens = coordinator_model.max_tokens
    team = Team(
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
            "uloo_review_policy": dict(review_policy),
            "uloo_completion": dict(completion),
            "uloo_handoff_context": handoff_context,
        },
        telemetry=False,
        monitoring=False,
        markdown=False,
        stream_member_events=True,
    )

    _wrap_team_transfer_function(team)
    return team
