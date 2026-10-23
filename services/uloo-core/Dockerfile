# AI生成
# ULOO Core Standalone Engine Dockerfile
FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8200

# Install build/system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml and install production dependencies
COPY pyproject.toml /app/
RUN pip install --upgrade pip && \
    pip install ".[base]" || pip install \
    "fastapi>=0.121.0,<1.0.0" \
    "uvicorn[standard]>=0.30.0,<1.0.0" \
    "pydantic>=2.10.0,<3.0.0" \
    "pydantic-settings>=2.6.0,<3.0.0" \
    "sqlalchemy[asyncio]>=2.0.36,<3.0.0" \
    "asyncpg>=0.30.0,<1.0.0" \
    "psycopg2-binary>=2.9.0,<3.0.0" \
    "alembic>=1.14.0,<2.0.0" \
    "structlog>=24.4.0,<25.0.0" \
    "httpx>=0.28.0,<1.0.0" \
    "python-dotenv>=1.0.0,<2.0.0" \
    "agno>=1.8.4,<1.9.0" \
    "jsonschema>=4.23,<5" \
    "openai>=1.109.1,<2" \
    "ddgs>=9,<10" \
    "mcp>=1.0,<2.0"

# Copy source code, migrations, and alembic configs
COPY src/ /app/src/
COPY alembic/ /app/alembic/
COPY alembic.ini /app/alembic.ini

# Install the package itself in editable/wheel mode
RUN pip install -e .

EXPOSE 8200

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8200/api/v1/health/live || exit 1

CMD ["uvicorn", "uloo.main:app", "--host", "0.0.0.0", "--port", "8200"]
