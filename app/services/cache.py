from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)


class CacheService:
    def __init__(self, redis_url: str | None = settings.redis_url) -> None:
        self._client = (
            Redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            if redis_url
            else None
        )

    async def get_json(self, key: str) -> Any | None:
        if self._client is None:
            return None
        try:
            value = await self._client.get(key)
            return json.loads(value) if value else None
        except Exception as exc:  # Cache outages must not take down the API.
            logger.warning("Redis read failed: %s", exc)
            return None

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        if self._client is None or ttl_seconds <= 0:
            return
        try:
            await self._client.set(key, json.dumps(value, default=str), ex=ttl_seconds)
        except Exception as exc:
            logger.warning("Redis write failed: %s", exc)

    async def delete_pattern(self, pattern: str) -> None:
        if self._client is None:
            return
        try:
            keys = [key async for key in self._client.scan_iter(match=pattern, count=500)]
            if keys:
                await self._client.delete(*keys)
        except Exception as exc:
            logger.warning("Redis invalidation failed: %s", exc)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


cache = CacheService()
