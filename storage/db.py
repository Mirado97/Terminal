"""Асинхронный пул соединений к PostgreSQL (asyncpg)."""
from __future__ import annotations

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

_MIN_SIZE = 5
_MAX_SIZE = 20


class DatabasePool:
    """
    Обёртка над asyncpg.Pool с lifecycle management.

    Использование:
        pool = DatabasePool("postgresql://user:pass@host/db")
        await pool.start()
        row = await pool.fetchrow("SELECT 1")
        await pool.stop()
    """

    def __init__(
        self,
        dsn: str,
        min_size: int = _MIN_SIZE,
        max_size: int = _MAX_SIZE,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
        )
        logger.info("PostgreSQL pool создан", min=self._min_size, max=self._max_size)

    async def stop(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL pool закрыт")

    async def execute(self, sql: str, *args) -> str:
        assert self._pool, "DatabasePool не инициализирован"
        async with self._pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetchrow(self, sql: str, *args) -> asyncpg.Record | None:
        assert self._pool, "DatabasePool не инициализирован"
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetch(self, sql: str, *args) -> list[asyncpg.Record]:
        assert self._pool, "DatabasePool не инициализирован"
        async with self._pool.acquire() as conn:
            return await conn.fetch(sql, *args)

    async def fetchval(self, sql: str, *args):
        assert self._pool, "DatabasePool не инициализирован"
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, *args)
