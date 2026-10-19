# AI生成
"""Resolve persisted model references into configured Agno model instances."""

from dataclasses import dataclass

from agno.models.openai.like import OpenAILike
from pydantic import SecretStr

from ..config import Settings, settings


class RuntimeConfigurationError(ValueError):
    """A stable, user-actionable runtime configuration error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParsedModelRef:
    provider: str
    model_id: str
    raw_provider: str = ""
    is_dify_ref: bool = False


class ModelRegistry:
    """Allow-listed model providers configured by the ULOO service.

    Provider configuration is resolved in this priority order:
    1. **Runtime sync** – credentials pushed from the Dify workspace via
       ``PUT /api/v1/models/config`` (S1-3, eliminates .env dependency).
    2. **.env settings** – ``ULOO_MODEL_PROVIDERS`` + ``ULOO_MODEL_PROVIDER_API_KEYS``.

    Supports two model_ref formats:
    1. **ULOO .env format**: ``provider:model-id`` (e.g. ``deepseek:deepseek-chat``)
    2. **Dify workspace format**: ``langgenius/deepseek/deepseek:deepseek-chat``
       The Dify provider ID may contain slashes.  When ``model_provider_aliases``
       maps it to a .env provider name, that alias is used for credential lookup.
    """

    def __init__(self, app_settings: Settings = settings, *, workspace_id: str | None = None) -> None:
        self._settings = app_settings
        self._workspace_id = workspace_id

    def _resolve_alias(self, provider: str) -> str:
        """Resolve a Dify provider ID to a ULOO provider name using aliases."""
        # Runtime aliases take priority, then .env aliases
        from ..api.models import get_runtime_aliases

        runtime_aliases = get_runtime_aliases(self._workspace_id) if self._workspace_id is not None else {}
        if provider in runtime_aliases:
            return runtime_aliases[provider]
        return self._settings.model_provider_aliases.get(provider, provider)

    def _lookup_provider(self, provider: str) -> tuple[str, SecretStr] | None:
        """Look up base_url and api_key for a provider.

        Checks runtime sync cache first, then .env settings.
        Returns (base_url, api_key) or None if not found.
        """
        from ..api.models import get_runtime_config

        # 1. Runtime sync (Dify workspace) – scoped by workspace_id
        runtime = get_runtime_config(self._workspace_id) if self._workspace_id is not None else {}
        if provider in runtime:
            base_url, api_key, _model_ids = runtime[provider]
            if api_key and api_key.get_secret_value():
                return base_url, api_key

        # 2. .env settings (global fallback, not workspace-scoped)
        if provider in self._settings.model_providers and provider in self._settings.model_provider_api_keys:
            secret = self._settings.model_provider_api_keys[provider]
            if secret and secret.get_secret_value():
                return self._settings.model_providers[provider], secret

        return None

    def parse(self, model_ref: str) -> ParsedModelRef:
        ref = model_ref.strip()
        # Dify workspace refs have '/' in the provider ID (e.g.
        # "langgenius/deepseek/deepseek:deepseek-chat").  Split on the *last*
        # colon so model IDs may themselves contain colons.
        # ULOO .env refs use a simple provider name (no slashes); split on the
        # *first* colon so the model ID can contain colons (e.g. "provider:model:v2").
        first_part = ref.split(":", 1)[0] if ":" in ref else ref
        if "/" in first_part:
            provider, separator, model_id = ref.rpartition(":")
        else:
            provider, separator, model_id = ref.partition(":")
        if not separator or not provider or not model_id:
            raise RuntimeConfigurationError(
                "MODEL_CONFIG_MISSING",
                "model_ref must use the form 'provider:model-id'",
            )
        is_dify = "/" in provider
        resolved_provider = provider
        if is_dify:
            resolved_provider = self._resolve_alias(provider)
        return ParsedModelRef(
            provider=resolved_provider,
            model_id=model_id,
            raw_provider=provider,
            is_dify_ref=is_dify,
        )

    def configured_refs(self) -> list[str]:
        """Return configured provider names without exposing URLs or credentials."""
        from ..api.models import get_runtime_config

        refs = {
            provider
            for provider in self._settings.model_providers
            if provider in self._settings.model_provider_api_keys
        }
        # Only include runtime-synced providers when we have a workspace context.
        # Without workspace_id, runtime config keys are workspace IDs, not providers.
        if self._workspace_id is not None:
            refs.update(get_runtime_config(self._workspace_id).keys())
        return sorted(refs)

    def resolve(self, model_ref: str) -> OpenAILike:
        parsed = self.parse(model_ref)
        lookup = self._lookup_provider(parsed.provider)
        if lookup is None:
            raise RuntimeConfigurationError(
                "MODEL_CONFIG_MISSING",
                f"Model provider '{parsed.provider}' is not configured",
            )
        base_url_str, secret = lookup

        base_url = base_url_str.strip() or None
        return OpenAILike(
            id=parsed.model_id,
            provider=parsed.provider,
            api_key=secret.get_secret_value(),
            base_url=base_url,
            timeout=self._settings.model_timeout_seconds,
        )
