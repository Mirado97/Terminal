"""Bybit адаптер: объединяет WS (spot + linear) и REST."""
from __future__ import annotations

import time
from typing import Any

from core.models import (
    Balance, Exchange, ExchangeHealth, MarketType,
    Order, OrderSide, OrderType,
)
from exchanges.base import BaseExchange
from exchanges.bybit.rest import BybitRestClient
from exchanges.bybit.ws import BybitWsClient
from exchanges.rate_limiter import RateLimiter
from credentials.manager import ExchangeCredentials


class BybitAdapter(BaseExchange):
    name = Exchange.BYBIT

    def __init__(self, config: dict, credentials: ExchangeCredentials) -> None:
        super().__init__()
        testnet: bool = config.get("testnet", False)
        ws_base = "wss://stream-testnet.bybit.com/v5" if testnet else "wss://stream.bybit.com/v5"
        rest_base = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
        rl_cfg = config.get("rate_limit", {})
        rl = RateLimiter(
            requests_per_second=rl_cfg.get("requests_per_second", 10),
            orders_per_second=rl_cfg.get("orders_per_second", 5),
        )

        self._ws_spot = BybitWsClient(f"{ws_base}/public/spot", MarketType.SPOT)
        self._ws_linear = BybitWsClient(f"{ws_base}/public/linear", MarketType.PERPETUAL)
        self._rest = BybitRestClient(rest_base, credentials, rl)

        # Пробрасываем handlers из базового класса в WS клиенты
        self._ws_spot.add_ob_handler(self._forward_ob)
        self._ws_spot.add_ticker_handler(self._forward_ticker)
        self._ws_linear.add_ob_handler(self._forward_ob)
        self._ws_linear.add_ticker_handler(self._forward_ticker)

        self._health = ExchangeHealth(
            exchange=Exchange.BYBIT,
            ws_connected=False,
            rest_ok=False,
            last_message_ms=0,
        )

    # ---- lifecycle ----

    def set_latency_tracker(self, tracker: Any, name: str = "bybit") -> None:
        self._ws_spot.set_latency_callback(lambda us: tracker.record_ws(name, us))

    async def connect(self) -> None:
        await self._rest.start()
        await self._ws_spot.start()
        await self._ws_linear.start()

    async def disconnect(self) -> None:
        await self._ws_spot.stop()
        await self._ws_linear.stop()
        await self._rest.stop()

    # ---- subscriptions ----

    async def subscribe_orderbook(self, symbol: str, market_type: MarketType, depth: int = 50) -> None:
        ws = self._ws_spot if market_type == MarketType.SPOT else self._ws_linear
        await ws.subscribe_orderbook(symbol, depth)

    async def subscribe_ticker(self, symbol: str, market_type: MarketType) -> None:
        ws = self._ws_spot if market_type == MarketType.SPOT else self._ws_linear
        await ws.subscribe_ticker(symbol)

    # ---- health ----

    @property
    def health(self) -> ExchangeHealth:
        self._health.ws_connected = self._ws_spot.is_connected or self._ws_linear.is_connected
        self._health.last_message_ms = max(
            self._ws_spot.last_message_ms,
            self._ws_linear.last_message_ms,
        )
        return self._health

    # ---- REST ----

    async def get_balances(self) -> list[Balance]:
        return await self._rest.get_balances()

    async def place_order(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        order_type: OrderType,
        qty: float,
        price: float | None = None,
        client_order_id: str | None = None,
    ) -> Order:
        return await self._rest.place_order(symbol, market_type, side, order_type, qty, price, client_order_id)

    async def cancel_order(self, symbol: str, order_id: str, market_type: MarketType) -> None:
        await self._rest.cancel_order(symbol, order_id, market_type)

    async def get_order(self, symbol: str, order_id: str, market_type: MarketType) -> Order:
        return await self._rest.get_order(symbol, order_id, market_type)

    # ---- internal ----

    async def _forward_ob(self, book) -> None:
        self._emit_orderbook(book)

    async def _forward_ticker(self, ticker) -> None:
        self._emit_ticker(ticker)
