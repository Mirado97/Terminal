"""InventoryManager: трекинг позиций и PnL на основе результатов исполнения."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from core.models import Exchange
from execution.models import ExecutionResult


@dataclass
class SymbolExposure:
    """Открытая позиция по символу на бирже."""
    exchange: Exchange
    symbol: str
    qty: float = 0.0          # base currency (BTC, ETH...)
    cost_usdt: float = 0.0    # затраты на покупку / выручка от продажи
    side: str = "flat"        # "long" | "short" | "flat"

    @property
    def exposure_usdt(self) -> float:
        return abs(self.cost_usdt)


class InventoryManager:
    """
    Отслеживает позиции и реализованный PnL на основе ExecutionResult.

    Позиции строятся из наших собственных сделок (не из REST).
    Для production нужно сверять с реальными балансами биржи.

    PnL трекинг:
    - realized_pnl: накопленный с момента старта
    - peak_pnl:     максимум realized_pnl (для drawdown)
    - drawdown_pct: (peak - current) / initial_capital * 100
    """

    def __init__(self, initial_capital_usdt: float = 10_000.0) -> None:
        self._initial_capital = initial_capital_usdt
        self._realized_pnl: float = 0.0
        self._peak_pnl: float = 0.0

        # (exchange, symbol) → SymbolExposure
        self._positions: dict[tuple[Exchange, str], SymbolExposure] = {}

        # Последние N цен для volatility tracking (symbol → deque[(ts, mid_price)])
        self._price_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))

        self._trade_count: int = 0

    def on_execution_result(self, result: ExecutionResult) -> None:
        """Обновить позиции и PnL после завершения сделки."""
        if not result.buy_leg or not result.sell_leg:
            return
        if result.buy_leg.filled_qty == 0 and result.sell_leg.filled_qty == 0:
            return

        self._trade_count += 1
        pnl = result.pnl_usdt
        self._realized_pnl += pnl
        self._peak_pnl = max(self._peak_pnl, self._realized_pnl)

        # Обновляем позиции по обеим ногам
        opp = result.opportunity
        buy_qty = result.buy_leg.filled_qty
        sell_qty = result.sell_leg.filled_qty

        if buy_qty > 0:
            self._update_position(
                opp.buy_exchange, opp.symbol, +buy_qty,
                result.buy_leg.avg_price * buy_qty,
            )
        if sell_qty > 0:
            self._update_position(
                opp.sell_exchange, opp.symbol, -sell_qty,
                result.sell_leg.avg_price * sell_qty,
            )

    def record_price(self, symbol: str, mid_price: float) -> None:
        """Записать текущую цену для volatility tracking."""
        self._price_history[symbol].append((time.monotonic(), mid_price))

    def price_move_bps(self, symbol: str, window_s: float = 60.0) -> float:
        """Движение цены за последние window_s секунд в bps."""
        hist = self._price_history.get(symbol)
        if not hist or len(hist) < 2:
            return 0.0
        cutoff = time.monotonic() - window_s
        window = [(ts, p) for ts, p in hist if ts >= cutoff]
        if len(window) < 2:
            return 0.0
        prices = [p for _, p in window]
        lo, hi = min(prices), max(prices)
        mid = (lo + hi) / 2
        return (hi - lo) / mid * 10_000 if mid else 0.0

    # ---- exposure queries ----

    def total_exposure_usdt(self) -> float:
        return sum(p.exposure_usdt for p in self._positions.values())

    def exchange_exposure_usdt(self, exchange: Exchange) -> float:
        return sum(
            p.exposure_usdt for (ex, _), p in self._positions.items() if ex == exchange
        )

    def symbol_exposure_usdt(self, symbol: str) -> float:
        return sum(
            p.exposure_usdt for (_, sym), p in self._positions.items() if sym == symbol
        )

    def net_delta_usdt(self, symbol: str, price: float) -> float:
        """
        Суммарная нетто-позиция по символу в USDT.
        Для delta-neutral арбитража должна быть близка к 0.
        """
        net_qty = sum(
            p.qty for (_, sym), p in self._positions.items() if sym == symbol
        )
        return net_qty * price

    # ---- PnL ----

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def drawdown_pct(self) -> float:
        """Текущая просадка от пика в % от начального капитала."""
        drop = self._peak_pnl - self._realized_pnl
        if drop <= 0:
            return 0.0
        return drop / self._initial_capital * 100.0

    @property
    def total_pnl_pct(self) -> float:
        return self._realized_pnl / self._initial_capital * 100.0

    @property
    def trade_count(self) -> int:
        return self._trade_count

    def snapshot(self) -> dict:
        return {
            "realized_pnl": round(self._realized_pnl, 4),
            "peak_pnl": round(self._peak_pnl, 4),
            "drawdown_pct": round(self.drawdown_pct, 3),
            "total_exposure_usdt": round(self.total_exposure_usdt(), 2),
            "trade_count": self._trade_count,
            "positions": {
                f"{ex.value}:{sym}": {"qty": p.qty, "exposure": round(p.exposure_usdt, 2)}
                for (ex, sym), p in self._positions.items()
                if abs(p.qty) > 1e-8
            },
        }

    # ---- internal ----

    def _update_position(
        self, exchange: Exchange, symbol: str, delta_qty: float, cost_usdt: float
    ) -> None:
        key = (exchange, symbol)
        if key not in self._positions:
            self._positions[key] = SymbolExposure(exchange=exchange, symbol=symbol)
        pos = self._positions[key]
        pos.qty += delta_qty
        pos.cost_usdt += cost_usdt if delta_qty > 0 else -cost_usdt
        pos.side = "long" if pos.qty > 1e-8 else ("short" if pos.qty < -1e-8 else "flat")
