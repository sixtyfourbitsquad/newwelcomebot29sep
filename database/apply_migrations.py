"""Apply SQL files in database/migrations in filename order.

Usage (from the project root, with POSTGRES_DSN set):

    python -m database.apply_migrations
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg

from configs.settings import get_settings


async def apply() -> list[str]:
    settings = get_settings()
    migrations = sorted(Path(__file__).resolve().parent.joinpath("migrations").glob("*.sql"))
    conn = await asyncpg.connect(settings.postgres_dsn)
    applied: list[str] = []
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        for path in migrations:
            version = path.name
            already = await conn.fetchval(
                "SELECT 1 FROM schema_migrations WHERE version = $1;",
                version,
            )
            if already:
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1);",
                    version,
                )
            applied.append(version)
    finally:
        await conn.close()
    return applied


def main() -> None:
    done = asyncio.run(apply())
    if not done:
        print("No new migrations.")
        return
    print("Applied:")
    for name in done:
        print(f"  {name}")


if __name__ == "__main__":
    main()
