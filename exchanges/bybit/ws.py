"""Bybit V5 WebSocket клиент: подписки на orderbook и ticker."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import orjson
import structlog
import websockets

from core.models import Exchange, MarketType, OrderBook, Price, Ticker
from exchanges.ws_client import BaseWsClient

logger = structlog.get_logger(__name__)


class BybitWsClient(BaseWsClient):
    """
    Один WS клиент = один Bybit endpoint (spot | linear | inverse).
    market_type инжектируется снаружи — соответствует URL endpoint'а.
    """

    def __init__(self, url: str, market_type: MarketType) -> None:
        super().__init__(url=url, ping_interval=20.0)
        self._market_type = market_type
        self._subscriptions: set[str] = set()
        self._ob_handlers: list[Callable[[OrderBook], Coroutine[Any, Any, None]]] = []
        self._ticker_handlers: list[Callable[[Ticker], Coroutine[Any, Any, None]]] = []

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    def add_ticker_handler(self, fn: Callable[[Ticker], Coroutine[Any, Any, None]]) -> None:
        self._ticker_handlers.append(fn)

    async def subscribe_orderbook(self, symbol: str, depth: int = 50) -> None:
        topic = f"orderbook.{depth}.{symbol}"
        self._subscriptions.add(topic)
        if self.is_connected:
            await self.send_raw(orjson.dumps({"op": "subscribe", "args": [topic]}).decode())

    async def subscribe_ticker(self, symbol: str) -> None:
        topic = f"tickers.{symbol}"
        self._subscriptions.add(topic)
        if self.is_connected:
            await self.send_raw(orjson.dumps({"op": "subscribe", "args": [topic]}).decode())

    # ---- BaseWsClient hooks ----

    async def _on_connect(self, ws: websockets.WebSocketClientProtocol) -> None:
        subs = list(self._subscriptions)
        if not subs:
            return
        # Bybit: максимум 10 топиков на одно сообщение
        for i in range(0, len(subs), 10):
            chunk = subs[i:i + 10]
            await ws.send(orjson.dumps({"op": "subscribe", "args": chunk}).decode())
        logger.info("Bybit WS подписки отправлены", count=len(subs))

    async def _send_ping(self, ws: websockets.WebSocketClientProtocol) -> None:
        await ws.send(orjson.dumps({"op": "ping"}).decode())

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = orjson.loads(raw)
        except Exception:
            return

        topic: str = msg.get("topic", "")
        if not topic:
            return

        if topic.startswith("orderbook."):
            self._dispatch_orderbook(msg)
        elif topic.startswith("tickers."):
            self._dispatch_ticker(msg)

    def _dispatch_orderbook(self, msg: dict) -> None:
        data = msg.get("data", {})
        symbol: str = data.get("s", "")
        if not symbol:
            return

        bids = [Price(float(p[0]), float(p[1])) for p in data.get("b", [])]
        asks = [Price(float(p[0]), float(p[1])) for p in data.get("a", [])]

        book = OrderBook(
            exchange=Exchange.BYBIT,
            symbol=symbol,
            market_type=self._market_type,
            bids=bids,
            asks=asks,
            timestamp_ms=msg.get("ts", 0),
            sequence=data.get("u", 0),
            checksum=data.get("checksum", 0),
            is_snapshot=(msg.get("type") == "snapshot"),
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))

    def _dispatch_ticker(self, msg: dict) -> None:
        data = msg.get("data", {})
        symbol: str = data.get("symbol", "")
        bid_str = data.get("bid1Price", "")
        ask_str = data.get("ask1Price", "")
        if not symbol or not bid_str or not ask_str:
            return

        ticker = Ticker(
            exchange=Exchange.BYBIT,
            symbol=symbol,
            market_type=self._market_type,
            bid=float(bid_str),
            ask=float(ask_str),
            last=float(data.get("lastPrice", 0)),
            volume_24h=float(data.get("volume24h", 0)),
            timestamp_ms=msg.get("ts", 0),
        )
        for h in self._ticker_handlers:
            asyncio.create_task(h(ticker))
