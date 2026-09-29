"""Admin audit log for login, logout, denials, and privileged changes."""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg


class AuditRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def write(
        self,
        *,
        admin_id: Optional[int],
        action: str,
        success: bool = True,
        ip: str = "",
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO admin_audit_logs (admin_id, action, success, ip, detail)
                VALUES ($1, $2, $3, $4, $5::jsonb);
                """,
                admin_id,
                action,
                success,
                (ip or "")[:80],
                json.dumps(detail or {}),
            )

    async def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, admin_id, action, success, ip, detail, created_at
                FROM admin_audit_logs
                ORDER BY id DESC
                LIMIT $1;
                """,
                limit,
            )
        return [dict(r) for r in rows]
