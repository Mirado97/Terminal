"""
MEXC: цены через Spot REST поллинг (250ms), ордера через Spot REST.
Spot WS и Futures REST заблокированы Akamai для датацентровых IP.
Spot REST (api.mexc.com) работает с VPS — проверено.
"""
from __future__ import annotations

from typing import Any

from core.models import Balance, Exchange, ExchangeHealth, MarketType, Order
from exchanges.base import BaseExchange
from exchanges.mexc.spot_rest_poller import MexcSpotRestPoller
from exchanges.mexc.spot_rest import MexcSpotRestClient
from credentials.manager import ExchangeCredentials


class MexcHybridAdapterReal(BaseExchange):
    """Spot REST поллинг для котировок + Spot REST для исполнения."""

    name = Exchange.MEXC

    def __init__(self, credentials: ExchangeCredentials) -> None:
        super().__init__()
        self._poller = MexcSpotRestPoller()
        self._rest   = MexcSpotRestClient(credentials)
        self._poller.add_ob_handler(self._forward_ob)

        self._health = ExchangeHealth(
            exchange=Exchange.MEXC,
            ws_connected=False,
            rest_ok=False,
            last_message_ms=0,
        )

    def set_latency_tracker(self, tracker: Any, name: str = "mexc_spot") -> None:
        pass  # REST polling, нет WS latency

    async def connect(self) -> None:
        await self._rest.start()
        await self._poller.start()

    async def disconnect(self) -> None:
        await self._poller.stop()
        await self._rest.stop()

    async def subscribe_orderbook(self, symbol: str, market_type: MarketType, depth: int = 5) -> None:
        self._poller.subscribe(symbol)

    async def subscribe_ticker(self, symbol: str, market_type: MarketType) -> None:
        pass  # bookTicker уже содержит bid/ask

    @property
    def health(self) -> ExchangeHealth:
        self._health.ws_connected    = self._poller.is_connected
        self._health.last_message_ms = self._poller.last_message_ms
        return self._health

    async def get_balances(self) -> list[Balance]:
        return []

    async def open_long(self, symbol: str, qty_usdt: float, ref_price: float) -> Order:
        return await self._rest.buy_market(symbol, qty_usdt)

    async def close_long(self, symbol: str, token_qty: float, ref_price: float) -> Order:
        return await self._rest.sell_market(symbol, token_qty)

    async def open_short(self, symbol: str, qty_usdt: float, ref_price: float) -> Order:
        raise NotImplementedError("MEXC Spot не поддерживает шорт")

    async def close_short(self, symbol: str, token_qty: float, ref_price: float) -> Order:
        raise NotImplementedError("MEXC Spot не поддерживает шорт")

    async def place_order(self, symbol, market_type, side, order_type, qty, price=None, client_order_id=None) -> Order:
        raise NotImplementedError

    async def cancel_order(self, symbol: str, order_id: str, market_type: MarketType) -> None:
        pass

    async def get_order(self, symbol: str, order_id: str, market_type: MarketType) -> Order:
        raise NotImplementedError

    async def _forward_ob(self, book) -> None:
        self._emit_orderbook(book)
