"""Stable API errors and exception handlers."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .logging import get_logger
from .schemas.common import ErrorResponse

logger = get_logger(__name__)


class ApiError(HTTPException):
    """HTTP exception carrying a stable machine-readable error code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message, "details": details},
            headers=headers,
        )


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status_code: {"model": ErrorResponse, "description": HTTPStatus(status_code).phrase}
    for status_code in (400, 401, 403, 404, 409, 422, 500, 501, 502, 503, 504)
}


def _correlation_ids(request: Request) -> tuple[str, str]:
    request_id = getattr(request.state, "request_id", "unknown")
    trace_id = getattr(request.state, "trace_id", request_id)
    return request_id, trace_id


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id, trace_id = _correlation_ids(request)
    response_headers = dict(headers or {})
    response_headers.update({"X-Request-ID": request_id, "X-Trace-ID": trace_id})
    body = ErrorResponse(
        code=code,
        message=message,
        request_id=request_id,
        trace_id=trace_id,
        details=details,
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body, exclude_none=True),
        headers=response_headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register one public error envelope for framework and application errors."""

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code") or f"HTTP_{exc.status_code}")
            message = str(exc.detail.get("message") or HTTPStatus(exc.status_code).phrase)
            details = exc.detail.get("details")
        else:
            code = f"HTTP_{exc.status_code}"
            message = str(exc.detail)
            details = None
        return _error_response(
            request,
            status_code=exc.status_code,
            code=code,
            message=message,
            details=details,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(request: Request, exc: RequestValidationError) -> JSONResponse:
        details: list[dict[str, Any]] = []
        for error in exc.errors():
            safe_error = dict(error)
            if context := safe_error.get("ctx"):
                safe_error["ctx"] = {key: str(value) for key, value in context.items()}
            details.append(safe_error)
        return _error_response(
            request,
            status_code=422,
            code="VALIDATION_ERROR",
            message="Request validation failed",
            details=details,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        request_id, trace_id = _correlation_ids(request)
        logger.exception(
            "unhandled_request_error",
            request_id=request_id,
            trace_id=trace_id,
            error_type=type(exc).__name__,
        )
        return _error_response(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="An unexpected error occurred",
        )
