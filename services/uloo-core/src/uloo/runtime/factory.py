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
    planner_ref = settings.planner_model_ref.strip()
    if not planner_ref:
        if settings.llm_base_url.strip() and settings.llm_model.strip():
            planner_ref = f"default:{settings.llm_model.strip()}"
        else:
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
        model=model_registry.resolve(planner_ref),
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
    tool_call_limit: int | None = 8,
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
        "【权威官方信源硬性约束】: 报告中引用的所有出处超链接必须是真实官方网站或权威机构网站（如国家卫健委/市场监管总局/国家食品安全风险评估中心 cfsa.net.cn、世界卫生组织 WHO、美国 FDA、国家标准公开平台、央媒新华社/央视网/人民网、大学与科研院所）。严禁引用任何搜索引擎中间跳转链接（如包含 sogou.com/link 或 bing.com 等跳转链接），严禁引用自媒体营销号、文库分享网站（如豆丁网、百度文库）或商业推销网站。"
    )
    instructions.append(
        "【客观穿透与闭环抗辩审查规范 (Adversarial Rigor & Zero-Fluff Principle)】:\n"
        "1. 严禁迎合与虚假包装：在进行资质评估、可行性研判、尽调与合规分析时，必须实事求是。严禁为了凑篇幅或迎合用户而凭空捏造因果关系（如将普通企业采购主管强行挂钩国家安全战略、编造数千万虚假经济价值）。\n"
        "2. 拒绝教条复读：禁止花篇幅科普通用法条定义、政策名词解释或标准常识。必须直入主题，将 80% 以上篇幅用于将【目标对象的具体事实】与【法定/行业审核门槛】进行硬碰硬比对与证据链闭环审查。\n"
        "3. 勇于指出死穴：发现证据链断裂、条件不符、关键指标缺失时，必须直接判定风险等级并明确揭示死穴（如'证据链断裂'、'拒签率>95%'），坚决指出'不行/不适合'的具体法理与事实依据，并给出真正可行、务实的替代方案。"
    )
    skill_instructions, _ = tool_registry.skill_config(definition.skill_refs or [])
    instructions.extend(skill_instructions)
    if "web-search" in (definition.tool_refs or []):
        instructions.append(
            "CITATION MANDATE: When providing findings from web search, ALWAYS cite the real official source URL [Source Title](URL) using the actual destination website URL from search results. NEVER cite search engine redirect links (sogou.com/link, bing.com, etc.). Strictly filter out marketing blogs, SEO content farms, doc sharing sites, and commercial spam."
        )
    if any(k in (definition.name or "") or k in (definition.role or "") for k in ("统筹", "主编", "合流", "终审")):
        instructions.append(
            "【终审主编直接合流撰写指令】：作为终审主编与统筹岗位，前序各专员已经完成了详尽的附件解析、全网搜索、信源交叉核验与数据测算。你的首要任务是综合前序各岗位的实质成果直接进行系统合流、红蓝对抗审查与全篇决策研报撰写，严禁调用联网搜索发起多轮重复检索，必须立即集中全部算力完成深度长篇决策研报的直接撰写与交付！"
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
        tool_call_limit=tool_call_limit,
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

        def _resolve_target_member(raw_id: str):
            for m in getattr(team, "members", []):
                m_uuid = str(getattr(m, "agent_id", ""))
                m_name = getattr(m, "name", "")
                m_url_safe = str(team._get_member_id(m) or "")
                if raw_id in (m_uuid, m_name, m_url_safe) or raw_id.lower() in (m_name.lower(), m_url_safe.lower()):
                    return m, m_url_safe
            return None, raw_id

        if inspect.isasyncgenfunction(orig_entrypoint):
            async def wrapped_entrypoint(
                member_id: str, task_description: str, expected_output: str | None = None
            ):
                target_m, call_member_id = _resolve_target_member(member_id)
                member_name = getattr(target_m, "name", member_id) if target_m else member_id
                real_id = getattr(target_m, "agent_id", member_id) if target_m else member_id
                emit_live_event(
                    event_type="agent.started",
                    source_type="agent",
                    source_id=real_id,
                    payload={
                        "member_id": real_id,
                        "name": member_name,
                        "task": task_description,
                        "message": f"协调员向【{member_name}】派发任务: {task_description[:120]}",
                    },
                )
                accumulated = []
                try:
                    async for chunk in orig_entrypoint(call_member_id, task_description, expected_output=expected_output):
                        if isinstance(chunk, str):
                            accumulated.append(chunk)
                        elif hasattr(chunk, "content") and chunk.content:
                            accumulated.append(str(chunk.content))
                        yield chunk
                    full_content = "".join(accumulated).strip()
                    emit_live_event(
                        event_type="agent.completed",
                        source_type="agent",
                        source_id=real_id,
                        payload={
                            "member_id": real_id,
                            "name": member_name,
                            "round": "initial",
                            "task": task_description,
                            "output": {"content": full_content},
                            "char_count": len(full_content),
                            "message": f"【{member_name}】完成阶段推演，产出 {len(full_content)} 字",
                        },
                    )
                except Exception as exc:
                    emit_live_event(
                        event_type="agent.failed",
                        source_type="agent",
                        source_id=real_id,
                        status="failed",
                        payload={"member_id": real_id, "name": member_name, "error": str(exc)},
                    )
                    raise

        elif inspect.isgeneratorfunction(orig_entrypoint):
            def wrapped_entrypoint(
                member_id: str, task_description: str, expected_output: str | None = None
            ):
                target_m, call_member_id = _resolve_target_member(member_id)
                member_name = getattr(target_m, "name", member_id) if target_m else member_id
                real_id = getattr(target_m, "agent_id", member_id) if target_m else member_id
                emit_live_event(
                    event_type="agent.started",
                    source_type="agent",
                    source_id=real_id,
                    payload={
                        "member_id": real_id,
                        "name": member_name,
                        "task": task_description,
                        "message": f"协调员向【{member_name}】派发任务: {task_description[:120]}",
                    },
                )
                accumulated = []
                try:
                    for chunk in orig_entrypoint(call_member_id, task_description, expected_output=expected_output):
                        if isinstance(chunk, str):
                            accumulated.append(chunk)
                        elif hasattr(chunk, "content") and chunk.content:
                            accumulated.append(str(chunk.content))
                        yield chunk
                    full_content = "".join(accumulated).strip()
                    emit_live_event(
                        event_type="agent.completed",
                        source_type="agent",
                        source_id=real_id,
                        payload={
                            "member_id": real_id,
                            "name": member_name,
                            "round": "initial",
                            "task": task_description,
                            "output": {"content": full_content},
                            "char_count": len(full_content),
                            "message": f"【{member_name}】完成阶段推演，产出 {len(full_content)} 字",
                        },
                    )
                except Exception as exc:
                    emit_live_event(
                        event_type="agent.failed",
                        source_type="agent",
                        source_id=real_id,
                        status="failed",
                        payload={"member_id": real_id, "name": member_name, "error": str(exc)},
                    )
                    raise

        elif inspect.iscoroutinefunction(orig_entrypoint):
            async def wrapped_entrypoint(
                member_id: str, task_description: str, expected_output: str | None = None
            ):
                target_m, call_member_id = _resolve_target_member(member_id)
                member_name = getattr(target_m, "name", member_id) if target_m else member_id
                real_id = getattr(target_m, "agent_id", member_id) if target_m else member_id
                emit_live_event(
                    event_type="agent.started",
                    source_type="agent",
                    source_id=real_id,
                    payload={
                        "member_id": real_id,
                        "name": member_name,
                        "task": task_description,
                        "message": f"协调员向【{member_name}】派发任务: {task_description[:120]}",
                    },
                )
                try:
                    res = await orig_entrypoint(call_member_id, task_description, expected_output=expected_output)
                    full_content = str(res or "").strip()
                    emit_live_event(
                        event_type="agent.completed",
                        source_type="agent",
                        source_id=real_id,
                        payload={
                            "member_id": real_id,
                            "name": member_name,
                            "round": "initial",
                            "task": task_description,
                            "output": {"content": full_content},
                            "char_count": len(full_content),
                            "message": f"【{member_name}】完成阶段推演，产出 {len(full_content)} 字",
                        },
                    )
                    return res
                except Exception as exc:
                    emit_live_event(
                        event_type="agent.failed",
                        source_type="agent",
                        source_id=real_id,
                        status="failed",
                        payload={"member_id": real_id, "name": member_name, "error": str(exc)},
                    )
                    raise

        else:
            def wrapped_entrypoint(
                member_id: str, task_description: str, expected_output: str | None = None
            ):
                target_m, call_member_id = _resolve_target_member(member_id)
                member_name = getattr(target_m, "name", member_id) if target_m else member_id
                real_id = getattr(target_m, "agent_id", member_id) if target_m else member_id
                emit_live_event(
                    event_type="agent.started",
                    source_type="agent",
                    source_id=real_id,
                    payload={
                        "member_id": real_id,
                        "name": member_name,
                        "task": task_description,
                        "message": f"协调员向【{member_name}】派发任务: {task_description[:120]}",
                    },
                )
                try:
                    res = orig_entrypoint(call_member_id, task_description, expected_output=expected_output)
                    full_content = str(res or "").strip()
                    emit_live_event(
                        event_type="agent.completed",
                        source_type="agent",
                        source_id=real_id,
                        payload={
                            "member_id": real_id,
                            "name": member_name,
                            "round": "initial",
                            "task": task_description,
                            "output": {"content": full_content},
                            "char_count": len(full_content),
                            "message": f"【{member_name}】完成阶段推演，产出 {len(full_content)} 字",
                        },
                    )
                    return res
                except Exception as exc:
                    emit_live_event(
                        event_type="agent.failed",
                        source_type="agent",
                        source_id=real_id,
                        status="failed",
                        payload={"member_id": real_id, "name": member_name, "error": str(exc)},
                    )
                    raise

        wrapped_entrypoint.__doc__ = orig_entrypoint.__doc__
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
    limits = definition.limits or {}
    member_tool_limit = limits.get("max_iterations", 8)
    runtime_members: list[Agent | Team] = [
        build_agent(
            members_by_id[member_id],
            models=model_registry,
            tools=tool_registry,
            workspace_id=workspace_id,
            tool_call_limit=member_tool_limit,
        )
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
        member_workflow_steps = []
        for idx, m_id in enumerate(expected_ids):
            m_def = members_by_id.get(m_id)
            if not m_def:
                continue
            member_workflow_steps.append(
                f"   - 阶段 {idx+1}【{m_def.name}】(职责: {m_def.role or '专业协同'}): 必须调用 transfer_task_to_member(member_id='{m_def.name}', task_description='...') 向该成员派发任务，等待其输出实质性成果后再推进下一阶段；"
            )
        pipeline_desc = "\n".join(member_workflow_steps)
        instructions.append(
            f"【协同调度与岗位完整覆盖规范 (Mandatory Multi-Agent Pipeline Protocol)】:\n"
            f"1. 严禁跳步或遗漏任何配置成员。审批方案规划了本团队全部 {len(expected_ids)} 个专业岗位，你必须按严密的流水线分工依次调用 transfer_task_to_member 调度所有成员：\n"
            f"{pipeline_desc}\n"
            f"2. 【全员履职硬性要求】：所有配置成员岗位均必须有实质性输出。必须等待前序岗位成果返回，将上一岗位的输出成果作为输入明确移交给下一岗位，环环相扣。即使你是领队，也必须调用【研报统筹员】（或终审岗位）完成统筹合流！\n"
            f"3. 针对权威信源与交付标准：\n"
            f"   - 【检索节制防超时】：各调研岗位联网检索必须聚焦核心关键事实，单次任务检索严格控制在 2~3 次高质量查询以内。如遇公开数据未直接匹配，应立即基于已获信息开展推演，严禁无限循环重搜！特别强调：【终审主编与研报统筹员】的核心职责是对前三位专员已经检索核验的大量真实信源进行综合研判、去伪存真与全篇合流撰写，严禁在终审阶段进行多轮次冗余检索（最多 1 次补充核实），必须全力保障在时限内高质量完成终审研报！\n"
            f"   - 【信源规范】：所有引用链接必须来自真实官方网站（政府部门、世卫组织、国家标准委员会、权威央媒），绝对禁止引用搜索引擎中间跳转链接；\n"
            f"   - 在各成员均完成实质性工作后，由终审统筹员交付高规格决策研报。\n"
            f"4. 【内容丰满度与深度输出硬性规范 (Comprehensive & In-Depth Delivery Standard)】:\n"
            f"   - 【拒绝简短摘要】：向各岗位派发任务时，task_description 必须明确指示该成员：充分展开论述、详细列出背景数据、事实细节、分析论证推导过程，输出篇幅充实、细节详备的专业分析长文，严禁两三句话草草了事或只给干瘪清单；\n"
            f"   - 【层层递进】：下一岗位必须在前序岗位提供的大量详实事实与数据基础上进一步展开深度挖掘与交叉研判，不得丢失或压缩前序成果中的关键细节；\n"
            f"   - 【高规格研报交付】：终审主编与统筹岗位必须将全流程所有详实事实、核验证据、商业推演与权威信源完整合流，产出具有深度商业洞察与完整决策依据的高规格长篇决策研报。\n"
            f"5. 【文档与附件优先解析与主体锚定规范 (Attachment Parsing & Subject Grounding)】:\n"
            f"   - 若用户指令或上下文包含附件文档（如【用户上传附件文档】或 .pdf 文件）：\n"
            f"   - 必须指示阶段 1 成员（信息检索/调研专员）首先调用 pdf-parser 工具解析该文档，完整提取其中的个人姓名、年龄背景、教育工作经历、专业技能与数据表格；\n"
            f"   - 后续所有成员（数据分析师、核验专员、统筹研报员）必须严格基于从该附件中解析出的真实个人履历与事实开展适配评估与可行性推演，严禁脱离附件内容进行空洞泛化的法条罗列！\n"
            f"6. 【证据链闭环与对抗性评估准则 (Adversarial Rigor & Closed-Loop Evidence Integrity)】:\n"
            f"   - 【严禁自说自话与虚构包装】：评估类任务（如资格审查、可行性论证、合规审查）必须坚持客观事实与证据闭环。严禁把普通岗位职责（如车间采购、日常质检、行政主管）强行升华拔高为'国家级战略贡献'，严禁虚构缺乏实据支撑的经济数据与战略关联！\n"
            f"   - 【红蓝对抗审查】：派发任务给信源核验与数据分析岗位时，必须明确指示其扮演'苛刻的官方审查官/移民官/风控审计师'角色，对申请人或项目的每一项主张进行证据链闭环检验（是否排他？是否具备全国性重大影响？是否有第三方客观权威成果？是否仅属于特定雇主的日常职务职责？）；\n"
            f"   - 【严禁法条与概念复读】：报告严禁大段抄录法规条文、政策定义或名词解释。审查的唯一重点是'人/标的物与标准的硬碰硬对碰'，必须开门见山给出定性结论（适不适合、可行不可行、拒签/失败概率多大、致命死穴在哪）；\n"
            f"   - 【务实替代方案】：若评估结论为不适合或高风险，报告必须基于当事人或标的物的真实长处，给出合乎现实、切实可行的替代落地路径（如雇主担保、跨国派遣或其他稳妥方案），而不是兜售虚假希望。"
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
    min_team_tool_limit = max(limits.get("max_iterations", 8), len(runtime_members) + 4)
    team = Team(
        team_id=str(definition.id),
        add_datetime_to_instructions=True,
        name=definition.name,
        description=definition.description,
        mode=cast(TeamMode, definition.mode),
        members=runtime_members,
        model=coordinator_model,
        tool_call_limit=min_team_tool_limit,
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
