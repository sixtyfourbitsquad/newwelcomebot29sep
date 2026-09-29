"""Admin roles and authorization helpers."""

from __future__ import annotations

from typing import Optional

import asyncpg

from models.domain import AdminRole


class AdminRepository:
    """Persistence for `admins` table."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def is_admin(self, user_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 FROM admins WHERE admin_id = $1;", user_id)
        return row is not None

    async def get_role(self, user_id: int) -> Optional[AdminRole]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT role FROM admins WHERE admin_id = $1;", user_id)
        if row is None:
            return None
        return AdminRole(row["role"])

    async def add_admin(self, admin_id: int, role: AdminRole, added_by: Optional[int]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO admins (admin_id, role, added_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (admin_id) DO UPDATE SET role = EXCLUDED.role;
                """,
                admin_id,
                role.value,
                added_by,
            )

    async def remove_admin(self, admin_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM admins WHERE admin_id = $1;", admin_id)

    async def list_admins(self) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT admin_id, role, added_at FROM admins ORDER BY admin_id;")
        return [dict(r) for r in rows]

    async def list_panel_admins(self) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT admin_id, role, added_at, added_by, is_active, updated_at, updated_by, last_login_at
                FROM admins
                ORDER BY admin_id;
                """
            )
        return [dict(r) for r in rows]

    async def count_owners(self) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*)::bigint AS c FROM admins WHERE role = 'owner';"
            )
        return int(row["c"]) if row else 0

    async def get_admin(self, admin_id: int) -> Optional[dict]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM admins WHERE admin_id = $1;", admin_id)
        return dict(row) if row else None

    async def is_active_admin(self, admin_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM admins
                WHERE admin_id = $1 AND COALESCE(is_active, TRUE) = TRUE;
                """,
                admin_id,
            )
        return row is not None

    async def list_active_ids(self) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT admin_id FROM admins
                WHERE COALESCE(is_active, TRUE) = TRUE
                ORDER BY admin_id;
                """
            )
        return [int(r["admin_id"]) for r in rows]

    async def set_active(self, admin_id: int, active: bool, updated_by: Optional[int]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE admins
                SET is_active = $2, updated_by = $3, updated_at = now()
                WHERE admin_id = $1;
                """,
                admin_id,
                active,
                updated_by,
            )

    async def set_role(self, admin_id: int, role: AdminRole, updated_by: Optional[int]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE admins
                SET role = $2, updated_by = $3, updated_at = now()
                WHERE admin_id = $1;
                """,
                admin_id,
                role.value,
                updated_by,
            )

    async def touch_login(self, admin_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE admins SET last_login_at = now() WHERE admin_id = $1;",
                admin_id,
            )
