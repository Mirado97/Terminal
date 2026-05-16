"""Модели состояний и результатов исполнения арбитражной сделки."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.models import Order, SpreadOpportunity


class TradeState(str, Enum):
    PENDING    = "pending"     # инициализирован, не стартовал
    PLACING    = "placing"     # размещаем ордера
    WAITING    = "waiting"     # ждём исполнения
    HEDGING    = "hedging"     # нога 1 заполнена, хеджируем ногу 2
    RECOVERY   = "recovery"    # нога 2 провалилась, аварийный хедж
    COMPLETED  = "completed"   # обе ноги заполнены
    FAILED     = "failed"      # не удалось закрыть позицию


@dataclass
class LegResult:
    order: Order
    requested_qty: float
    filled_qty: float = 0.0
    avg_price: float = 0.0
    fee_usdt: float = 0.0
    success: bool = False

    @property
    def fill_ratio(self) -> float:
        return self.filled_qty / self.requested_qty if self.requested_qty else 0.0

    @property
    def is_fully_filled(self) -> bool:
        return self.fill_ratio >= 0.999


@dataclass
class ExecutionResult:
    opportunity: SpreadOpportunity
    state: TradeState
    buy_leg: LegResult | None = None
    sell_leg: LegResult | None = None
    execution_time_ms: int = 0
    error: str | None = None

    @property
    def pnl_usdt(self) -> float:
        if not self.buy_leg or not self.sell_leg:
            return 0.0
        qty = min(self.buy_leg.filled_qty, self.sell_leg.filled_qty)
        gross = (self.sell_leg.avg_price - self.buy_leg.avg_price) * qty
        fees = self.buy_leg.fee_usdt + self.sell_leg.fee_usdt
        return gross - fees

    @property
    def realized_spread_bps(self) -> float:
        if not self.buy_leg or not self.sell_leg:
            return 0.0
        if not self.buy_leg.avg_price or not self.sell_leg.avg_price:
            return 0.0
        mid = (self.buy_leg.avg_price + self.sell_leg.avg_price) / 2
        return (self.sell_leg.avg_price - self.buy_leg.avg_price) / mid * 10_000

    @property
    def success(self) -> bool:
        return self.state == TradeState.COMPLETED
