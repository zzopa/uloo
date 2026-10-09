"""Shared test fixtures.

Tests run against a real PostgreSQL instance.  The implementation plan forbids
FakeModel and silent fallbacks, and the API is genuinely PostgreSQL-specific
(JSONB and UUID columns), so there is no SQLite shortcut here.

Isolation strategy
------------------
The schema is asserted to be present (``alembic upgrade head`` is the source of
truth, not ``metadata.create_all``).  Each test then opens one outer
transaction, binds the session to it with
``join_transaction_mode="create_savepoint"``, and rolls the outer transaction
back during teardown.  Because ``get_db`` commits inside the request handler,
that commit becomes a SAVEPOINT release rather than a real one, so tests can
run repeatedly against the same database without leaking rows.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from uloo.config import settings
from uloo.db import get_db
from uloo.main import app
from uloo.models import AgentDefinition, Run, RunEvent, TeamDefinition, TeamMember  # noqa: F401

REQUIRED_TABLES = ("agent_definitions", "team_definitions", "team_members", "runs", "run_events")
TEST_API_TOKEN = "uloo-test-service-token"
TEST_WORKSPACE_ID = "10000000-0000-0000-0000-000000000001"
settings.api_token = TEST_API_TOKEN
settings.model_providers = {"openai-compatible": "https://models.example.test/v1"}
settings.model_provider_api_keys = {"openai-compatible": SecretStr("server-secret")}


@pytest.fixture(scope="session")
async def engine():
    """Session-scoped engine; verifies the migrated schema once per run."""
    eng = create_async_engine(settings.database_url, poolclass=NullPool)

    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
            tables = set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
            columns = await conn.run_sync(
                lambda sync_conn: {
                    table: {column["name"] for column in inspect(sync_conn).get_columns(table)}
                    for table in REQUIRED_TABLES
                    if table in tables
                }
            )
    except Exception as exc:  # noqa: BLE001 - turn infrastructure failures into one actionable test error
        await eng.dispose()
        pytest.fail(
            f"cannot reach the test database at {settings.db_host}:{settings.db_port}/{settings.db_database} ({exc}). "
            "Start the PostgreSQL/pgvector dependency, then run: alembic upgrade head"
        )

    missing = [t for t in REQUIRED_TABLES if t not in tables]
    if missing:
        await eng.dispose()
        pytest.fail(
            f"missing tables {missing} in database '{settings.db_database}'. "
            "Migrations are the source of truth for the schema -- run: alembic upgrade head"
        )
    missing_workspace = [table for table in REQUIRED_TABLES if "workspace_id" not in columns[table]]
    if missing_workspace:
        await eng.dispose()
        pytest.fail(f"workspace migration missing on tables {missing_workspace}; run: alembic upgrade head")

    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """A session whose writes are always rolled back at the end of the test."""
    connection = await engine.connect()
    transaction = await connection.begin()
    session = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )()

    try:
        yield session
    finally:
        await session.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client bound to the isolated session."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={
                "Authorization": f"Bearer {TEST_API_TOKEN}",
                "X-ULOO-Workspace": TEST_WORKSPACE_ID,
            },
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
