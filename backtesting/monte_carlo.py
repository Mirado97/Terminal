"""MonteCarloEngine: N симуляций с возмущёнными параметрами."""
from __future__ import annotations

import copy
import random
import statistics

from backtesting.metrics import BacktestMetrics
from backtesting.models import BacktestConfig, BacktestResult, BacktestTrade, MonteCarloResult


class MonteCarloEngine:
    """
    Запускает N прогонов бэктеста с пертурбированными параметрами.

    Вместо повторного replay стакана — bootstrap-выборка из уже симулированных сделок.
    Каждый прогон: случайная выборка (с возвратом) из trades базового результата,
    с небольшим шумом к pnl (+/- 10%) для симуляции рыночной нестабильности.

    Это даёт распределение возможных итогов стратегии.
    """

    def __init__(self, n_simulations: int = 500, noise_pct: float = 0.10) -> None:
        self._n    = n_simulations
        self._noise = noise_pct

    def run(self, base_result: BacktestResult) -> MonteCarloResult:
        """Провести N симуляций и вернуть MonteCarloResult с перцентилями."""
        mc = MonteCarloResult(n_simulations=self._n)

        if not base_result.trades:
            return mc

        for _ in range(self._n):
            sim = self._one_run(base_result)
            mc.results.append(sim)

        pnls      = sorted(r.total_return_pct for r in mc.results)
        sharpes   = sorted(r.sharpe_ratio      for r in mc.results)
        drawdowns = sorted(r.max_drawdown_pct  for r in mc.results)

        mc.pnl_p5          = self._percentile(pnls,      5)
        mc.pnl_p50         = self._percentile(pnls,     50)
        mc.pnl_p95         = self._percentile(pnls,     95)
        mc.sharpe_p5       = self._percentile(sharpes,   5)
        mc.sharpe_p50      = self._percentile(sharpes,  50)
        mc.sharpe_p95      = self._percentile(sharpes,  95)
        mc.max_drawdown_p5 = self._percentile(drawdowns, 5)
        mc.max_drawdown_p95= self._percentile(drawdowns,95)

        return mc

    def _one_run(self, base: BacktestResult) -> BacktestResult:
        """Один bootstrap-прогон: выборка сделок с возвратом + шум PnL."""
        n_trades = len(base.trades)
        sampled  = random.choices(base.trades, k=n_trades)

        noisy_trades: list[BacktestTrade] = []
        for t in sampled:
            noise  = 1.0 + random.uniform(-self._noise, self._noise)
            new_pnl = t.pnl_usdt * noise
            noisy   = BacktestTrade(
                timestamp_ms   = t.timestamp_ms,
                symbol         = t.symbol,
                buy_exchange   = t.buy_exchange,
                sell_exchange  = t.sell_exchange,
                raw_spread_bps = t.raw_spread_bps,
                slippage_bps   = t.slippage_bps,
                fee_bps        = t.fee_bps,
                net_spread_bps = t.net_spread_bps,
                size_usdt      = t.size_usdt,
                pnl_usdt       = new_pnl,
                latency_ms     = t.latency_ms,
                success        = t.success,
                reason         = t.reason,
            )
            noisy_trades.append(noisy)

        result = BacktestResult(config=base.config, trades=noisy_trades)
        return BacktestMetrics.compute(result)

    @staticmethod
    def _percentile(sorted_data: list[float], pct: int) -> float:
        if not sorted_data:
            return 0.0
        idx = max(0, int(len(sorted_data) * pct / 100) - 1)
        return sorted_data[idx]
