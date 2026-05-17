"""Bitget Futures WS: books1 → best bid/ask.
URL: wss://ws.bitget.com/v2/ws/public
Символы в формате Bybit (BTCUSDT) — Bitget использует тот же формат.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from typing import Any

import orjson
import structlog

from core.models import Exchange, MarketType, OrderBook, Price
from exchanges.ws_client import BaseWsClient

logger = structlog.get_logger(__name__)

_BATCH = 50  # символов на одно subscribe сообщение


class BitgetFuturesWsClient(BaseWsClient):
    WS_URL = "wss://ws.bitget.com/v2/ws/public"

    def __init__(self) -> None:
        super().__init__(url=self.WS_URL, ping_interval=20.0)
        self._symbols: list[str] = []
        self._ob_handlers: list[Callable[[OrderBook], Coroutine[Any, Any, None]]] = []

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    async def subscribe_orderbook(self, symbol: str, depth: int = 1) -> None:
        if symbol not in self._symbols:
            self._symbols.append(symbol)

    async def _on_connect(self, ws) -> None:
        for i in range(0, len(self._symbols), _BATCH):
            batch = self._symbols[i:i + _BATCH]
            args = [
                {"instType": "USDT-FUTURES", "channel": "books1", "instId": sym}
                for sym in batch
            ]
            await ws.send(orjson.dumps({"op": "subscribe", "args": args}).decode())
            await asyncio.sleep(0.1)
        logger.info("Bitget Futures WS подключён", subs=len(self._symbols))

    async def _send_ping(self, ws) -> None:
        await ws.send("ping")

    async def _on_message(self, raw: str | bytes) -> None:
        if raw == "pong" or raw == b"pong":
            return
        try:
            msg = orjson.loads(raw)
        except Exception:
            return

        action = msg.get("action")
        if action not in ("snapshot", "update"):
            return
        arg = msg.get("arg", {})
        if arg.get("channel") != "books1":
            return

        symbol = arg.get("instId", "")
        data   = msg.get("data", [{}])[0]
        ts_ms  = int(data.get("ts") or int(time.time() * 1000))

        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if not bids or not asks:
            return

        bid = float(bids[0][0])
        ask = float(asks[0][0])
        if bid <= 0 or ask <= 0:
            return

        bid_vol = float(bids[0][1]) if len(bids[0]) > 1 else 1e9
        ask_vol = float(asks[0][1]) if len(asks[0]) > 1 else 1e9

        book = OrderBook(
            exchange     = Exchange.BITGET,
            symbol       = symbol,
            market_type  = MarketType.PERPETUAL,
            bids         = [Price(bid, bid_vol or 1e9)],
            asks         = [Price(ask, ask_vol or 1e9)],
            timestamp_ms = ts_ms,
            sequence     = 0,
            is_snapshot  = True,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))
