"""Тесты LocalOrderBook и OrderBookEngine."""
from __future__ import annotations

import asyncio
import time

import pytest

from core.models import Exchange, MarketType, OrderBook, Price
from orderbook.book import LocalOrderBook
from orderbook.engine import OrderBookEngine


# ---- helpers ----

def _book(bids: list[tuple], asks: list[tuple]) -> LocalOrderBook:
    ob = LocalOrderBook(Exchange.BYBIT, "BTCUSDT", MarketType.SPOT)
    ob.apply_snapshot(
        [Price(p, s) for p, s in bids],
        [Price(p, s) for p, s in asks],
        sequence=1,
        ts_ms=1_700_000_000_000,
    )
    return ob


def _ob_msg(bids, asks, is_snapshot=True, sequence=1, checksum=0) -> OrderBook:
    return OrderBook(
        exchange=Exchange.BYBIT,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        bids=[Price(p, s) for p, s in bids],
        asks=[Price(p, s) for p, s in asks],
        timestamp_ms=1_700_000_000_000,
        sequence=sequence,
        checksum=checksum,
        is_snapshot=is_snapshot,
    )


# ---- LocalOrderBook: базовые свойства ----

def test_best_bid_ask():
    ob = _book(
        bids=[(49990, 1.0), (49980, 2.0), (49970, 0.5)],
        asks=[(50010, 1.0), (50020, 3.0)],
    )
    assert ob.best_bid == 49990.0
    assert ob.best_ask == 50010.0


def test_mid_price():
    ob = _book(bids=[(49990, 1.0)], asks=[(50010, 1.0)])
    assert ob.mid_price == pytest.approx(50000.0)


def test_spread_bps():
    ob = _book(bids=[(49990, 1.0)], asks=[(50010, 1.0)])
    # spread = 20, mid = 50000, spread_bps = 20/50000*10000 = 4bps
    assert ob.spread_bps == pytest.approx(4.0, rel=0.01)


def test_empty_book():
    ob = LocalOrderBook(Exchange.BYBIT, "BTCUSDT", MarketType.SPOT)
    assert ob.best_bid == 0.0
    assert ob.best_ask == 0.0
    assert ob.mid_price == 0.0
    assert not ob.is_synced


# ---- apply_delta ----

def test_delta_update_size():
    ob = _book(bids=[(50000, 1.0), (49990, 2.0)], asks=[(50010, 1.0)])
    ob.apply_delta(
        bids=[Price(50000, 1.5)],  # обновляем размер
        asks=[],
        sequence=2,
        ts_ms=1_700_000_001_000,
    )
    assert ob._bids[50000.0] == 1.5


def test_delta_remove_level():
    ob = _book(bids=[(50000, 1.0), (49990, 2.0)], asks=[(50010, 1.0)])
    ob.apply_delta(
        bids=[Price(50000, 0.0)],  # size=0 → удаляем
        asks=[],
        sequence=2,
        ts_ms=1_700_000_001_000,
    )
    assert 50000.0 not in ob._bids
    assert ob.best_bid == 49990.0


def test_delta_add_new_level():
    ob = _book(bids=[(49990, 1.0)], asks=[(50010, 1.0)])
    ob.apply_delta(
        bids=[Price(50000, 0.5)],  # новый уровень выше текущего best bid
        asks=[],
        sequence=2,
        ts_ms=0,
    )
    assert ob.best_bid == 50000.0


# ---- snapshot replaces all ----

def test_snapshot_replaces_book():
    ob = _book(bids=[(49990, 1.0), (49980, 2.0)], asks=[(50010, 1.0)])
    ob.apply_snapshot(
        bids=[Price(50100, 5.0)],
        asks=[Price(50200, 3.0)],
        sequence=99,
        ts_ms=0,
    )
    assert len(ob._bids) == 1
    assert ob.best_bid == 50100.0


# ---- invalidate ----

def test_invalidate():
    ob = _book(bids=[(50000, 1.0)], asks=[(50010, 1.0)])
    assert ob.is_synced
    ob.invalidate()
    assert not ob.is_synced
    assert len(ob._bids) == 0


# ---- top_bids / top_asks ----

def test_top_bids_sorted_desc():
    ob = _book(
        bids=[(49980, 1.0), (50000, 2.0), (49990, 1.5)],
        asks=[(50010, 1.0)],
    )
    top = ob.top_bids(3)
    assert [p.price for p in top] == [50000.0, 49990.0, 49980.0]


def test_top_asks_sorted_asc():
    ob = _book(
        bids=[(49990, 1.0)],
        asks=[(50030, 1.0), (50010, 2.0), (50020, 1.5)],
    )
    top = ob.top_asks(3)
    assert [p.price for p in top] == [50010.0, 50020.0, 50030.0]


# ---- slippage ----

def test_slippage_buy_exact():
    ob = _book(
        bids=[(49990, 1.0)],
        asks=[(50010, 0.5), (50020, 1.0)],
    )
    # Покупаем 0.5 — всё по 50010
    price = ob.slippage_for_qty(0.5, "buy")
    assert price == pytest.approx(50010.0)


def test_slippage_buy_across_levels():
    ob = _book(
        bids=[(49990, 1.0)],
        asks=[(50010, 1.0), (50020, 1.0)],
    )
    # Покупаем 1.5 BTC: 1.0 по 50010 + 0.5 по 50020
    price = ob.slippage_for_qty(1.5, "buy")
    expected = (1.0 * 50010 + 0.5 * 50020) / 1.5
    assert price == pytest.approx(expected)


def test_slippage_insufficient_liquidity():
    ob = _book(bids=[(49990, 0.1)], asks=[(50010, 0.1)])
    # Хотим купить 10 BTC, есть только 0.1
    assert ob.slippage_for_qty(10.0, "buy") == 0.0


# ---- imbalance ----

def test_imbalance_balanced():
    ob = _book(
        bids=[(49990, 1.0)],
        asks=[(50010, 1.0)],
    )
    assert ob.imbalance(depth=1) == pytest.approx(0.5)


def test_imbalance_bid_heavy():
    ob = _book(
        bids=[(49990, 3.0)],
        asks=[(50010, 1.0)],
    )
    assert ob.imbalance(depth=1) == pytest.approx(0.75)


# ---- OrderBookEngine ----

@pytest.mark.asyncio
async def test_engine_snapshot():
    engine = OrderBookEngine()
    collected: list[LocalOrderBook] = []

    async def handler(b: LocalOrderBook) -> None:
        collected.append(b)

    engine.on_update(handler)
    await engine.handle(_ob_msg(
        bids=[(49990, 1.0)], asks=[(50010, 1.0)], is_snapshot=True,
    ))
    await asyncio.sleep(0)

    assert len(collected) == 1
    assert collected[0].is_synced
    assert collected[0].best_bid == 49990.0


@pytest.mark.asyncio
async def test_engine_delta_after_snapshot():
    engine = OrderBookEngine()

    await engine.handle(_ob_msg([(49990, 1.0)], [(50010, 1.0)], is_snapshot=True, sequence=1))
    await engine.handle(_ob_msg([(50000, 0.5)], [], is_snapshot=False, sequence=2))
    await asyncio.sleep(0)

    book = engine.get(Exchange.BYBIT, "BTCUSDT", MarketType.SPOT)
    assert book is not None
    assert book.best_bid == 50000.0


@pytest.mark.asyncio
async def test_engine_delta_before_snapshot_ignored():
    engine = OrderBookEngine()

    # Дельта без предшествующего snapshot — должна быть проигнорирована
    await engine.handle(_ob_msg([(50000, 1.0)], [], is_snapshot=False, sequence=1))
    await asyncio.sleep(0)

    book = engine.get(Exchange.BYBIT, "BTCUSDT", MarketType.SPOT)
    assert book is not None
    assert not book.is_synced


@pytest.mark.asyncio
async def test_engine_multiple_symbols():
    engine = OrderBookEngine()

    msg_btc = _ob_msg([(49990, 1.0)], [(50010, 1.0)], is_snapshot=True)
    msg_eth = OrderBook(
        exchange=Exchange.BYBIT, symbol="ETHUSDT", market_type=MarketType.SPOT,
        bids=[Price(2990.0, 5.0)], asks=[Price(3010.0, 3.0)],
        timestamp_ms=1_700_000_000_000, is_snapshot=True,
    )
    await engine.handle(msg_btc)
    await engine.handle(msg_eth)

    assert len(engine.all_books()) == 2
    eth = engine.get(Exchange.BYBIT, "ETHUSDT", MarketType.SPOT)
    assert eth is not None
    assert eth.best_bid == 2990.0


@pytest.mark.asyncio
async def test_engine_stale_books():
    engine = OrderBookEngine()
    await engine.handle(_ob_msg([(49990, 1.0)], [(50010, 1.0)], is_snapshot=True))

    book = engine.get(Exchange.BYBIT, "BTCUSDT", MarketType.SPOT)
    # Имитируем устаревание
    book._last_update_s = time.monotonic() - 100.0
    assert len(engine.stale_books()) == 1
