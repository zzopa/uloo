# AI生成
"""Tests for ModelRegistry Dify workspace model reference support."""

import pytest

from uloo.config import Settings
from uloo.runtime.models import ModelRegistry, RuntimeConfigurationError


def _settings(**overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        model_providers={"deepseek": "http://localhost:3000/v1"},
        model_provider_api_keys={"deepseek": "sk-test"},
        model_provider_aliases={"langgenius/deepseek/deepseek": "deepseek"},
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestParseModelRef:
    """parse() must accept both ULOO .env and Dify workspace model refs."""

    def test_uloo_simple_ref(self):
        registry = ModelRegistry(_settings())
        parsed = registry.parse("deepseek:deepseek-chat")
        assert parsed.provider == "deepseek"
        assert parsed.model_id == "deepseek-chat"
        assert parsed.is_dify_ref is False
        assert parsed.raw_provider == "deepseek"

    def test_dify_ref_with_slashes(self):
        registry = ModelRegistry(_settings())
        parsed = registry.parse("langgenius/deepseek/deepseek:deepseek-chat")
        assert parsed.provider == "deepseek"  # resolved via alias
        assert parsed.model_id == "deepseek-chat"
        assert parsed.is_dify_ref is True
        assert parsed.raw_provider == "langgenius/deepseek/deepseek"

    def test_dify_ref_without_alias_falls_back_to_raw(self):
        """A Dify provider without an alias should use the raw provider ID."""
        registry = ModelRegistry(_settings())
        parsed = registry.parse("langgenius/openai/openai:gpt-4o")
        assert parsed.provider == "langgenius/openai/openai"
        assert parsed.model_id == "gpt-4o"
        assert parsed.is_dify_ref is True

    def test_invalid_ref_no_colon(self):
        registry = ModelRegistry(_settings())
        with pytest.raises(RuntimeConfigurationError) as exc_info:
            registry.parse("just-a-provider")
        assert exc_info.value.code == "MODEL_CONFIG_MISSING"

    def test_invalid_ref_empty_model(self):
        registry = ModelRegistry(_settings())
        with pytest.raises(RuntimeConfigurationError):
            registry.parse("deepseek:")

    def test_ref_with_colon_in_model_id(self):
        """Model IDs containing colons should still parse correctly via rpartition."""
        registry = ModelRegistry(_settings())
        parsed = registry.parse("deepseek:model:v2")
        assert parsed.provider == "deepseek"
        assert parsed.model_id == "model:v2"


class TestResolveDifyRef:
    """resolve() must build Agno models from Dify-style refs using aliases."""

    def test_resolve_dify_ref_via_alias(self):
        registry = ModelRegistry(_settings())
        model = registry.resolve("langgenius/deepseek/deepseek:deepseek-chat")
        assert model.id == "deepseek-chat"
        assert model.provider == "deepseek"

    def test_resolve_uloo_ref_still_works(self):
        registry = ModelRegistry(_settings())
        model = registry.resolve("deepseek:deepseek-chat")
        assert model.id == "deepseek-chat"
        assert model.provider == "deepseek"

    def test_resolve_dify_ref_no_alias_no_provider_config(self):
        """A Dify provider without alias and without .env config should fail."""
        registry = ModelRegistry(_settings())
        with pytest.raises(RuntimeConfigurationError) as exc_info:
            registry.resolve("langgenius/openai/openai:gpt-4o")
        assert exc_info.value.code == "MODEL_CONFIG_MISSING"


class TestCapabilitiesProviderAliases:
    """Capabilities endpoint must expose provider_aliases for the frontend."""

    def test_capabilities_includes_aliases(self):
        from uloo.config import settings as global_settings
        # The global settings should have the alias from .env
        assert "langgenius/deepseek/deepseek" in global_settings.model_provider_aliases
        assert global_settings.model_provider_aliases["langgenius/deepseek/deepseek"] == "deepseek"
