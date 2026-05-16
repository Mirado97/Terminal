"""ExecutionEngine: координирует двуногую арбитражную сделку."""
from __future__ import annotations

import asyncio
import time

import structlog

from core.models import OrderSide, SpreadOpportunity
from exchanges.base import BaseExchange
from execution.models import ExecutionResult, LegResult, TradeState
from execution.order_manager import OrderManager
from execution.router import SmartOrderRouter
from orderbook.engine import OrderBookEngine

logger = structlog.get_logger(__name__)

_MAX_RECOVERY_ATTEMPTS = 3


class ExecutionEngine:
    """
    Стратегия исполнения: simultaneous taker (обе ноги одновременно).

    Флоу:
    1. Проверяем что оба стакана актуальны
    2. Роутер выбирает тип ордера для каждой ноги
    3. Размещаем обе ноги через asyncio.gather (минимальный leg risk)
    4. Если обе наполнились → COMPLETED
    5. Если только нога 1 → агрессивный хедж ноги 2 (RECOVERY)
    6. Если только нога 2 → агрессивный хедж ноги 1 (RECOVERY)
    7. Ни одна → CANCELLED (нет позиции, нет риска)
    """

    def __init__(
        self,
        buy_exchange: BaseExchange,
        sell_exchange: BaseExchange,
        ob_engine: OrderBookEngine,
        router: SmartOrderRouter | None = None,
        fill_timeout_ms: int = 2_000,
        poll_interval_ms: int = 100,
    ) -> None:
        self._buy_ex = buy_exchange
        self._sell_ex = sell_exchange
        self._ob_engine = ob_engine
        self._router = router or SmartOrderRouter()
        self._buy_mgr = OrderManager(buy_exchange, poll_interval_ms, fill_timeout_ms)
        self._sell_mgr = OrderManager(sell_exchange, poll_interval_ms, fill_timeout_ms)

        self._total_trades = 0
        self._successful_trades = 0
        self._failed_hedges = 0

    async def execute(self, opp: SpreadOpportunity) -> ExecutionResult:
        """Исполнить арбитражную возможность."""
        start_ms = int(time.time() * 1000)
        self._total_trades += 1

        # Проверяем актуальность стаканов
        buy_book = self._ob_engine.get(opp.buy_exchange, opp.symbol, opp.buy_market)
        sell_book = self._ob_engine.get(opp.sell_exchange, opp.symbol, opp.sell_market)

        if not buy_book or not sell_book or buy_book.is_stale or sell_book.is_stale:
            return ExecutionResult(
                opportunity=opp,
                state=TradeState.FAILED,
                error="Стаканы устарели к моменту исполнения",
                execution_time_ms=int(time.time() * 1000) - start_ms,
            )

        # Роутинг
        buy_qty = opp.max_size_usdt / buy_book.best_ask
        sell_qty = buy_qty  # хедж точно под покупку

        buy_decision = self._router.route_entry(buy_book, OrderSide.BUY, opp.executable_spread_bps)
        sell_decision = self._router.route_hedge(sell_book, OrderSide.SELL)

        logger.info(
            "Исполняем арбитраж",
            symbol=opp.symbol,
            buy_ex=opp.buy_exchange.value,
            sell_ex=opp.sell_exchange.value,
            spread_bps=f"{opp.executable_spread_bps:.2f}",
            size_usdt=f"{opp.max_size_usdt:.0f}",
            buy_type=buy_decision.order_type.value,
            sell_type=sell_decision.order_type.value,
        )

        # Размещаем обе ноги одновременно
        buy_task = asyncio.create_task(self._buy_mgr.place_and_wait(
            opp.symbol, opp.buy_market, OrderSide.BUY,
            buy_decision.order_type, buy_qty, buy_decision.price,
        ))
        sell_task = asyncio.create_task(self._sell_mgr.place_and_wait(
            opp.symbol, opp.sell_market, OrderSide.SELL,
            sell_decision.order_type, sell_qty, sell_decision.price,
        ))

        buy_leg_res: LegResult
        sell_leg_res: LegResult
        buy_leg_res, sell_leg_res = await asyncio.gather(buy_task, sell_task)

        elapsed_ms = int(time.time() * 1000) - start_ms

        # Анализируем результат
        buy_filled = buy_leg_res.filled_qty > 0
        sell_filled = sell_leg_res.filled_qty > 0

        if buy_filled and sell_filled:
            self._successful_trades += 1
            result = ExecutionResult(
                opportunity=opp,
                state=TradeState.COMPLETED,
                buy_leg=buy_leg_res,
                sell_leg=sell_leg_res,
                execution_time_ms=elapsed_ms,
            )
            logger.info(
                "Арбитраж исполнен",
                symbol=opp.symbol,
                pnl=f"{result.pnl_usdt:.4f}",
                spread_bps=f"{result.realized_spread_bps:.2f}",
                time_ms=elapsed_ms,
            )
            return result

        if buy_filled and not sell_filled:
            # Нога 1 заполнена, хедж провалился → аварийный sell
            logger.warning("Нога 2 не заполнена, запускаем recovery", symbol=opp.symbol)
            sell_leg_res = await self._emergency_hedge(
                opp.symbol, opp.sell_market, OrderSide.SELL,
                buy_leg_res.filled_qty, sell_book.best_bid,
            )

        elif sell_filled and not buy_filled:
            # Нога 2 заполнена, нога 1 нет → аварийный buy для закрытия
            logger.warning("Нога 1 не заполнена, запускаем recovery", symbol=opp.symbol)
            buy_leg_res = await self._emergency_hedge(
                opp.symbol, opp.buy_market, OrderSide.BUY,
                sell_leg_res.filled_qty, buy_book.best_ask,
            )

        else:
            # Ни одна нога не заполнилась — позиции нет
            return ExecutionResult(
                opportunity=opp,
                state=TradeState.FAILED,
                buy_leg=buy_leg_res,
                sell_leg=sell_leg_res,
                execution_time_ms=elapsed_ms,
                error="Обе ноги не заполнились (spread ушёл)",
            )

        final_state = TradeState.COMPLETED if (buy_leg_res.success and sell_leg_res.success) else TradeState.FAILED
        result = ExecutionResult(
            opportunity=opp,
            state=final_state,
            buy_leg=buy_leg_res,
            sell_leg=sell_leg_res,
            execution_time_ms=int(time.time() * 1000) - start_ms,
        )
        logger.info(
            "Сделка завершена",
            symbol=opp.symbol,
            state=final_state.value,
            pnl=f"{result.pnl_usdt:.4f}",
        )
        return result

    async def _emergency_hedge(
        self,
        symbol: str,
        market_type,
        side: OrderSide,
        qty: float,
        ref_price: float,
    ) -> LegResult:
        self._failed_hedges += 1
        mgr = self._sell_mgr if side == OrderSide.SELL else self._buy_mgr
        return await mgr.place_aggressive(
            symbol, market_type, side, qty, ref_price, max_retries=_MAX_RECOVERY_ATTEMPTS
        )

    @property
    def stats(self) -> dict:
        return {
            "total_trades": self._total_trades,
            "successful": self._successful_trades,
            "failed_hedges": self._failed_hedges,
            "success_rate": self._successful_trades / self._total_trades if self._total_trades else 0.0,
        }
