"""Тесты execution layer: models, router, order_manager, engine."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import (
    Exchange, MarketType, Order, OrderSide, OrderStatus, OrderType,
    Price, SpreadOpportunity,
)
from execution.models import ExecutionResult, LegResult, TradeState
from execution.router import SmartOrderRouter
from orderbook.book import LocalOrderBook


# ---- helpers ----

def _make_order(status: OrderStatus, filled: float = 0.0, avg_price: float = 0.0) -> Order:
    return Order(
        id="ord1",
        client_order_id="clt1",
        exchange=Exchange.BYBIT,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        order_type=OrderType.IOC,
        status=status,
        price=50000.0,
        qty=0.1,
        filled_qty=filled,
        avg_fill_price=avg_price,
    )


def _make_book(exchange: Exchange, bids: list[tuple], asks: list[tuple]) -> LocalOrderBook:
    ob = LocalOrderBook(exchange, "BTCUSDT", MarketType.SPOT)
    ob.apply_snapshot(
        [Price(p, s) for p, s in bids],
        [Price(p, s) for p, s in asks],
        sequence=1, ts_ms=1_700_000_000_000,
    )
    return ob


def _make_opp(buy_ex=Exchange.BINANCE, sell_ex=Exchange.BYBIT) -> SpreadOpportunity:
    return SpreadOpportunity(
        buy_exchange=buy_ex, sell_exchange=sell_ex,
        symbol="BTCUSDT",
        buy_market=MarketType.SPOT, sell_market=MarketType.SPOT,
        raw_spread_bps=30.0, effective_spread_bps=10.0,
        net_spread_bps=8.0, executable_spread_bps=7.0,
        buy_price=50000.0, sell_price=50150.0,
        max_size_usdt=500.0,
        timestamp_ms=1_700_000_000_000,
    )


# ---- ExecutionResult ----

def test_execution_result_pnl():
    buy_leg = LegResult(
        order=_make_order(OrderStatus.FILLED, 0.1, 50000.0),
        requested_qty=0.1, filled_qty=0.1, avg_price=50000.0, fee_usdt=5.0, success=True,
    )
    sell_leg = LegResult(
        order=_make_order(OrderStatus.FILLED, 0.1, 50150.0),
        requested_qty=0.1, filled_qty=0.1, avg_price=50150.0, fee_usdt=5.0, success=True,
    )
    result = ExecutionResult(
        opportunity=_make_opp(),
        state=TradeState.COMPLETED,
        buy_leg=buy_leg, sell_leg=sell_leg,
    )
    # gross = (50150 - 50000) * 0.1 = 15 USDT, fees = 10 USDT → pnl = 5
    assert result.pnl_usdt == pytest.approx(5.0)
    assert result.success is True


def test_execution_result_realized_spread_bps():
    buy_leg = LegResult(
        order=_make_order(OrderStatus.FILLED), requested_qty=0.1,
        filled_qty=0.1, avg_price=50000.0, success=True,
    )
    sell_leg = LegResult(
        order=_make_order(OrderStatus.FILLED), requested_qty=0.1,
        filled_qty=0.1, avg_price=50100.0, success=True,
    )
    result = ExecutionResult(opportunity=_make_opp(), state=TradeState.COMPLETED,
                             buy_leg=buy_leg, sell_leg=sell_leg)
    # (50100 - 50000) / 50050 * 10000 ≈ 19.98 bps
    assert result.realized_spread_bps == pytest.approx(19.98, abs=0.1)


def test_leg_result_fill_ratio():
    leg = LegResult(
        order=_make_order(OrderStatus.PARTIALLY_FILLED, 0.05),
        requested_qty=0.1, filled_qty=0.05,
    )
    assert leg.fill_ratio == pytest.approx(0.5)
    assert not leg.is_fully_filled


def test_leg_result_fully_filled():
    leg = LegResult(
        order=_make_order(OrderStatus.FILLED, 0.1),
        requested_qty=0.1, filled_qty=0.1, success=True,
    )
    assert leg.is_fully_filled


# ---- SmartOrderRouter ----

def test_router_large_spread_uses_maker():
    router = SmartOrderRouter(maker_threshold_bps=20.0)
    book = _make_book(Exchange.BYBIT, bids=[(49990, 1.0)], asks=[(50010, 1.0)])
    decision = router.route_entry(book, OrderSide.BUY, executable_spread_bps=30.0)
    assert decision.order_type == OrderType.POST_ONLY
    assert decision.urgency == "maker"
    assert decision.price == 49990.0  # best bid для BUY post-only


def test_router_small_spread_uses_taker():
    router = SmartOrderRouter(maker_threshold_bps=20.0, price_buffer_bps=2.0)
    book = _make_book(Exchange.BYBIT, bids=[(49990, 1.0)], asks=[(50010, 1.0)])
    decision = router.route_entry(book, OrderSide.BUY, executable_spread_bps=10.0)
    assert decision.order_type == OrderType.IOC
    assert decision.urgency == "taker"
    # price = best_ask * (1 + buffer) = 50010 * 1.0002
    assert decision.price == pytest.approx(50010.0 * 1.0002, rel=0.001)


def test_router_hedge_always_taker():
    router = SmartOrderRouter()
    book = _make_book(Exchange.BYBIT, bids=[(50000, 1.0)], asks=[(50010, 1.0)])
    decision = router.route_hedge(book, OrderSide.SELL)
    assert decision.order_type == OrderType.IOC
    assert decision.urgency == "taker"


def test_router_emergency_market():
    router = SmartOrderRouter()
    book = _make_book(Exchange.BYBIT, bids=[(50000, 1.0)], asks=[(50010, 1.0)])
    decision = router.route_emergency(book, OrderSide.SELL)
    assert decision.order_type == OrderType.MARKET
    assert decision.price is None
    assert decision.urgency == "aggressive"


# ---- ExecutionEngine (mock-based) ----

@pytest.mark.asyncio
async def test_engine_both_legs_filled():
    from execution.engine import ExecutionEngine
    from orderbook.engine import OrderBookEngine
    from core.models import OrderBook

    ob_engine = OrderBookEngine()
    buy_msg = OrderBook(
        exchange=Exchange.BINANCE, symbol="BTCUSDT", market_type=MarketType.SPOT,
        bids=[Price(49990, 2.0)], asks=[Price(50000, 2.0)],
        timestamp_ms=1_700_000_000_000, is_snapshot=True,
    )
    sell_msg = OrderBook(
        exchange=Exchange.BYBIT, symbol="BTCUSDT", market_type=MarketType.SPOT,
        bids=[Price(50150, 2.0)], asks=[Price(50160, 1.0)],
        timestamp_ms=1_700_000_000_000, is_snapshot=True,
    )
    await ob_engine.handle(buy_msg)
    await ob_engine.handle(sell_msg)

    filled_order = _make_order(OrderStatus.FILLED, filled=0.01, avg_price=50000.0)
    filled_order.exchange = Exchange.BINANCE

    buy_ex = MagicMock()
    buy_ex.name = Exchange.BINANCE
    buy_ex.place_order = AsyncMock(return_value=filled_order)
    buy_ex.get_order = AsyncMock(return_value=filled_order)

    sell_order = _make_order(OrderStatus.FILLED, filled=0.01, avg_price=50150.0)
    sell_order.exchange = Exchange.BYBIT
    sell_ex = MagicMock()
    sell_ex.name = Exchange.BYBIT
    sell_ex.place_order = AsyncMock(return_value=sell_order)
    sell_ex.get_order = AsyncMock(return_value=sell_order)

    engine = ExecutionEngine(buy_ex, sell_ex, ob_engine, fill_timeout_ms=500)
    result = await engine.execute(_make_opp())

    assert result.state == TradeState.COMPLETED
    assert result.buy_leg is not None
    assert result.sell_leg is not None
    assert engine.stats["successful"] == 1


@pytest.mark.asyncio
async def test_engine_stale_book_returns_failed():
    from execution.engine import ExecutionEngine
    from orderbook.engine import OrderBookEngine
    import time

    ob_engine = OrderBookEngine()
    from core.models import OrderBook
    msg = OrderBook(
        exchange=Exchange.BINANCE, symbol="BTCUSDT", market_type=MarketType.SPOT,
        bids=[Price(49990, 2.0)], asks=[Price(50000, 2.0)],
        timestamp_ms=1_700_000_000_000, is_snapshot=True,
    )
    await ob_engine.handle(msg)
    book = ob_engine.get(Exchange.BINANCE, "BTCUSDT", MarketType.SPOT)
    book._last_update_s = time.monotonic() - 100.0  # stale

    buy_ex = MagicMock(); buy_ex.name = Exchange.BINANCE
    sell_ex = MagicMock(); sell_ex.name = Exchange.BYBIT

    engine = ExecutionEngine(buy_ex, sell_ex, ob_engine)
    result = await engine.execute(_make_opp())

    assert result.state == TradeState.FAILED
    assert "устарели" in (result.error or "")
