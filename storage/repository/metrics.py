"""Репозитории латентностей и системных метрик."""
from __future__ import annotations

import json

from storage.repository.base import BaseRepository


class LatencyRepository(BaseRepository):
    """
    Хранит измерения задержек.

    metric_type: 'ws_message' | 'rest_order' | 'execution' | 'orderbook_sync'
    value_us: значение в микросекундах.
    """

    async def insert(
        self,
        exchange: str,
        metric_type: str,
        value_us: int,
        symbol: str | None = None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO latency (exchange, metric_type, value_us, symbol)
            VALUES ($1,$2,$3,$4)
            """,
            exchange, metric_type, value_us, symbol,
        )

    async def percentiles(
        self,
        exchange: str,
        metric_type: str,
        window_s: int = 300,
    ) -> dict:
        """P50/P95/P99 за последние window_s секунд."""
        rows = await self._pool.fetch(
            """
            SELECT
                PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY value_us) AS p50,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY value_us) AS p95,
                PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY value_us) AS p99,
                COUNT(*) AS samples
            FROM latency
            WHERE exchange=$1 AND metric_type=$2
              AND created_at >= NOW() - ($3 || ' seconds')::INTERVAL
            """,
            exchange, metric_type, str(window_s),
        )
        if not rows:
            return {"p50": 0, "p95": 0, "p99": 0, "samples": 0}
        r = rows[0]
        return {
            "p50": int(r["p50"] or 0),
            "p95": int(r["p95"] or 0),
            "p99": int(r["p99"] or 0),
            "samples": r["samples"],
        }


class MetricsRepository(BaseRepository):
    """
    Хранит произвольные числовые метрики с метками (JSONB labels).

    Пример: insert("spread_detector.scans", 1234, {"symbol": "BTCUSDT"})
    """

    async def insert(
        self,
        name: str,
        value: float,
        labels: dict | None = None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO metrics (metric_name, value, labels)
            VALUES ($1,$2,$3)
            """,
            name, value,
            json.dumps(labels) if labels else None,
        )

    async def latest(self, name: str, limit: int = 100) -> list[dict]:
        rows = await self._pool.fetch(
            """
            SELECT metric_name, value, labels, created_at
            FROM metrics WHERE metric_name=$1
            ORDER BY created_at DESC LIMIT $2
            """,
            name, limit,
        )
        return [dict(r) for r in rows]
