# AI生成
"""Dify knowledge retrieval tool: reuses Dify's retrieval pipeline (S1-7).

When an agent references a Dify dataset via ``knowledge_refs``, this module
creates an Agno Function tool that calls Dify's inner retrieval API to search
the dataset.  Credentials and indexing stay entirely in Dify.
"""

import hashlib
import json

import httpx
from agno.tools.function import Function

from ..config import settings
from .dify_tools import _runtime_caller
from .models import RuntimeConfigurationError


def dify_knowledge_tool(dataset_id: str, dataset_name: str, workspace_id: str) -> Function:
    """Create a retrieval tool for one Dify dataset.

    The tool exposes a single ``search`` function that accepts a ``query``
    parameter and returns relevant chunks from the Dify dataset.
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
            "query": {
                "type": "string",
                "description": f"Search query for the '{dataset_name}' knowledge base",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def search(**arguments) -> str:
        caller = _resolve_caller()
        payload = {
            "caller": {
                "tenant_id": str(workspace_id),
                "app_id": caller["app_id"],
                "user_id": caller["user_id"],
                "user_from": "account",
                "invoke_from": "debugger",
            },
            "retrieval_setting": {
                "query": arguments["query"],
                "top_k": 5,
                "score_threshold": 0.0,
            },
            "dataset_id": dataset_id,
        }
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                response = await client.post(
                    settings.dify_api_url.rstrip("/") + "/inner/api/datasets/retrieval",
                    headers={"X-Inner-Api-Key": settings.dify_inner_api_key.get_secret_value()},
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
                # Format the retrieved chunks for the LLM
                records = result.get("records", [])
                if not records:
                    return json.dumps({"observation": "No relevant documents found.", "messages": []}, ensure_ascii=False)
                chunks = []
                for record in records:
                    segment = record.get("segment", {})
                    chunks.append(
                        {
                            "content": segment.get("content", ""),
                            "title": segment.get("title", ""),
                            "score": record.get("score", 0),
                            "document_name": record.get("document", {}).get("name", ""),
                        }
                    )
                return json.dumps({"observation": json.dumps(chunks, ensure_ascii=False), "messages": []}, ensure_ascii=False)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise RuntimeConfigurationError("DIFY_KNOWLEDGE_RETRIEVAL_FAILED", "Dify knowledge retrieval failed") from exc

    tool_key = f"dify-knowledge-{dataset_id}"
    return Function(
        name="dify_knowledge_" + hashlib.sha256(tool_key.encode()).hexdigest()[:24],
        description=f"Search the '{dataset_name}' knowledge base (Dify dataset {dataset_id})",
        parameters=schema,
        entrypoint=search,
        skip_entrypoint_processing=True,
    )
