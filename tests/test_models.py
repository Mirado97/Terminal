"""Тесты типизированных моделей."""
from __future__ import annotations

import pytest
import msgspec

from core.models import (
    Balance, Exchange, FundingRate, MarketType,
    Order, OrderBook, OrderSide, OrderStatus, OrderType,
    PositionSide, Price, SpreadOpportunity, Ticker,
)


def test_price_frozen():
    p = Price(price=50000.0, size=0.1)
    with pytest.raises((AttributeError, TypeError)):
        p.price = 999.0  # type: ignore[misc]


def test_orderbook_json_roundtrip():
    book = OrderBook(
        exchange=Exchange.BYBIT,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        bids=[Price(49990.0, 1.5), Price(49980.0, 2.0)],
        asks=[Price(50010.0, 1.0), Price(50020.0, 3.0)],
        timestamp_ms=1_700_000_000_000,
    )
    raw = msgspec.json.encode(book)
    decoded = msgspec.json.decode(raw, type=OrderBook)
    assert decoded.exchange == Exchange.BYBIT
    assert decoded.bids[0].price == 49990.0
    assert decoded.asks[1].size == 3.0


def test_spread_opportunity_bps():
    opp = SpreadOpportunity(
        buy_exchange=Exchange.BINANCE,
        sell_exchange=Exchange.BYBIT,
        symbol="ETHUSDT",
        buy_market=MarketType.SPOT,
        sell_market=MarketType.PERPETUAL,
        raw_spread_bps=25.0,
        effective_spread_bps=15.0,
        net_spread_bps=10.0,
        executable_spread_bps=8.0,
        buy_price=3000.0,
        sell_price=3007.5,
        max_size_usdt=500.0,
        timestamp_ms=1_700_000_000_000,
    )
    assert opp.raw_spread_bps > opp.effective_spread_bps > opp.net_spread_bps
    assert opp.sell_price > opp.buy_price


def test_order_state_transitions():
    order = Order(
        id="ord_001",
        client_order_id="clt_001",
        exchange=Exchange.BYBIT,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        status=OrderStatus.PENDING,
        price=50000.0,
        qty=0.01,
    )
    order.status = OrderStatus.OPEN
    order.filled_qty = 0.005
    order.avg_fill_price = 50001.0
    assert order.status == OrderStatus.OPEN
    assert order.filled_qty == 0.005


def test_balance_json_roundtrip():
    bal = Balance(
        exchange=Exchange.BINANCE,
        currency="USDT",
        available=9500.0,
        locked=500.0,
        total=10000.0,
        updated_at_ms=1_700_000_000_000,
    )
    raw = msgspec.json.encode(bal)
    decoded = msgspec.json.decode(raw, type=Balance)
    assert decoded.total == 10000.0
    assert decoded.available + decoded.locked == decoded.total


def test_funding_rate():
    fr = FundingRate(
        exchange=Exchange.BYBIT,
        symbol="BTCUSDT",
        rate=0.0001,
        next_funding_ms=1_700_028_800_000,
        interval_hours=8,
        timestamp_ms=1_700_000_000_000,
    )
    assert fr.rate == 0.0001
    assert fr.interval_hours == 8


def test_ticker_spread():
    t = Ticker(
        exchange=Exchange.BYBIT,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        bid=49990.0,
        ask=50010.0,
        last=50000.0,
        volume_24h=1_000_000.0,
        timestamp_ms=1_700_000_000_000,
    )
    spread_bps = (t.ask - t.bid) / t.last * 10_000
    assert abs(spread_bps - 4.0) < 0.01
