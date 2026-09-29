"""Server-side web admin sessions. The cookie holds the raw token; the database holds a digest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

from webadmin.security import token_digest


class SessionRepository:
    def __init__(self, pool: asyncpg.Pool, *, secret: str) -> None:
        self._pool = pool
        self._secret = secret

    def digest(self, token: str) -> str:
        return token_digest(self._secret, token)

    async def create(
        self,
        *,
        token: str,
        admin_id: int,
        role: str,
        csrf_token: str,
        ttl_hours: int,
        ip: str,
        user_agent: str,
    ) -> None:
        expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO web_sessions (
                    token_hash, admin_id, role, csrf_token, expires_at, ip, user_agent
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7);
                """,
                self.digest(token),
                admin_id,
                role,
                csrf_token,
                expires,
                ip[:80],
                user_agent[:300],
            )

    async def get_valid(self, token: str) -> Optional[dict]:
        if not token:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, admin_id, role, csrf_token, expires_at
                FROM web_sessions
                WHERE token_hash = $1
                  AND revoked_at IS NULL
                  AND expires_at > now();
                """,
                self.digest(token),
            )
        return dict(row) if row else None

    async def revoke(self, token: str) -> None:
        if not token:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE web_sessions
                SET revoked_at = now()
                WHERE token_hash = $1 AND revoked_at IS NULL;
                """,
                self.digest(token),
            )
