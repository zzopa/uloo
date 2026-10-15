# AI生成
"""Agno calls Dify's existing tool runtime, without receiving provider secrets."""

import hashlib
import json
from contextvars import ContextVar

import httpx
from agno.tools.function import Function
from jsonschema import Draft202012Validator

from ..config import settings
from .models import RuntimeConfigurationError

# S1-6: Per-request caller identity forwarded from the Dify BFF.
# Set by the runs endpoint before building the agent; read by dify_tool.invoke().
_runtime_caller: ContextVar[dict | None] = ContextVar("uloo_runtime_caller", default=None)


def set_runtime_caller(user_id: str | None, app_id: str | None, workspace_id: str) -> None:
    """Set the caller identity for the current request scope (S1-6)."""
    if user_id and app_id:
        _runtime_caller.set({"app_id": app_id, "user_id": user_id, "workspace_id": workspace_id})


def clear_runtime_caller() -> None:
    """Clear the caller identity after the request completes."""
    _runtime_caller.set(None)


def dify_tool(item: dict, workspace_id: str) -> Function:
    config = item["config"]

    def _resolve_caller() -> dict:
        """Resolve caller: runtime context first, then .env fallback."""
        runtime = _runtime_caller.get()
        if runtime and all(runtime.get(k) for k in ("app_id", "user_id")):
            return runtime
        caller = settings.dify_workspace_callers.get(str(workspace_id), {})
        if not settings.dify_inner_api_key.get_secret_value() or not all(caller.get(k) for k in ("app_id", "user_id")):
            raise RuntimeConfigurationError("DIFY_TOOL_CONTEXT_MISSING", "Dify tool service context is not configured")
        return caller

    schema = config["tool_schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    async def invoke(**arguments) -> str:
        validator.validate(arguments)
        caller = _resolve_caller()
        payload = {
            "caller": {
                "tenant_id": str(workspace_id),
                "app_id": caller["app_id"],
                "user_id": caller["user_id"],
                "user_from": "account",
                "invoke_from": "debugger",
            },
            "tool": {
                "provider_type": config["provider_type"],
                "provider_id": config["provider_id"],
                "tool_name": config["tool_name"],
                "credential_id": config.get("credential_id"),
                "tool_parameters": arguments,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                response = await client.post(
                    settings.dify_api_url.rstrip("/") + "/inner/api/agent/tools/invoke",
                    headers={"X-Inner-Api-Key": settings.dify_inner_api_key.get_secret_value()},
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
                return json.dumps(
                    {"observation": result["observation"], "messages": result["messages"]}, ensure_ascii=False
                )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # Do not echo Dify error bodies, which may include provider request headers.
            raise RuntimeConfigurationError("DIFY_TOOL_INVOKE_FAILED", "Dify tool invocation failed") from exc

    return Function(
        name="dify_" + hashlib.sha256(item["key"].encode()).hexdigest()[:24],
        description=item["description"] or item["name"],
        parameters=schema,
        entrypoint=invoke,
        skip_entrypoint_processing=True,
    )
