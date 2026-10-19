# AI生成
"""Capabilities endpoint - describes what ULOO Core actually supports."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..constants import TEAM_MODES
from ..db import transaction_session
from ..runtime.models import ModelRegistry, RuntimeConfigurationError
from ..runtime.resources import load_resources
from ..schemas.common import CapabilitiesResponse, FeatureCapabilities, ModelProviderInfo
from ..security import RequestContext, require_service_context

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


def _selectable_models(provider: str, workspace_id: str | None = None) -> list[str]:
    """Model ids the UI may offer for `provider`.

    Source priority: explicit `ULOO_MODEL_CATALOG` → models derived from
    `ULOO_PLANNER_MODEL_REF` when it references this provider → empty.
    Credentials are never returned.
    """
    catalog = settings.model_catalog.get(provider)
    if catalog:
        # dedup preserve order
        seen: set[str] = set()
        out: list[str] = []
        for m in catalog:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out
    if settings.planner_model_ref:
        try:
            parsed = ModelRegistry(workspace_id=workspace_id).parse(settings.planner_model_ref)
            if parsed.provider == provider:
                return [parsed.model_id]
        except RuntimeConfigurationError:
            return []
    return []


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
            provider: ModelProviderInfo(
                base_url=url or "default",
                models=_selectable_models(provider, str(context.workspace_id)),
            )
            for provider, url in settings.model_providers.items()
        },
        tools=(await load_resources(db, context.workspace_id)).available_refs(),
        agno_enabled=settings.agno_enabled,
        provider_aliases=settings.model_provider_aliases,
    )
