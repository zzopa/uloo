"""Resolve persisted model references into configured Agno model instances."""

from dataclasses import dataclass

from agno.models.openai.like import OpenAILike

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


class ModelRegistry:
    """Allow-listed model providers configured only by the ULOO service."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self._settings = app_settings

    def parse(self, model_ref: str) -> ParsedModelRef:
        provider, separator, model_id = model_ref.strip().partition(":")
        if not separator or not provider or not model_id:
            raise RuntimeConfigurationError(
                "MODEL_CONFIG_MISSING",
                "model_ref must use the form 'provider:model-id'",
            )
        return ParsedModelRef(provider=provider, model_id=model_id)

    def configured_refs(self) -> list[str]:
        """Return configured provider names without exposing URLs or credentials."""
        return sorted(
            provider
            for provider in self._settings.model_providers
            if provider in self._settings.model_provider_api_keys
        )

    def resolve(self, model_ref: str) -> OpenAILike:
        parsed = self.parse(model_ref)
        if parsed.provider not in self._settings.model_providers:
            raise RuntimeConfigurationError(
                "MODEL_CONFIG_MISSING",
                f"Model provider '{parsed.provider}' is not configured",
            )
        secret = self._settings.model_provider_api_keys.get(parsed.provider)
        if secret is None or not secret.get_secret_value():
            raise RuntimeConfigurationError(
                "MODEL_CONFIG_MISSING",
                f"API key for model provider '{parsed.provider}' is not configured",
            )

        base_url = self._settings.model_providers[parsed.provider].strip() or None
        return OpenAILike(
            id=parsed.model_id,
            provider=parsed.provider,
            api_key=secret.get_secret_value(),
            base_url=base_url,
            timeout=self._settings.model_timeout_seconds,
        )
