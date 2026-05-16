"""Gate.io адаптер: WS spot.book_ticker."""
from __future__ import annotations

from typing import Any

from core.models import (
    Balance, Exchange, ExchangeHealth, MarketType,
    Order, OrderSide, OrderType,
)
from exchanges.base import BaseExchange
from exchanges.gate.ws import GateWsClient
from credentials.manager import ExchangeCredentials


class GateAdapter(BaseExchange):
    name = Exchange.GATE

    def __init__(self, credentials: ExchangeCredentials) -> None:
        super().__init__()
        self._ws = GateWsClient()
        self._ws.add_ob_handler(self._forward_ob)
        self._ws.add_ticker_handler(self._forward_ticker)

        self._health = ExchangeHealth(
            exchange        = Exchange.GATE,
            ws_connected    = False,
            rest_ok         = False,
            last_message_ms = 0,
        )

    def set_latency_tracker(self, tracker: Any, name: str = "gate") -> None:
        self._ws.set_latency_callback(lambda us: tracker.record_ws(name, us))

    # ---- lifecycle ----

    async def connect(self) -> None:
        await self._ws.start()

    async def disconnect(self) -> None:
        await self._ws.stop()

    # ---- subscriptions ----

    async def subscribe_orderbook(self, symbol: str, market_type: MarketType, depth: int = 5) -> None:
        await self._ws.subscribe_orderbook(symbol, depth)

    async def subscribe_ticker(self, symbol: str, market_type: MarketType) -> None:
        await self._ws.subscribe_ticker(symbol)

    # ---- health ----

    @property
    def health(self) -> ExchangeHealth:
        self._health.ws_connected    = self._ws.is_connected
        self._health.last_message_ms = self._ws.last_message_ms
        return self._health

    # ---- REST (не реализован) ----

    async def get_balances(self) -> list[Balance]:
        return []

    async def place_order(self, symbol, market_type, side, order_type, qty, price=None, client_order_id=None) -> Order:
        raise NotImplementedError("Gate.io торговля не подключена")

    async def cancel_order(self, symbol, order_id, market_type) -> None:
        raise NotImplementedError

    async def get_order(self, symbol, order_id, market_type) -> Order:
        raise NotImplementedError

    # ---- internal ----

    async def _forward_ob(self, book) -> None:
        self._emit_orderbook(book)

    async def _forward_ticker(self, ticker) -> None:
        self._emit_ticker(ticker)
