# AI生成
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from jsonschema import ValidationError
from pydantic import SecretStr

from uloo.config import settings
from uloo.runtime.dify_tools import dify_tool
from uloo.runtime.models import RuntimeConfigurationError
from uloo.runtime.resources import ResourceRegistry


def tool_item():
    return {
        "key": "dify-search",
        "name": "Search",
        "description": "Search Dify",
        "enabled": True,
        "config": {
            "tool_type": "dify",
            "provider_type": "mcp",
            "provider_id": "provider",
            "tool_name": "search",
            "tool_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


@pytest.fixture
def dify_context(monkeypatch):
    monkeypatch.setattr(settings, "dify_inner_api_key", SecretStr("test-service-secret"))
    monkeypatch.setattr(settings, "dify_workspace_callers", {"workspace": {"app_id": "app", "user_id": "account"}})


async def test_native_invoke_keeps_workspace_and_secret_server_side(dify_context):
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://dify"),
        json={
            "observation": "found",
            "messages": [{"type": "text", "message": "found"}],
        },
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response) as post:
        registry = ResourceRegistry([tool_item()], [], "workspace")
        fn = dify_tool(tool_item(), "workspace")
        result = json.loads(await fn.entrypoint(query="boards"))
    assert result["observation"] == "found"
    assert post.call_args.kwargs["json"]["caller"]["tenant_id"] == "workspace"
    assert post.call_args.kwargs["json"]["tool"]["tool_parameters"] == {"query": "boards"}
    assert "test-service-secret" not in json.dumps(registry.snapshot)


async def test_workspace_context_cannot_be_borrowed(dify_context):
    """Without a configured caller, invocation raises (not creation time, S1-6)."""
    from uloo.runtime.dify_tools import clear_runtime_caller

    clear_runtime_caller()
    fn = dify_tool(tool_item(), "other-workspace")
    with pytest.raises(RuntimeConfigurationError, match="not configured"):
        await fn.entrypoint(query="test")


async def test_invalid_model_arguments_never_reach_dify(dify_context):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post, pytest.raises(ValidationError):
        await dify_tool(tool_item(), "workspace").entrypoint(query=42)
    post.assert_not_called()


async def test_upstream_error_does_not_echo_secrets(dify_context):
    response = httpx.Response(403, request=httpx.Request("POST", "http://dify"), text="secret-provider-token")
    with (
        patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response),
        pytest.raises(RuntimeConfigurationError) as error,
    ):
        await dify_tool(tool_item(), "workspace").entrypoint(query="boards")
    assert "secret-provider-token" not in str(error.value)
