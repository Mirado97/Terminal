"""RiskEngine: pre-trade gate + post-trade update + emergency stop."""
from __future__ import annotations

import time
from enum import Enum

import structlog

from core.models import Exchange, SpreadOpportunity
from execution.models import ExecutionResult
from risk.inventory import InventoryManager
from risk.limits import RiskLimits

logger = structlog.get_logger(__name__)


class RiskViolation(str, Enum):
    OK               = "ok"
    EMERGENCY_STOP   = "emergency_stop"
    MAX_EXPOSURE     = "max_exposure"
    EXCHANGE_LIMIT   = "exchange_limit"
    SYMBOL_LIMIT     = "symbol_limit"
    MAX_DRAWDOWN     = "max_drawdown"
    VOLATILITY_SPIKE = "volatility_spike"
    STALE_SIGNAL     = "stale_signal"
    OPEN_ORDERS      = "open_orders"


class RiskEngine:
    """
    Центральный контроллер рисков.

    Pre-trade:  check(opportunity) → (allowed: bool, violation: RiskViolation)
    Post-trade: on_result(result) → обновляет inventory, проверяет drawdown
    Emergency:  halt() → полный стоп, требует ручного сброса

    Circuit breaker:
    - при drawdown > max_drawdown_pct → автопауза (can_resume after reset)
    - при drawdown > emergency_stop_pct → полный halt (требует ручного сброса)
    """

    def __init__(
        self,
        limits: RiskLimits,
        inventory: InventoryManager,
        open_order_count_fn=None,  # callable() → int, для счётчика ордеров
    ) -> None:
        self._limits = limits
        self._inventory = inventory
        self._open_order_count_fn = open_order_count_fn or (lambda: 0)

        self._emergency_stopped: bool = False
        self._paused: bool = False
        self._pause_reason: str = ""
        self._violations: list[dict] = []

    # ---- pre-trade gate ----

    def check(
        self,
        opp: SpreadOpportunity,
        size_usdt: float | None = None,
    ) -> tuple[bool, RiskViolation]:
        """
        Проверить можно ли исполнить opportunity.
        Возвращает (allowed, violation_type).
        """
        if self._emergency_stopped:
            return False, RiskViolation.EMERGENCY_STOP

        if self._paused:
            return False, RiskViolation.MAX_DRAWDOWN

        # Возраст сигнала
        age_ms = int(time.time() * 1000) - opp.timestamp_ms
        if age_ms > self._limits.max_spread_age_ms:
            return False, RiskViolation.STALE_SIGNAL

        # Волатильность (raw spread аномалия)
        if opp.raw_spread_bps > self._limits.volatility_halt_spread_bps:
            self._record_violation(RiskViolation.VOLATILITY_SPIKE, opp.symbol, opp.raw_spread_bps)
            return False, RiskViolation.VOLATILITY_SPIKE

        # Ценовой drift
        for symbol in [opp.symbol]:
            move = self._inventory.price_move_bps(symbol, self._limits.volatility_window_s)
            if move > self._limits.volatility_max_move_bps:
                self._record_violation(RiskViolation.VOLATILITY_SPIKE, symbol, move)
                return False, RiskViolation.VOLATILITY_SPIKE

        # Количество открытых ордеров
        if self._open_order_count_fn() >= self._limits.max_open_orders:
            return False, RiskViolation.OPEN_ORDERS

        trade_size = size_usdt or opp.max_size_usdt

        # Суммарный экспозур
        if self._inventory.total_exposure_usdt() + trade_size > self._limits.max_exposure_usdt:
            return False, RiskViolation.MAX_EXPOSURE

        # Экспозур по биржам
        for ex in (opp.buy_exchange, opp.sell_exchange):
            if self._inventory.exchange_exposure_usdt(ex) + trade_size > self._limits.max_per_exchange_usdt:
                return False, RiskViolation.EXCHANGE_LIMIT

        # Экспозур по символу
        if self._inventory.symbol_exposure_usdt(opp.symbol) + trade_size > self._limits.max_per_symbol_usdt:
            return False, RiskViolation.SYMBOL_LIMIT

        return True, RiskViolation.OK

    # ---- post-trade update ----

    def on_result(self, result: ExecutionResult) -> None:
        """Обновить состояние после исполнения сделки."""
        self._inventory.on_execution_result(result)
        self._check_drawdown()

    def record_mid_price(self, symbol: str, mid_price: float) -> None:
        """Обновить ценовую историю (вызывается из OrderBookEngine)."""
        self._inventory.record_price(symbol, mid_price)

    # ---- emergency controls ----

    def halt(self, reason: str = "manual") -> None:
        """Полный экстренный стоп. Требует ручного reset()."""
        self._emergency_stopped = True
        logger.critical("EMERGENCY STOP активирован", reason=reason)

    def pause(self, reason: str) -> None:
        """Временная пауза (например при drawdown). Можно сбросить через resume()."""
        self._paused = True
        self._pause_reason = reason
        logger.warning("Торговля приостановлена", reason=reason)

    def resume(self) -> None:
        """Снять паузу (не снимает emergency stop)."""
        self._paused = False
        self._pause_reason = ""
        logger.info("Торговля возобновлена")

    def reset_emergency(self) -> None:
        """Ручной сброс emergency stop после проверки."""
        self._emergency_stopped = False
        logger.warning("Emergency stop сброшен вручную")

    # ---- state ----

    @property
    def is_halted(self) -> bool:
        return self._emergency_stopped

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_trading_allowed(self) -> bool:
        return not self._emergency_stopped and not self._paused

    def status(self) -> dict:
        inv = self._inventory.snapshot()
        return {
            "halted": self._emergency_stopped,
            "paused": self._paused,
            "pause_reason": self._pause_reason,
            "drawdown_pct": inv["drawdown_pct"],
            "realized_pnl": inv["realized_pnl"],
            "total_exposure_usdt": inv["total_exposure_usdt"],
            "trade_count": inv["trade_count"],
            "violations": len(self._violations),
        }

    # ---- internal ----

    def _check_drawdown(self) -> None:
        dd = self._inventory.drawdown_pct
        if dd >= self._limits.emergency_stop_pct:
            self.halt(reason=f"drawdown {dd:.2f}% >= emergency limit {self._limits.emergency_stop_pct}%")
        elif dd >= self._limits.max_drawdown_pct and not self._paused:
            self.pause(reason=f"drawdown {dd:.2f}% >= max {self._limits.max_drawdown_pct}%")

    def _record_violation(self, violation: RiskViolation, symbol: str, value: float) -> None:
        self._violations.append({
            "type": violation.value,
            "symbol": symbol,
            "value": value,
            "ts": int(time.time() * 1000),
        })
        logger.warning("Risk violation", type=violation.value, symbol=symbol, value=value)
