"""Тесты Monitoring: LatencyTracker, Collectors, PrometheusExporter."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

import monitoring.metrics as m
from monitoring.collectors import (
    ExchangeCollector,
    PnLCollector,
    SpreadCollector,
    WatchdogCollector,
)
from monitoring.exporter import PrometheusExporter
from monitoring.latency import LatencyTracker


# ---- вспомогательные мок-объекты ----

def _mock_inventory(
    pnl: float = 50.0,
    peak: float = 80.0,
    drawdown: float = 2.5,
    exposure: float = 1000.0,
    trade_count: int = 10,
) -> MagicMock:
    inv = MagicMock()
    inv.snapshot.return_value = {
        "realized_pnl": pnl,
        "peak_pnl": peak,
        "drawdown_pct": drawdown,
        "total_exposure_usdt": exposure,
        "trade_count": trade_count,
    }
    return inv


def _mock_orchestrator(running: int = 5, failed: int = 1) -> MagicMock:
    orch = MagicMock()
    orch.status.return_value = {
        "running": running,
        "failed": failed,
        "disabled": 0,
        "stopped": 0,
        "starting": 0,
        "workers": [
            {"symbol": "BTCUSDT", "restarts": 2, "state": "running"},
        ],
    }
    return orch


def _mock_ob_engine() -> MagicMock:
    book_ok = MagicMock()
    book_ok.is_stale = False
    book_ok.spread_bps = 5.0

    book_stale = MagicMock()
    book_stale.is_stale = True
    book_stale.spread_bps = None

    engine = MagicMock()
    engine.all_books.return_value = {
        "binance:BTCUSDT:spot": book_ok,
        "bybit:ETHUSDT:spot": book_stale,
    }
    return engine


# =========================================================
# LatencyTracker
# =========================================================

def test_latency_tracker_record_ws():
    """record_ws() обновляет Prometheus histogram и локальный буфер."""
    tracker = LatencyTracker()
    with patch.object(m.ws_latency_us.labels(exchange="binance"), "observe") as mock_obs:
        tracker.record_ws("binance", 350)
        mock_obs.assert_called_once_with(350)


def test_latency_tracker_record_rest():
    tracker = LatencyTracker()
    with patch.object(
        m.rest_latency_us.labels(exchange="bybit", endpoint="place_order"), "observe"
    ) as mock_obs:
        tracker.record_rest("bybit", "place_order", 4200)
        mock_obs.assert_called_once_with(4200)


def test_latency_tracker_record_execution():
    tracker = LatencyTracker()
    with patch.object(m.execution_latency_ms, "observe") as mock_obs:
        tracker.record_execution(47)
        mock_obs.assert_called_once_with(47)


def test_latency_tracker_ws_stats_empty():
    tracker = LatencyTracker()
    stats = tracker.ws_stats("binance")
    assert stats == {"p50": 0, "p95": 0, "p99": 0, "count": 0}


def test_latency_tracker_ws_stats_populated():
    tracker = LatencyTracker()
    for v in [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]:
        tracker._ws_buf.setdefault("binance", __import__("collections").deque(maxlen=1000))
        tracker._ws_buf["binance"].append((time.monotonic(), v))

    stats = tracker.ws_stats("binance", window_s=60.0)
    assert stats["count"] == 10
    assert stats["p50"] > 0
    assert stats["p99"] >= stats["p95"] >= stats["p50"]


def test_latency_tracker_measure_ws_context_manager():
    """Контекст-менеджер measure_ws должен вызвать record_ws."""
    tracker = LatencyTracker()
    recorded = []
    original = tracker.record_ws
    tracker.record_ws = lambda ex, us: recorded.append((ex, us))

    with tracker.measure_ws("binance"):
        pass  # симулируем работу

    assert len(recorded) == 1
    exchange, latency_us = recorded[0]
    assert exchange == "binance"
    assert latency_us >= 0


def test_latency_tracker_measure_rest_context_manager():
    tracker = LatencyTracker()
    recorded = []
    tracker.record_rest = lambda ex, ep, us: recorded.append((ex, ep, us))

    with tracker.measure_rest("bybit", "cancel_order"):
        pass

    assert len(recorded) == 1
    assert recorded[0][0] == "bybit"
    assert recorded[0][1] == "cancel_order"


def test_latency_tracker_execution_stats():
    tracker = LatencyTracker()
    for v in [10, 20, 30, 40, 50]:
        tracker._exec_buf.append((time.monotonic(), v))

    stats = tracker.execution_stats(window_s=60.0)
    assert stats["count"] == 5
    assert stats["p50"] == 30


# =========================================================
# PnLCollector
# =========================================================

def test_pnl_collector_sets_gauges():
    inv = _mock_inventory(pnl=100.0, peak=120.0, drawdown=1.5, exposure=500.0)

    with (
        patch.object(m.realized_pnl, "set") as mock_pnl,
        patch.object(m.peak_pnl, "set") as mock_peak,
        patch.object(m.drawdown_pct, "set") as mock_dd,
        patch.object(m.total_exposure, "set") as mock_exp,
    ):
        PnLCollector(inv).update()
        mock_pnl.assert_called_once_with(100.0)
        mock_peak.assert_called_once_with(120.0)
        mock_dd.assert_called_once_with(1.5)
        mock_exp.assert_called_once_with(500.0)


def test_pnl_collector_calls_snapshot():
    inv = _mock_inventory()
    PnLCollector(inv).update()
    inv.snapshot.assert_called_once()


# =========================================================
# SpreadCollector
# =========================================================

def test_spread_collector_on_scan():
    detector = MagicMock()
    with patch.object(m.spread_scans_total, "inc") as mock_inc:
        SpreadCollector(detector).on_scan()
        mock_inc.assert_called_once()


def test_spread_collector_on_opportunity():
    detector = MagicMock()
    sc = SpreadCollector(detector)

    with (
        patch.object(m.spread_opportunities_total.labels(symbol="BTCUSDT"), "inc") as mock_inc,
        patch.object(m.spread_bps_hist, "observe") as mock_obs,
    ):
        sc.on_opportunity("BTCUSDT", 12.5)
        mock_inc.assert_called_once()
        mock_obs.assert_called_once_with(12.5)


def test_spread_collector_on_trade_completed():
    detector = MagicMock()
    sc = SpreadCollector(detector)

    with (
        patch.object(m.trades_total.labels(state="completed"), "inc") as mock_inc,
        patch.object(m.execution_latency_ms, "observe") as mock_latency,
        patch.object(m.spread_bps_hist, "observe") as mock_spread,
    ):
        sc.on_trade("completed", 45, 10.5)
        mock_inc.assert_called_once()
        mock_latency.assert_called_once_with(45)
        mock_spread.assert_called_once_with(10.5)


def test_spread_collector_on_trade_failed_no_spread():
    """Для failed сделок spread_bps_hist не обновляется."""
    detector = MagicMock()
    sc = SpreadCollector(detector)

    with (
        patch.object(m.trades_total.labels(state="failed"), "inc"),
        patch.object(m.execution_latency_ms, "observe"),
        patch.object(m.spread_bps_hist, "observe") as mock_spread,
    ):
        sc.on_trade("failed", 120, 0.0)
        mock_spread.assert_not_called()


# =========================================================
# WatchdogCollector
# =========================================================

def test_watchdog_collector_sets_all_states():
    orch = _mock_orchestrator(running=5, failed=1)
    wc = WatchdogCollector(orch)

    calls = {}
    original_labels = m.workers_by_state.labels

    def capture_labels(state):
        mock = MagicMock()
        calls[state] = mock
        return mock

    with patch.object(m.workers_by_state, "labels", side_effect=capture_labels):
        wc.update()

    assert "running" in calls
    assert "failed" in calls
    calls["running"].set.assert_called_once_with(5)
    calls["failed"].set.assert_called_once_with(1)


def test_watchdog_collector_calls_status():
    orch = _mock_orchestrator()
    WatchdogCollector(orch).update()
    orch.status.assert_called_once()


# =========================================================
# ExchangeCollector
# =========================================================

def test_exchange_collector_stale_book():
    ob = _mock_ob_engine()
    ec = ExchangeCollector(ob)

    stale_calls = {}
    spread_calls = {}

    def capture_stale(exchange, symbol):
        mock = MagicMock()
        stale_calls[(exchange, symbol)] = mock
        return mock

    def capture_spread(exchange, symbol):
        mock = MagicMock()
        spread_calls[(exchange, symbol)] = mock
        return mock

    with (
        patch.object(m.orderbook_stale, "labels", side_effect=capture_stale),
        patch.object(m.orderbook_spread_bps, "labels", side_effect=capture_spread),
    ):
        ec.update()

    # binance:BTCUSDT — не устарел → stale=0, spread обновлён
    assert ("binance", "BTCUSDT") in stale_calls
    stale_calls[("binance", "BTCUSDT")].set.assert_called_once_with(0)
    spread_calls[("binance", "BTCUSDT")].set.assert_called_once_with(5.0)

    # bybit:ETHUSDT — устарел → stale=1, spread не обновляется
    assert ("bybit", "ETHUSDT") in stale_calls
    stale_calls[("bybit", "ETHUSDT")].set.assert_called_once_with(1)
    assert ("bybit", "ETHUSDT") not in spread_calls


def test_exchange_collector_ws_status():
    ec = ExchangeCollector(MagicMock())

    with patch.object(m.ws_connected, "labels") as mock_labels:
        mock_gauge = MagicMock()
        mock_labels.return_value = mock_gauge

        ec.set_ws_status("binance", connected=True)
        mock_labels.assert_called_once_with(exchange="binance")
        mock_gauge.set.assert_called_once_with(1)

    with patch.object(m.ws_connected, "labels") as mock_labels:
        mock_gauge = MagicMock()
        mock_labels.return_value = mock_gauge

        ec.set_ws_status("bybit", connected=False)
        mock_gauge.set.assert_called_once_with(0)


# =========================================================
# PrometheusExporter
# =========================================================

def test_exporter_generate_output_is_bytes():
    exporter = PrometheusExporter()
    output = exporter.generate_output()
    assert isinstance(output, bytes)
    assert len(output) > 0


def test_exporter_output_contains_metric_names():
    exporter = PrometheusExporter()
    output = exporter.generate_output().decode("utf-8")
    assert "arb_realized_pnl_usdt" in output
    assert "arb_drawdown_pct" in output
    assert "arb_ws_latency_us" in output
    assert "arb_workers_total" in output


def test_exporter_run_collectors_calls_update():
    inv = _mock_inventory()
    pnl_collector = PnLCollector(inv)

    with patch.object(pnl_collector, "update") as mock_update:
        exporter = PrometheusExporter(pnl_collector=pnl_collector)
        exporter._run_collectors()
        mock_update.assert_called_once()


def test_exporter_run_collectors_ignores_errors():
    """Ошибка в одном collector не должна ронять остальные."""
    bad = MagicMock()
    bad.update.side_effect = RuntimeError("collector сломан")

    good = MagicMock()
    good.update.return_value = None

    exporter = PrometheusExporter(
        pnl_collector=bad,
        watchdog_collector=good,
    )
    exporter._run_collectors()  # не должен бросить исключение
    good.update.assert_called_once()


def test_exporter_start_stop():
    """start()/stop() должны завершаться без ошибок."""
    async def _run():
        exporter = PrometheusExporter(port=19090)  # нестандартный порт чтобы не конфликтовать
        await exporter.start()
        await asyncio.sleep(0.05)
        await exporter.stop()

    asyncio.run(_run())
