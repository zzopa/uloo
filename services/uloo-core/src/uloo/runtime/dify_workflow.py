# AI生成
"""Dify workflow execution tool: reuses Dify's workflow pipeline (S1-8).

When an agent references a Dify workflow app via ``workflow_refs``, this module
creates an Agno Function tool that calls Dify's inner workflow execution API.
The workflow logic, node configuration, and model credentials stay in Dify.
"""

import hashlib
import json

import httpx
from agno.tools.function import Function

from ..config import settings
from .dify_tools import _runtime_caller
from .models import RuntimeConfigurationError


def dify_workflow_tool(app_id: str, workflow_name: str, workspace_id: str) -> Function:
    """Create an execution tool for one Dify workflow app.

    The tool exposes a single ``run`` function that accepts an ``inputs``
    JSON object parameter and returns the workflow execution results.
    """

    def _resolve_caller() -> dict:
        runtime = _runtime_caller.get()
        if runtime and all(runtime.get(k) for k in ("app_id", "user_id")):
            return runtime
        caller = settings.dify_workspace_callers.get(str(workspace_id), {})
        if not settings.dify_inner_api_key.get_secret_value() or not all(caller.get(k) for k in ("app_id", "user_id")):
            raise RuntimeConfigurationError("DIFY_TOOL_CONTEXT_MISSING", "Dify tool service context is not configured")
        return caller

    schema = {
        "type": "object",
        "properties": {
            "inputs": {
                "type": "object",
                "description": f"Input variables for the '{workflow_name}' workflow (key-value pairs)",
            },
        },
        "required": ["inputs"],
        "additionalProperties": False,
    }

    async def run(**arguments) -> str:
        caller = _resolve_caller()
        payload = {
            "app_id": app_id,
            "inputs": arguments.get("inputs", {}),
            "caller": {
                "tenant_id": str(workspace_id),
                "app_id": caller["app_id"],
                "user_id": caller["user_id"],
                "user_from": "account",
                "invoke_from": "debugger",
            },
        }
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                response = await client.post(
                    settings.dify_api_url.rstrip("/") + "/inner/api/workflows/run",
                    headers={"X-Inner-Api-Key": settings.dify_inner_api_key.get_secret_value()},
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
                outputs = result.get("data", {}).get("outputs", {})
                return json.dumps(
                    {"observation": json.dumps(outputs, ensure_ascii=False), "messages": []},
                    ensure_ascii=False,
                )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise RuntimeConfigurationError(
                "DIFY_WORKFLOW_EXECUTION_FAILED", f"Dify workflow '{workflow_name}' execution failed"
            ) from exc

    tool_key = f"dify-workflow-{app_id}"
    return Function(
        name="dify_workflow_" + hashlib.sha256(tool_key.encode()).hexdigest()[:24],
        description=f"Execute the '{workflow_name}' workflow (Dify app {app_id})",
        parameters=schema,
        entrypoint=run,
        skip_entrypoint_processing=True,
    )
