"""Capabilities endpoint - describes what ULOO Core actually supports."""

from fastapi import APIRouter

from ..config import settings
from ..constants import TEAM_MODES
from ..schemas.common import CapabilitiesResponse, FeatureCapabilities

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("", response_model=CapabilitiesResponse)
async def get_capabilities() -> CapabilitiesResponse:
    """Return implemented capabilities; planned features remain false."""
    return CapabilitiesResponse(
        version="0.1.0",
        modes=list(TEAM_MODES),
        features=FeatureCapabilities(
            agents=True,
            teams=True,
            agent_test_runs=False,
            team_runs=False,
            streaming=False,
            memory=False,
        ),
        models={
            provider: {"base_url": url or "default"}
            for provider, url in settings.model_providers.items()
        },
        tools=[],
        agno_enabled=settings.agno_enabled,
    )
