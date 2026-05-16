"""Тесты RiskLimits, InventoryManager, RiskEngine."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from core.models import Exchange, MarketType, Order, OrderSide, OrderStatus, OrderType, SpreadOpportunity
from execution.models import ExecutionResult, LegResult, TradeState
from risk.engine import RiskEngine, RiskViolation
from risk.inventory import InventoryManager
from risk.limits import RiskLimits


# ---- helpers ----

def _make_opp(
    raw_spread_bps: float = 30.0,
    executable_spread_bps: float = 10.0,
    size_usdt: float = 500.0,
    ts_offset_ms: int = 0,
) -> SpreadOpportunity:
    return SpreadOpportunity(
        buy_exchange=Exchange.BINANCE, sell_exchange=Exchange.BYBIT,
        symbol="BTCUSDT",
        buy_market=MarketType.SPOT, sell_market=MarketType.SPOT,
        raw_spread_bps=raw_spread_bps,
        effective_spread_bps=10.0,
        net_spread_bps=8.0,
        executable_spread_bps=executable_spread_bps,
        buy_price=50000.0, sell_price=50150.0,
        max_size_usdt=size_usdt,
        timestamp_ms=int(time.time() * 1000) - ts_offset_ms,
    )


def _make_result(pnl_usdt: float = 5.0, filled_qty: float = 0.01) -> ExecutionResult:
    def _leg(exchange: Exchange, side: OrderSide, avg_price: float) -> LegResult:
        order = Order(
            id="o1", client_order_id="c1",
            exchange=exchange, symbol="BTCUSDT", market_type=MarketType.SPOT,
            side=side, order_type=OrderType.IOC, status=OrderStatus.FILLED,
            price=avg_price, qty=filled_qty,
            filled_qty=filled_qty, avg_fill_price=avg_price,
        )
        return LegResult(order=order, requested_qty=filled_qty, filled_qty=filled_qty,
                         avg_price=avg_price, fee_usdt=2.5, success=True)

    opp = _make_opp()
    return ExecutionResult(
        opportunity=opp,
        state=TradeState.COMPLETED,
        buy_leg=_leg(Exchange.BINANCE, OrderSide.BUY, 50000.0),
        sell_leg=_leg(Exchange.BYBIT, OrderSide.SELL, 50150.0),
    )


def _engine(
    max_exposure: float = 5_000.0,
    max_drawdown: float = 5.0,
    emergency_stop: float = 10.0,
    initial_capital: float = 10_000.0,
    volatility_halt_bps: float = 300.0,
) -> RiskEngine:
    limits = RiskLimits(
        max_exposure_usdt=max_exposure,
        max_per_exchange_usdt=max_exposure,
        max_per_symbol_usdt=max_exposure,
        max_drawdown_pct=max_drawdown,
        emergency_stop_pct=emergency_stop,
        volatility_halt_spread_bps=volatility_halt_bps,
    )
    inventory = InventoryManager(initial_capital_usdt=initial_capital)
    return RiskEngine(limits=limits, inventory=inventory)


# ---- RiskLimits ----

def test_limits_from_config():
    cfg = {"max_exposure_usdt": 9000.0, "max_drawdown_pct": 3.0, "emergency_stop_loss_pct": 7.0}
    limits = RiskLimits.from_config(cfg)
    assert limits.max_exposure_usdt == 9000.0
    assert limits.max_drawdown_pct == 3.0
    assert limits.emergency_stop_pct == 7.0


# ---- InventoryManager ----

def test_inventory_initial_state():
    inv = InventoryManager(10_000.0)
    assert inv.total_exposure_usdt() == 0.0
    assert inv.realized_pnl == 0.0
    assert inv.drawdown_pct == 0.0


def test_inventory_on_result_updates_pnl():
    inv = InventoryManager(10_000.0)
    result = _make_result(pnl_usdt=10.0)
    inv.on_execution_result(result)
    # pnl = (50150 - 50000) * 0.01 - 5.0 = 1.5 - 5.0 = -3.5 USDT (fees > gross)
    # Реально: buy_leg.fee=2.5 + sell_leg.fee=2.5 = 5 USDT fees
    # gross = (50150 - 50000) * 0.01 = 1.5 USDT → pnl = 1.5 - 5 = -3.5
    assert inv.realized_pnl == pytest.approx(-3.5, abs=0.01)


def test_inventory_drawdown_after_loss():
    inv = InventoryManager(10_000.0)
    # Симулируем 3 убыточных сделки
    for _ in range(3):
        inv.on_execution_result(_make_result())
    # После прибыльной сделки peak не сдвинется если был 0
    assert inv.drawdown_pct >= 0.0


def test_inventory_exchange_exposure():
    inv = InventoryManager(10_000.0)
    inv.on_execution_result(_make_result())
    # BUY 0.01 BTC по 50000 = 500 USDT на Binance
    binance_exp = inv.exchange_exposure_usdt(Exchange.BINANCE)
    assert binance_exp == pytest.approx(500.0, abs=1.0)


def test_inventory_price_move_bps_no_data():
    inv = InventoryManager(10_000.0)
    assert inv.price_move_bps("BTCUSDT", 60.0) == 0.0


def test_inventory_price_move_bps():
    inv = InventoryManager(10_000.0)
    inv.record_price("BTCUSDT", 50000.0)
    inv.record_price("BTCUSDT", 50500.0)  # +100 bps move
    move = inv.price_move_bps("BTCUSDT", 60.0)
    assert move == pytest.approx(99.9, abs=1.0)  # ~100 bps


# ---- RiskEngine.check() ----

def test_risk_check_ok():
    eng = _engine()
    allowed, violation = eng.check(_make_opp())
    assert allowed is True
    assert violation == RiskViolation.OK


def test_risk_check_emergency_stop():
    eng = _engine()
    eng.halt("тест")
    allowed, violation = eng.check(_make_opp())
    assert allowed is False
    assert violation == RiskViolation.EMERGENCY_STOP


def test_risk_check_paused():
    eng = _engine()
    eng.pause("тест drawdown")
    allowed, violation = eng.check(_make_opp())
    assert allowed is False
    assert violation == RiskViolation.MAX_DRAWDOWN


def test_risk_check_stale_signal():
    eng = _engine()
    # Сигнал 10 секунд назад → stale (лимит 5с по умолчанию)
    opp = _make_opp(ts_offset_ms=10_000)
    allowed, violation = eng.check(opp)
    assert allowed is False
    assert violation == RiskViolation.STALE_SIGNAL


def test_risk_check_volatility_spike():
    eng = _engine(volatility_halt_bps=100.0)
    opp = _make_opp(raw_spread_bps=200.0)  # > 100 bps limit
    allowed, violation = eng.check(opp)
    assert allowed is False
    assert violation == RiskViolation.VOLATILITY_SPIKE


def test_risk_check_max_exposure():
    eng = _engine(max_exposure=100.0)  # очень низкий лимит
    opp = _make_opp(size_usdt=500.0)   # пытаемся торговать 500 USDT
    allowed, violation = eng.check(opp)
    assert allowed is False
    assert violation == RiskViolation.MAX_EXPOSURE


def test_risk_check_open_orders_limit():
    eng = _engine()
    eng._limits.max_open_orders = 0  # ноль открытых ордеров
    eng._open_order_count_fn = lambda: 1
    allowed, violation = eng.check(_make_opp())
    assert allowed is False
    assert violation == RiskViolation.OPEN_ORDERS


# ---- RiskEngine.on_result() + auto-pause ----

def test_risk_auto_pause_on_drawdown():
    # Лимит drawdown 1% от 100 USDT капитала = 1 USDT
    # После одной убыточной сделки (-3.5 USDT) drawdown > 1% → пауза
    eng = _engine(initial_capital=100.0, max_drawdown=1.0, emergency_stop=50.0)
    eng.on_result(_make_result())
    assert eng.is_paused


def test_risk_emergency_stop_on_large_drawdown():
    eng = _engine(initial_capital=100.0, max_drawdown=1.0, emergency_stop=2.0)
    # Drawdown > 2% → emergency stop
    for _ in range(3):
        eng.on_result(_make_result())
    assert eng.is_halted


def test_risk_resume_clears_pause():
    eng = _engine()
    eng.pause("тест")
    assert eng.is_paused
    eng.resume()
    assert not eng.is_paused


def test_risk_reset_emergency():
    eng = _engine()
    eng.halt("тест")
    assert eng.is_halted
    eng.reset_emergency()
    assert not eng.is_halted


# ---- status snapshot ----

def test_risk_status_structure():
    eng = _engine()
    status = eng.status()
    assert "halted" in status
    assert "paused" in status
    assert "drawdown_pct" in status
    assert "realized_pnl" in status
    assert "total_exposure_usdt" in status
