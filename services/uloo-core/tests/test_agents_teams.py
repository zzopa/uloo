"""Tests for Agent and Team CRUD operations."""

import pytest
from httpx import ASGITransport, AsyncClient

from uloo.main import app


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def test_create_and_get_agent(client):
    """Create an agent and retrieve it by ID."""
    resp = await client.post("/api/v1/agents", json={
        "key": "test-researcher",
        "name": "Test Researcher",
        "role": "Research specialist",
        "model_ref": "openai-compatible:gpt-4o-mini",
        "tool_refs": ["web_search"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["key"] == "test-researcher"
    assert data["version"] == 1
    agent_id = data["id"]

    # Get by ID
    resp = await client.get(f"/api/v1/agents/{agent_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Researcher"


async def test_list_agents(client):
    """List agents with search."""
    resp = await client.get("/api/v1/agents", params={"q": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


async def test_update_agent_optimistic_lock(client):
    """Update agent with correct and incorrect version."""
    # Create agent
    resp = await client.post("/api/v1/agents", json={
        "key": "test-lock",
        "name": "Lock Test",
        "role": "test",
        "model_ref": "test-model",
    })
    agent_id = resp.json()["id"]

    # Wrong version -> 409
    resp = await client.patch(f"/api/v1/agents/{agent_id}", json={
        "name": "Updated",
        "expected_version": 99,
    })
    assert resp.status_code == 409

    # Correct version -> 200
    resp = await client.patch(f"/api/v1/agents/{agent_id}", json={
        "name": "Updated",
        "expected_version": 1,
    })
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["name"] == "Updated"


async def test_create_team_with_members(client):
    """Create a team with two agents."""
    # Create two agents
    a1 = await client.post("/api/v1/agents", json={
        "key": "team-leader",
        "name": "Leader",
        "role": "leader",
        "model_ref": "test-model",
    })
    a2 = await client.post("/api/v1/agents", json={
        "key": "team-member",
        "name": "Member",
        "role": "member",
        "model_ref": "test-model",
    })
    a1_id = a1.json()["id"]
    a2_id = a2.json()["id"]

    # Create team
    resp = await client.post("/api/v1/teams", json={
        "key": "test-team",
        "name": "Test Team",
        "mode": "coordinate",
        "leader_agent_id": a1_id,
        "member_agent_ids": [a1_id, a2_id],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["mode"] == "coordinate"
    assert len(data["member_agent_ids"]) == 2
    assert data["version"] == 1

    # Get by key
    resp = await client.get("/api/v1/teams/by-key/test-team")
    assert resp.status_code == 200
    assert resp.json()["key"] == "test-team"

    # Validate
    resp = await client.post(f"/api/v1/teams/{data['id']}/validate")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


async def test_team_mode_validation(client):
    """coordinate mode requires leader_agent_id."""
    resp = await client.post("/api/v1/teams", json={
        "key": "bad-team",
        "name": "Bad Team",
        "mode": "coordinate",
        "member_agent_ids": ["00000000-0000-0000-0000-000000000001"],
    })
    assert resp.status_code == 422


async def test_delete_agent_blocked_by_team(client):
    """Cannot delete agent used by enabled team."""
    a1 = await client.post("/api/v1/agents", json={
        "key": "blocked-agent",
        "name": "Blocked",
        "role": "test",
        "model_ref": "test-model",
    })
    a2 = await client.post("/api/v1/agents", json={
        "key": "other-agent",
        "name": "Other",
        "role": "test",
        "model_ref": "test-model",
    })
    a1_id = a1.json()["id"]
    a2_id = a2.json()["id"]

    await client.post("/api/v1/teams", json={
        "key": "blocking-team",
        "name": "Blocking Team",
        "mode": "collaborate",
        "member_agent_ids": [a1_id, a2_id],
    })

    # Delete should fail with 409
    resp = await client.delete(f"/api/v1/agents/{a1_id}")
    assert resp.status_code == 409
