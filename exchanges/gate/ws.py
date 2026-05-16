"""Gate.io V4 WebSocket клиент: spot.book_ticker — реальное время."""
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

_WS_URL = "wss://api.gateio.ws/ws/v4/"


class GateWsClient(BaseWsClient):
    """
    Gate.io Spot WS: spot.book_ticker — лучший bid/ask в реальном времени.

    Символы Gate.io: BTC_USDT (подчёркивание). Внутри системы: BTCUSDT.
    Subscribe: {"time": ts, "channel": "spot.book_ticker", "event": "subscribe", "payload": ["BTC_USDT"]}
    Message:   {"channel": "spot.book_ticker", "event": "update",
                 "result": {"s": "BTC_USDT", "b": bid, "B": bidQty, "a": ask, "A": askQty, "u": seq}}
    Ping:      {"time": ts, "channel": "spot.ping", "event": ""}
    """

    def __init__(self) -> None:
        super().__init__(url=_WS_URL, ping_interval=20.0)
        self._gate_subs: set[str] = set()        # в формате BTC_USDT
        self._symbol_map: dict[str, str] = {}    # BTC_USDT → BTCUSDT
        self._ob_handlers: list[Callable[[OrderBook], Coroutine[Any, Any, None]]] = []
        self._ticker_handlers: list[Callable[[Ticker], Coroutine[Any, Any, None]]] = []

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    def add_ticker_handler(self, fn: Callable[[Ticker], Coroutine[Any, Any, None]]) -> None:
        self._ticker_handlers.append(fn)

    async def subscribe_orderbook(self, symbol: str, depth: int = 5) -> None:
        """symbol — внутренний формат BTCUSDT."""
        gate_sym = _to_gate(symbol)
        self._gate_subs.add(gate_sym)
        self._symbol_map[gate_sym] = symbol
        if self.is_connected:
            await self._send_subscribe([gate_sym])

    async def subscribe_ticker(self, symbol: str) -> None:
        await self.subscribe_orderbook(symbol)

    # ---- BaseWsClient hooks ----

    async def _on_connect(self, ws: websockets.WebSocketClientProtocol) -> None:
        subs = list(self._gate_subs)
        if not subs:
            return
        # Gate.io: нет жёсткого лимита, шлём чанками по 50
        for i in range(0, len(subs), 50):
            chunk = subs[i:i + 50]
            await ws.send(orjson.dumps({
                "time":    int(time.time()),
                "channel": "spot.book_ticker",
                "event":   "subscribe",
                "payload": chunk,
            }).decode())
        logger.info("Gate.io WS подключён", subs=len(subs))

    async def _send_ping(self, ws: websockets.WebSocketClientProtocol) -> None:
        await ws.send(orjson.dumps({
            "time":    int(time.time()),
            "channel": "spot.ping",
            "event":   "",
        }).decode())

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = orjson.loads(raw)
        except Exception:
            return

        channel: str = msg.get("channel", "")
        event: str   = msg.get("event", "")

        # Системные сообщения
        if channel in ("spot.pong",) or event in ("subscribe", "unsubscribe", "error"):
            return

        if channel == "spot.book_ticker" and event == "update":
            result = msg.get("result")
            if result:
                ts_ms = msg.get("time_ms") or int(time.time() * 1000)
                self._dispatch(result, ts_ms)

    def _dispatch(self, data: dict, ts_ms: int) -> None:
        gate_sym: str = data.get("s", "")
        symbol = self._symbol_map.get(gate_sym, gate_sym.replace("_", ""))
        bid_str = data.get("b", "")
        ask_str = data.get("a", "")
        if not bid_str or not ask_str:
            return

        bid = float(bid_str)
        ask = float(ask_str)
        if bid <= 0 or ask <= 0:
            return

        bid_qty = float(data.get("B") or 1)
        ask_qty = float(data.get("A") or 1)

        book = OrderBook(
            exchange     = Exchange.GATE,
            symbol       = symbol,
            market_type  = MarketType.SPOT,
            bids         = [Price(bid, bid_qty)],
            asks         = [Price(ask, ask_qty)],
            timestamp_ms = ts_ms,
            sequence     = data.get("u", 0),
            is_snapshot  = True,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))

        ticker = Ticker(
            exchange     = Exchange.GATE,
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

    async def _send_subscribe(self, gate_symbols: list[str]) -> None:
        await self.send_raw(orjson.dumps({
            "time":    int(time.time()),
            "channel": "spot.book_ticker",
            "event":   "subscribe",
            "payload": gate_symbols,
        }).decode())


def _to_gate(symbol: str) -> str:
    """BTCUSDT → BTC_USDT"""
    if symbol.endswith("USDT"):
        return symbol[:-4] + "_USDT"
    return symbol
