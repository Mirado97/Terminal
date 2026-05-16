"""Центральный реестр всех Prometheus метрик.

Используется изолированный CollectorRegistry (не глобальный REGISTRY),
чтобы не смешиваться с системными метриками process/python и упростить тесты.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

# ---- latency buckets ----
_US_BUCKETS = (100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000)
_MS_BUCKETS = (1, 5, 10, 25, 50, 100, 250, 500, 1_000, 2_000, 5_000)

# =========================================================
# PnL / Trading
# =========================================================
realized_pnl = Gauge(
    "arb_realized_pnl_usdt",
    "Накопленный реализованный PnL (USDT)",
    registry=REGISTRY,
)
peak_pnl = Gauge(
    "arb_peak_pnl_usdt",
    "Пиковый реализованный PnL (USDT)",
    registry=REGISTRY,
)
drawdown_pct = Gauge(
    "arb_drawdown_pct",
    "Текущая просадка от пика (%)",
    registry=REGISTRY,
)
total_exposure = Gauge(
    "arb_exposure_usdt",
    "Суммарная открытая позиция (USDT)",
    registry=REGISTRY,
)
trades_total = Counter(
    "arb_trades_total",
    "Всего завершённых сделок",
    ["state"],          # completed | failed
    registry=REGISTRY,
)

# =========================================================
# Spread
# =========================================================
spread_bps_hist = Histogram(
    "arb_spread_bps",
    "Распределение исполненных спредов (bps)",
    buckets=(1, 2, 5, 10, 15, 20, 30, 50, 100, 200),
    registry=REGISTRY,
)
spread_scans_total = Counter(
    "arb_spread_scans_total",
    "Количество сканирований спреда",
    registry=REGISTRY,
)
spread_opportunities_total = Counter(
    "arb_spread_opportunities_total",
    "Найдено arbitrage opportunities",
    ["symbol"],
    registry=REGISTRY,
)

# =========================================================
# Latency
# =========================================================
ws_latency_us = Histogram(
    "arb_ws_latency_us",
    "WebSocket message latency (мкс)",
    ["exchange"],
    buckets=_US_BUCKETS,
    registry=REGISTRY,
)
rest_latency_us = Histogram(
    "arb_rest_latency_us",
    "REST API latency (мкс)",
    ["exchange", "endpoint"],
    buckets=_US_BUCKETS,
    registry=REGISTRY,
)
execution_latency_ms = Histogram(
    "arb_execution_latency_ms",
    "Время полного исполнения арбитражной сделки (мс)",
    buckets=_MS_BUCKETS,
    registry=REGISTRY,
)

# =========================================================
# Watchdog
# =========================================================
workers_by_state = Gauge(
    "arb_workers_total",
    "Количество воркеров по состоянию",
    ["state"],          # running | failed | disabled | stopped | starting
    registry=REGISTRY,
)
worker_restarts_total = Counter(
    "arb_worker_restarts_total",
    "Количество перезапусков воркера",
    ["symbol"],
    registry=REGISTRY,
)

# =========================================================
# Exchange health
# =========================================================
ws_connected = Gauge(
    "arb_ws_connected",
    "1 если WebSocket соединение активно",
    ["exchange"],
    registry=REGISTRY,
)
orderbook_stale = Gauge(
    "arb_orderbook_stale",
    "1 если стакан устарел (>10s без обновления)",
    ["exchange", "symbol"],
    registry=REGISTRY,
)
orderbook_spread_bps = Gauge(
    "arb_orderbook_spread_bps",
    "Текущий bid-ask спред в стакане (bps)",
    ["exchange", "symbol"],
    registry=REGISTRY,
)
