"""LatencyTracker: измерение и запись задержек в Prometheus + rolling buffer."""
from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager

from monitoring import metrics


class LatencyTracker:
    """
    Утилита измерения задержек.

    Использование:
        tracker = LatencyTracker()

        # Явная запись (если уже измерено):
        tracker.record_ws("binance", latency_us=350)
        tracker.record_rest("bybit", "place_order", latency_us=4200)
        tracker.record_execution(latency_ms=47)

        # Context manager (измеряет автоматически):
        with tracker.measure_ws("binance"):
            message = await ws.recv()

        with tracker.measure_rest("binance", "cancel_order"):
            await rest.cancel_order(...)
    """

    # Размер rolling window для локальной статистики (не Prometheus)
    _WINDOW = 1000

    def __init__(self) -> None:
        # Rolling buffers: exchange → deque[(ts, value_us)]
        self._ws_buf: dict[str, deque] = {}
        self._rest_buf: dict[str, deque] = {}
        self._exec_buf: deque = deque(maxlen=self._WINDOW)

    # ---- запись ----

    def record_ws(self, exchange: str, latency_us: int) -> None:
        metrics.ws_latency_us.labels(exchange=exchange).observe(latency_us)
        buf = self._ws_buf.setdefault(exchange, deque(maxlen=self._WINDOW))
        buf.append((time.monotonic(), latency_us))

    def record_rest(self, exchange: str, endpoint: str, latency_us: int) -> None:
        metrics.rest_latency_us.labels(exchange=exchange, endpoint=endpoint).observe(latency_us)
        buf = self._rest_buf.setdefault(exchange, deque(maxlen=self._WINDOW))
        buf.append((time.monotonic(), latency_us))

    def record_execution(self, latency_ms: int) -> None:
        metrics.execution_latency_ms.observe(latency_ms)
        self._exec_buf.append((time.monotonic(), latency_ms))

    # ---- context managers ----

    @contextmanager
    def measure_ws(self, exchange: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed_us = int((time.perf_counter() - t0) * 1_000_000)
            self.record_ws(exchange, elapsed_us)

    @contextmanager
    def measure_rest(self, exchange: str, endpoint: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed_us = int((time.perf_counter() - t0) * 1_000_000)
            self.record_rest(exchange, endpoint, elapsed_us)

    # ---- локальная статистика (без запроса к Prometheus) ----

    def ws_stats(self, exchange: str, window_s: float = 60.0) -> dict:
        """P50/P95/P99 WebSocket latency за последние window_s секунд."""
        return self._percentiles(self._ws_buf.get(exchange, deque()), window_s)

    def rest_stats(self, exchange: str, window_s: float = 60.0) -> dict:
        return self._percentiles(self._rest_buf.get(exchange, deque()), window_s)

    def execution_stats(self, window_s: float = 60.0) -> dict:
        return self._percentiles(self._exec_buf, window_s)

    @staticmethod
    def _percentiles(buf: deque, window_s: float) -> dict:
        cutoff = time.monotonic() - window_s
        values = sorted(v for ts, v in buf if ts >= cutoff)
        if not values:
            return {"p50": 0, "p95": 0, "p99": 0, "count": 0}
        n = len(values)
        return {
            "p50": values[int(n * 0.50)],
            "p95": values[int(n * 0.95)],
            "p99": values[min(int(n * 0.99), n - 1)],
            "count": n,
        }
