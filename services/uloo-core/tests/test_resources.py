"""Resource persistence, tenancy, and effective runtime bindings."""

import uuid

import pytest

from uloo.models.agent import AgentDefinition
from uloo.runtime.factory import build_agent
from uloo.runtime.models import RuntimeConfigurationError
from uloo.runtime.resources import load_resources

from .conftest import TEST_WORKSPACE_ID
from .test_agents_teams import create_agent
from .test_runtime_factory import configured_models


async def test_resource_crud_version_tenant_and_delete_protection(client):
    payload = {"key": "market-search", "name": "Market search", "config": {"query_prefix": "plywood", "max_results": 3}}
    created = await client.post("/api/v1/tools", json=payload)
    assert created.status_code == 201, created.text
    assert (await client.post("/api/v1/tools", json=payload)).status_code == 409
    item = created.json()
    assert item["config"]["adapter"] == "web-search"
    assert (await client.get("/api/v1/capabilities")).json()["tools"] == ["market-search", "web-search"]
    update = {k: v for k, v in item.items() if k not in {"key", "version"}}
    update["expected_version"] = 1
    update["name"] = "Updated search"
    assert (await client.patch("/api/v1/tools/market-search", json=update)).status_code == 200
    assert (await client.patch("/api/v1/tools/market-search", json=update)).status_code == 409
    other = {"X-ULOO-Workspace": str(uuid.uuid4())}
    assert [r["key"] for r in (await client.get("/api/v1/tools", headers=other)).json()] == ["web-search"]
    assert (await client.patch("/api/v1/tools/market-search", json=update, headers=other)).status_code == 404
    skill = {
        "key": "market-research",
        "name": "Research",
        "config": {"instructions": ["Cite every price with a source and date."], "tool_refs": ["market-search"]},
    }
    assert (await client.post("/api/v1/skills", json=skill)).status_code == 201
    assert (await client.delete("/api/v1/tools/market-search")).status_code == 409
    agent = await create_agent(client, skill_refs=["market-research"])
    assert agent["skill_refs"] == ["market-research"]
    assert (await client.delete("/api/v1/skills/market-research")).status_code == 409
    assert (
        await client.patch(f"/api/v1/agents/{agent['id']}", json={"expected_version": 1, "skill_refs": []})
    ).status_code == 200
    assert (await client.delete("/api/v1/skills/market-research")).status_code == 204
    assert (await client.delete("/api/v1/tools/market-search")).status_code == 204


async def test_skill_binding_loads_real_instructions_and_required_tool(client, db_session, monkeypatch):
    await client.post(
        "/api/v1/tools",
        json={"key": "price-search", "name": "Prices", "config": {"query_prefix": "plywood", "max_results": 2}},
    )
    await client.post(
        "/api/v1/skills",
        json={
            "key": "price-check",
            "name": "Price check",
            "config": {
                "instructions": ["Always distinguish quote date from retrieval date."],
                "tool_refs": ["price-search"],
            },
        },
    )
    created = await create_agent(client, skill_refs=["price-check"], tool_refs=["price-search"])
    definition = await db_session.get(AgentDefinition, uuid.UUID(created["id"]))
    resources = await load_resources(db_session, uuid.UUID(TEST_WORKSPACE_ID))
    runtime = build_agent(definition, models=configured_models(), tools=resources)
    assert "Always distinguish quote date from retrieval date." in runtime.instructions
    assert len(runtime.tools) == 1
    calls = []
    monkeypatch.setattr(
        "uloo.runtime.resources.web_search", lambda query, count: calls.append((query, count)) or "source result"
    )
    assert runtime.tools[0]("price") == "source result"
    assert calls == [("plywood price", 2)]
    validate = await client.post(f"/api/v1/agents/{created['id']}/validate")
    assert validate.json()["valid"] is True
    item = (await client.get("/api/v1/skills")).json()[0]
    update = {k: v for k, v in item.items() if k not in {"key", "version"}}
    update.update(expected_version=1, enabled=False)
    assert (await client.patch("/api/v1/skills/price-check", json=update)).status_code == 200
    assert (await client.post(f"/api/v1/agents/{created['id']}/validate")).json()["valid"] is False
    with pytest.raises(RuntimeConfigurationError, match="disabled"):
        build_agent(
            definition, models=configured_models(), tools=await load_resources(db_session, uuid.UUID(TEST_WORKSPACE_ID))
        )


async def test_builtin_configuration_can_be_disabled_and_invalid_adapters_rejected(client):
    invalid = await client.post(
        "/api/v1/tools", json={"key": "unsafe", "name": "Unsafe", "config": {"adapter": "python", "code": "print(1)"}}
    )
    assert invalid.status_code == 422
    invalid_skill = await client.post(
        "/api/v1/skills", json={"key": "empty", "name": "Empty", "config": {"instructions": []}}
    )
    assert invalid_skill.status_code == 422
    item = (await client.get("/api/v1/tools")).json()[0]
    update = {k: v for k, v in item.items() if k not in {"key", "version"}}
    update.update(expected_version=0, enabled=False)
    assert (await client.patch("/api/v1/tools/web-search", json=update)).status_code == 200
    agent = await create_agent(client, tool_refs=["web-search"])
    assert (await client.post(f"/api/v1/agents/{agent['id']}/validate")).json()["valid"] is False
    assert (await client.delete("/api/v1/tools/web-search")).status_code == 409
    assert (await client.get("/api/v1/capabilities")).json()["tools"] == []


async def test_team_run_consumes_bound_skill_and_preserves_configuration(client, monkeypatch):
    from uloo.runtime.execution import AgentExecutionResult

    from .test_runs import prepare_approved_task

    task, team = await prepare_approved_task(client, monkeypatch)
    await client.post(
        "/api/v1/skills",
        json={
            "key": "review",
            "name": "Review",
            "config": {"instructions": ["Verify source dates."], "tool_refs": ["web-search"]},
        },
    )
    agent_id = team["member_agent_ids"][0]
    assert (
        await client.patch(f"/api/v1/agents/{agent_id}", json={"expected_version": 1, "skill_refs": ["review"]})
    ).status_code == 200

    async def execute(runtime, prompt, *, timeout_seconds):
        assert "Verify source dates." in runtime.members[0].instructions
        assert len(runtime.members[0].tools) == 1
        return AgentExecutionResult(
            run_id="checked-team",
            output={"content": "Verified"},
            usage=None,
            member_results=[
                {
                    "agent_id": agent_id,
                    "name": "Reviewer",
                    "run_id": "member",
                    "output": {"content": "Verified"},
                    "usage": None,
                }
            ],
        )

    monkeypatch.setattr("uloo.api.runs.execute_agent", execute)
    response = await client.post(f"/api/v1/tasks/{task['id']}/runs", json={})
    assert response.status_code == 201, response.text
    snapshot = response.json()["configuration_snapshot"]["resources"]
    assert snapshot["skills"][0]["key"] == "review"
    assert snapshot["skills"][0]["version"] == 1
    assert [tool["key"] for tool in snapshot["tools"]] == ["web-search"]
    await client.patch(
        "/api/v1/skills/review",
        json={
            "name": "Review",
            "description": "",
            "enabled": True,
            "expected_version": 1,
            "config": {"instructions": ["Updated instructions"], "tool_refs": []},
        },
    )
    detail = (await client.get(f"/api/v1/runs/{response.json()['id']}")).json()
    assert detail["configuration_snapshot"]["resources"] == snapshot
