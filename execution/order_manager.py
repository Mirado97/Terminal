"""OrderManager: размещение, отслеживание и отмена ордеров с поллингом статуса."""
from __future__ import annotations

import asyncio
import time
import uuid

import structlog

from core.models import MarketType, Order, OrderSide, OrderStatus, OrderType
from exchanges.base import BaseExchange
from execution.models import LegResult

logger = structlog.get_logger(__name__)

_TERMINAL_STATUSES = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}


class OrderManager:
    """
    Тонкая обёртка над BaseExchange для одной ноги сделки.
    Отвечает за: place → poll → cancel при таймауте.
    """

    def __init__(
        self,
        exchange: BaseExchange,
        poll_interval_ms: int = 100,
        fill_timeout_ms: int = 3_000,
    ) -> None:
        self._exchange = exchange
        self._poll_interval = poll_interval_ms / 1_000
        self._fill_timeout = fill_timeout_ms / 1_000

    async def place_and_wait(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        order_type: OrderType,
        qty: float,
        price: float | None = None,
    ) -> LegResult:
        """
        Размещает ордер и ждёт исполнения до fill_timeout_ms.
        При таймауте отменяет ордер и возвращает частичный fill.
        """
        coid = f"arb{uuid.uuid4().hex[:14]}"
        order = await self._exchange.place_order(
            symbol, market_type, side, order_type, qty, price, coid
        )
        logger.info(
            "Ордер размещён",
            exchange=self._exchange.name.value,
            symbol=symbol,
            side=side.value,
            type=order_type.value,
            qty=qty,
            price=price,
            order_id=order.exchange_order_id,
        )

        # IOC/FOK/Market исполняются немедленно — не нужен поллинг
        if order_type in (OrderType.IOC, OrderType.FOK, OrderType.MARKET):
            await asyncio.sleep(self._poll_interval)
            try:
                order = await self._exchange.get_order(symbol, order.exchange_order_id, market_type)
            except Exception:
                pass
            return self._to_leg_result(order, qty)

        # Limit/PostOnly — поллим до таймаута
        deadline = time.monotonic() + self._fill_timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(self._poll_interval)
            try:
                order = await self._exchange.get_order(symbol, order.exchange_order_id, market_type)
            except Exception as e:
                logger.warning("Ошибка при поллинге ордера", error=str(e))
                continue

            if order.status in _TERMINAL_STATUSES:
                break

        # Отменяем если не заполнен полностью
        if order.status not in _TERMINAL_STATUSES:
            await self._try_cancel(order)
            # Финальный запрос статуса после отмены
            try:
                order = await self._exchange.get_order(symbol, order.exchange_order_id, market_type)
            except Exception:
                pass

        result = self._to_leg_result(order, qty)
        logger.info(
            "Нога исполнена",
            exchange=self._exchange.name.value,
            symbol=symbol,
            filled=result.filled_qty,
            requested=result.requested_qty,
            avg_price=result.avg_price,
            success=result.success,
        )
        return result

    async def place_aggressive(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        qty: float,
        price: float,
        max_retries: int = 3,
    ) -> LegResult:
        """
        Агрессивное исполнение для хеджа: IOC → Market на 2-м retry.
        Используется при аварийном закрытии незахеджированной позиции.
        """
        for attempt in range(max_retries):
            order_type = OrderType.IOC if attempt < max_retries - 1 else OrderType.MARKET
            # С каждой попыткой даём чуть больше slippage
            adjusted_price = price * (1 + 0.001 * attempt) if side == OrderSide.BUY else price * (1 - 0.001 * attempt)
            try:
                result = await self.place_and_wait(symbol, market_type, side, order_type, qty, adjusted_price)
                if result.filled_qty >= qty * 0.95:
                    return result
                qty -= result.filled_qty  # хеджируем остаток
            except Exception as e:
                logger.error("Ошибка агрессивного исполнения", attempt=attempt, error=str(e))

        logger.critical("Не удалось захеджировать позицию!", symbol=symbol, qty=qty)
        return LegResult(order=Order.__new__(Order), requested_qty=qty, success=False)

    # ---- internal ----

    async def _try_cancel(self, order: Order) -> None:
        try:
            await self._exchange.cancel_order(order.symbol, order.exchange_order_id, order.market_type)
        except Exception as e:
            logger.warning("Не удалось отменить ордер", order_id=order.exchange_order_id, error=str(e))

    @staticmethod
    def _to_leg_result(order: Order, requested_qty: float) -> LegResult:
        filled = order.filled_qty
        return LegResult(
            order=order,
            requested_qty=requested_qty,
            filled_qty=filled,
            avg_price=order.avg_fill_price,
            fee_usdt=order.fee,
            success=order.status == OrderStatus.FILLED or filled >= requested_qty * 0.999,
        )
