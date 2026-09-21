"""Tests for Agent and Team CRUD operations.

Every test builds its own fixtures with unique keys, so the suite is unaffected
by rows left behind by earlier runs (see ``conftest.py`` for the rollback-based
isolation that keeps it that way).
"""

import asyncio
import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from uloo.api.agents import update_agent
from uloo.errors import ApiError
from uloo.models.agent import AgentDefinition
from uloo.schemas.agent import AgentUpdate
from uloo.security import RequestContext

from .conftest import TEST_WORKSPACE_ID


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

    body = resp.json()
    keys = [agent["key"] for agent in body["items"]]
    assert keys == [key]
    assert body["total"] == 1


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


async def test_agent_test_run_is_honestly_unavailable(client):
    agent = await create_agent(client, name="No Runtime Yet")
    resp = await client.post(f"/api/v1/agents/{agent['id']}/test-runs")
    assert resp.status_code == 501
    data = resp.json()
    assert data["code"] == "NOT_IMPLEMENTED"
    assert data["details"] == {"required_stage": 3}
    assert "is_mock" not in data


async def test_service_authentication_is_required(client):
    resp = await client.get("/api/v1/agents", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"

    resp = await client.get(
        "/api/v1/agents",
        headers={"Authorization": client.headers["Authorization"], "X-ULOO-Workspace": ""},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "WORKSPACE_REQUIRED"


async def test_agents_are_isolated_by_workspace(client):
    key = unique("workspace")
    first = await create_agent(client, key=key)
    workspace_a = client.headers["X-ULOO-Workspace"]
    workspace_b = "20000000-0000-0000-0000-000000000002"

    client.headers["X-ULOO-Workspace"] = workspace_b
    assert (await client.get(f"/api/v1/agents/{first['id']}")).status_code == 404
    second = await create_agent(client, key=key)
    assert second["id"] != first["id"]

    client.headers["X-ULOO-Workspace"] = workspace_a
    assert (await client.get(f"/api/v1/agents/{first['id']}")).status_code == 200


async def test_team_rejects_duplicate_members_and_invalid_uuid(client):
    agent = await create_agent(client)
    duplicate = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("duplicates"),
            "name": "Duplicates",
            "mode": "collaborate",
            "member_agent_ids": [agent["id"], agent["id"]],
        },
    )
    assert duplicate.status_code == 422

    invalid = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("invalid-uuid"),
            "name": "Invalid UUID",
            "mode": "collaborate",
            "member_agent_ids": ["not-a-uuid"],
        },
    )
    assert invalid.status_code == 422


async def test_team_patch_validates_the_merged_configuration(client):
    leader = await create_agent(client, role="leader")
    member = await create_agent(client, role="member")
    created = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("patch-team"),
            "name": "Patch Team",
            "mode": "coordinate",
            "leader_agent_id": leader["id"],
            "member_agent_ids": [leader["id"], member["id"]],
        },
    )
    assert created.status_code == 201, created.text
    team = created.json()

    remove_leader = await client.patch(
        f"/api/v1/teams/{team['id']}",
        json={"member_agent_ids": [member["id"]], "expected_version": 1},
    )
    assert remove_leader.status_code == 422
    assert remove_leader.json()["code"] == "INVALID_TEAM_CONFIGURATION"

    empty = await client.patch(
        f"/api/v1/teams/{team['id']}",
        json={"member_agent_ids": [], "expected_version": 1},
    )
    assert empty.status_code == 422


async def test_team_limits_have_safe_bounds(client):
    agent = await create_agent(client)
    resp = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("bad-limits"),
            "name": "Bad Limits",
            "mode": "collaborate",
            "member_agent_ids": [agent["id"]],
            "limits": {"max_iterations": 0, "timeout_seconds": 99999, "max_tokens": 0},
        },
    )
    assert resp.status_code == 422


async def test_soft_deleted_keys_remain_reserved(client):
    key = unique("reserved")
    agent = await create_agent(client, key=key)
    assert (await client.delete(f"/api/v1/agents/{agent['id']}")).status_code == 204

    replacement = await client.post(
        "/api/v1/agents",
        json={"key": key, "name": "Replacement", "role": "test", "model_ref": "test-model"},
    )
    assert replacement.status_code == 409
    assert replacement.json()["code"] == "AGENT_KEY_CONFLICT"


async def test_team_cannot_reference_another_workspace_agent(client):
    foreign_agent = await create_agent(client)
    original_workspace = client.headers["X-ULOO-Workspace"]
    client.headers["X-ULOO-Workspace"] = "30000000-0000-0000-0000-000000000003"

    resp = await client.post(
        "/api/v1/teams",
        json={
            "key": unique("foreign-member"),
            "name": "Foreign Member",
            "mode": "collaborate",
            "member_agent_ids": [foreign_agent["id"]],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "AGENT_NOT_FOUND"
    client.headers["X-ULOO-Workspace"] = original_workspace


async def test_concurrent_agent_updates_allow_only_one_winner(engine):
    """The row lock makes the version check atomic across database sessions."""
    workspace_id = uuid.UUID(TEST_WORKSPACE_ID)
    agent_id = uuid.uuid4()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as setup:
        setup.add(
            AgentDefinition(
                id=agent_id,
                workspace_id=workspace_id,
                key=unique("concurrent"),
                name="Concurrent",
                role="test",
                model_ref="test-model",
            )
        )
        await setup.commit()

    async def attempt(name: str):
        async with session_factory() as session:
            try:
                result = await update_agent(
                    agent_id,
                    AgentUpdate(name=name, expected_version=1),
                    RequestContext(workspace_id=workspace_id),
                    session,
                )
                await session.commit()
                return result
            except ApiError as exc:
                await session.rollback()
                return exc

    try:
        outcomes = await asyncio.gather(attempt("First"), attempt("Second"))
        successes = [outcome for outcome in outcomes if not isinstance(outcome, ApiError)]
        conflicts = [outcome for outcome in outcomes if isinstance(outcome, ApiError)]
        assert len(successes) == 1
        assert successes[0].version == 2
        assert len(conflicts) == 1
        assert conflicts[0].status_code == 409
        assert conflicts[0].detail["code"] == "VERSION_CONFLICT"
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(delete(AgentDefinition).where(AgentDefinition.id == agent_id))
            await cleanup.commit()
