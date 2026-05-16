"""Пороговые значения риск-менеджмента."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskLimits:
    # ---- exposure ----
    max_exposure_usdt: float = 5_000.0      # суммарная открытая позиция
    max_per_exchange_usdt: float = 3_000.0  # позиция на одной бирже
    max_per_symbol_usdt: float = 1_000.0    # позиция по одному символу

    # ---- drawdown ----
    max_drawdown_pct: float = 5.0           # % от пика → пауза
    emergency_stop_pct: float = 10.0        # % от начального капитала → полный стоп

    # ---- orders ----
    max_open_orders: int = 50

    # ---- volatility ----
    volatility_halt_spread_bps: float = 300.0  # raw spread > X bps → halt (bad tick / spike)
    volatility_window_s: float = 60.0          # окно измерения волатильности
    volatility_max_move_bps: float = 500.0     # макс ход цены за окно в bps

    # ---- spread anomaly ----
    max_spread_age_ms: int = 5_000          # возраст opportunity старше X ms → игнор

    @classmethod
    def from_config(cls, cfg: dict) -> "RiskLimits":
        return cls(
            max_exposure_usdt=cfg.get("max_exposure_usdt", 5_000.0),
            max_per_exchange_usdt=cfg.get("max_per_exchange_usdt", 3_000.0),
            max_per_symbol_usdt=cfg.get("max_per_symbol_usdt", 1_000.0),
            max_drawdown_pct=cfg.get("max_drawdown_pct", 5.0),
            emergency_stop_pct=cfg.get("emergency_stop_loss_pct", 10.0),
            max_open_orders=cfg.get("max_open_orders", 50),
        )
