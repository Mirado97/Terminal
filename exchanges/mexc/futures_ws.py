"""MEXC Futures WebSocket: sub.ticker → bid1/ask1."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any

import orjson
import structlog

from core.models import Exchange, MarketType, OrderBook, Price, Ticker
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
    Канал: sub.ticker → push.ticker (bid1/ask1)
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
        self._ticker_handlers: list[Callable[[Ticker], Coroutine[Any, Any, None]]] = []

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    def add_ticker_handler(self, fn: Callable[[Ticker], Coroutine[Any, Any, None]]) -> None:
        self._ticker_handlers.append(fn)

    async def subscribe_ticker(self, symbol: str) -> None:
        if symbol not in self._symbols:
            self._symbols.append(symbol)

    async def subscribe_orderbook(self, symbol: str, depth: int = 5) -> None:
        await self.subscribe_ticker(symbol)

    # ---- BaseWsClient hooks ----

    async def _on_connect(self, ws) -> None:
        for sym in self._symbols:
            msg = orjson.dumps({"method": "sub.ticker", "param": {"symbol": _to_mexc(sym)}})
            await ws.send(msg.decode())
            await asyncio.sleep(0.05)
        logger.info("MEXC Futures WS подключён", subs=len(self._symbols))

    async def _send_ping(self, ws) -> None:
        await ws.send(orjson.dumps({"method": "ping"}).decode())

    async def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = orjson.loads(raw)
        except Exception:
            return

        channel = msg.get("channel", "")
        if channel != "push.ticker":
            return

        data = msg.get("data", {})
        mexc_sym = data.get("symbol", "")
        if not mexc_sym:
            return

        symbol = _from_mexc(mexc_sym)
        ts_ms  = msg.get("ts", int(time.time() * 1000))

        try:
            bid = float(data.get("bid1", 0))
            ask = float(data.get("ask1", 0))
        except (ValueError, TypeError):
            return

        if bid <= 0 or ask <= 0:
            return

        # Объём берём из ask1Size/bid1Size если есть, иначе используем большой дефолт
        # чтобы не фильтровать пары только из-за отсутствия объёма в тикере
        try:
            bid_vol = float(data.get("bid1Size", 0) or data.get("bid1Vol", 0) or 1e9)
            ask_vol = float(data.get("ask1Size", 0) or data.get("ask1Vol", 0) or 1e9)
        except (ValueError, TypeError):
            bid_vol = ask_vol = 1e9

        book = OrderBook(
            exchange     = Exchange.MEXC,
            symbol       = symbol,
            market_type  = MarketType.PERPETUAL,
            bids         = [Price(bid, bid_vol)],
            asks         = [Price(ask, ask_vol)],
            timestamp_ms = ts_ms,
            sequence     = 0,
            is_snapshot  = True,
        )
        for h in self._ob_handlers:
            asyncio.create_task(h(book))

        ticker = Ticker(
            exchange     = Exchange.MEXC,
            symbol       = symbol,
            market_type  = MarketType.PERPETUAL,
            bid          = bid,
            ask          = ask,
            last         = float(data.get("lastPrice", bid)),
            volume_24h   = float(data.get("volume24", 0) or 0),
            timestamp_ms = ts_ms,
        )
        for h in self._ticker_handlers:
            asyncio.create_task(h(ticker))
