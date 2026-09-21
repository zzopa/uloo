"""Service authentication and Dify workspace request context."""

import secrets
import uuid
from dataclasses import dataclass

from fastapi import Header, status

from .config import settings
from .errors import ApiError


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Trusted scope injected by the authenticated Dify BFF."""

    workspace_id: uuid.UUID


async def require_service_context(
    authorization: str | None = Header(None),
    x_uloo_workspace: str | None = Header(None),
) -> RequestContext:
    """Authenticate the caller and parse its immutable workspace scope."""
    if not settings.api_token:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_AUTH_NOT_CONFIGURED",
            message="ULOO Core service authentication is not configured",
        )

    scheme, _, credential = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not credential or not secrets.compare_digest(
        credential, settings.api_token
    ):
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message="A valid service credential is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not x_uloo_workspace:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="WORKSPACE_REQUIRED",
            message="X-ULOO-Workspace is required",
        )
    try:
        workspace_id = uuid.UUID(x_uloo_workspace)
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_WORKSPACE",
            message="X-ULOO-Workspace must be a UUID",
        ) from exc

    return RequestContext(workspace_id=workspace_id)
