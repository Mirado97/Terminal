"""Абстрактный интерфейс адаптера биржи."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

from core.models import (
    Balance, Exchange, ExchangeHealth, MarketType,
    Order, OrderBook, OrderSide, OrderType, Ticker,
)


ObHandler = Callable[[OrderBook], Coroutine[Any, Any, None]]
TickerHandler = Callable[[Ticker], Coroutine[Any, Any, None]]


class BaseExchange(ABC):
    """
    Единый интерфейс для всех адаптеров бирж.
    Подписчики регистрируют обработчики через on_orderbook / on_ticker.
    Dispatch через asyncio.create_task — не блокирует приём WS сообщений.
    """

    name: Exchange

    def __init__(self) -> None:
        self._ob_handlers: list[ObHandler] = []
        self._ticker_handlers: list[TickerHandler] = []

    # ---- регистрация обработчиков ----

    def on_orderbook(self, fn: ObHandler) -> None:
        self._ob_handlers.append(fn)

    def on_ticker(self, fn: TickerHandler) -> None:
        self._ticker_handlers.append(fn)

    # ---- dispatch (вызывается из WS клиентов) ----

    def _emit_orderbook(self, book: OrderBook) -> None:
        for h in self._ob_handlers:
            asyncio.create_task(h(book))

    def _emit_ticker(self, ticker: Ticker) -> None:
        for h in self._ticker_handlers:
            asyncio.create_task(h(ticker))

    # ---- lifecycle ----

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    # ---- market data subscriptions ----

    @abstractmethod
    async def subscribe_orderbook(self, symbol: str, market_type: MarketType, depth: int = 50) -> None: ...

    @abstractmethod
    async def subscribe_ticker(self, symbol: str, market_type: MarketType) -> None: ...

    # ---- health ----

    @property
    @abstractmethod
    def health(self) -> ExchangeHealth: ...

    # ---- private REST ----

    @abstractmethod
    async def get_balances(self) -> list[Balance]: ...

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        order_type: OrderType,
        qty: float,
        price: float | None = None,
        client_order_id: str | None = None,
    ) -> Order: ...

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str, market_type: MarketType) -> None: ...

    @abstractmethod
    async def get_order(self, symbol: str, order_id: str, market_type: MarketType) -> Order: ...
