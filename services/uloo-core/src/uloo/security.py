# AI生成
"""Service authentication and Dify workspace request context."""

import secrets
import uuid
from dataclasses import dataclass

from fastapi import Header, Query, status

from .config import settings
from .errors import ApiError


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Trusted scope injected by the authenticated Dify BFF."""

    workspace_id: uuid.UUID
    # S1-6: Caller identity forwarded from the Dify console session.
    # Used when Core invokes Dify inner APIs (e.g. tool invocation).
    user_id: str | None = None
    app_id: str | None = None


async def require_service_context(
    authorization: str | None = Header(None),
    x_uloo_workspace: str | None = Header(None),
    x_uloo_user_id: str | None = Header(None, alias="X-ULOO-User-Id"),
    x_uloo_app_id: str | None = Header(None, alias="X-ULOO-App-Id"),
    token_param: str | None = Query(None, alias="token"),
    workspace_param: str | None = Query(None, alias="workspace"),
) -> RequestContext:
    """Authenticate the caller and parse its immutable workspace scope.

    Supports Authorization header / X-ULOO-Workspace, with fallback to query
    parameters token / workspace for browser-native EventSource / streaming.
    """
    if not settings.api_token:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_AUTH_NOT_CONFIGURED",
            message="ULOO Core service authentication is not configured",
        )

    credential: str | None = None
    if authorization:
        scheme, _, cred = authorization.partition(" ")
        if scheme.lower() == "bearer" and cred:
            credential = cred
    elif token_param:
        credential = token_param

    if not credential or not secrets.compare_digest(credential, settings.api_token):
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message="A valid service credential is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    effective_workspace = x_uloo_workspace or workspace_param
    if not effective_workspace:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="WORKSPACE_REQUIRED",
            message="X-ULOO-Workspace is required",
        )
    try:
        workspace_id = uuid.UUID(effective_workspace)
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="INVALID_WORKSPACE",
            message="X-ULOO-Workspace must be a UUID",
        ) from exc

    return RequestContext(
        workspace_id=workspace_id,
        user_id=x_uloo_user_id,
        app_id=x_uloo_app_id,
    )
