"""The committed OpenAPI contract must stay in sync with the app.

``contracts/openapi.yaml`` is the interface the Dify BFF and the agent strategy
code against (implementation plan section 4). If the app changes without the
contract being regenerated this test fails, which is the point: the file is a
locked artefact, not documentation that drifts quietly.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.yaml"

sys.path.insert(0, str(REPO_ROOT / "scripts"))


def test_contract_file_exists() -> None:
    assert CONTRACT_PATH.exists(), f"{CONTRACT_PATH} is missing. Run: python scripts/export_openapi.py"


def test_committed_contract_matches_the_app() -> None:
    from export_openapi import render_contract

    assert CONTRACT_PATH.read_text(encoding="utf-8") == render_contract(), (
        "contracts/openapi.yaml is out of date. Run: python scripts/export_openapi.py"
    )


def test_contract_covers_the_stage_two_agent_and_team_surface() -> None:
    """Guard against a path being dropped from the contract by accident."""
    import yaml

    document = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    paths = set(document["paths"])

    assert {
        "/api/v1/agents",
        "/api/v1/agents/{agent_id}",
        "/api/v1/agents/{agent_id}/validate",
        "/api/v1/teams",
        "/api/v1/teams/{team_id}",
        "/api/v1/teams/by-key/{team_key}",
        "/api/v1/teams/{team_id}/validate",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/capabilities",
    } <= paths
