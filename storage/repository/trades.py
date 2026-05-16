"""Репозиторий сделок и детализации ног (fills)."""
from __future__ import annotations

from execution.models import ExecutionResult
from storage.repository.base import BaseRepository


class TradeRepository(BaseRepository):
    """
    Хранит результаты арбитражных сделок.

    Каждая сделка = 1 запись в trades + 1-2 записи в fills (по ноге).
    """

    async def insert(self, session_id: str, result: ExecutionResult) -> int:
        """Сохранить сделку. Возвращает id записи."""
        opp = result.opportunity
        fee = (result.buy_leg.fee_usdt if result.buy_leg else 0.0) + \
              (result.sell_leg.fee_usdt if result.sell_leg else 0.0)

        row = await self._pool.fetchrow(
            """
            INSERT INTO trades (
                session_id, symbol,
                buy_exchange, sell_exchange,
                buy_order_id, sell_order_id,
                buy_price, sell_price, qty,
                pnl_usdt, realized_spread_bps, fee_usdt,
                state, execution_time_ms
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            RETURNING id
            """,
            session_id,
            opp.symbol,
            opp.buy_exchange.value,
            opp.sell_exchange.value,
            result.buy_leg.order.id if result.buy_leg else None,
            result.sell_leg.order.id if result.sell_leg else None,
            opp.buy_price,
            opp.sell_price,
            result.buy_leg.filled_qty if result.buy_leg else 0.0,
            result.pnl_usdt,
            result.realized_spread_bps,
            fee,
            result.state.value,
            result.execution_time_ms or 0,
        )
        trade_id: int = row["id"]

        if result.buy_leg:
            await self._insert_fill(trade_id, result.buy_leg, "buy")
        if result.sell_leg:
            await self._insert_fill(trade_id, result.sell_leg, "sell")

        return trade_id

    async def _insert_fill(self, trade_id: int, leg, side: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO fills (trade_id, exchange, order_id, side, filled_qty, avg_price, fee_usdt)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            """,
            trade_id,
            leg.order.exchange.value,
            leg.order.id,
            side,
            leg.filled_qty,
            leg.avg_price,
            leg.fee_usdt,
        )

    async def get(self, trade_id: int) -> dict | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM trades WHERE id = $1", trade_id
        )
        return dict(row) if row else None

    async def list_recent(self, session_id: str, limit: int = 100) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT * FROM trades WHERE session_id = $1 ORDER BY created_at DESC LIMIT $2",
            session_id, limit,
        )
        return [dict(r) for r in rows]

    async def total_pnl(self, session_id: str) -> float:
        val = await self._pool.fetchval(
            "SELECT COALESCE(SUM(pnl_usdt), 0) FROM trades WHERE session_id = $1",
            session_id,
        )
        return float(val or 0.0)
