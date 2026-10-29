# AI生成
"""Embedding client for semantic vector search via OpenAI / SiliconFlow compatible endpoints."""

from __future__ import annotations

import httpx

from ..config import settings
from ..logging import get_logger

logger = get_logger(__name__)


def is_embedding_configured() -> bool:
    """Return whether the external embedding API is configured with base URL and key."""
    api_key = (
        settings.embedding_api_key.get_secret_value()
        if hasattr(settings.embedding_api_key, "get_secret_value")
        else str(settings.embedding_api_key or "")
    )
    base_url = (settings.embedding_base_url or "").strip()
    return bool(api_key.strip() and base_url)


async def get_text_embedding_async(text: str) -> list[float] | None:
    """Generate normalized vector embedding for input text using configured embedding API."""
    if not is_embedding_configured():
        return None

    clean_text = text.strip()
    if not clean_text:
        return None

    api_key = (
        settings.embedding_api_key.get_secret_value()
        if hasattr(settings.embedding_api_key, "get_secret_value")
        else str(settings.embedding_api_key)
    )
    base_url = settings.embedding_base_url.strip().rstrip("/")
    if base_url.endswith("/embeddings"):
        endpoint = base_url
    elif base_url.endswith("/v1"):
        endpoint = f"{base_url}/embeddings"
    else:
        endpoint = f"{base_url}/v1/embeddings"
    model_name = settings.embedding_model or "BAAI/bge-m3"

    payload = {
        "model": model_name,
        "input": clean_text,
    }
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.embedding_timeout_seconds) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "embedding_api_error_response",
                    status_code=resp.status_code,
                    body=resp.text[:200],
                    model=model_name,
                )
                return None
            data = resp.json()
            embedding = data.get("data", [{}])[0].get("embedding")
            if embedding and isinstance(embedding, list):
                return [float(x) for x in embedding]
            return None
    except Exception as exc:
        logger.warning("embedding_request_failed", model=model_name, error=str(exc))
        return None


def get_text_embedding(text: str) -> list[float] | None:
    """Synchronous wrapper for generating text embedding safely across thread/async boundaries."""
    import asyncio
    import concurrent.futures

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(get_text_embedding_async(text))).result()
    else:
        return asyncio.run(get_text_embedding_async(text))
