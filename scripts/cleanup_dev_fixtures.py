"""Remove development-database residue left by earlier non-isolated test runs.

TODO.md ULOO-005 asks for the leftover `test-*` / `smoke-*` rows in the
development database to be cleaned up *and the operation recorded*. This script
is that record: it is safe by default and only touches rows whose `key` matches
the documented prefix pattern.

    python scripts/cleanup_dev_fixtures.py            # dry run, prints a plan
    python scripts/cleanup_dev_fixtures.py --apply    # backs up, then deletes

A backup of every row that will be removed is always written to
`tmp/dev-db-cleanup-<timestamp>.json` before anything is deleted, so the
operation is reversible. Rows that merely look like fixtures but do not match
the documented prefix (for example `content-team` or `writer`) are reported but
left alone - deciding their fate needs a human, not a regex.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "uloo-core" / "src"))

from uloo.config import settings

# The pattern named in TODO.md ULOO-005. Keep it explicit rather than clever.
RESIDUE_PREFIXES = ("test-", "smoke-")

AGENT_TABLE = "agent_definitions"
TEAM_TABLE = "team_definitions"


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_username,
        password=settings.db_password,
        database=settings.db_database,
        # Match Core's connection so the script targets the same schema.
        server_settings={"search_path": settings.db_schema},
    )


async def fetch_rows(connection: asyncpg.Connection, table: str) -> list[dict]:
    """Return every row of a table as JSON-safe dictionaries."""
    records = await connection.fetch(f"SELECT * FROM {table}")
    rows: list[dict] = []
    for record in records:
        row: dict = {}
        for key, value in dict(record).items():
            if isinstance(value, datetime):
                row[key] = value.isoformat()
            elif hasattr(value, "hex"):  # uuid.UUID
                row[key] = str(value)
            else:
                row[key] = value
        rows.append(row)
    return rows


def split_residue(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    residue = [row for row in rows if str(row.get("key", "")).startswith(RESIDUE_PREFIXES)]
    others = [row for row in rows if row not in residue]
    return residue, others


async def count_residue(connection: asyncpg.Connection, table: str) -> int:
    patterns = [f"{prefix}%" for prefix in RESIDUE_PREFIXES]
    return await connection.fetchval(
        f"SELECT count(*) FROM {table} WHERE key LIKE ANY($1::text[])", patterns
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Clean documented dev-database residue")
    parser.add_argument("--apply", action="store_true", help="perform the deletion; default is a dry run")
    args = parser.parse_args()

    connection = await connect()
    try:
        agents = await fetch_rows(connection, AGENT_TABLE)
        teams = await fetch_rows(connection, TEAM_TABLE)

        agent_residue, agent_others = split_residue(agents)
        team_residue, team_others = split_residue(teams)

        print(f"database      : {settings.db_database}@{settings.db_host}:{settings.db_port}")
        print(f"pattern       : key starts with {RESIDUE_PREFIXES}")
        print(f"{AGENT_TABLE}: {len(agent_residue)} residue / {len(agent_others)} other")
        print(f"{TEAM_TABLE} : {len(team_residue)} residue / {len(team_others)} other")

        for row in agent_residue:
            print(f"  [remove] {AGENT_TABLE} key={row['key']!r} name={row['name']!r}")
        for row in team_residue:
            print(f"  [remove] {TEAM_TABLE} key={row['key']!r} name={row['name']!r}")

        if agent_others or team_others:
            print("\nNot matching the pattern, left untouched - confirm these by hand:")
            for row in agent_others:
                print(f"  [keep]   {AGENT_TABLE} key={row['key']!r} name={row['name']!r}")
            for row in team_others:
                print(f"  [keep]   {TEAM_TABLE} key={row['key']!r} name={row['name']!r}")

        if not agent_residue and not team_residue:
            print("\nNothing to clean.")
            return 0

        if not args.apply:
            print("\nDry run. Re-run with --apply to delete the rows listed above.")
            return 0

        # Snapshot member rows too, so the backup can restore the graph.
        team_ids = [row["id"] for row in team_residue]
        members: list[dict] = []
        if team_ids:
            member_records = await connection.fetch(
                "SELECT * FROM team_members WHERE team_id = ANY($1::uuid[])", team_ids
            )
            for record in member_records:
                row = {}
                for key, value in dict(record).items():
                    if isinstance(value, datetime):
                        row[key] = value.isoformat()
                    elif hasattr(value, "hex"):
                        row[key] = str(value)
                    else:
                        row[key] = value
                members.append(row)

        backup_dir = REPO_ROOT / "tmp"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"dev-db-cleanup-{stamp}.json"
        backup_path.write_text(
            json.dumps(
                {
                    "created_at": stamp,
                    "database": settings.db_database,
                    "pattern": list(RESIDUE_PREFIXES),
                    AGENT_TABLE: agent_residue,
                    TEAM_TABLE: team_residue,
                    "team_members": members,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nBackup written: {backup_path.relative_to(REPO_ROOT)}")

        async with connection.transaction():
            if team_ids:
                removed_members = await connection.execute(
                    "DELETE FROM team_members WHERE team_id = ANY($1::uuid[])", team_ids
                )
                print(f"deleted team_members: {removed_members}")
            removed_teams = await connection.execute(
                f"DELETE FROM {TEAM_TABLE} WHERE key LIKE ANY($1::text[])",
                [f"{prefix}%" for prefix in RESIDUE_PREFIXES],
            )
            print(f"deleted {TEAM_TABLE}: {removed_teams}")
            removed_agents = await connection.execute(
                f"DELETE FROM {AGENT_TABLE} WHERE key LIKE ANY($1::text[])",
                [f"{prefix}%" for prefix in RESIDUE_PREFIXES],
            )
            print(f"deleted {AGENT_TABLE}: {removed_agents}")

        remaining_agents = await count_residue(connection, AGENT_TABLE)
        remaining_teams = await count_residue(connection, TEAM_TABLE)
        print(f"\nremaining residue: {AGENT_TABLE}={remaining_agents} {TEAM_TABLE}={remaining_teams}")
    finally:
        await connection.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
