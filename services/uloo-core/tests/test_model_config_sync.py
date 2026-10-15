# AI生成
"""Tests for S1-3: runtime model config sync and .env fallback.

S1-补1: Runtime config is now keyed by workspace_id to enforce isolation.
Tests verify that different workspaces cannot see or override each other's
provider configurations.
"""

import pytest
from pydantic import SecretStr

from uloo.config import Settings
from uloo.runtime.models import ModelRegistry, RuntimeConfigurationError

_TEST_WS = "10000000-0000-0000-0000-000000000001"
_OTHER_WS = "20000000-0000-0000-0000-000000000002"


def _settings_with_env(**overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        model_providers={"deepseek": "http://localhost:3000/v1"},
        model_provider_api_keys={"deepseek": "sk-env-key"},
        model_provider_aliases={"langgenius/deepseek/deepseek": "deepseek"},
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestRuntimeConfigSync:
    """ModelRegistry should use runtime-synced config and fall back to .env."""

    def test_env_only_works(self):
        """When no runtime config is synced, .env config still works."""
        # Clear all runtime config
        from uloo.api.models import get_runtime_config
        get_runtime_config().clear()

        registry = ModelRegistry(_settings_with_env())
        model = registry.resolve("deepseek:deepseek-chat")
        assert model.id == "deepseek-chat"
        assert model.api_key == "sk-env-key"

    def test_runtime_config_overrides_env(self):
        """Runtime-synced config should take priority over .env."""
        from uloo.api.models import get_runtime_config, get_runtime_aliases

        get_runtime_config().clear()
        get_runtime_config(_TEST_WS)["deepseek"] = (
            "http://runtime:4000/v1",
            SecretStr("sk-runtime-key"),
            ["deepseek-chat", "deepseek-coder"],
        )

        registry = ModelRegistry(_settings_with_env(), workspace_id=_TEST_WS)
        model = registry.resolve("deepseek:deepseek-chat")
        assert model.id == "deepseek-chat"
        assert model.api_key == "sk-runtime-key"
        assert model.base_url == "http://runtime:4000/v1"

        # Cleanup
        get_runtime_config().clear()

    def test_runtime_config_with_dify_alias(self):
        """Runtime aliases should resolve Dify provider IDs."""
        from uloo.api.models import get_runtime_config, get_runtime_aliases

        get_runtime_config().clear()
        get_runtime_aliases().clear()
        get_runtime_config(_TEST_WS)["deepseek"] = (
            "http://runtime:4000/v1",
            SecretStr("sk-runtime-key"),
            ["deepseek-chat"],
        )
        get_runtime_aliases(_TEST_WS)["langgenius/deepseek/deepseek"] = "deepseek"

        registry = ModelRegistry(_settings_with_env(), workspace_id=_TEST_WS)
        model = registry.resolve("langgenius/deepseek/deepseek:deepseek-chat")
        assert model.id == "deepseek-chat"
        assert model.api_key == "sk-runtime-key"

        # Cleanup
        get_runtime_config().clear()
        get_runtime_aliases().clear()

    def test_runtime_config_new_provider_not_in_env(self):
        """A provider only in runtime config should be resolvable."""
        from uloo.api.models import get_runtime_config

        get_runtime_config().clear()
        get_runtime_config(_TEST_WS)["openai"] = (
            "https://api.openai.com/v1",
            SecretStr("sk-openai-key"),
            ["gpt-4o"],
        )

        registry = ModelRegistry(_settings_with_env(), workspace_id=_TEST_WS)
        model = registry.resolve("openai:gpt-4o")
        assert model.id == "gpt-4o"
        assert model.api_key == "sk-openai-key"

        # Cleanup
        get_runtime_config().clear()

    def test_no_config_anywhere_raises(self):
        """When neither runtime nor .env has the provider, raise."""
        from uloo.api.models import get_runtime_config

        get_runtime_config().clear()

        registry = ModelRegistry(_settings_with_env(), workspace_id=_TEST_WS)
        with pytest.raises(RuntimeConfigurationError) as exc_info:
            registry.resolve("unknown-provider:some-model")
        assert exc_info.value.code == "MODEL_CONFIG_MISSING"

    def test_configured_refs_includes_runtime(self):
        """configured_refs should include runtime-synced providers."""
        from uloo.api.models import get_runtime_config

        get_runtime_config().clear()
        get_runtime_config(_TEST_WS)["runtime-provider"] = (
            "",
            SecretStr("sk-key"),
            [],
        )

        registry = ModelRegistry(_settings_with_env(), workspace_id=_TEST_WS)
        refs = registry.configured_refs()
        assert "deepseek" in refs  # from .env
        assert "runtime-provider" in refs  # from runtime

        # Cleanup
        get_runtime_config().clear()


class TestWorkspaceIsolation:
    """S1-补1: Runtime config must be isolated by workspace_id."""

    def test_different_workspaces_do_not_share_config(self):
        """Provider config in one workspace must not be visible in another."""
        from uloo.api.models import get_runtime_config

        get_runtime_config().clear()
        get_runtime_config(_TEST_WS)["shared-provider"] = (
            "http://ws1:4000/v1",
            SecretStr("sk-ws1-key"),
            ["model-a"],
        )
        get_runtime_config(_OTHER_WS)["shared-provider"] = (
            "http://ws2:4000/v1",
            SecretStr("sk-ws2-key"),
            ["model-a"],
        )

        ws1_registry = ModelRegistry(_settings_with_env(), workspace_id=_TEST_WS)
        ws2_registry = ModelRegistry(_settings_with_env(), workspace_id=_OTHER_WS)

        ws1_model = ws1_registry.resolve("shared-provider:model-a")
        ws2_model = ws2_registry.resolve("shared-provider:model-a")

        assert ws1_model.api_key == "sk-ws1-key"
        assert ws1_model.base_url == "http://ws1:4000/v1"
        assert ws2_model.api_key == "sk-ws2-key"
        assert ws2_model.base_url == "http://ws2:4000/v1"

        # Cleanup
        get_runtime_config().clear()

    def test_workspace_without_runtime_config_falls_back_to_env(self):
        """A workspace with no runtime config should fall back to .env."""
        from uloo.api.models import get_runtime_config

        get_runtime_config().clear()
        # Only set config for _TEST_WS, not _OTHER_WS
        get_runtime_config(_TEST_WS)["deepseek"] = (
            "http://runtime:4000/v1",
            SecretStr("sk-runtime-key"),
            ["deepseek-chat"],
        )

        # _OTHER_WS should fall back to .env
        other_registry = ModelRegistry(_settings_with_env(), workspace_id=_OTHER_WS)
        model = other_registry.resolve("deepseek:deepseek-chat")
        assert model.api_key == "sk-env-key"  # .env key, not runtime key

        # Cleanup
        get_runtime_config().clear()

    def test_same_provider_name_different_workspaces_no_overwrite(self):
        """Two workspaces configuring the same provider name must not overwrite each other."""
        from uloo.api.models import get_runtime_config

        get_runtime_config().clear()
        get_runtime_config(_TEST_WS)["deepseek"] = (
            "http://ws1:4000/v1",
            SecretStr("sk-ws1"),
            ["deepseek-chat"],
        )
        get_runtime_config(_OTHER_WS)["deepseek"] = (
            "http://ws2:4000/v1",
            SecretStr("sk-ws2"),
            ["deepseek-chat"],
        )

        # Re-resolve ws1 after ws2 was configured – ws1 must still have its own key
        ws1_registry = ModelRegistry(_settings_with_env(), workspace_id=_TEST_WS)
        model = ws1_registry.resolve("deepseek:deepseek-chat")
        assert model.api_key == "sk-ws1"
        assert model.base_url == "http://ws1:4000/v1"

        # Cleanup
        get_runtime_config().clear()


class TestModelConfigSyncEndpoint:
    """Test the /api/v1/models/config endpoint via TestClient."""

    @pytest.fixture(autouse=True)
    def _clear_runtime(self):
        from uloo.api.models import get_runtime_config, get_runtime_aliases

        get_runtime_config().clear()
        get_runtime_aliases().clear()
        yield
        get_runtime_config().clear()
        get_runtime_aliases().clear()

    def test_sync_endpoint_accepts_config(self):
        from fastapi.testclient import TestClient
        from uloo.main import app
        from uloo.config import settings

        client = TestClient(app)
        resp = client.put(
            "/api/v1/models/config",
            json={
                "providers": [
                    {
                        "provider": "test-provider",
                        "base_url": "http://test:4000/v1",
                        "api_key": "sk-test",
                        "model_ids": ["model-a", "model-b"],
                    }
                ],
                "aliases": {"langgenius/test/test": "test-provider"},
            },
            headers={
                "Authorization": f"Bearer {settings.api_token}",
                "X-ULOO-Workspace": _TEST_WS,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "test-provider" in data["synced_providers"]
        assert data["total_models"] == 2

    def test_synced_config_resolves_model(self):
        from uloo.api.models import get_runtime_config

        get_runtime_config(_TEST_WS)["synced-provider"] = (
            "http://synced:4000/v1",
            SecretStr("sk-synced"),
            ["synced-model"],
        )

        registry = ModelRegistry(_settings_with_env(), workspace_id=_TEST_WS)
        model = registry.resolve("synced-provider:synced-model")
        assert model.api_key == "sk-synced"

    def test_sync_endpoint_isolates_by_workspace(self):
        """Config synced for one workspace must not be visible to another."""
        from fastapi.testclient import TestClient
        from uloo.main import app
        from uloo.config import settings

        client = TestClient(app)
        # Sync config for _TEST_WS
        client.put(
            "/api/v1/models/config",
            json={
                "providers": [
                    {
                        "provider": "isolated-provider",
                        "base_url": "http://ws1:4000/v1",
                        "api_key": "sk-ws1",
                        "model_ids": ["model-a"],
                    }
                ],
            },
            headers={
                "Authorization": f"Bearer {settings.api_token}",
                "X-ULOO-Workspace": _TEST_WS,
            },
        )
        # _OTHER_WS should not see isolated-provider
        other_registry = ModelRegistry(_settings_with_env(), workspace_id=_OTHER_WS)
        with pytest.raises(RuntimeConfigurationError) as exc_info:
            other_registry.resolve("isolated-provider:model-a")
        assert exc_info.value.code == "MODEL_CONFIG_MISSING"

        # _TEST_WS should see it
        ws1_registry = ModelRegistry(_settings_with_env(), workspace_id=_TEST_WS)
        model = ws1_registry.resolve("isolated-provider:model-a")
        assert model.api_key == "sk-ws1"
