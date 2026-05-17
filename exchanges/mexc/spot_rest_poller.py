"""MEXC Spot REST поллер: GET /api/v3/ticker/bookTicker каждые 250ms.
Один запрос возвращает все символы сразу — фильтруем нужные.
Spot REST не заблокирован Akamai (в отличие от Spot WS и Futures REST).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any

import aiohttp
import orjson
import structlog

from core.models import Exchange, MarketType, OrderBook, Price

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.mexc.com"
_POLL_INTERVAL = 0.25  # секунды


class MexcSpotRestPoller:
    def __init__(self) -> None:
        self._symbols: set[str] = set()
        self._ob_handlers: list[Callable[[OrderBook], Coroutine[Any, Any, None]]] = []
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._connected = False
        self._last_message_ms = 0

    def add_ob_handler(self, fn: Callable[[OrderBook], Coroutine[Any, Any, None]]) -> None:
        self._ob_handlers.append(fn)

    def subscribe(self, symbol: str) -> None:
        self._symbols.add(symbol)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_message_ms(self) -> int:
        return self._last_message_ms

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=5, ttl_dns_cache=300),
            timeout=aiohttp.ClientTimeout(total=5),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("MEXC Spot поллер запущен", interval_ms=int(_POLL_INTERVAL * 1000))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._session:
            await self._session.close()

    async def _poll_loop(self) -> None:
        while True:
            t0 = time.monotonic()
            try:
                await self._poll_once()
                self._connected = True
            except Exception as e:
                self._connected = False
                logger.warning("MEXC Spot поллер: ошибка", error=str(e))
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0.0, _POLL_INTERVAL - elapsed))

    async def _poll_once(self) -> None:
        assert self._session
        async with self._session.get(f"{_BASE_URL}/api/v3/ticker/bookTicker") as r:
            raw = await r.read()
        items: list[dict] = orjson.loads(raw)
        now_ms = int(time.time() * 1000)
        for item in items:
            sym = item.get("symbol", "")
            if sym not in self._symbols:
                continue
            bid = float(item.get("bidPrice") or 0)
            ask = float(item.get("askPrice") or 0)
            if bid <= 0 or ask <= 0:
                continue
            book = OrderBook(
                exchange=Exchange.MEXC,
                symbol=sym,
                market_type=MarketType.SPOT,
                bids=[Price(bid, float(item.get("bidQty") or 0) or 1e9)],
                asks=[Price(ask, float(item.get("askQty") or 0) or 1e9)],
                timestamp_ms=now_ms,
                sequence=0,
                is_snapshot=True,
            )
            for h in self._ob_handlers:
                asyncio.create_task(h(book))
        self._last_message_ms = now_ms
