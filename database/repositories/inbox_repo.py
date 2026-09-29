"""Shared inbox persistence. Telegram forwarding stays in LiveChatService."""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg


class InboxRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_incoming(
        self,
        *,
        telegram_user_id: int,
        original_message_id: Optional[int],
        message_type: str,
        content_text: Optional[str],
        file_id: Optional[str],
        payload: dict[str, Any],
    ) -> int:
        async with self._pool.acquire() as conn:
            if original_message_id is None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO inbox_messages (
                        telegram_user_id, original_message_id, message_type,
                        content_text, file_id, payload
                    )
                    VALUES ($1, NULL, $2, $3, $4, $5::jsonb)
                    RETURNING id;
                    """,
                    telegram_user_id,
                    message_type,
                    content_text,
                    file_id,
                    json.dumps(payload),
                )
                return int(row["id"])
            row = await conn.fetchrow(
                """
                INSERT INTO inbox_messages (
                    telegram_user_id, original_message_id, message_type,
                    content_text, file_id, payload
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (telegram_user_id, original_message_id) DO UPDATE SET
                    message_type = EXCLUDED.message_type,
                    content_text = EXCLUDED.content_text,
                    file_id = EXCLUDED.file_id,
                    payload = EXCLUDED.payload
                RETURNING id;
                """,
                telegram_user_id,
                original_message_id,
                message_type,
                content_text,
                file_id,
                json.dumps(payload),
            )
        return int(row["id"])

    async def record_forward(
        self,
        *,
        inbox_message_id: int,
        admin_telegram_id: int,
        forwarded_message_id: Optional[int],
        delivery_status: str,
        telegram_error: Optional[str] = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO inbox_forwards (
                    inbox_message_id, admin_telegram_id, forwarded_message_id,
                    delivery_status, telegram_error
                )
                VALUES ($1, $2, $3, $4, $5);
                """,
                inbox_message_id,
                admin_telegram_id,
                forwarded_message_id,
                delivery_status,
                telegram_error,
            )

    async def user_for_forward(self, admin_telegram_id: int, forwarded_message_id: int) -> Optional[int]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT m.telegram_user_id
                FROM inbox_forwards f
                JOIN inbox_messages m ON m.id = f.inbox_message_id
                WHERE f.admin_telegram_id = $1 AND f.forwarded_message_id = $2
                ORDER BY f.id DESC
                LIMIT 1;
                """,
                admin_telegram_id,
                forwarded_message_id,
            )
        if row is None:
            return None
        return int(row["telegram_user_id"])

    async def reply_exists(self, admin_telegram_id: int, source_message_id: int) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM inbox_replies
                WHERE admin_telegram_id = $1 AND source_message_id = $2
                LIMIT 1;
                """,
                admin_telegram_id,
                source_message_id,
            )
        return row is not None

    async def record_reply(
        self,
        *,
        inbox_message_id: Optional[int],
        admin_telegram_id: int,
        target_user_id: int,
        reply_message_id: Optional[int],
        source_message_id: Optional[int],
        message_type: str,
        content_text: Optional[str],
        payload: dict[str, Any],
        delivery_status: str,
        telegram_error: Optional[str] = None,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO inbox_replies (
                    inbox_message_id, admin_telegram_id, target_user_id,
                    reply_message_id, source_message_id, message_type,
                    content_text, payload, delivery_status, telegram_error
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
                RETURNING id;
                """,
                inbox_message_id,
                admin_telegram_id,
                target_user_id,
                reply_message_id,
                source_message_id,
                message_type,
                content_text,
                json.dumps(payload),
                delivery_status,
                telegram_error,
            )
        return int(row["id"])

    async def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.*, u.username, u.first_name,
                    (
                        SELECT r.delivery_status
                        FROM inbox_replies r
                        WHERE r.target_user_id = t.telegram_user_id
                        ORDER BY r.created_at DESC
                        LIMIT 1
                    ) AS last_reply_status
                FROM (
                    SELECT DISTINCT ON (m.telegram_user_id)
                        m.id, m.telegram_user_id, m.message_type, m.content_text,
                        m.file_id, m.received_at
                    FROM inbox_messages m
                    ORDER BY m.telegram_user_id, m.received_at DESC
                ) t
                LEFT JOIN users u ON u.user_id = t.telegram_user_id
                ORDER BY t.received_at DESC
                LIMIT $1;
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def list_thread(self, telegram_user_id: int, limit: int = 200) -> dict[str, list[dict[str, Any]]]:
        async with self._pool.acquire() as conn:
            messages = await conn.fetch(
                """
                SELECT * FROM inbox_messages
                WHERE telegram_user_id = $1
                ORDER BY received_at ASC
                LIMIT $2;
                """,
                telegram_user_id,
                limit,
            )
            replies = await conn.fetch(
                """
                SELECT * FROM inbox_replies
                WHERE target_user_id = $1
                ORDER BY created_at ASC
                LIMIT $2;
                """,
                telegram_user_id,
                limit,
            )
        return {
            "messages": [dict(r) for r in messages],
            "replies": [dict(r) for r in replies],
        }

    async def get_message(self, message_id: int) -> Optional[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM inbox_messages WHERE id = $1;", message_id)
        return dict(row) if row else None

    async def get_reply(self, reply_id: int) -> Optional[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM inbox_replies WHERE id = $1;", reply_id)
        return dict(row) if row else None

    async def mark_reply(self, reply_id: int, *, status: str, error: Optional[str], sent_message_id: Optional[int]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE inbox_replies
                SET delivery_status = $2,
                    telegram_error = $3,
                    reply_message_id = COALESCE($4, reply_message_id)
                WHERE id = $1;
                """,
                reply_id,
                status,
                error,
                sent_message_id,
            )

    async def failed_replies(self, limit: int = 30) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM inbox_replies
                WHERE delivery_status = 'failed'
                ORDER BY id DESC
                LIMIT $1;
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def counts(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM inbox_messages)::bigint AS messages,
                    (SELECT COUNT(*) FROM inbox_replies)::bigint AS replies,
                    (SELECT COUNT(*) FROM inbox_replies WHERE delivery_status = 'failed')::bigint AS failed_replies;
                """
            )
        return {k: int(row[k]) for k in row.keys()} if row else {}
