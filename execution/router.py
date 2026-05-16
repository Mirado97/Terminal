"""SmartOrderRouter: выбирает тип ордера для каждой ноги."""
from __future__ import annotations

from dataclasses import dataclass

from core.models import OrderSide, OrderType
from orderbook.book import LocalOrderBook


@dataclass(frozen=True)
class RoutingDecision:
    order_type: OrderType
    price: float | None      # None = market order
    urgency: str             # "maker" | "taker" | "aggressive"


class SmartOrderRouter:
    """
    Выбирает тип ордера на основе:
    - размера спреда (крупный → maker, мелкий → taker)
    - роли ноги (hedge leg → всегда taker/aggressive)
    - доступной ликвидности на best уровне

    Правило:
      executable_spread > maker_threshold → пробуем maker (ждём fill)
      executable_spread ≤ maker_threshold → taker (IOC по best price)
      hedge leg всегда → taker или aggressive (не рискуем позицией)
    """

    def __init__(
        self,
        maker_threshold_bps: float = 20.0,  # при каком спреде выгоден maker
        price_buffer_bps: float = 2.0,       # буфер для IOC (не промахнуться)
    ) -> None:
        self._maker_threshold = maker_threshold_bps
        self._price_buffer = price_buffer_bps / 10_000

    def route_entry(
        self,
        book: LocalOrderBook,
        side: OrderSide,
        executable_spread_bps: float,
    ) -> RoutingDecision:
        """Первая нога — можем использовать maker для снижения комиссии."""
        if executable_spread_bps >= self._maker_threshold:
            # Достаточный спред — пробуем maker (post-only)
            price = book.best_bid if side == OrderSide.BUY else book.best_ask
            return RoutingDecision(OrderType.POST_ONLY, price, "maker")
        else:
            # Мелкий спред — скорость важнее комиссии
            return self._taker_decision(book, side)

    def route_hedge(
        self,
        book: LocalOrderBook,
        side: OrderSide,
    ) -> RoutingDecision:
        """Хедж-нога — всегда taker, позицию закрыть важнее комиссии."""
        return self._taker_decision(book, side)

    def route_emergency(
        self,
        book: LocalOrderBook,
        side: OrderSide,
    ) -> RoutingDecision:
        """Аварийный хедж — агрессивный market ордер."""
        return RoutingDecision(OrderType.MARKET, None, "aggressive")

    def _taker_decision(self, book: LocalOrderBook, side: OrderSide) -> RoutingDecision:
        """IOC с небольшим буфером за лучшей ценой."""
        if side == OrderSide.BUY:
            # Покупаем чуть выше ask — гарантируем fill
            price = book.best_ask * (1 + self._price_buffer)
        else:
            # Продаём чуть ниже bid
            price = book.best_bid * (1 - self._price_buffer)
        return RoutingDecision(OrderType.IOC, price, "taker")
