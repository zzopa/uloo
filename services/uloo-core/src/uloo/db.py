"""Database engine and session management."""

from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings
from .logging import get_logger

logger = get_logger(__name__)

engine = create_async_engine(
    settings.database_url,
    connect_args={"server_settings": {"search_path": settings.db_schema}},
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ULOO models."""



async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Commit or roll back before sending the response, so success guarantees durable writes.
transaction_session = Depends(get_db, scope="function")


async def check_db_connection() -> bool:
    """Check if the database is reachable. Returns True if healthy."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:  # noqa: BLE001 - health probes report unavailable for any driver failure
        logger.error("database_health_check_failed", error=str(e))
        return False
