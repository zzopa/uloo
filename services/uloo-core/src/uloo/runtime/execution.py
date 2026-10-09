"""Small execution boundary around Agno responses."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from agno.agent import Agent
from agno.team.team import Team
from jsonschema.validators import validator_for
from pydantic import BaseModel
from referencing import Registry


@dataclass(frozen=True)
class AgentExecutionResult:
    run_id: str | None
    output: dict[str, Any]
    usage: dict[str, int] | None
    member_results: list[dict[str, Any]] = field(default_factory=list)


def _serialize_output(content: Any) -> dict[str, Any]:
    if isinstance(content, BaseModel):
        return content.model_dump(mode="json")
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        return {"items": content}
    return {"content": "" if content is None else str(content)}


def _serialize_usage(metrics: Any) -> dict[str, int] | None:
    if metrics is None:
        return None
    values = metrics if isinstance(metrics, dict) else vars(metrics)
    usage: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = values.get(key)
        if isinstance(value, list):
            value = sum(item for item in value if isinstance(item, int) and not isinstance(item, bool))
        if isinstance(value, int) and not isinstance(value, bool):
            usage[key] = value
    return usage or None


def _response_output(agent: Agent | Team, content: Any) -> dict[str, Any]:
    if content is None or (isinstance(content, str) and not content.strip()):
        raise ValueError("Model returned an empty result")
    schema = (getattr(agent, "extra_data", None) or {}).get("uloo_output_schema")
    if schema is not None:
        if isinstance(content, str):
            fence = re.fullmatch(r"```(?:json)?\s*\n(.*)\n```", content.strip(), flags=re.DOTALL)
            if fence:
                content = fence.group(1)
            content = json.loads(content)
        validator_for(schema)(schema, registry=Registry()).validate(content)
    return _serialize_output(content)


async def execute_agent(agent: Agent | Team, prompt: str, *, timeout_seconds: float) -> AgentExecutionResult:
    """Execute one non-streaming Agno Agent or Team run with a hard service timeout."""
    response = await asyncio.wait_for(
        agent.arun(prompt, stream=False),
        timeout=timeout_seconds,
    )
    member_results = []
    members = (
        {member.agent_id: member for member in agent.members if isinstance(member, Agent)}
        if isinstance(agent, Team)
        else {}
    )
    for member_response in getattr(response, "member_responses", []) or []:
        member_id = getattr(member_response, "agent_id", None)
        member = members.get(member_id)
        if member is None:
            continue
        member_results.append(
            {
                "agent_id": member_id,
                "name": member.name,
                "run_id": member_response.run_id,
                "output": _response_output(member, member_response.content),
                "usage": _serialize_usage(member_response.metrics),
            }
        )
    usage = _serialize_usage(getattr(response, "metrics", None))
    for member_result in member_results:
        for key, value in (member_result["usage"] or {}).items():
            if usage is None:
                usage = {}
            usage[key] = usage.get(key, 0) + value
    return AgentExecutionResult(
        run_id=str(response.run_id) if getattr(response, "run_id", None) else None,
        output=_response_output(agent, getattr(response, "content", None)),
        usage=usage,
        member_results=member_results,
    )
