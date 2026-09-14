"""ULOO Core FastAPI application."""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from .api import agents, capabilities, health, teams
from .config import settings
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
    )

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
