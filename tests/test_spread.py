"""Тесты FeeTable, SpreadCalculator, SpreadDetector."""
from __future__ import annotations

import asyncio

import pytest

from core.models import Exchange, MarketType, Price, SpreadOpportunity
from orderbook.book import LocalOrderBook
from orderbook.engine import OrderBookEngine
from spread.calculator import SpreadCalculator
from spread.detector import SpreadDetector
from spread.fees import FeeSchedule, FeeTable


# ---- helpers ----

def _make_book(
    exchange: Exchange,
    symbol: str,
    market_type: MarketType,
    bids: list[tuple],
    asks: list[tuple],
) -> LocalOrderBook:
    ob = LocalOrderBook(exchange, symbol, market_type)
    ob.apply_snapshot(
        [Price(p, s) for p, s in bids],
        [Price(p, s) for p, s in asks],
        sequence=1,
        ts_ms=1_700_000_000_000,
    )
    return ob


# ---- FeeTable ----

def test_fee_table_default_bybit_spot():
    ft = FeeTable()
    assert ft.maker_bps(Exchange.BYBIT, MarketType.SPOT) == 10.0
    assert ft.taker_bps(Exchange.BYBIT, MarketType.SPOT) == 10.0


def test_fee_table_bybit_perpetual():
    ft = FeeTable()
    assert ft.maker_bps(Exchange.BYBIT, MarketType.PERPETUAL) == 2.0
    assert ft.taker_bps(Exchange.BYBIT, MarketType.PERPETUAL) == 5.5


def test_fee_table_round_trip():
    ft = FeeTable()
    # Bybit spot taker + Binance spot taker = 10 + 10 = 20 bps
    total = ft.round_trip_taker_bps(
        Exchange.BYBIT, MarketType.SPOT,
        Exchange.BINANCE, MarketType.SPOT,
    )
    assert total == pytest.approx(20.0)


def test_fee_table_override():
    ft = FeeTable(overrides={
        (Exchange.BYBIT, MarketType.SPOT): FeeSchedule(maker_bps=7.0, taker_bps=7.0),
    })
    assert ft.taker_bps(Exchange.BYBIT, MarketType.SPOT) == 7.0


def test_fee_table_unknown_exchange_fallback():
    ft = FeeTable()
    # BITGET FUTURES не задан — должен вернуть fallback 10/10
    result = ft.get(Exchange.BITGET, MarketType.FUTURES)
    assert result.taker_bps == 10.0


# ---- SpreadCalculator ----

def test_calculator_positive_spread():
    """Binance ask < Bybit bid → есть возможность купить дешевле продать дороже."""
    buy_book = _make_book(
        Exchange.BINANCE, "BTCUSDT", MarketType.SPOT,
        bids=[(49980, 1.0)],
        asks=[(50000, 2.0)],   # покупаем по 50000
    )
    sell_book = _make_book(
        Exchange.BYBIT, "BTCUSDT", MarketType.SPOT,
        bids=[(50100, 2.0)],   # продаём по 50100
        asks=[(50110, 1.0)],
    )
    calc = SpreadCalculator(FeeTable(), latency_us=1_000)
    result = calc.compute(buy_book, sell_book, max_size_usdt=1_000.0)

    assert result is not None
    assert result.buy_price == 50000.0
    assert result.sell_price == 50100.0
    # raw spread = (50100 - 50000) / 50050 * 10000 ≈ 19.98 bps
    assert result.raw_spread_bps == pytest.approx(19.98, abs=0.1)
    # effective = raw - 20bps fees = ~0 bps
    assert result.effective_spread_bps == pytest.approx(-0.02, abs=0.5)


def test_calculator_spread_components():
    """Проверяем цепочку: raw → effective → net → executable."""
    buy_book = _make_book(
        Exchange.BYBIT, "ETHUSDT", MarketType.PERPETUAL,
        bids=[(2990, 5.0)],
        asks=[(3000, 5.0)],   # покупаем по 3000
    )
    sell_book = _make_book(
        Exchange.BINANCE, "ETHUSDT", MarketType.PERPETUAL,
        bids=[(3050, 5.0)],   # продаём по 3050
        asks=[(3060, 1.0)],
    )
    calc = SpreadCalculator(FeeTable(), latency_us=2_000, drift_bps_per_ms=0.1)
    result = calc.compute(buy_book, sell_book, max_size_usdt=5_000.0)

    assert result is not None
    # raw ≈ (3050 - 3000) / 3025 * 10000 ≈ 165 bps
    assert result.raw_spread_bps > 100.0
    # effective = raw - fees(perp bybit + perp binance) = raw - 5.5 - 4.0
    assert result.effective_spread_bps == pytest.approx(result.raw_spread_bps - 9.5, abs=0.5)
    # net ≤ effective (slippage ≥ 0)
    assert result.net_spread_bps <= result.effective_spread_bps
    # executable ≤ net
    assert result.executable_spread_bps <= result.net_spread_bps
    # latency cost = 2ms * 0.1 bps/ms = 0.2 bps
    assert result.latency_cost_bps == pytest.approx(0.2, abs=0.01)


def test_calculator_returns_none_if_stale():
    buy_book = _make_book(Exchange.BYBIT, "BTCUSDT", MarketType.SPOT,
                          bids=[(49990, 1.0)], asks=[(50000, 1.0)])
    sell_book = _make_book(Exchange.BINANCE, "BTCUSDT", MarketType.SPOT,
                           bids=[(50100, 1.0)], asks=[(50110, 1.0)])
    # Помечаем как stale
    import time
    buy_book._last_update_s = time.monotonic() - 100.0

    calc = SpreadCalculator(FeeTable())
    assert calc.compute(buy_book, sell_book, 1_000.0) is None


def test_calculator_returns_none_insufficient_liquidity():
    buy_book = _make_book(Exchange.BYBIT, "BTCUSDT", MarketType.SPOT,
                          bids=[(49990, 0.001)], asks=[(50000, 0.001)])
    sell_book = _make_book(Exchange.BINANCE, "BTCUSDT", MarketType.SPOT,
                           bids=[(50100, 0.001)], asks=[(50110, 0.001)])
    calc = SpreadCalculator(FeeTable(), min_qty_usdt=100.0)
    # 0.001 BTC * 50000 = 50 USDT < min_qty_usdt=100
    result = calc.compute(buy_book, sell_book, 1_000.0)
    assert result is None


def test_calculator_size_limited_by_available():
    """Объём ограничивается доступным размером на best уровне."""
    buy_book = _make_book(Exchange.BYBIT, "BTCUSDT", MarketType.SPOT,
                          bids=[(49990, 1.0)], asks=[(50000, 0.1)])  # только 0.1 BTC на ask
    sell_book = _make_book(Exchange.BINANCE, "BTCUSDT", MarketType.SPOT,
                           bids=[(50100, 5.0)], asks=[(50110, 1.0)])
    calc = SpreadCalculator(FeeTable())
    result = calc.compute(buy_book, sell_book, max_size_usdt=10_000.0)
    assert result is not None
    assert result.qty_base <= 0.1


# ---- SpreadDetector ----

@pytest.mark.asyncio
async def test_detector_finds_opportunity():
    engine = OrderBookEngine()
    calc = SpreadCalculator(FeeTable(), latency_us=1_000)
    detector = SpreadDetector(
        engine, calc,
        min_executable_spread_bps=1.0,
        min_size_usdt=10.0,
        max_position_usdt=5_000.0,
    )

    opportunities: list[SpreadOpportunity] = []
    detector.on_opportunity(lambda opp: opportunities.append(opp) or asyncio.sleep(0))

    # Загружаем стаканы с заметным спредом
    from core.models import OrderBook
    bybit_msg = OrderBook(
        exchange=Exchange.BYBIT, symbol="BTCUSDT", market_type=MarketType.SPOT,
        bids=[Price(50200, 2.0)], asks=[Price(50210, 2.0)],
        timestamp_ms=1_700_000_000_000, is_snapshot=True,
    )
    binance_msg = OrderBook(
        exchange=Exchange.BINANCE, symbol="BTCUSDT", market_type=MarketType.SPOT,
        bids=[Price(49990, 2.0)], asks=[Price(50000, 2.0)],
        timestamp_ms=1_700_000_000_000, is_snapshot=True,
    )
    await engine.handle(bybit_msg)
    await engine.handle(binance_msg)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Bybit bid=50200 > Binance ask=50000 → должна найтись возможность
    assert len(opportunities) > 0
    opp = opportunities[0]
    assert opp.symbol == "BTCUSDT"
    assert opp.buy_exchange == Exchange.BINANCE
    assert opp.sell_exchange == Exchange.BYBIT


@pytest.mark.asyncio
async def test_detector_filters_anomalous_spread():
    engine = OrderBookEngine()
    calc = SpreadCalculator(FeeTable())
    detector = SpreadDetector(
        engine, calc,
        min_executable_spread_bps=1.0,
        max_raw_spread_bps=50.0,   # фильтруем > 50 bps
        max_position_usdt=5_000.0,
    )
    opportunities: list[SpreadOpportunity] = []
    detector.on_opportunity(lambda opp: opportunities.append(opp) or asyncio.sleep(0))

    from core.models import OrderBook
    # Создаём аномальный спред (1000 bps) — должен быть отфильтрован
    msg1 = OrderBook(
        exchange=Exchange.BYBIT, symbol="BTCUSDT", market_type=MarketType.SPOT,
        bids=[Price(55000, 2.0)], asks=[Price(55010, 2.0)],
        timestamp_ms=1_700_000_000_000, is_snapshot=True,
    )
    msg2 = OrderBook(
        exchange=Exchange.BINANCE, symbol="BTCUSDT", market_type=MarketType.SPOT,
        bids=[Price(50000, 2.0)], asks=[Price(50010, 2.0)],
        timestamp_ms=1_700_000_000_000, is_snapshot=True,
    )
    await engine.handle(msg1)
    await engine.handle(msg2)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(opportunities) == 0
    assert detector.stats.opportunities_filtered > 0


@pytest.mark.asyncio
async def test_detector_stats_updated():
    engine = OrderBookEngine()
    calc = SpreadCalculator(FeeTable())
    detector = SpreadDetector(engine, calc, min_executable_spread_bps=999.0)

    from core.models import OrderBook
    msg = OrderBook(
        exchange=Exchange.BYBIT, symbol="BTCUSDT", market_type=MarketType.SPOT,
        bids=[Price(50000, 1.0)], asks=[Price(50010, 1.0)],
        timestamp_ms=1_700_000_000_000, is_snapshot=True,
    )
    await engine.handle(msg)
    await asyncio.sleep(0)

    assert detector.stats.scans >= 1
