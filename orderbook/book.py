"""Локальный стакан: хранит уровни, применяет snapshot/delta."""
from __future__ import annotations

import time

from core.models import Exchange, MarketType, OrderBook, Price

_STALE_THRESHOLD_S = 10.0


class LocalOrderBook:
    """
    In-memory orderbook для одной пары на одной бирже.

    Хранение: dict[price → size] — O(1) обновление.
    best_bid/best_ask — O(n) скан при каждом вызове.
    Для нескольких сотен уровней это приемлемо (~мкс).
    """

    __slots__ = (
        "exchange", "symbol", "market_type",
        "_bids", "_asks", "_sequence",
        "_last_update_s", "_is_synced",
    )

    def __init__(self, exchange: Exchange, symbol: str, market_type: MarketType) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self.market_type = market_type
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}
        self._sequence: int = 0
        self._last_update_s: float = 0.0
        self._is_synced: bool = False

    # ---- применение обновлений ----

    def apply_snapshot(self, bids: list[Price], asks: list[Price], sequence: int, ts_ms: int) -> None:
        self._bids = {p.price: p.size for p in bids if p.size > 0}
        self._asks = {p.price: p.size for p in asks if p.size > 0}
        self._sequence = sequence
        self._last_update_s = time.monotonic()
        self._is_synced = True

    def apply_delta(self, bids: list[Price], asks: list[Price], sequence: int, ts_ms: int) -> None:
        """size == 0 означает удаление уровня."""
        for p in bids:
            if p.size == 0.0:
                self._bids.pop(p.price, None)
            else:
                self._bids[p.price] = p.size
        for p in asks:
            if p.size == 0.0:
                self._asks.pop(p.price, None)
            else:
                self._asks[p.price] = p.size
        self._sequence = sequence
        self._last_update_s = time.monotonic()

    def invalidate(self) -> None:
        """Сброс — требуется ресинк (checksum mismatch или разрыв sequence)."""
        self._is_synced = False
        self._bids.clear()
        self._asks.clear()

    # ---- лучшие цены ----

    @property
    def best_bid(self) -> float:
        return max(self._bids.keys(), default=0.0)

    @property
    def best_ask(self) -> float:
        return min(self._asks.keys(), default=0.0)

    @property
    def mid_price(self) -> float:
        bb, ba = self.best_bid, self.best_ask
        return (bb + ba) / 2.0 if bb and ba else 0.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        return (self.spread / mid * 10_000) if mid else 0.0

    # ---- состояние ----

    @property
    def is_synced(self) -> bool:
        return self._is_synced

    @property
    def is_stale(self) -> bool:
        if not self._is_synced:
            return True
        return (time.monotonic() - self._last_update_s) > _STALE_THRESHOLD_S

    @property
    def sequence(self) -> int:
        return self._sequence

    # ---- аналитика ----

    def imbalance(self, depth: int = 10) -> float:
        """
        Соотношение bid/ask объёма в top-N уровнях.
        1.0 = только bids, 0.0 = только asks, 0.5 = баланс.
        """
        bid_vol = sum(v for _, v in sorted(self._bids.items(), reverse=True)[:depth])
        ask_vol = sum(v for _, v in sorted(self._asks.items())[:depth])
        total = bid_vol + ask_vol
        return bid_vol / total if total else 0.5

    def slippage_for_qty(self, qty_base: float, side: str) -> float:
        """
        Средневзвешенная цена исполнения для заданного объёма.
        side: 'buy' (sweep asks) | 'sell' (sweep bids).
        Возвращает 0.0 если ликвидности недостаточно.
        """
        levels = (
            sorted(self._asks.items())
            if side == "buy"
            else sorted(self._bids.items(), reverse=True)
        )
        remaining = qty_base
        cost = 0.0
        for price, size in levels:
            filled = min(remaining, size)
            cost += filled * price
            remaining -= filled
            if remaining <= 1e-10:
                break
        return cost / qty_base if remaining <= 1e-10 else 0.0

    def liquidity_usdt(self, side: str, depth_bps: float = 10.0) -> float:
        """
        Суммарная ликвидность в USDT в пределах depth_bps базисных пунктов от best price.
        side: 'bid' | 'ask'
        """
        if side == "bid":
            ref = self.best_bid
            levels = sorted(self._bids.items(), reverse=True)
            threshold = ref * (1 - depth_bps / 10_000)
            return sum(p * s for p, s in levels if p >= threshold)
        else:
            ref = self.best_ask
            levels = sorted(self._asks.items())
            threshold = ref * (1 + depth_bps / 10_000)
            return sum(p * s for p, s in levels if p <= threshold)

    # ---- доступ к уровням ----

    def top_bids(self, n: int = 25) -> list[Price]:
        return [Price(p, s) for p, s in sorted(self._bids.items(), reverse=True)[:n]]

    def top_asks(self, n: int = 25) -> list[Price]:
        return [Price(p, s) for p, s in sorted(self._asks.items())[:n]]

    def to_snapshot(self, depth: int = 25) -> OrderBook:
        return OrderBook(
            exchange=self.exchange,
            symbol=self.symbol,
            market_type=self.market_type,
            bids=self.top_bids(depth),
            asks=self.top_asks(depth),
            timestamp_ms=int(self._last_update_s * 1000),
            sequence=self._sequence,
            is_snapshot=True,
        )

    def __repr__(self) -> str:
        return (
            f"LocalOrderBook({self.exchange.value}:{self.symbol} "
            f"bid={self.best_bid:.2f} ask={self.best_ask:.2f} "
            f"spread={self.spread_bps:.2f}bps synced={self._is_synced})"
        )
