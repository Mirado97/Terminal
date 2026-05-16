"""SpreadCalculator: вычисляет все уровни спреда между двумя стаканами."""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.models import SpreadOpportunity
from orderbook.book import LocalOrderBook
from spread.fees import FeeTable


@dataclass(frozen=True)
class SpreadResult:
    """Полный расчёт спреда между двумя стаканами в одном направлении."""
    raw_spread_bps: float        # (sell_bid - buy_ask) / mid * 10000
    fee_cost_bps: float          # суммарные taker комиссии обеих ног
    effective_spread_bps: float  # raw - fees
    buy_slippage_bps: float      # реальный avg_price - best_ask в bps
    sell_slippage_bps: float     # best_bid - реальный avg_price в bps
    net_spread_bps: float        # effective - slippage обеих ног
    latency_cost_bps: float      # стоимость latency (drift цены)
    executable_spread_bps: float # net - latency
    buy_price: float             # best ask на buy бирже
    sell_price: float            # best bid на sell бирже
    buy_avg_price: float         # средняя цена покупки с учётом depth
    sell_avg_price: float        # средняя цена продажи с учётом depth
    qty_base: float              # исполнимый объём в base currency
    size_usdt: float             # объём в USDT


class SpreadCalculator:
    """
    Вычисляет спред между buy_book (покупаем) и sell_book (продаём).

    Модель latency cost:
        latency_cost_bps = latency_ms * drift_bps_per_ms
    Типичное значение drift_bps_per_ms = 0.05–0.2 для BTC.
    Настраивается через конфиг.
    """

    def __init__(
        self,
        fee_table: FeeTable,
        latency_us: int = 5_000,      # ожидаемая latency исполнения, мкс
        drift_bps_per_ms: float = 0.1, # ценовой drift за 1 мс
        min_qty_usdt: float = 10.0,    # минимальный объём для расчёта
    ) -> None:
        self._fees = fee_table
        self._latency_us = latency_us
        self._drift = drift_bps_per_ms
        self._min_qty_usdt = min_qty_usdt

    def compute(
        self,
        buy_book: LocalOrderBook,
        sell_book: LocalOrderBook,
        max_size_usdt: float,
    ) -> SpreadResult | None:
        """
        Считает спред buy_book → sell_book.
        Возвращает None если невозможно исполнить (нет ликвидности, stale).
        """
        if not buy_book.is_synced or not sell_book.is_synced:
            return None
        if buy_book.is_stale or sell_book.is_stale:
            return None

        buy_price = buy_book.best_ask   # платим ask при покупке
        sell_price = sell_book.best_bid  # получаем bid при продаже

        if buy_price <= 0 or sell_price <= 0:
            return None

        mid = (buy_price + sell_price) / 2.0

        # Объём: ограничен лимитом и доступным размером на best уровне
        max_qty_base = max_size_usdt / buy_price
        avail_buy = buy_book._asks.get(buy_price, 0.0)
        avail_sell = sell_book._bids.get(sell_price, 0.0)
        qty_base = min(max_qty_base, avail_buy, avail_sell)

        if qty_base * buy_price < self._min_qty_usdt:
            return None

        # 1. Raw spread
        raw_spread_bps = (sell_price - buy_price) / mid * 10_000

        # 2. Fee cost
        fee_cost_bps = self._fees.round_trip_taker_bps(
            buy_book.exchange, buy_book.market_type,
            sell_book.exchange, sell_book.market_type,
        )
        effective_spread_bps = raw_spread_bps - fee_cost_bps

        # 3. Slippage (реальная средняя цена исполнения vs best price)
        buy_avg = buy_book.slippage_for_qty(qty_base, "buy")
        sell_avg = sell_book.slippage_for_qty(qty_base, "sell")

        if buy_avg == 0.0 or sell_avg == 0.0:
            return None

        buy_slippage_bps = max(0.0, (buy_avg - buy_price) / mid * 10_000)
        sell_slippage_bps = max(0.0, (sell_price - sell_avg) / mid * 10_000)
        net_spread_bps = effective_spread_bps - buy_slippage_bps - sell_slippage_bps

        # 4. Latency cost
        latency_ms = self._latency_us / 1_000.0
        latency_cost_bps = latency_ms * self._drift
        executable_spread_bps = net_spread_bps - latency_cost_bps

        return SpreadResult(
            raw_spread_bps=raw_spread_bps,
            fee_cost_bps=fee_cost_bps,
            effective_spread_bps=effective_spread_bps,
            buy_slippage_bps=buy_slippage_bps,
            sell_slippage_bps=sell_slippage_bps,
            net_spread_bps=net_spread_bps,
            latency_cost_bps=latency_cost_bps,
            executable_spread_bps=executable_spread_bps,
            buy_price=buy_price,
            sell_price=sell_price,
            buy_avg_price=buy_avg,
            sell_avg_price=sell_avg,
            qty_base=qty_base,
            size_usdt=qty_base * mid,
        )

    def to_opportunity(
        self,
        result: SpreadResult,
        buy_book: LocalOrderBook,
        sell_book: LocalOrderBook,
    ) -> SpreadOpportunity:
        return SpreadOpportunity(
            buy_exchange=buy_book.exchange,
            sell_exchange=sell_book.exchange,
            symbol=buy_book.symbol,
            buy_market=buy_book.market_type,
            sell_market=sell_book.market_type,
            raw_spread_bps=result.raw_spread_bps,
            effective_spread_bps=result.effective_spread_bps,
            net_spread_bps=result.net_spread_bps,
            executable_spread_bps=result.executable_spread_bps,
            buy_price=result.buy_price,
            sell_price=result.sell_price,
            max_size_usdt=result.size_usdt,
            timestamp_ms=int(time.time() * 1000),
        )
