"""Bitget Futures USDT: WS для котировок + REST для исполнения."""
from __future__ import annotations

from typing import Any

from core.models import Balance, Exchange, ExchangeHealth, MarketType, Order
from exchanges.base import BaseExchange
from exchanges.bitget.futures_ws import BitgetFuturesWsClient
from exchanges.bitget.futures_rest import BitgetFuturesRestClient
from credentials.manager import ExchangeCredentials


class BitgetAdapterReal(BaseExchange):
    name = Exchange.BITGET

    def __init__(self, credentials: ExchangeCredentials, passphrase: str) -> None:
        super().__init__()
        self._ws   = BitgetFuturesWsClient()
        self._rest = BitgetFuturesRestClient(credentials, passphrase)
        self._ws.add_ob_handler(self._forward_ob)
        self._health = ExchangeHealth(
            exchange=Exchange.BITGET,
            ws_connected=False,
            rest_ok=False,
            last_message_ms=0,
        )

    def set_latency_tracker(self, tracker: Any, name: str = "bitget") -> None:
        self._ws.set_latency_callback(lambda us: tracker.record_ws(name, us))

    async def connect(self) -> None:
        await self._rest.start()
        await self._ws.start()

    async def disconnect(self) -> None:
        await self._ws.stop()
        await self._rest.stop()

    async def subscribe_orderbook(self, symbol: str, market_type: MarketType, depth: int = 1) -> None:
        await self._ws.subscribe_orderbook(symbol)

    async def subscribe_ticker(self, symbol: str, market_type: MarketType) -> None:
        pass

    @property
    def health(self) -> ExchangeHealth:
        self._health.ws_connected    = self._ws.is_connected
        self._health.last_message_ms = self._ws.last_message_ms
        return self._health

    async def get_balances(self) -> list[Balance]:
        return []

    # ── Исполнение ────────────────────────────────────────────────────────

    async def open_long(self, symbol: str, qty_usdt: float, ref_price: float) -> Order:
        size = self._rest.compute_size(symbol, qty_usdt, ref_price)
        return await self._rest.place_order(symbol, "buy", "open", size)

    async def close_long(self, symbol: str, token_qty: float, ref_price: float) -> Order:
        return await self._rest.place_order(symbol, "sell", "close", token_qty)

    async def open_short(self, symbol: str, qty_usdt: float, ref_price: float) -> Order:
        size = self._rest.compute_size(symbol, qty_usdt, ref_price)
        return await self._rest.place_order(symbol, "sell", "open", size)

    async def close_short(self, symbol: str, token_qty: float, ref_price: float) -> Order:
        return await self._rest.place_order(symbol, "buy", "close", token_qty)

    async def place_order(self, symbol, market_type, side, order_type, qty, price=None, client_order_id=None) -> Order:
        raise NotImplementedError

    async def cancel_order(self, symbol: str, order_id: str, market_type: MarketType) -> None:
        pass

    async def get_order(self, symbol: str, order_id: str, market_type: MarketType) -> Order:
        raise NotImplementedError

    async def _forward_ob(self, book) -> None:
        self._emit_orderbook(book)
