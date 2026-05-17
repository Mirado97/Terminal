"""MEXC Spot WebSocket: bookTicker → bid/ask."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any

import orjson
import structlog

from core.models import Exchange, MarketType, OrderBook, Price
from exchanges.ws_client import BaseWsClient

logger = structlog.get_logger(__name__)


class MexcSpotWsClient(BaseWsClient):
    WS_URL = "wss://wbs.mexc.com/ws"

    def __init__(self) -> None:
        super().__init__(
            url=self.WS_URL,
            ping_interval=15.0,
            additional_headers={"User-Agent": "Mozilla/5.0"},
        )
        self._symbols: list[str] = []
        self._ob_handlers: list[Callable[[OrderBook], Coroutine[Any, Any, None]]] = []

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    async def subscribe_orderbook(self, symbol: str, depth: int = 5) -> None:
        if symbol not in self._symbols:
            self._symbols.append(symbol)

    async def _on_connect(self, ws) -> None:
        params = [f"spot@public.bookTicker.v3.api@{sym}" for sym in self._symbols]
        for i in range(0, len(params), 30):
            msg = orjson.dumps({"method": "SUBSCRIPTION", "params": params[i:i + 30]})
            await ws.send(msg.decode())
            await asyncio.sleep(0.1)
        logger.info("MEXC Spot WS подключён", subs=len(self._symbols))

    async def _send_ping(self, ws) -> None:
        await ws.send(orjson.dumps({"method": "PING"}).decode())

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = orjson.loads(raw)
        except Exception:
            return

        channel = msg.get("c", "")
        if "bookTicker" not in channel:
            return

        data = msg.get("d", {})
        symbol = data.get("s", "")
        if not symbol:
            return

        ts_ms = msg.get("t", int(time.time() * 1000))
        if ts_ms < 1e12:
            ts_ms = int(ts_ms * 1000)

        try:
            bid = float(data.get("b", 0) or 0)
            ask = float(data.get("a", 0) or 0)
            bid_vol = float(data.get("B", 0) or 0) or 1e9
            ask_vol = float(data.get("A", 0) or 0) or 1e9
        except (ValueError, TypeError):
            return

        if bid <= 0 or ask <= 0:
            return

        book = OrderBook(
            exchange     = Exchange.MEXC,
            symbol       = symbol,
            market_type  = MarketType.SPOT,
            bids         = [Price(bid, bid_vol)],
            asks         = [Price(ask, ask_vol)],
            timestamp_ms = ts_ms,
            sequence     = 0,
            is_snapshot  = True,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))
