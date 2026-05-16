"""MEXC WebSocket клиент: spot incremental orderbook."""
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


class MexcWsClient(BaseWsClient):
    """
    MEXC Spot WS: wss://wbs.mexc.com/ws

    Протокол:
    - Subscribe: {"method": "SUBSCRIPTION", "params": ["spot@public.increase.depth.v3.api@BTCUSDT"]}
    - Ping:      {"method": "PING"}
    - Depth msg: {"c": "spot@public.increase.depth.v3.api@BTCUSDT",
                  "d": {"asks": [...], "bids": [...], "e": 0|1}, "s": "BTCUSDT", "t": ts_ms}
                  e=0 → полный снимок, e=1 → дельта
    """

    WS_URL = "wss://wbs.mexc.com/ws"

    def __init__(self, proxy_url: str = "") -> None:
        super().__init__(
            url=self.WS_URL,
            ping_interval=15.0,
            additional_headers={"User-Agent": "Mozilla/5.0"},
            proxy_url=proxy_url,
        )
        self._subscriptions: set[str] = set()
        self._ob_handlers: list[Callable[[OrderBook], Coroutine[Any, Any, None]]] = []
        self._ticker_handlers: list[Callable[[Ticker], Coroutine[Any, Any, None]]] = []
        self._first_msg: set[str] = set()

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    def add_ticker_handler(self, fn: Callable[[Ticker], Coroutine[Any, Any, None]]) -> None:
        self._ticker_handlers.append(fn)

    async def subscribe_orderbook(self, symbol: str, depth: int = 20) -> None:
        # increase.depth заблокирован на MEXC — используем bookTicker (best bid/ask)
        topic = f"spot@public.bookTicker.v3.api@{symbol}"
        self._subscriptions.add(topic)
        if self.is_connected:
            await self._send_subscribe([topic])

    async def subscribe_ticker(self, symbol: str) -> None:
        topic = f"spot@public.bookTicker.v3.api@{symbol}"
        self._subscriptions.add(topic)
        if self.is_connected:
            await self._send_subscribe([topic])

    # ---- BaseWsClient hooks ----

    async def _on_connect(self, ws: websockets.WebSocketClientProtocol) -> None:
        self._first_msg.clear()
        subs = list(self._subscriptions)
        if not subs:
            logger.info("MEXC WS подключён", subs=0)
            return
        # MEXC принимает не более 5 топиков за раз, пауза между чанками обязательна
        for i in range(0, len(subs), 5):
            chunk = subs[i:i + 5]
            await ws.send(orjson.dumps({"method": "SUBSCRIPTION", "params": chunk}).decode())
            await asyncio.sleep(0.3)
        logger.info("MEXC WS подключён", subs=len(subs))

    async def _send_ping(self, ws: websockets.WebSocketClientProtocol) -> None:
        await ws.send(orjson.dumps({"method": "PING"}).decode())

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = orjson.loads(raw)
        except Exception:
            return

        # Pong и системные сообщения
        if msg.get("msg") == "PONG":
            return
        if "code" in msg:
            logger.warning("MEXC системное сообщение", code=msg.get("code"), msg=msg.get("msg"), raw=str(raw)[:200])
            return

        logger.warning("MEXC raw", raw=str(raw)[:300])

        channel: str = msg.get("c", "")
        data: dict = msg.get("d", {})
        symbol: str = msg.get("s", "")

        if not symbol or not data:
            return

        if "increase.depth" in channel:
            self._dispatch_orderbook(symbol, data, msg.get("t", int(time.time() * 1000)))
        elif "bookTicker" in channel:
            self._dispatch_bookticker(symbol, data, msg.get("t", int(time.time() * 1000)))

    # ---- внутреннее ----

    async def _send_subscribe(self, topics: list[str]) -> None:
        for i in range(0, len(topics), 5):
            chunk = topics[i:i + 5]
            await self.send_raw(orjson.dumps({"method": "SUBSCRIPTION", "params": chunk}).decode())
            if i + 5 < len(topics):
                await asyncio.sleep(0.3)

    def _dispatch_orderbook(self, symbol: str, data: dict, ts_ms: int) -> None:
        # Первое сообщение по символу всегда считаем снапшотом
        is_snapshot = symbol not in self._first_msg
        if is_snapshot:
            self._first_msg.add(symbol)

        raw_bids = data.get("bids", [])
        raw_asks = data.get("asks", [])

        # MEXC присылает либо [price, qty] либо {"p": price, "v": qty}
        bids = [Price(float(b[0]), float(b[1])) for b in raw_bids if float(b[1]) > 0]
        asks = [Price(float(a[0]), float(a[1])) for a in raw_asks if float(a[1]) > 0]

        book = OrderBook(
            exchange    = Exchange.MEXC,
            symbol      = symbol,
            market_type = MarketType.SPOT,
            bids        = bids,
            asks        = asks,
            timestamp_ms= ts_ms,
            sequence    = 0,
            is_snapshot = is_snapshot,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))

    def _dispatch_bookticker(self, symbol: str, data: dict, ts_ms: int) -> None:
        """bookTicker → синтетический одноуровневый стакан + тикер."""
        bid = float(data.get("b", 0) or data.get("bid", 0))
        ask = float(data.get("a", 0) or data.get("ask", 0))
        if bid <= 0 or ask <= 0:
            return

        # Синтетический стакан: один уровень на каждой стороне
        is_snapshot = symbol not in self._first_msg
        if is_snapshot:
            self._first_msg.add(symbol)

        book = OrderBook(
            exchange     = Exchange.MEXC,
            symbol       = symbol,
            market_type  = MarketType.SPOT,
            bids         = [Price(bid, 1.0)],
            asks         = [Price(ask, 1.0)],
            timestamp_ms = ts_ms,
            sequence     = 0,
            is_snapshot  = is_snapshot,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))

        ticker = Ticker(
            exchange     = Exchange.MEXC,
            symbol       = symbol,
            market_type  = MarketType.SPOT,
            bid          = bid,
            ask          = ask,
            last         = float(data.get("c", bid)),
            volume_24h   = 0.0,
            timestamp_ms = ts_ms,
        )
        for h in self._ticker_handlers:
            asyncio.create_task(h(ticker))

    def _dispatch_ticker(self, symbol: str, data: dict, ts_ms: int) -> None:
        bid = float(data.get("b", 0) or data.get("bid", 0))
        ask = float(data.get("a", 0) or data.get("ask", 0))
        if bid <= 0 or ask <= 0:
            return

        ticker = Ticker(
            exchange    = Exchange.MEXC,
            symbol      = symbol,
            market_type = MarketType.SPOT,
            bid         = bid,
            ask         = ask,
            last        = float(data.get("c", bid)),
            volume_24h  = 0.0,
            timestamp_ms= ts_ms,
        )
        for h in self._ticker_handlers:
            asyncio.create_task(h(ticker))
