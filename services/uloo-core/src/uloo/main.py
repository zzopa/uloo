"""ULOO Core FastAPI application."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import re
import uuid

from fastapi import FastAPI, Request

from .api import agents, capabilities, health, teams
from .config import settings
from .errors import ERROR_RESPONSES, register_exception_handlers
from .logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle."""
    configure_logging()
    logger = get_logger("uloo.core")
    logger.info("starting", app=settings.app_name, version="0.1.0")
    yield
    logger.info("stopping", app=settings.app_name)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        responses=ERROR_RESPONSES,
    )

    register_exception_handlers(app)

    @app.middleware("http")
    async def correlation_ids(request: Request, call_next):
        """Attach bounded request and trace identifiers to every response."""
        valid_id = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
        incoming_request_id = request.headers.get("X-Request-ID", "")
        incoming_trace_id = request.headers.get("X-Trace-ID", "")
        request.state.request_id = (
            incoming_request_id if valid_id.fullmatch(incoming_request_id) else str(uuid.uuid4())
        )
        request.state.trace_id = (
            incoming_trace_id if valid_id.fullmatch(incoming_trace_id) else request.state.request_id
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response

    # Register routers under /api/v1 prefix
    api_prefix = settings.api_prefix
    app.include_router(health.router, prefix=api_prefix)
    app.include_router(capabilities.router, prefix=api_prefix)
    app.include_router(agents.router, prefix=api_prefix)
    app.include_router(teams.router, prefix=api_prefix)

    # Root redirect to docs
    @app.get("/")
    async def root():
        return {"service": settings.app_name, "version": "0.1.0", "docs": "/docs"}

    return app


app = create_app()
