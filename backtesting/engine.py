"""BacktestEngine: оркестратор полного прогона бэктеста."""
from __future__ import annotations

from backtesting.metrics import BacktestMetrics
from backtesting.models import BacktestConfig, BacktestResult
from backtesting.monte_carlo import MonteCarloEngine
from backtesting.replay import OrderBookReplayer
from backtesting.simulator import TradeSimulator
from backtesting.models import OrderBookTick, MonteCarloResult


class BacktestEngine:
    """
    Полный pipeline бэктеста:
    1. OrderBookReplayer — генерирует пары тиков
    2. TradeSimulator    — симулирует исполнение каждой пары
    3. BacktestMetrics   — считает итоговые метрики
    4. MonteCarloEngine  — опционально запускает N симуляций

    Использование:
        engine = BacktestEngine(config)
        result = engine.run(ticks)
        mc     = engine.run_monte_carlo(result, n=500)
    """

    def __init__(self, config: BacktestConfig) -> None:
        self._config    = config
        self._simulator = TradeSimulator(config)
        self._replayer  = OrderBookReplayer()

    def run(self, ticks: list[OrderBookTick]) -> BacktestResult:
        """
        Прогнать бэктест по списку тиков стакана.

        Тики должны быть отсортированы по timestamp_ms.
        """
        self._replayer.reset()
        result = BacktestResult(config=self._config)

        # Открытые позиции по символу (только 1 одновременно)
        open_positions: dict[str, int] = {}

        for buy_tick, sell_tick in self._replayer.feed(ticks):
            symbol = buy_tick.symbol

            # Ограничение: 1 открытая сделка на символ
            if open_positions.get(symbol, 0) >= self._config.max_trades_per_symbol:
                continue

            size = self._simulator.size_for_tick(buy_tick)
            if size <= 0:
                continue

            trade = self._simulator.simulate(buy_tick, sell_tick, size)
            if trade is None:
                continue

            result.trades.append(trade)

            if trade.success:
                open_positions[symbol] = open_positions.get(symbol, 0) + 1
                # В реальности позиция закрывается после исполнения — сбрасываем сразу
                open_positions[symbol] -= 1

        BacktestMetrics.compute(result)
        return result

    def run_monte_carlo(
        self,
        base_result: BacktestResult,
        n_simulations: int = 500,
        noise_pct: float = 0.10,
    ) -> MonteCarloResult:
        """Bootstrap Monte Carlo поверх уже симулированных сделок."""
        mc_engine = MonteCarloEngine(n_simulations=n_simulations, noise_pct=noise_pct)
        return mc_engine.run(base_result)
