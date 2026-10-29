# AI生成
"""ULOO Core FastAPI application."""

import re
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api import agents, capabilities, files, health, memories, models, resources, runs, tasks, teams
from .config import settings
from .errors import ERROR_RESPONSES, register_exception_handlers
from .logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle."""
    configure_logging()
    logger = get_logger("uloo.core")
    logger.info("starting", app=settings.app_name, version="0.1.0")
    # S5-补2: Clean up any runs left in 'running' status from a previous
    # process crash or restart.
    try:
        cleaned = await runs.cleanup_leftover_runs()
        if cleaned:
            logger.info("startup_cleanup", leftover_runs_cleaned=cleaned)
    except Exception:
        logger.exception("startup_cleanup_failed")
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
    app.include_router(resources.router, prefix=api_prefix)
    app.include_router(teams.router, prefix=api_prefix)
    app.include_router(tasks.router, prefix=api_prefix)
    app.include_router(runs.router, prefix=api_prefix)
    app.include_router(models.router, prefix=api_prefix)
    app.include_router(memories.router, prefix=api_prefix)
    app.include_router(files.router, prefix=api_prefix)

    # Mount standalone lightweight UI
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/ui", include_in_schema=False)
        @app.get("/ui/{rest_of_path:path}", include_in_schema=False)
        async def serve_ui():
            return FileResponse(
                str(static_dir / "index.html"),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        @app.get("/", include_in_schema=False)
        async def root():
            return RedirectResponse(url="/ui")
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"service": settings.app_name, "version": "0.1.0", "docs": "/docs"}

    return app


app = create_app()
