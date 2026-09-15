"""Tests for Agent and Team CRUD operations.

Every test builds its own fixtures with unique keys, so the suite is unaffected
by rows left behind by earlier runs (see ``conftest.py`` for the rollback-based
isolation that keeps it that way).
"""

import uuid


def unique(prefix: str) -> str:
    """A key that cannot collide with anything already in the database."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def create_agent(client, **overrides) -> dict:
    """Create an agent and return the response body, asserting it succeeded."""
    payload = {
        "key": unique("agent"),
        "name": "Agent",
        "role": "test",
        "model_ref": "test-model",
    }
    payload.update(overrides)

    resp = await client.post("/api/v1/agents", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_and_get_agent(client):
    """Create an agent and retrieve it by ID."""
    created = await create_agent(client, name="Test Researcher", role="Research specialist")
    assert created["version"] == 1

    resp = await client.get(f"/api/v1/agents/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Researcher"


async def test_list_agents_search(client):
    """Search finds the agent this test just created."""
    key = unique("searchable")
    await create_agent(client, key=key, name=f"Searchable {key}")

    resp = await client.get("/api/v1/agents", params={"q": key})
    assert resp.status_code == 200

    keys = [a["key"] for a in resp.json()]
    assert keys == [key]


async def test_duplicate_agent_key_conflicts(client):
    """The same key cannot be created twice."""
    key = unique("dupe")
    await create_agent(client, key=key)

    resp = await client.post("/api/v1/agents", json={
        "key": key,
        "name": "Second",
        "role": "test",
        "model_ref": "test-model",
    })
    assert resp.status_code == 409


async def test_update_agent_optimistic_lock(client):
    """Update agent with correct and incorrect version."""
    created = await create_agent(client)
    agent_id = created["id"]

    resp = await client.patch(f"/api/v1/agents/{agent_id}", json={
        "name": "Updated",
        "expected_version": 99,
    })
    assert resp.status_code == 409

    resp = await client.patch(f"/api/v1/agents/{agent_id}", json={
        "name": "Updated",
        "expected_version": 1,
    })
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["name"] == "Updated"


async def test_create_team_with_members(client):
    """Create a team with two agents and read it back."""
    leader = await create_agent(client, name="Leader", role="leader")
    member = await create_agent(client, name="Member", role="member")

    team_key = unique("team")
    resp = await client.post("/api/v1/teams", json={
        "key": team_key,
        "name": "Test Team",
        "mode": "coordinate",
        "leader_agent_id": leader["id"],
        "member_agent_ids": [leader["id"], member["id"]],
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["mode"] == "coordinate"
    assert len(data["member_agent_ids"]) == 2
    assert data["version"] == 1

    resp = await client.get(f"/api/v1/teams/by-key/{team_key}")
    assert resp.status_code == 200
    assert resp.json()["key"] == team_key

    resp = await client.post(f"/api/v1/teams/{data['id']}/validate")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True, resp.text


async def test_create_team_rejects_unknown_member(client):
    """A team cannot reference an agent that does not exist."""
    resp = await client.post("/api/v1/teams", json={
        "key": unique("team"),
        "name": "Ghost Team",
        "mode": "collaborate",
        "member_agent_ids": ["00000000-0000-0000-0000-00000000dead"],
    })
    assert resp.status_code == 404


async def test_team_mode_validation(client):
    """coordinate mode requires leader_agent_id."""
    resp = await client.post("/api/v1/teams", json={
        "key": unique("bad-team"),
        "name": "Bad Team",
        "mode": "coordinate",
        "member_agent_ids": ["00000000-0000-0000-0000-000000000001"],
    })
    assert resp.status_code == 422


async def test_delete_agent_blocked_by_team(client):
    """Cannot delete agent used by enabled team."""
    first = await create_agent(client, name="Blocked")
    second = await create_agent(client, name="Other")

    resp = await client.post("/api/v1/teams", json={
        "key": unique("blocking-team"),
        "name": "Blocking Team",
        "mode": "collaborate",
        "member_agent_ids": [first["id"], second["id"]],
    })
    assert resp.status_code == 201, resp.text

    resp = await client.delete(f"/api/v1/agents/{first['id']}")
    assert resp.status_code == 409


async def test_delete_agent_without_team_succeeds(client):
    """An agent not referenced by an enabled team is soft deleted."""
    agent = await create_agent(client, name="Unused")

    resp = await client.delete(f"/api/v1/agents/{agent['id']}")
    assert resp.status_code == 204

    assert (await client.get(f"/api/v1/agents/{agent['id']}")).status_code == 404
