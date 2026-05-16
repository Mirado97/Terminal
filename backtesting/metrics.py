"""BacktestMetrics: расчёт метрик качества стратегии."""
from __future__ import annotations

import math

from backtesting.models import BacktestResult, BacktestTrade, EquityCurve


class BacktestMetrics:
    """
    Рассчитывает Sharpe, Sortino, max drawdown, win rate, profit factor.
    Заполняет поля BacktestResult in-place.
    """

    @staticmethod
    def compute(result: BacktestResult) -> BacktestResult:
        """Заполнить все метрики result и вернуть его же."""
        trades = result.trades
        config = result.config

        result.total_trades   = len(trades)
        result.winning_trades = sum(1 for t in trades if t.pnl_usdt > 0)
        result.losing_trades  = sum(1 for t in trades if t.pnl_usdt < 0)

        if not trades:
            return result

        pnls = [t.pnl_usdt for t in trades]

        result.avg_pnl_per_trade = sum(pnls) / len(pnls)
        result.win_rate          = result.winning_trades / len(trades)

        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss   = abs(sum(p for p in pnls if p < 0))
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Equity curve
        equity = config.initial_capital_usdt
        equities   = []
        peak       = equity
        drawdowns  = []
        timestamps = [t.timestamp_ms for t in trades]

        for pnl in pnls:
            equity += pnl
            equities.append(equity)
            peak = max(peak, equity)
            dd   = (peak - equity) / peak * 100 if peak > 0 else 0.0
            drawdowns.append(dd)

        result.equity_curve = EquityCurve(
            timestamps = timestamps,
            equity     = equities,
            drawdowns  = drawdowns,
        )

        result.total_return_pct = (equities[-1] - config.initial_capital_usdt) / config.initial_capital_usdt * 100
        result.max_drawdown_pct = max(drawdowns) if drawdowns else 0.0

        # Sharpe и Sortino (аннуализированные)
        n = len(pnls)
        mean_pnl = sum(pnls) / n

        variance = sum((p - mean_pnl) ** 2 for p in pnls) / n
        std_pnl  = math.sqrt(variance) if variance > 0 else 0.0

        downside_variance = sum((p - mean_pnl) ** 2 for p in pnls if p < mean_pnl) / n
        downside_std      = math.sqrt(downside_variance) if downside_variance > 0 else 0.0

        scale = math.sqrt(config.trades_per_year_estimate)

        result.sharpe_ratio  = (mean_pnl / std_pnl  * scale) if std_pnl  > 0 else 0.0
        result.sortino_ratio = (mean_pnl / downside_std * scale) if downside_std > 0 else 0.0

        return result
