"""Health check endpoints."""

from typing import Any

from fastapi import APIRouter, Response, status

from ..config import settings
from ..db import check_db_connection
from ..logging import get_logger

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger(__name__)


@router.get("/live")
async def liveness() -> dict[str, Any]:
    """Process is alive."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(response: Response) -> dict[str, Any]:
    """All dependencies are ready. Returns 503 if any dependency is down."""
    checks: dict[str, bool] = {}

    # Database check
    checks["database"] = await check_db_connection()

    # Agno check (import availability)
    agno_ok = False
    agno_error: str | None = None
    if settings.agno_enabled:
        try:
            import agno  # noqa: F401

            agno_ok = True
        except ImportError as e:
            agno_error = str(e)
    else:
        agno_ok = True  # not required
    checks["agno"] = agno_ok

    # Model config check (at least one provider configured)
    checks["model_config"] = len(settings.model_providers) > 0

    # Service authentication must be configured before data APIs are usable.
    checks["service_auth"] = bool(settings.api_token)

    all_ready = all(checks.values())

    if not all_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    result: dict[str, Any] = {"status": "ready" if all_ready else "not_ready", "checks": checks}
    if agno_error:
        result["agno_error"] = agno_error
    return result
