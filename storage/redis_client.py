"""Redis клиент (redis.asyncio)."""
from __future__ import annotations

import redis.asyncio as aioredis
import structlog

logger = structlog.get_logger(__name__)


class RedisClient:
    """
    Обёртка над redis.asyncio.Redis с lifecycle management.

    Использование:
        client = RedisClient("redis://localhost:6379/0")
        await client.start()
        await client.client.set("key", "value")
        await client.stop()
    """

    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        self._url = url
        self._client: aioredis.Redis | None = None

    async def start(self) -> None:
        self._client = aioredis.from_url(
            self._url,
            decode_responses=True,
            encoding="utf-8",
        )
        await self._client.ping()
        logger.info("Redis подключён", url=self._url)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("Redis отключён")

    @property
    def client(self) -> aioredis.Redis:
        assert self._client, "RedisClient не инициализирован — вызовите start()"
        return self._client
