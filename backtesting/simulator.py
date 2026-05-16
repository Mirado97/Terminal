"""TradeSimulator: симуляция исполнения одной арбитражной сделки."""
from __future__ import annotations

from backtesting.latency import LatencyModel
from backtesting.models import BacktestConfig, BacktestTrade, OrderBookTick
from backtesting.slippage import SlippageModel


class TradeSimulator:
    """
    Симулирует исполнение одной арбитражной пары тиков.

    Логика:
    1. Проверить минимальный спред (до слиппажа)
    2. Рассчитать слиппаж по обеим ногам
    3. Сэмплировать latency и добавить latency cost
    4. Вычесть комиссию
    5. Если net_spread_bps > 0 — сделка исполнена, считаем PnL
    """

    def __init__(self, config: BacktestConfig) -> None:
        self._config    = config
        self._slippage  = SlippageModel(config)
        self._latency   = LatencyModel(config)

    def simulate(
        self,
        buy_tick: OrderBookTick,
        sell_tick: OrderBookTick,
        size_usdt: float,
    ) -> BacktestTrade | None:
        """
        Попытаться исполнить арбитраж: купить на buy_tick, продать на sell_tick.

        Возвращает BacktestTrade (success=True/False) или None если стакан пустой.
        """
        if not buy_tick.asks or not sell_tick.bids:
            return None

        best_ask = buy_tick.asks[0][0]
        best_bid = sell_tick.bids[0][0]

        if best_ask <= 0 or best_bid <= 0:
            return None

        raw_spread_bps = (best_bid - best_ask) / best_ask * 10_000

        # Применяем слиппаж
        net_after_slip, total_slip = self._slippage.apply(
            raw_spread_bps,
            size_usdt,
            buy_tick.asks,
            sell_tick.bids,
            best_ask,
        )

        # Latency cost
        latency_ms   = self._latency.sample()
        latency_cost = self._latency.latency_cost_bps(latency_ms)

        # Комиссия
        net_spread_bps = net_after_slip - latency_cost - self._config.fee_bps

        pnl_usdt = size_usdt * net_spread_bps / 10_000

        symbol = buy_tick.symbol

        if raw_spread_bps < self._config.min_spread_bps:
            return BacktestTrade(
                timestamp_ms   = buy_tick.timestamp_ms,
                symbol         = symbol,
                buy_exchange   = buy_tick.exchange.value,
                sell_exchange  = sell_tick.exchange.value,
                raw_spread_bps = raw_spread_bps,
                slippage_bps   = total_slip,
                fee_bps        = self._config.fee_bps,
                net_spread_bps = net_spread_bps,
                size_usdt      = size_usdt,
                pnl_usdt       = 0.0,
                latency_ms     = latency_ms,
                success        = False,
                reason         = "spread_below_min",
            )

        return BacktestTrade(
            timestamp_ms   = buy_tick.timestamp_ms,
            symbol         = symbol,
            buy_exchange   = buy_tick.exchange.value,
            sell_exchange  = sell_tick.exchange.value,
            raw_spread_bps = raw_spread_bps,
            slippage_bps   = total_slip,
            fee_bps        = self._config.fee_bps,
            net_spread_bps = net_spread_bps,
            size_usdt      = size_usdt,
            pnl_usdt       = pnl_usdt,
            latency_ms     = latency_ms,
            success        = net_spread_bps > 0,
            reason         = "" if net_spread_bps > 0 else "negative_net_spread",
        )

    def size_for_tick(self, tick: OrderBookTick) -> float:
        """Размер сделки: min(max_size, доступная ликвидность на 3 уровнях)."""
        liquidity = sum(p * q for p, q in tick.asks[:3]) if tick.asks else 0.0
        return min(self._config.max_size_usdt, liquidity) if liquidity > 0 else self._config.max_size_usdt
