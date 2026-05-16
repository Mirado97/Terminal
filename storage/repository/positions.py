"""Репозиторий открытых позиций."""
from __future__ import annotations

from storage.repository.base import BaseRepository


class PositionRepository(BaseRepository):
    """
    Хранит текущие позиции на биржах.

    Использует UPSERT (INSERT ... ON CONFLICT DO UPDATE) —
    одна запись на (session_id, exchange, symbol).
    """

    async def upsert(
        self,
        session_id: str,
        exchange: str,
        symbol: str,
        qty: float,
        cost_usdt: float,
        side: str,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO positions (session_id, exchange, symbol, qty, cost_usdt, side, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,NOW())
            ON CONFLICT (session_id, exchange, symbol)
            DO UPDATE SET
                qty        = EXCLUDED.qty,
                cost_usdt  = EXCLUDED.cost_usdt,
                side       = EXCLUDED.side,
                updated_at = NOW()
            """,
            session_id, exchange, symbol, qty, cost_usdt, side,
        )

    async def get(self, session_id: str, exchange: str, symbol: str) -> dict | None:
        row = await self._pool.fetchrow(
            """
            SELECT * FROM positions
            WHERE session_id=$1 AND exchange=$2 AND symbol=$3
            """,
            session_id, exchange, symbol,
        )
        return dict(row) if row else None

    async def get_all(self, session_id: str) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT * FROM positions WHERE session_id=$1 AND ABS(qty) > 1e-8",
            session_id,
        )
        return [dict(r) for r in rows]
