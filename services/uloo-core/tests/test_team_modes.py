"""Contract tests: ULOO's Team modes must be executable by the Agno runtime.

The plan requires Agno to be the only Team scheduler, so accepting a mode Agno
cannot run would let a broken Team sit in PostgreSQL until the first Run.  These
tests fail the suite instead.
"""

from typing import get_args, get_type_hints

from agno.team.team import Team

from uloo.constants import TEAM_MODES, TEAM_MODES_REQUIRING_LEADER


def test_team_modes_match_agno_runtime():
    """TEAM_MODES must equal Agno's Team.mode Literal, in both directions."""
    agno_modes = get_args(get_type_hints(Team)["mode"])

    assert agno_modes, "could not read Team.mode from the installed Agno runtime"
    assert set(TEAM_MODES) == set(agno_modes), (
        f"ULOO accepts {set(TEAM_MODES)} but Agno {set(agno_modes)}. "
        "Update src/uloo/constants.py to match the installed Agno runtime."
    )


def test_leader_modes_are_known_modes():
    """Modes that demand a leader must be a subset of the accepted modes."""
    assert TEAM_MODES_REQUIRING_LEADER <= set(TEAM_MODES)


async def test_capabilities_reports_agno_modes(client):
    """The Dify UI is driven by /capabilities, so it must not advertise stale modes."""
    resp = await client.get("/api/v1/capabilities")
    assert resp.status_code == 200

    agno_modes = get_args(get_type_hints(Team)["mode"])
    assert set(resp.json()["modes"]) == set(agno_modes)


async def test_unsupported_mode_is_rejected(client):
    """'tasks' does not exist in Agno 1.8.4 and must not be accepted."""
    resp = await client.post("/api/v1/teams", json={
        "key": "unsupported-mode-team",
        "name": "Unsupported Mode",
        "mode": "tasks",
        "member_agent_ids": ["00000000-0000-0000-0000-000000000001"],
    })
    assert resp.status_code == 422


async def test_route_mode_requires_leader(client):
    """'route' is Agno's delegation mode, so ULOO requires a leader for it."""
    resp = await client.post("/api/v1/teams", json={
        "key": "route-without-leader",
        "name": "Route Without Leader",
        "mode": "route",
        "member_agent_ids": ["00000000-0000-0000-0000-000000000001"],
    })
    assert resp.status_code == 422
    assert "leader" in resp.text
