"""MEXC WebSocket клиент: protobuf aggre bookTicker (wbs-api.mexc.com)."""
from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import structlog

from core.models import Exchange, MarketType, OrderBook, Price, Ticker
from exchanges.ws_client import BaseWsClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "proto"))
from PushDataV3ApiWrapper_pb2 import PushDataV3ApiWrapper  # noqa: E402

logger = structlog.get_logger(__name__)


class MexcWsClient(BaseWsClient):
    """
    MEXC Spot WS: wss://wbs-api.mexc.com/ws
    Канал: spot@public.aggre.bookTicker.v3.api.pb@10ms@{SYMBOL}
    Сообщения: binary protobuf (PushDataV3ApiWrapper)
    """

    WS_URL = "wss://wbs-api.mexc.com/ws"
    BATCH_SIZE = 10

    def __init__(self, proxy_url: str = "") -> None:
        super().__init__(
            url=self.WS_URL,
            ping_interval=20.0,
            additional_headers={
                "Origin": "https://www.mexc.com",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            proxy_url=proxy_url,
        )
        self._subscriptions: list[str] = []
        self._ob_handlers: list[Callable[[OrderBook], Coroutine[Any, Any, None]]] = []
        self._ticker_handlers: list[Callable[[Ticker], Coroutine[Any, Any, None]]] = []

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    def add_ticker_handler(self, fn: Callable[[Ticker], Coroutine[Any, Any, None]]) -> None:
        self._ticker_handlers.append(fn)

    async def subscribe_orderbook(self, symbol: str, depth: int = 20) -> None:
        topic = f"spot@public.aggre.bookTicker.v3.api.pb@10ms@{symbol}"
        if topic not in self._subscriptions:
            self._subscriptions.append(topic)

    async def subscribe_ticker(self, symbol: str) -> None:
        await self.subscribe_orderbook(symbol)

    # ---- BaseWsClient hooks ----

    async def _on_connect(self, ws) -> None:
        import orjson
        for i in range(0, len(self._subscriptions), self.BATCH_SIZE):
            batch = self._subscriptions[i: i + self.BATCH_SIZE]
            await ws.send(orjson.dumps({"method": "SUBSCRIPTION", "params": batch}).decode())
            await asyncio.sleep(0.1)
        logger.info("MEXC WS подключён", subs=len(self._subscriptions))

    async def _send_ping(self, ws) -> None:
        import orjson
        await ws.send(orjson.dumps({"method": "PING"}).decode())

    async def _on_message(self, raw: str | bytes) -> None:
        if isinstance(raw, str):
            return  # PONG и системные — игнорируем

        try:
            wrapper = PushDataV3ApiWrapper.FromString(raw)
        except Exception as e:
            logger.warning("MEXC protobuf parse error", error=str(e))
            return

        if wrapper.WhichOneof("body") != "publicAggreBookTicker":
            return

        t = wrapper.publicAggreBookTicker
        try:
            bid = float(t.bidPrice)
            ask = float(t.askPrice)
        except (ValueError, AttributeError):
            return

        if bid <= 0 or ask <= 0:
            return

        symbol = wrapper.symbol
        ts_ms = wrapper.sendTime

        book = OrderBook(
            exchange     = Exchange.MEXC,
            symbol       = symbol,
            market_type  = MarketType.SPOT,
            bids         = [Price(bid, float(t.bidQuantity))],
            asks         = [Price(ask, float(t.askQuantity))],
            timestamp_ms = ts_ms,
            sequence     = 0,
            is_snapshot  = True,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))

        ticker = Ticker(
            exchange     = Exchange.MEXC,
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
