"""Collectors: читают состояние системы и обновляют Prometheus метрики."""
from __future__ import annotations

from monitoring import metrics


class PnLCollector:
    """
    Обновляет PnL/drawdown/exposure метрики из InventoryManager.

    Вызывается периодически (pull-модель) — обычно каждые 5 секунд.
    InventoryManager принимается как Any (duck-typing) для гибкости в тестах.
    """

    def __init__(self, inventory: object) -> None:
        self._inventory = inventory

    def update(self) -> None:
        snap = self._inventory.snapshot()
        metrics.realized_pnl.set(snap["realized_pnl"])
        metrics.peak_pnl.set(snap["peak_pnl"])
        metrics.drawdown_pct.set(snap["drawdown_pct"])
        metrics.total_exposure.set(snap["total_exposure_usdt"])


class SpreadCollector:
    """
    Обновляет метрики спреда из DetectorStats.

    Вызывается после каждого сканирования SpreadDetector.
    """

    def __init__(self, detector: object) -> None:
        self._detector = detector

    def on_scan(self) -> None:
        """Зафиксировать одно сканирование."""
        metrics.spread_scans_total.inc()

    def on_opportunity(self, symbol: str, spread_bps: float) -> None:
        """Зафиксировать найденную opportunity и её спред."""
        metrics.spread_opportunities_total.labels(symbol=symbol).inc()
        metrics.spread_bps_hist.observe(spread_bps)

    def on_trade(self, state: str, execution_time_ms: int, realized_spread_bps: float) -> None:
        """Зафиксировать завершённую сделку."""
        metrics.trades_total.labels(state=state).inc()
        metrics.execution_latency_ms.observe(execution_time_ms)
        if state == "completed":
            metrics.spread_bps_hist.observe(realized_spread_bps)


class WatchdogCollector:
    """
    Обновляет метрики watchdog-системы из WatchdogOrchestrator.

    Читает статус supervisor и обновляет gauge воркеров по состояниям.
    """

    _STATES = ("running", "failed", "disabled", "stopped", "starting")

    def __init__(self, orchestrator: object) -> None:
        self._orchestrator = orchestrator

    def update(self) -> None:
        status = self._orchestrator.status()

        for state in self._STATES:
            metrics.workers_by_state.labels(state=state).set(status.get(state, 0))

        # Перезапуски по символам
        for worker in status.get("workers", []):
            symbol = worker["symbol"]
            restarts = worker["restarts"]
            if restarts > 0:
                # Prometheus Counter нельзя сбросить — записываем delta через хранение prev
                pass  # контроль через watchdog_logs в БД


class ExchangeCollector:
    """
    Обновляет метрики здоровья бирж из OrderBookEngine.
    """

    def __init__(self, ob_engine: object) -> None:
        self._ob = ob_engine

    def update(self) -> None:
        books = self._ob.all_books()
        for key, book in books.items():
            # key: "exchange:symbol:market_type"
            parts = key.split(":")
            if len(parts) < 2:
                continue
            exchange, symbol = parts[0], parts[1]

            metrics.orderbook_stale.labels(
                exchange=exchange, symbol=symbol
            ).set(1 if book.is_stale else 0)

            if not book.is_stale and book.spread_bps is not None:
                metrics.orderbook_spread_bps.labels(
                    exchange=exchange, symbol=symbol
                ).set(book.spread_bps)

    def set_ws_status(self, exchange: str, connected: bool) -> None:
        """Обновить статус WebSocket соединения биржи."""
        metrics.ws_connected.labels(exchange=exchange).set(1 if connected else 0)
