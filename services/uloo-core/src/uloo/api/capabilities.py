"""Capabilities endpoint - describes what ULOO Core supports."""

from typing import Any

from fastapi import APIRouter

from ..config import settings
from ..constants import TEAM_MODES

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("")
async def get_capabilities() -> dict[str, Any]:
    """Return supported modes, models, tools, and streaming capability."""
    return {
        "version": "0.1.0",
        "modes": list(TEAM_MODES),
        "streaming": True,
        "memory": True,
        "models": {
            provider: {"base_url": url or "default"}
            for provider, url in settings.model_providers.items()
        },
        "tools": [],
        "agno_enabled": settings.agno_enabled,
    }
