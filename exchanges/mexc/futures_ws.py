"""MEXC Futures WebSocket: sub.depth → реальный стакан с объёмами."""
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


def _to_mexc(symbol: str) -> str:
    """BTCUSDT → BTC_USDT"""
    if symbol.endswith("USDT") and "_" not in symbol:
        return symbol[:-4] + "_USDT"
    return symbol


def _from_mexc(symbol: str) -> str:
    """BTC_USDT → BTCUSDT"""
    return symbol.replace("_", "")


class MexcFuturesWsClient(BaseWsClient):
    """
    MEXC Futures WS: wss://contract.mexc.com/edge
    Канал: sub.depth → push.depth / push.depth.full
    Даёт реальные объёмы на каждом уровне стакана.
    """

    WS_URL = "wss://contract.mexc.com/edge"

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

    def add_ticker_handler(self, fn: Any) -> None:
        pass  # depth-режим не даёт отдельный ticker

    async def subscribe_ticker(self, symbol: str) -> None:
        await self.subscribe_orderbook(symbol)

    async def subscribe_orderbook(self, symbol: str, depth: int = 5) -> None:
        if symbol not in self._symbols:
            self._symbols.append(symbol)

    # ---- BaseWsClient hooks ----

    async def _on_connect(self, ws) -> None:
        for sym in self._symbols:
            msg = orjson.dumps({"method": "sub.depth", "param": {"symbol": _to_mexc(sym)}})
            await ws.send(msg.decode())
            await asyncio.sleep(0.05)
        logger.info("MEXC Futures WS подключён (depth)", subs=len(self._symbols))

    async def _send_ping(self, ws) -> None:
        await ws.send(orjson.dumps({"method": "ping"}).decode())

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = orjson.loads(raw)
        except Exception:
            return

        channel = msg.get("channel", "")
        if channel not in ("push.depth", "push.depth.full"):
            return

        symbol_raw = msg.get("symbol", "")
        if not symbol_raw:
            return
        symbol = _from_mexc(symbol_raw)

        data   = msg.get("data", {})
        ts_ms  = msg.get("ts", int(time.time() * 1000))
        seq    = data.get("version", 0)

        try:
            bids = [
                Price(float(row[0]), float(row[1]))
                for row in data.get("bids", [])
                if float(row[1]) > 0
            ]
            asks = [
                Price(float(row[0]), float(row[1]))
                for row in data.get("asks", [])
                if float(row[1]) > 0
            ]
        except (ValueError, TypeError, IndexError):
            return

        if not bids or not asks:
            return

        book = OrderBook(
            exchange     = Exchange.MEXC,
            symbol       = symbol,
            market_type  = MarketType.PERPETUAL,
            bids         = bids,
            asks         = asks,
            timestamp_ms = ts_ms,
            sequence     = seq,
            is_snapshot  = True,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))
