"""MEXC Futures адаптер с реальным исполнением ордеров."""
from __future__ import annotations

from typing import Any

from core.models import (
    Balance, Exchange, ExchangeHealth, MarketType,
    Order, OrderSide, OrderType,
)
from exchanges.base import BaseExchange
from exchanges.mexc.futures_ws import MexcFuturesWsClient
from exchanges.mexc.futures_rest import MexcFuturesRestClient
from credentials.manager import ExchangeCredentials


class MexcFuturesAdapterReal(BaseExchange):
    name = Exchange.MEXC

    def __init__(self, credentials: ExchangeCredentials) -> None:
        super().__init__()
        self._ws   = MexcFuturesWsClient()
        self._rest = MexcFuturesRestClient(credentials)
        self._ws.add_ob_handler(self._forward_ob)
        self._ws.add_ticker_handler(self._forward_ticker)

        self._health = ExchangeHealth(
            exchange=Exchange.MEXC,
            ws_connected=False,
            rest_ok=False,
            last_message_ms=0,
        )

    def set_latency_tracker(self, tracker: Any, name: str = "mexc_fut") -> None:
        self._ws.set_latency_callback(lambda us: tracker.record_ws(name, us))

    async def connect(self) -> None:
        await self._rest.start()   # загружает размеры контрактов
        await self._ws.start()

    async def disconnect(self) -> None:
        await self._ws.stop()
        await self._rest.stop()

    async def subscribe_orderbook(self, symbol: str, market_type: MarketType, depth: int = 5) -> None:
        await self._ws.subscribe_orderbook(symbol, depth)

    async def subscribe_ticker(self, symbol: str, market_type: MarketType) -> None:
        await self._ws.subscribe_ticker(symbol)

    @property
    def health(self) -> ExchangeHealth:
        self._health.ws_connected    = self._ws.is_connected
        self._health.last_message_ms = self._ws.last_message_ms
        return self._health

    @property
    def contract_sizes(self) -> dict[str, float]:
        return self._rest._contract_sizes

    async def get_balances(self) -> list[Balance]:
        return []

    # ── Реальное исполнение ───────────────────────────────────────────────────

    async def open_long(self, symbol: str, qty_usdt: float, ref_price: float) -> Order:
        """Открыть лонг (market buy). qty_usdt — размер в USDT."""
        return await self._rest.place_order(
            symbol=symbol, side=OrderSide.BUY,
            qty_usdt=qty_usdt, ref_price=ref_price, close_position=False,
        )

    async def open_short(self, symbol: str, qty_usdt: float, ref_price: float) -> Order:
        """Открыть шорт (market sell). qty_usdt — размер в USDT."""
        return await self._rest.place_order(
            symbol=symbol, side=OrderSide.SELL,
            qty_usdt=qty_usdt, ref_price=ref_price, close_position=False,
        )

    async def close_long(self, symbol: str, vol: float, ref_price: float) -> Order:
        """Закрыть лонг (side=4). vol — количество контрактов."""
        return await self._rest.place_order(
            symbol=symbol, side=OrderSide.SELL,
            qty_usdt=vol * self._rest._contract_sizes.get(symbol, 1.0) * ref_price,
            ref_price=ref_price, close_position=True,
        )

    async def close_short(self, symbol: str, vol: float, ref_price: float) -> Order:
        """Закрыть шорт (side=2). vol — количество контрактов."""
        return await self._rest.place_order(
            symbol=symbol, side=OrderSide.BUY,
            qty_usdt=vol * self._rest._contract_sizes.get(symbol, 1.0) * ref_price,
            ref_price=ref_price, close_position=True,
        )

    # BaseExchange совместимость (не используется в tui_real.py напрямую)
    async def place_order(self, symbol, market_type, side, order_type, qty, price=None, client_order_id=None) -> Order:
        raise NotImplementedError("Используй open_long/open_short/close_long/close_short")

    async def cancel_order(self, symbol: str, order_id: str, market_type: MarketType) -> None:
        await self._rest.cancel_order(symbol, order_id)

    async def get_order(self, symbol: str, order_id: str, market_type: MarketType) -> Order:
        return await self._rest.get_order(symbol, order_id)

    async def _forward_ob(self, book) -> None:
        self._emit_orderbook(book)

    async def _forward_ticker(self, ticker) -> None:
        self._emit_ticker(ticker)
