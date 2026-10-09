"""Capabilities endpoint - describes what ULOO Core actually supports."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..constants import TEAM_MODES
from ..db import transaction_session
from ..runtime.resources import load_resources
from ..schemas.common import CapabilitiesResponse, FeatureCapabilities
from ..security import RequestContext, require_service_context

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("", response_model=CapabilitiesResponse)
async def get_capabilities(context: RequestContext = Depends(require_service_context), db: AsyncSession = transaction_session) -> CapabilitiesResponse:
    """Return implemented capabilities; planned features remain false."""
    return CapabilitiesResponse(
        version="0.1.0",
        modes=list(TEAM_MODES),
        features=FeatureCapabilities(
            agents=True,
            teams=True,
            agent_test_runs=True,
            team_runs=True,
            streaming=False,
            memory=False,
        ),
        models={
            provider: {"base_url": url or "default"}
            for provider, url in settings.model_providers.items()
        },
        tools=(await load_resources(db, context.workspace_id)).available_refs(),
        agno_enabled=settings.agno_enabled,
    )
