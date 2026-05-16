"""SlippageModel: симуляция рыночного воздействия (market impact)."""
from __future__ import annotations

import math

from backtesting.models import BacktestConfig, SlippageModelType


class SlippageModel:
    """
    Оценивает слиппаж при исполнении ордера заданного размера.

    Три модели:
    - FIXED_BPS  — фиксированный слиппаж, не зависит от размера
    - LINEAR     — слиппаж растёт линейно с размером: bps = factor * size / liquidity
    - SQRT       — квадратный корень: bps = factor * sqrt(size / liquidity)
                   ближе к реальности для крипто-маркетов

    Liquidity оценивается как сумма доступного объёма в asks/bids на 5 уровнях.
    """

    def __init__(self, config: BacktestConfig) -> None:
        self._model = config.slippage_model
        self._fixed_bps = config.slippage_fixed_bps
        self._factor = config.slippage_factor

    def estimate(
        self,
        size_usdt: float,
        side_levels: list[tuple[float, float]],  # [(price, qty), ...]
        ref_price: float,
    ) -> float:
        """
        Вернуть оценку слиппажа в bps для ордера размером size_usdt.

        side_levels: уровни со стороны исполнения (asks для BUY, bids для SELL).
        """
        if self._model == SlippageModelType.FIXED_BPS:
            return self._fixed_bps

        liquidity_usdt = sum(p * q for p, q in side_levels[:5]) if side_levels else size_usdt
        if liquidity_usdt <= 0:
            return self._fixed_bps

        ratio = size_usdt / liquidity_usdt
        if self._model == SlippageModelType.LINEAR:
            return self._factor * ratio * 10_000  # перевод в bps
        else:  # SQRT
            return self._factor * math.sqrt(ratio) * 10_000

    def apply(
        self,
        raw_spread_bps: float,
        size_usdt: float,
        buy_asks: list[tuple[float, float]],
        sell_bids: list[tuple[float, float]],
        ref_price: float,
    ) -> tuple[float, float]:
        """
        Вернуть (net_spread_bps, total_slippage_bps) после учёта слиппажа
        на обеих ногах сделки.
        """
        slip_buy  = self.estimate(size_usdt, buy_asks,  ref_price)
        slip_sell = self.estimate(size_usdt, sell_bids, ref_price)
        total_slip = slip_buy + slip_sell
        return raw_spread_bps - total_slip, total_slip
