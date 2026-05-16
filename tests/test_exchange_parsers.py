"""Тесты парсинга WS сообщений Bybit и Binance без реального подключения."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

from core.models import Exchange, MarketType, OrderBook, Ticker
from exchanges.bybit.ws import BybitWsClient
from exchanges.binance.ws import BinanceWsClient


# ---- фикстуры ----

BYBIT_OB_SNAPSHOT = {
    "topic": "orderbook.50.BTCUSDT",
    "type": "snapshot",
    "ts": 1700000000000,
    "data": {
        "s": "BTCUSDT",
        "b": [["49990.00", "1.5"], ["49980.00", "2.0"]],
        "a": [["50010.00", "1.0"], ["50020.00", "3.0"]],
        "u": 12345,
        "checksum": 99999,
    },
}

BYBIT_TICKER = {
    "topic": "tickers.BTCUSDT",
    "ts": 1700000000000,
    "data": {
        "symbol": "BTCUSDT",
        "bid1Price": "49990.00",
        "ask1Price": "50010.00",
        "lastPrice": "50000.00",
        "volume24h": "1000000.00",
    },
}

BYBIT_PONG = {"op": "pong"}

BINANCE_OB = {
    "stream": "btcusdt@depth20@100ms",
    "data": {
        "lastUpdateId": 99999,
        "T": 1700000000000,
        "bids": [["49990.00", "1.5"], ["49980.00", "2.0"]],
        "asks": [["50010.00", "1.0"], ["50020.00", "3.0"]],
    },
}

BINANCE_BOOK_TICKER = {
    "stream": "btcusdt@bookTicker",
    "data": {
        "s": "BTCUSDT",
        "b": "49990.00",
        "a": "50010.00",
        "T": 1700000000000,
    },
}


# ---- helpers ----

async def _collect(ws_client, msg: dict, handler_field: str, count: int = 1) -> list:
    collected: list[Any] = []

    async def handler(item: Any) -> None:
        collected.append(item)

    getattr(ws_client, f"add_{handler_field}_handler")(handler)
    await ws_client._on_message(orjson.dumps(msg))

    # Даём create_task отработать
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return collected


# ---- Bybit тесты ----

@pytest.mark.asyncio
async def test_bybit_orderbook_parsed():
    client = BybitWsClient("wss://example.com", MarketType.SPOT)
    books = await _collect(client, BYBIT_OB_SNAPSHOT, "ob")

    assert len(books) == 1
    book: OrderBook = books[0]
    assert book.exchange == Exchange.BYBIT
    assert book.symbol == "BTCUSDT"
    assert book.market_type == MarketType.SPOT
    assert book.bids[0].price == 49990.0
    assert book.asks[0].price == 50010.0
    assert book.sequence == 12345
    assert book.checksum == 99999
    assert book.timestamp_ms == 1700000000000


@pytest.mark.asyncio
async def test_bybit_ticker_parsed():
    client = BybitWsClient("wss://example.com", MarketType.SPOT)
    tickers = await _collect(client, BYBIT_TICKER, "ticker")

    assert len(tickers) == 1
    t: Ticker = tickers[0]
    assert t.exchange == Exchange.BYBIT
    assert t.symbol == "BTCUSDT"
    assert t.bid == 49990.0
    assert t.ask == 50010.0
    assert t.last == 50000.0


@pytest.mark.asyncio
async def test_bybit_pong_ignored():
    client = BybitWsClient("wss://example.com", MarketType.SPOT)
    books: list[Any] = []
    client.add_ob_handler(lambda b: books.append(b) or asyncio.sleep(0))
    await client._on_message(orjson.dumps(BYBIT_PONG))
    await asyncio.sleep(0)
    assert len(books) == 0


@pytest.mark.asyncio
async def test_bybit_invalid_json_ignored():
    client = BybitWsClient("wss://example.com", MarketType.SPOT)
    # Не должен бросить исключение
    await client._on_message(b"not-json{{{")


# ---- Binance тесты ----

@pytest.mark.asyncio
async def test_binance_orderbook_parsed():
    client = BinanceWsClient("wss://example.com", MarketType.SPOT)
    books = await _collect(client, BINANCE_OB, "ob")

    assert len(books) == 1
    book: OrderBook = books[0]
    assert book.exchange == Exchange.BINANCE
    assert book.symbol == "BTCUSDT"
    assert book.market_type == MarketType.SPOT
    assert book.bids[0].price == 49990.0
    assert book.asks[0].size == 1.0
    assert book.sequence == 99999


@pytest.mark.asyncio
async def test_binance_ticker_parsed():
    client = BinanceWsClient("wss://example.com", MarketType.SPOT)
    tickers = await _collect(client, BINANCE_BOOK_TICKER, "ticker")

    assert len(tickers) == 1
    t: Ticker = tickers[0]
    assert t.exchange == Exchange.BINANCE
    assert t.symbol == "BTCUSDT"
    assert t.bid == 49990.0
    assert t.ask == 50010.0


@pytest.mark.asyncio
async def test_binance_stream_url_built():
    client = BinanceWsClient("wss://stream.testnet.binance.vision", MarketType.SPOT)
    await client.subscribe_orderbook("BTCUSDT", depth=20)
    await client.subscribe_ticker("ETHUSDT")

    url = client._build_url()
    assert "btcusdt@depth20@100ms" in url
    assert "ethusdt@bookTicker" in url
    assert "/stream?streams=" in url


def test_bybit_subscription_stored():
    client = BybitWsClient("wss://example.com", MarketType.SPOT)
    # Когда не подключён — только добавляем в set

    async def _run():
        await client.subscribe_orderbook("BTCUSDT", depth=50)
        await client.subscribe_ticker("ETHUSDT")
        assert "orderbook.50.BTCUSDT" in client._subscriptions
        assert "tickers.ETHUSDT" in client._subscriptions

    asyncio.run(_run())
