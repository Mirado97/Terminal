"""Репозиторий событий watchdog-системы."""
from __future__ import annotations

from storage.repository.base import BaseRepository


class WatchdogLogRepository(BaseRepository):
    """
    Хранит события жизненного цикла воркеров:
    'start' | 'stop' | 'restart' | 'failed' | 'disabled'.
    """

    async def insert(
        self,
        symbol: str,
        event_type: str,
        worker_state: str | None = None,
        restart_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO watchdog_logs
                (symbol, event_type, worker_state, restart_count, error_message)
            VALUES ($1,$2,$3,$4,$5)
            """,
            symbol, event_type, worker_state, restart_count, error_message,
        )

    async def list_recent(self, symbol: str, limit: int = 100) -> list[dict]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM watchdog_logs
            WHERE symbol=$1 ORDER BY created_at DESC LIMIT $2
            """,
            symbol, limit,
        )
        return [dict(r) for r in rows]

    async def error_count(self, symbol: str, window_s: int = 3600) -> int:
        """Количество ошибок за последние window_s секунд."""
        val = await self._pool.fetchval(
            """
            SELECT COUNT(*) FROM watchdog_logs
            WHERE symbol=$1 AND event_type='failed'
              AND created_at >= NOW() - ($2 || ' seconds')::INTERVAL
            """,
            symbol, str(window_s),
        )
        return int(val or 0)
