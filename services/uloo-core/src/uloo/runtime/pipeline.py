# AI生成
"""DAG Parallel Pipeline Executor for high-speed multi-agent concurrent collaboration."""

from __future__ import annotations

import asyncio
from typing import Any
import uuid

from ..config import settings
from ..logging import get_logger
from ..models.agent import AgentDefinition
from ..models.task import TaskPlan
from ..models.team import TeamDefinition
from .execution import AgentExecutionResult, execute_agent
from .factory import build_agent
from .live_events import emit_live_event, get_active_run, wait_pending_live_events
from .resources import ResourceRegistry

logger = get_logger("uloo.runtime.pipeline")


def build_dag_stages(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group plan steps into topological waves (stages) for parallel execution.

    Steps in Stage 0 have no dependencies (depends_on == []).
    Steps in Stage K only depend on steps from previous stages (0 .. K-1).
    All steps within the same stage can be safely executed in parallel with asyncio.gather.
    """
    if not steps:
        return []

    remaining = [dict(s) for s in steps]
    completed_ids: set[str] = set()
    stages: list[list[dict[str, Any]]] = []

    while remaining:
        current_wave = []
        unmet = []
        for step in remaining:
            step_id = str(step.get("id", ""))
            deps = {str(d) for d in (step.get("depends_on") or []) if d}
            if deps <= completed_ids:
                current_wave.append(step)
            else:
                unmet.append(step)

        if not current_wave:
            # Broken dependency or cycle: force advance the first unmet step to break deadlocks
            current_wave.append(unmet.pop(0))

        stages.append(current_wave)
        for s in current_wave:
            completed_ids.add(str(s.get("id", "")))
        remaining = unmet

    return stages


def should_execute_dag_pipeline(plan: TaskPlan | None, team_definition: TeamDefinition) -> bool:
    """Return whether the task run is eligible for DAG parallel execution."""
    if not getattr(settings, "parallel_pipeline_enabled", True):
        return False
    if not plan or not plan.steps:
        return False
    # Single step tasks do not need pipeline fan-out
    if len(plan.steps) < 2:
        return False
    # Route mode delegates to a single chosen agent
    if getattr(team_definition, "mode", None) == "route":
        return False
    return True


def _merge_usage(accumulated: dict[str, int], usage: dict[str, int] | None) -> None:
    if not usage:
        return
    for key in ("total_tokens", "input_tokens", "output_tokens"):
        val = usage.get(key)
        if isinstance(val, int) and not isinstance(val, bool):
            accumulated[key] = accumulated.get(key, 0) + val


async def execute_dag_pipeline(
    plan: TaskPlan,
    team_definition: TeamDefinition,
    member_definitions: list[AgentDefinition],
    resources: ResourceRegistry,
    workspace_id: str,
    query_text: str,
    timeout_seconds: float,
    recalled_context: str | None = None,
) -> AgentExecutionResult:
    """Execute multi-step approved plan using DAG parallel stages with fan-out concurrency."""
    members_by_id = {str(m.id): m for m in member_definitions}
    coordinator_definition = (
        members_by_id.get(str(team_definition.leader_agent_id))
        if team_definition.leader_agent_id
        else (member_definitions[0] if member_definitions else None)
    )

    stages = build_dag_stages(plan.steps)
    logger.info(
        "dag_pipeline_started",
        total_steps=len(plan.steps),
        total_stages=len(stages),
        stages_summary=[len(st) for st in stages],
    )

    step_outputs: dict[str, str] = {}
    member_results: list[dict[str, Any]] = []
    tool_summaries: list[dict[str, Any]] = []
    total_usage: dict[str, int] = {}
    last_res: AgentExecutionResult | None = None

    for stage_idx, stage_steps in enumerate(stages):
        is_final_stage = (stage_idx == len(stages) - 1)

        async def run_single_step(step: dict[str, Any]) -> tuple[dict[str, Any], AgentDefinition, AgentExecutionResult]:
            step_id = str(step.get("id", ""))
            assigned_id = str(step.get("assigned_agent_id") or "")
            m_def = members_by_id.get(assigned_id) or coordinator_definition
            if not m_def:
                raise ValueError(f"Agent definition not found for step {step_id}")

            agent = build_agent(m_def, tools=resources, workspace_id=workspace_id)

            # Build evidence from prior completed steps that this step depends on
            deps = [str(d) for d in (step.get("depends_on") or []) if str(d) in step_outputs]
            prior_context_parts = []
            if deps:
                for dep_id in deps:
                    matching_step = next((s for s in plan.steps if str(s.get("id")) == dep_id), None)
                    dep_title = matching_step.get("title", dep_id) if matching_step else dep_id
                    prior_context_parts.append(
                        f"【前序步骤：{dep_title}】成果：\n{step_outputs[dep_id]}"
                    )
            elif is_final_stage and step_outputs:
                # If final synthesis step, provide all previous outputs
                for prev_id, prev_out in step_outputs.items():
                    matching_step = next((s for s in plan.steps if str(s.get("id")) == prev_id), None)
                    prev_title = matching_step.get("title", prev_id) if matching_step else prev_id
                    prior_context_parts.append(
                        f"【协同专员成果：{prev_title}】：\n{prev_out}"
                    )

            prior_context_str = "\n\n".join(prior_context_parts) if prior_context_parts else "（本阶段为首发调研，无前序输入）"

            prompt = (
                f"你是岗位【{m_def.name}】（职责：{m_def.role or '专业协同'}）。\n"
                f"请执行当前协同推演步骤：【{step.get('title', '专业协同')}】。\n\n"
                f"【核心业务目标与用户诉求】：\n{query_text}\n\n"
                f"{f'【长期记忆先验经验】：\n{recalled_context}\n\n' if recalled_context else ''}"
                f"【前序协同成员已产出的实质性成果与依据】：\n{prior_context_str}\n\n"
                f"【本步骤专属任务指引】：\n{step.get('description') or '展开深入详尽的论证与分析'}\n\n"
                f"【执行硬性规范】：\n"
                f"1. 展开充分详尽的论述，提供完整的事实、论据、推演逻辑与深入洞察，严禁两句话草草了事；\n"
                f"2. 保持客观对抗性审核视角，严禁缺乏实据支撑的空洞拔高；\n"
                f"3. 若涉及网络检索或信源引用，精准聚焦核心关键词，保留真实出处链接 [标题](URL)；\n"
                f"4. 最终产出篇幅充实、细节详备的高质量专业成果。"
            )

            emit_live_event(
                "agent.started",
                {"name": m_def.name, "step": step.get("title", ""), "stage": stage_idx},
                source_type="agent",
                source_id=str(m_def.id),
            )

            step_timeout = min(timeout_seconds, 240.0)
            res = await execute_agent(agent, prompt, timeout_seconds=step_timeout)

            emit_live_event(
                "agent.completed",
                {
                    "agent_id": str(m_def.id),
                    "name": m_def.name,
                    "run_id": res.run_id or f"dag-{step.get('id')}",
                    "output": res.output,
                    "usage": res.usage,
                    "message": f"{m_def.name} completed its assigned work",
                    "step": step.get("title", ""),
                    "stage": stage_idx,
                    "round": "initial",
                },
                source_type="agent",
                source_id=str(m_def.id),
            )

            return step, m_def, res

        # Concurrently execute all steps in this wave
        logger.info("dag_wave_executing", stage=stage_idx, parallel_count=len(stage_steps))
        wave_results = await asyncio.gather(*(run_single_step(s) for s in stage_steps))

        for step, m_def, res in wave_results:
            last_res = res
            content_val = res.output.get("content", "") if isinstance(res.output, dict) else str(res.output or "")
            step_outputs[str(step.get("id", ""))] = content_val

            member_results.append({
                "agent_id": str(m_def.id),
                "name": m_def.name,
                "run_id": res.run_id or f"dag-{step.get('id')}",
                "output": res.output,
                "usage": res.usage,
            })
            tool_summaries.extend(res.tool_summaries or [])
            _merge_usage(total_usage, res.usage)

    # Determine final deliverable:
    # Prefer final stage or coordinator/editor output with substantive length
    final_output = {"content": ""}
    for mr in reversed(member_results):
        mr_name = mr.get("name", "")
        mr_content = mr.get("output", {}).get("content", "") if isinstance(mr.get("output"), dict) else str(mr.get("output") or "")
        if any(kw in mr_name for kw in ("统筹", "主编", "终审", "交付", "研报")) and len(mr_content) > 300:
            final_output = mr.get("output", final_output)
            break
    else:
        if last_res and last_res.output:
            final_output = last_res.output
        elif member_results:
            final_output = member_results[-1].get("output", {"content": ""})

    run_id = last_res.run_id if last_res else str(uuid.uuid4())
    logger.info("dag_pipeline_completed", total_members=len(member_results), stages=len(stages))

    active_ctx = get_active_run()
    if active_ctx and active_ctx.get("run_id"):
        await wait_pending_live_events(active_ctx["run_id"])

    return AgentExecutionResult(
        run_id=run_id,
        output=final_output,
        usage=total_usage or None,
        member_results=member_results,
        tool_summaries=tool_summaries,
    )
