"""Run the reproducible ULOO verification gate.

The Core suite and all Alembic operations use a disposable PostgreSQL schema.
The development schema is never migrated, truncated, or otherwise changed.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import psycopg2
from psycopg2 import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = REPO_ROOT / "services" / "uloo-core"
DIFY_ROOT = REPO_ROOT / "vendor" / "dify"


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    """Run one verification command and fail immediately on a non-zero exit."""
    print(f"\n[{cwd.relative_to(REPO_ROOT)}] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def database_connection_kwargs() -> dict[str, object]:
    """Build psycopg connection parameters from the same ULOO settings as Core."""
    return {
        "host": os.getenv("ULOO_DB_HOST", "localhost"),
        "port": int(os.getenv("ULOO_DB_PORT", "5432")),
        "user": os.getenv("ULOO_DB_USERNAME", "lightrag"),
        "password": os.getenv("ULOO_DB_PASSWORD", "lightrag123"),
        "dbname": os.getenv("ULOO_DB_DATABASE", "uloo"),
    }


def change_schema(schema: str, *, create: bool) -> None:
    """Create or drop the disposable schema with identifier-safe SQL composition."""
    statement = (
        sql.SQL("CREATE SCHEMA {}") if create else sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE")
    ).format(sql.Identifier(schema))
    with psycopg2.connect(**database_connection_kwargs()) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(statement)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ULOO Core and its Dify BFF integration")
    parser.add_argument("--skip-dify", action="store_true", help="skip Dify BFF and lock checks")
    parser.add_argument("--keep-schema", action="store_true", help="preserve the disposable schema for debugging")
    args = parser.parse_args()

    uv = shutil.which("uv")
    if not uv:
        parser.error("uv is required and was not found on PATH")

    schema = f"uloo_test_{secrets.token_hex(6)}"
    env = os.environ.copy()
    env["ULOO_DB_SCHEMA"] = schema
    env.setdefault("ULOO_API_TOKEN", "verification-only-token")

    change_schema(schema, create=True)
    print(f"Using disposable PostgreSQL schema: {schema}")
    try:
        run([uv, "sync", "--extra", "dev", "--locked"], cwd=CORE_ROOT, env=env)
        run([uv, "run", "ruff", "check", "src", "tests", "migrations"], cwd=CORE_ROOT, env=env)
        # The repository-level scripts are product code too, so they answer to
        # the same lint configuration instead of drifting unlinted.
        run(
            [uv, "run", "ruff", "check", "--config", "pyproject.toml", str(REPO_ROOT / "scripts")],
            cwd=CORE_ROOT,
            env=env,
        )
        run([uv, "run", "mypy", "src"], cwd=CORE_ROOT, env=env)

        run([uv, "run", "alembic", "upgrade", "head"], cwd=CORE_ROOT, env=env)
        run([uv, "run", "alembic", "downgrade", "base"], cwd=CORE_ROOT, env=env)
        run([uv, "run", "alembic", "upgrade", "head"], cwd=CORE_ROOT, env=env)
        run([uv, "run", "alembic", "check"], cwd=CORE_ROOT, env=env)
        run([uv, "run", "pytest", "--cov=uloo", "--cov-report=term-missing"], cwd=CORE_ROOT, env=env)
        run([uv, "run", "python", str(REPO_ROOT / "scripts" / "export_openapi.py"), "--check"], cwd=CORE_ROOT, env=env)

        if not args.skip_dify:
            if not DIFY_ROOT.is_dir():
                raise RuntimeError("vendor/dify is missing; cannot verify the Dify integration")
            run([uv, "lock", "--check"], cwd=DIFY_ROOT / "api", env=env)
            run(
                [
                    uv,
                    "run",
                    "--project",
                    ".",
                    "pytest",
                    "tests/unit_tests/controllers/console/test_uloo_proxy.py",
                    "-o",
                    "addopts=",
                    "-q",
                ],
                cwd=DIFY_ROOT / "api",
                env=env,
            )
    finally:
        if args.keep_schema:
            print(f"Preserved disposable schema: {schema}")
        else:
            change_schema(schema, create=False)

    print("\nULOO verification gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
