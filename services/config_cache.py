"""Short-TTL Redis cache for admin configuration reads."""

from __future__ import annotations

import logging
from typing import Any, Optional

import orjson
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class ConfigCache:
    def __init__(self, redis: Redis, *, prefix: str = "cfg:", ttl_seconds: int = 30) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _key(self, name: str) -> str:
        return f"{self._prefix}{name}"

    async def get_json(self, name: str) -> Optional[Any]:
        try:
            raw = await self._redis.get(self._key(name))
        except Exception:
            logger.exception("config cache read failed")
            return None
        if not raw:
            return None
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            return orjson.loads(text)
        except Exception:
            return None

    async def set_json(self, name: str, value: Any) -> None:
        try:
            await self._redis.set(self._key(name), orjson.dumps(value).decode("utf-8"), ex=self._ttl)
        except Exception:
            logger.exception("config cache write failed")

    async def invalidate(self, name: str) -> None:
        try:
            await self._redis.delete(self._key(name))
        except Exception:
            logger.exception("config cache invalidate failed")
