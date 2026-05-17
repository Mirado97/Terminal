"""Gate.io Futures USDT: WS для котировок + REST для исполнения."""
from __future__ import annotations

from typing import Any

from core.models import Balance, Exchange, ExchangeHealth, MarketType, Order, OrderSide, OrderType
from exchanges.base import BaseExchange
from exchanges.gate.futures_ws import GateFuturesWsClient, _to_gate
from exchanges.gate.futures_rest import GateFuturesRestClient
from credentials.manager import ExchangeCredentials


class GateAdapterReal(BaseExchange):
    name = Exchange.GATE

    def __init__(self, credentials: ExchangeCredentials) -> None:
        super().__init__()
        self._ws   = GateFuturesWsClient()
        self._rest = GateFuturesRestClient(credentials)
        self._ws.add_ob_handler(self._forward_ob)
        self._health = ExchangeHealth(
            exchange=Exchange.GATE,
            ws_connected=False,
            rest_ok=False,
            last_message_ms=0,
        )

    def set_latency_tracker(self, tracker: Any, name: str = "gate") -> None:
        self._ws.set_latency_callback(lambda us: tracker.record_ws(name, us))

    async def connect(self) -> None:
        await self._rest.start()   # загружает contract_specs
        await self._ws.start()

    async def disconnect(self) -> None:
        await self._ws.stop()
        await self._rest.stop()

    async def subscribe_orderbook(self, symbol: str, market_type: MarketType, depth: int = 5) -> None:
        await self._ws.subscribe_orderbook(symbol, depth)

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
        gate_sym = _to_gate(symbol)
        size     = self._rest.compute_size(gate_sym, qty_usdt, ref_price)
        return await self._rest.place_order(gate_sym, size)

    async def close_long(self, symbol: str, token_qty: float, ref_price: float) -> Order:
        gate_sym = _to_gate(symbol)
        return await self._rest.place_order(gate_sym, -int(token_qty), reduce_only=True)

    async def open_short(self, symbol: str, qty_usdt: float, ref_price: float) -> Order:
        gate_sym = _to_gate(symbol)
        size     = self._rest.compute_size(gate_sym, qty_usdt, ref_price)
        return await self._rest.place_order(gate_sym, -size)

    async def close_short(self, symbol: str, token_qty: float, ref_price: float) -> Order:
        gate_sym = _to_gate(symbol)
        return await self._rest.place_order(gate_sym, int(token_qty), reduce_only=True)

    async def place_order(self, symbol, market_type, side, order_type, qty, price=None, client_order_id=None) -> Order:
        raise NotImplementedError

    async def cancel_order(self, symbol: str, order_id: str, market_type: MarketType) -> None:
        pass

    async def get_order(self, symbol: str, order_id: str, market_type: MarketType) -> Order:
        raise NotImplementedError

    async def _forward_ob(self, book) -> None:
        self._emit_orderbook(book)
