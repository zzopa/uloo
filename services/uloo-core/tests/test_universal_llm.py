"""Test universal OpenAI-compatible LLM configuration."""

import pytest
from pydantic import SecretStr
from uloo.config import Settings
from uloo.runtime.models import ModelRegistry, RuntimeConfigurationError


def test_universal_llm_settings_fallback():
    # Test fallback when an unconfigured provider (e.g. qwen, mistral, custom) is passed
    settings = Settings(
        _env_file=None,
        llm_base_url="http://custom-llm.local/v1",
        llm_api_key=SecretStr("sk-universal-secret"),
        llm_model="custom-gpt-model",
        model_providers={},
        model_provider_api_keys={},
        planner_model_ref="",
    )
    registry = ModelRegistry(settings)

    # 1. Unregistered provider automatically falls back to universal LLM
    model = registry.resolve("unregistered-provider:some-model")
    assert model.base_url == "http://custom-llm.local/v1"
    assert model.api_key == "sk-universal-secret"
    assert model.id == "custom-gpt-model"

    # 2. Default provider automatically falls back to universal LLM
    model2 = registry.resolve("default:custom-gpt-model")
    assert model2.base_url == "http://custom-llm.local/v1"
    assert model2.api_key == "sk-universal-secret"
    assert model2.id == "custom-gpt-model"

    # 3. Model ref without colon is strictly rejected with 422 RuntimeConfigurationError
    with pytest.raises(RuntimeConfigurationError):
        registry.parse("invalid-model-without-colon")


def test_universal_llm_planner_fallback():
    settings = Settings(
        _env_file=None,
        llm_base_url="http://custom-llm.local/v1",
        llm_api_key=SecretStr("sk-universal-secret"),
        llm_model="custom-planner-model",
        planner_model_ref="",
    )
    registry = ModelRegistry(settings)
    # When planner_model_ref is empty, fallback planner ref format is default:<llm_model>
    planner_ref = f"default:{settings.llm_model}"
    parsed = registry.parse(planner_ref)
    assert parsed.provider == "default"
    assert parsed.model_id == "custom-planner-model"
    model = registry.resolve(planner_ref)
    assert model.base_url == "http://custom-llm.local/v1"
    assert model.id == "custom-planner-model"
