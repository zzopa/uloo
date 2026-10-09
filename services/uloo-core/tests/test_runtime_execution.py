"""Tests for the non-streaming Agno execution boundary."""

from types import SimpleNamespace

import pytest
from agno.agent import Agent
from agno.team.team import Team
from jsonschema.exceptions import ValidationError

from uloo.runtime.execution import execute_agent


class StubAgent:
    async def arun(self, prompt: str, *, stream: bool):
        assert prompt == "hello"
        assert stream is False
        return SimpleNamespace(
            run_id="agno-run-1",
            content="world",
            metrics={"input_tokens": 2, "output_tokens": 3, "ignored": "value"},
        )


async def test_execute_agent_normalizes_agno_response():
    result = await execute_agent(StubAgent(), "hello", timeout_seconds=1)  # type: ignore[arg-type]

    assert result.run_id == "agno-run-1"
    assert result.output == {"content": "world"}
    assert result.usage == {"input_tokens": 2, "output_tokens": 3}


class HangingAgent:
    async def arun(self, prompt: str, *, stream: bool):
        import asyncio

        await asyncio.sleep(10)


async def test_execute_agent_enforces_timeout():
    with pytest.raises(TimeoutError):
        await execute_agent(HangingAgent(), "hello", timeout_seconds=0.001)  # type: ignore[arg-type]


async def test_team_member_outputs_and_list_metrics_are_preserved(monkeypatch):
    member = Agent(agent_id="member-1", name="Researcher", telemetry=False)
    team = Team(members=[member], telemetry=False)

    async def run(_self, _prompt, *, stream):
        return SimpleNamespace(
            run_id="team-run",
            content="answer",
            metrics={"input_tokens": [5, 7], "output_tokens": [2, 3], "total_tokens": [7, 10]},
            member_responses=[
                SimpleNamespace(
                    agent_id="member-1",
                    run_id="member-run",
                    content="evidence",
                    metrics={"input_tokens": [4], "output_tokens": [6], "total_tokens": [10]},
                )
            ],
        )

    monkeypatch.setattr(Team, "arun", run)
    result = await execute_agent(team, "task", timeout_seconds=1)
    assert result.usage == {"input_tokens": 16, "output_tokens": 11, "total_tokens": 27}
    assert result.member_results[0]["output"] == {"content": "evidence"}
    assert result.member_results[0]["agent_id"] == "member-1"


@pytest.mark.parametrize("content", ['{"answer": 19}', '```json\n{"answer": 19}\n```'])
async def test_declared_output_schema_is_enforced(content):
    agent = StubAgent()
    agent.extra_data = {"uloo_output_schema": {"type": "integer"}}
    with pytest.raises(ValueError):
        await execute_agent(agent, "hello", timeout_seconds=1)  # type: ignore[arg-type]

    class JsonAgent(StubAgent):
        async def arun(self, prompt, *, stream):
            return SimpleNamespace(run_id="json-run", content=content, metrics=None)

    structured = JsonAgent()
    structured.extra_data = {
        "uloo_output_schema": {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "integer"}},
        }
    }
    result = await execute_agent(structured, "hello", timeout_seconds=1)  # type: ignore[arg-type]
    assert result.output == {"answer": 19}
    structured.extra_data["uloo_output_schema"]["required"] = ["missing"]
    with pytest.raises(ValidationError):
        await execute_agent(structured, "hello", timeout_seconds=1)  # type: ignore[arg-type]
