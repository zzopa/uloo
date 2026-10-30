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

# Copy package metadata and source code
COPY pyproject.toml alembic.ini /app/
COPY src/ /app/src/
COPY migrations/ /app/migrations/

# Install production dependencies and package from pyproject.toml
RUN pip install --upgrade pip && pip install .

EXPOSE 8200

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8200/api/v1/health/live || exit 1

CMD ["uvicorn", "uloo.main:app", "--host", "0.0.0.0", "--port", "8200"]
