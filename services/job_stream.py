"""Redis Stream helper for reliable jobs.

Existing broadcast, scheduler, retention, and onboarding workers keep their
current queues. New or retried web jobs can use this stream with ack, retry,
and a dead-letter stream.
"""

from __future__ import annotations

import time
from typing import Any

import orjson
from redis.asyncio import Redis


class JobStream:
    def __init__(self, redis: Redis, stream: str, *, group: str = "workers", dead_letter: str | None = None) -> None:
        self._redis = redis
        self.stream = stream
        self.group = group
        self.dead_letter = dead_letter or f"{stream}:dead"

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, job_id: str, payload: dict[str, Any]) -> str:
        body = orjson.dumps({"job_id": job_id, "payload": payload, "retry_count": 0}).decode("utf-8")
        return await self._redis.xadd(self.stream, {"body": body})

    async def read(self, consumer: str, *, count: int = 10, block_ms: int = 2000) -> list[tuple[str, dict[str, Any]]]:
        rows = await self._redis.xreadgroup(
            self.group,
            consumer,
            {self.stream: ">"},
            count=count,
            block=block_ms,
        )
        out: list[tuple[str, dict[str, Any]]] = []
        for _stream, messages in rows or []:
            for msg_id, fields in messages:
                raw = fields.get("body") if isinstance(fields, dict) else None
                if raw is None:
                    continue
                text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                out.append((str(msg_id), orjson.loads(text)))
        return out

    async def ack(self, message_id: str) -> None:
        await self._redis.xack(self.stream, self.group, message_id)

    async def fail(
        self,
        message_id: str,
        body: dict[str, Any],
        error: str,
        *,
        max_retries: int = 5,
    ) -> str:
        """Ack the current entry and either requeue with backoff or dead-letter it."""
        await self.ack(message_id)
        retry = int(body.get("retry_count") or 0) + 1
        record = dict(body)
        record["retry_count"] = retry
        record["last_error"] = error[:500]
        record["next_retry_at"] = time.time() + min(2**retry, 300)
        encoded = orjson.dumps(record).decode("utf-8")
        if retry > max_retries:
            record["status"] = "dead"
            await self._redis.xadd(self.dead_letter, {"body": orjson.dumps(record).decode("utf-8")})
            return "dead"
        await self._redis.xadd(self.stream, {"body": encoded})
        return "retried"
