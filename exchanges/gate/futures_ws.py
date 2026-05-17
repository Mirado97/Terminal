"""Gate.io Futures WS: book_ticker → bid/ask.
URL: wss://fx-ws.gateio.ws/v4/ws/usdt
Символы принимаются в Bybit-формате (BTCUSDT), эмитируются тоже в нём.
"""
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


def _to_gate(symbol: str) -> str:
    """BTCUSDT → BTC_USDT"""
    return symbol[:-4] + "_USDT" if symbol.endswith("USDT") else symbol


def _to_bybit(gate_sym: str) -> str:
    """BTC_USDT → BTCUSDT"""
    return gate_sym.replace("_", "")


class GateFuturesWsClient(BaseWsClient):
    WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"

    def __init__(self) -> None:
        super().__init__(url=self.WS_URL, ping_interval=20.0)
        self._symbols: list[str] = []  # Bybit-формат
        self._ob_handlers: list[Callable[[OrderBook], Coroutine[Any, Any, None]]] = []

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    async def subscribe_orderbook(self, symbol: str, depth: int = 5) -> None:
        if symbol not in self._symbols:
            self._symbols.append(symbol)

    async def _on_connect(self, ws) -> None:
        gate_syms = [_to_gate(s) for s in self._symbols]
        msg = orjson.dumps({
            "time":    int(time.time()),
            "channel": "futures.book_ticker",
            "event":   "subscribe",
            "payload": gate_syms,
        })
        await ws.send(msg.decode())
        logger.info("Gate.io Futures WS подключён", subs=len(gate_syms))

    async def _send_ping(self, ws) -> None:
        await ws.send(orjson.dumps({
            "time":    int(time.time()),
            "channel": "futures.ping",
        }).decode())

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = orjson.loads(raw)
        except Exception:
            return

        if msg.get("channel") != "futures.book_ticker" or msg.get("event") != "update":
            return

        result = msg.get("result", {})
        gate_sym = result.get("s", "")
        if not gate_sym:
            return

        symbol = _to_bybit(gate_sym)
        bid = float(result.get("b") or 0)
        ask = float(result.get("a") or 0)
        if bid <= 0 or ask <= 0:
            return

        bid_vol = float(result.get("B") or 0) or 1e9
        ask_vol = float(result.get("A") or 0) or 1e9
        ts_ms   = int(result.get("t") or int(time.time() * 1000))

        book = OrderBook(
            exchange     = Exchange.GATE,
            symbol       = symbol,
            market_type  = MarketType.PERPETUAL,
            bids         = [Price(bid, bid_vol)],
            asks         = [Price(ask, ask_vol)],
            timestamp_ms = ts_ms,
            sequence     = int(result.get("u") or 0),
            is_snapshot  = True,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))
