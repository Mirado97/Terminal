"""Binance WS клиент: bookTicker — реальное время, обновление на каждый тик."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any

import orjson
import structlog
import websockets

from core.models import Exchange, MarketType, OrderBook, Price, Ticker
from exchanges.ws_client import BaseWsClient

logger = structlog.get_logger(__name__)

_WS_URL = "wss://stream.binance.com:9443/ws"


class BinanceWsClient(BaseWsClient):
    """
    Binance Spot WS: bookTicker — лучший bid/ask на каждое изменение стакана.

    Subscribe: {"method": "SUBSCRIBE", "params": ["btcusdt@bookTicker"], "id": 1}
    Message:   {"u": updateId, "s": "BTCUSDT", "b": bid, "B": bidQty, "a": ask, "A": askQty}
    Ping:      Binance шлёт WebSocket ping-фреймы, библиотека отвечает pong автоматически.
    """

    def __init__(self) -> None:
        super().__init__(url=_WS_URL, ping_interval=30.0)
        self._subscriptions: set[str] = set()
        self._ob_handlers: list[Callable[[OrderBook], Coroutine[Any, Any, None]]] = []
        self._ticker_handlers: list[Callable[[Ticker], Coroutine[Any, Any, None]]] = []
        self._req_id = 0

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    def add_ticker_handler(self, fn: Callable[[Ticker], Coroutine[Any, Any, None]]) -> None:
        self._ticker_handlers.append(fn)

    async def subscribe_orderbook(self, symbol: str, depth: int = 5) -> None:
        stream = f"{symbol.lower()}@bookTicker"
        self._subscriptions.add(stream)
        if self.is_connected:
            await self._send_subscribe([stream])

    async def subscribe_ticker(self, symbol: str) -> None:
        await self.subscribe_orderbook(symbol)

    # ---- BaseWsClient hooks ----

    async def _on_connect(self, ws: websockets.WebSocketClientProtocol) -> None:
        subs = list(self._subscriptions)
        if not subs:
            return
        # Binance: до 200 потоков на соединение, шлём чанками по 100
        for i in range(0, len(subs), 100):
            self._req_id += 1
            chunk = subs[i:i + 100]
            await ws.send(orjson.dumps({
                "method": "SUBSCRIBE",
                "params": chunk,
                "id": self._req_id,
            }).decode())
        logger.info("Binance WS подключён", subs=len(subs))

    async def _send_ping(self, ws: websockets.WebSocketClientProtocol) -> None:
        self._req_id += 1
        await ws.send(orjson.dumps({"method": "ping", "id": self._req_id}).decode())

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = orjson.loads(raw)
        except Exception:
            return

        # Ответы на SUBSCRIBE / системные сообщения
        if "result" in msg or ("id" in msg and "s" not in msg):
            return

        symbol: str = msg.get("s", "")
        bid_str = msg.get("b", "")
        ask_str = msg.get("a", "")
        if not symbol or not bid_str or not ask_str:
            return

        bid = float(bid_str)
        ask = float(ask_str)
        bid_qty = float(msg.get("B", 1) or 1)
        ask_qty = float(msg.get("A", 1) or 1)

        if bid <= 0 or ask <= 0:
            return

        ts_ms = int(time.time() * 1000)

        book = OrderBook(
            exchange     = Exchange.BINANCE,
            symbol       = symbol,
            market_type  = MarketType.SPOT,
            bids         = [Price(bid, bid_qty)],
            asks         = [Price(ask, ask_qty)],
            timestamp_ms = ts_ms,
            sequence     = msg.get("u", 0),
            is_snapshot  = True,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))

        ticker = Ticker(
            exchange     = Exchange.BINANCE,
            symbol       = symbol,
            market_type  = MarketType.SPOT,
            bid          = bid,
            ask          = ask,
            last         = bid,
            volume_24h   = 0.0,
            timestamp_ms = ts_ms,
        )
        for h in self._ticker_handlers:
            asyncio.create_task(h(ticker))

    async def _send_subscribe(self, streams: list[str]) -> None:
        self._req_id += 1
        await self.send_raw(orjson.dumps({
            "method": "SUBSCRIBE",
            "params": streams,
            "id": self._req_id,
        }).decode())
