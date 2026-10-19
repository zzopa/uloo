# AI生成
"""Model configuration sync endpoint.

Allows the BFF to push Dify workspace model provider credentials to Core
at runtime, eliminating the need for .env configuration (S1-3).
"""

from typing import overload

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, SecretStr

from ..security import RequestContext, require_service_context

router = APIRouter(prefix="/models", tags=["models"])


class ModelProviderConfig(BaseModel):
    """One model provider configuration pushed from the Dify workspace."""

    provider: str = Field(description="Provider key (e.g. 'deepseek' or Dify ID)")
    base_url: str = Field(default="", description="API base URL, empty for default")
    api_key: SecretStr = Field(description="API key for this provider")
    model_ids: list[str] = Field(default_factory=list, description="Available model IDs")


class ModelConfigSync(BaseModel):
    """Batch of model provider configurations to sync."""

    providers: list[ModelProviderConfig]
    aliases: dict[str, str] = Field(default_factory=dict, description="Dify→ULOO provider aliases")


class ModelConfigSyncResponse(BaseModel):
    """Result of a model config sync."""

    synced_providers: list[str]
    total_models: int


# Runtime cache keyed by workspace_id to enforce workspace isolation.
# _runtime_config: workspace_id → (provider → (base_url, api_key, model_ids))
# _runtime_aliases: workspace_id → (dify_provider_id → uloo_provider_name)
# These are module-level so they persist across requests within the process.
_runtime_config: dict[str, dict[str, tuple[str, SecretStr, list[str]]]] = {}
_runtime_aliases: dict[str, dict[str, str]] = {}


@overload
def get_runtime_config(workspace_id: None = None) -> dict[str, dict[str, tuple[str, SecretStr, list[str]]]]: ...


@overload
def get_runtime_config(workspace_id: str) -> dict[str, tuple[str, SecretStr, list[str]]]: ...


def get_runtime_config(
    workspace_id: str | None = None,
) -> dict[str, tuple[str, SecretStr, list[str]]] | dict[str, dict[str, tuple[str, SecretStr, list[str]]]]:
    """Return the runtime model config cache.

    With *workspace_id*: return the provider→config dict for that workspace.
    Without: return the full workspace→providers mapping (for tests/cleanup).
    """
    if workspace_id is None:
        return _runtime_config
    return _runtime_config.setdefault(workspace_id, {})


@overload
def get_runtime_aliases(workspace_id: None = None) -> dict[str, dict[str, str]]: ...


@overload
def get_runtime_aliases(workspace_id: str) -> dict[str, str]: ...


def get_runtime_aliases(
    workspace_id: str | None = None,
) -> dict[str, str] | dict[str, dict[str, str]]:
    """Return the runtime provider aliases.

    With *workspace_id*: return the alias dict for that workspace.
    Without: return the full workspace→aliases mapping (for tests/cleanup).
    """
    if workspace_id is None:
        return _runtime_aliases
    return _runtime_aliases.setdefault(workspace_id, {})


@router.put("/config", response_model=ModelConfigSyncResponse)
async def sync_model_config(
    payload: ModelConfigSync,
    context: RequestContext = Depends(require_service_context),
) -> ModelConfigSyncResponse:
    """Receive model provider configurations from the BFF.

    The BFF calls this endpoint to sync Dify workspace model credentials
    to Core at startup or when the user updates model provider settings.
    This eliminates the need for .env configuration (S1-3).
    """
    ws_key = str(context.workspace_id)
    ws_config = _runtime_config.setdefault(ws_key, {})
    ws_aliases = _runtime_aliases.setdefault(ws_key, {})
    synced: list[str] = []
    total_models = 0

    for provider_cfg in payload.providers:
        ws_config[provider_cfg.provider] = (
            provider_cfg.base_url,
            provider_cfg.api_key,
            provider_cfg.model_ids,
        )
        synced.append(provider_cfg.provider)
        total_models += len(provider_cfg.model_ids)

    if payload.aliases:
        ws_aliases.update(payload.aliases)

    return ModelConfigSyncResponse(synced_providers=synced, total_models=total_models)
