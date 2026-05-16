"""RedisCache: горячий кэш позиций, балансов и PnL."""
from __future__ import annotations

import json
import time

from storage.redis_client import RedisClient

# TTL для кэша (секунды) — балансы устаревают быстро
_PNL_TTL = 3600
_POS_TTL = 3600
_BALANCE_TTL = 60


class RedisCache:
    """
    Горячий кэш в Redis для real-time данных.

    Ключи:
        pos:{session_id}:{exchange}:{symbol}   — HASH позиции
        pnl:{session_id}                        — HASH PnL snapshot
        balance:{exchange}:{asset}              — HASH баланса

    Формат: HSET с полями qty/cost_usdt/side/updated_at или
            available/total/updated_at.
    """

    def __init__(self, client: RedisClient) -> None:
        self._r = client

    # ---- позиции ----

    async def set_position(
        self,
        session_id: str,
        exchange: str,
        symbol: str,
        qty: float,
        cost_usdt: float,
        side: str,
    ) -> None:
        key = f"pos:{session_id}:{exchange}:{symbol}"
        await self._r.client.hset(key, mapping={
            "qty": qty,
            "cost_usdt": cost_usdt,
            "side": side,
            "updated_at": time.time(),
        })
        await self._r.client.expire(key, _POS_TTL)

    async def get_position(
        self, session_id: str, exchange: str, symbol: str
    ) -> dict | None:
        key = f"pos:{session_id}:{exchange}:{symbol}"
        data = await self._r.client.hgetall(key)
        if not data:
            return None
        return {
            "qty": float(data["qty"]),
            "cost_usdt": float(data["cost_usdt"]),
            "side": data["side"],
            "updated_at": float(data["updated_at"]),
        }

    async def get_all_positions(self, session_id: str) -> list[dict]:
        """Получить все позиции сессии через SCAN."""
        pattern = f"pos:{session_id}:*"
        keys = []
        async for key in self._r.client.scan_iter(pattern):
            keys.append(key)

        positions = []
        for key in keys:
            data = await self._r.client.hgetall(key)
            if data:
                parts = key.split(":")
                positions.append({
                    "exchange": parts[2],
                    "symbol": parts[3],
                    "qty": float(data["qty"]),
                    "cost_usdt": float(data["cost_usdt"]),
                    "side": data["side"],
                })
        return positions

    # ---- PnL ----

    async def set_pnl(
        self,
        session_id: str,
        realized_pnl: float,
        peak_pnl: float,
        drawdown_pct: float,
        trade_count: int,
    ) -> None:
        key = f"pnl:{session_id}"
        await self._r.client.hset(key, mapping={
            "realized_pnl": realized_pnl,
            "peak_pnl": peak_pnl,
            "drawdown_pct": drawdown_pct,
            "trade_count": trade_count,
            "updated_at": time.time(),
        })
        await self._r.client.expire(key, _PNL_TTL)

    async def get_pnl(self, session_id: str) -> dict | None:
        key = f"pnl:{session_id}"
        data = await self._r.client.hgetall(key)
        if not data:
            return None
        return {
            "realized_pnl": float(data["realized_pnl"]),
            "peak_pnl": float(data["peak_pnl"]),
            "drawdown_pct": float(data["drawdown_pct"]),
            "trade_count": int(data["trade_count"]),
        }

    # ---- балансы ----

    async def set_balance(
        self,
        exchange: str,
        asset: str,
        available: float,
        total: float,
    ) -> None:
        key = f"balance:{exchange}:{asset}"
        await self._r.client.hset(key, mapping={
            "available": available,
            "total": total,
            "updated_at": time.time(),
        })
        await self._r.client.expire(key, _BALANCE_TTL)

    async def get_balance(self, exchange: str, asset: str) -> dict | None:
        key = f"balance:{exchange}:{asset}"
        data = await self._r.client.hgetall(key)
        if not data:
            return None
        return {
            "available": float(data["available"]),
            "total": float(data["total"]),
            "updated_at": float(data["updated_at"]),
        }
