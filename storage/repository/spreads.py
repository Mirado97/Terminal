"""Репозиторий найденных спред-возможностей."""
from __future__ import annotations

from core.models import SpreadOpportunity
from storage.repository.base import BaseRepository


class SpreadRepository(BaseRepository):
    """
    Хранит историю найденных SpreadOpportunity.

    Пишется каждый выполненный спред — для анализа качества алгоритма,
    backtest-сравнения и визуализации в дашборде.
    """

    async def insert(self, opp: SpreadOpportunity) -> int:
        row = await self._pool.fetchrow(
            """
            INSERT INTO spreads (
                symbol, buy_exchange, sell_exchange,
                raw_spread_bps, executable_spread_bps,
                buy_price, sell_price, max_size_usdt
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING id
            """,
            opp.symbol,
            opp.buy_exchange.value,
            opp.sell_exchange.value,
            opp.raw_spread_bps,
            opp.executable_spread_bps,
            opp.buy_price,
            opp.sell_price,
            opp.max_size_usdt,
        )
        return row["id"]

    async def mark_executed(self, spread_id: int) -> None:
        await self._pool.execute(
            "UPDATE spreads SET executed=TRUE WHERE id=$1", spread_id
        )

    async def list_recent(self, symbol: str, limit: int = 200) -> list[dict]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM spreads WHERE symbol=$1
            ORDER BY created_at DESC LIMIT $2
            """,
            symbol, limit,
        )
        return [dict(r) for r in rows]
