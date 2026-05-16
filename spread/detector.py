"""SpreadDetector: сканирует обновления стаканов и эмитирует арбитражные возможности."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.models import SpreadOpportunity
from orderbook.book import LocalOrderBook
from orderbook.engine import OrderBookEngine
from spread.calculator import SpreadCalculator

logger = structlog.get_logger(__name__)

OppHandler = Callable[[SpreadOpportunity], Coroutine[Any, Any, None]]


@dataclass
class DetectorStats:
    scans: int = 0
    pairs_checked: int = 0
    opportunities_found: int = 0
    opportunities_filtered: int = 0
    last_opportunity_ms: int = 0


class SpreadDetector:
    """
    Подключается к OrderBookEngine.on_update.
    При каждом обновлении стакана проверяет все смежные биржи/рынки
    с тем же символом — в обоих направлениях.

    Фильтры:
    - min_executable_spread_bps  — минимальный исполнимый спред
    - min_size_usdt              — минимальный объём сделки
    - max_raw_spread_bps         — защита от аномальных тиков (bad data)
    """

    def __init__(
        self,
        engine: OrderBookEngine,
        calculator: SpreadCalculator,
        min_executable_spread_bps: float = 5.0,
        min_size_usdt: float = 50.0,
        max_raw_spread_bps: float = 500.0,  # > 5% = аномалия
        max_position_usdt: float = 1_000.0,
    ) -> None:
        self._engine = engine
        self._calc = calculator
        self._min_executable = min_executable_spread_bps
        self._min_size = min_size_usdt
        self._max_raw = max_raw_spread_bps
        self._max_position = max_position_usdt
        self._handlers: list[OppHandler] = []
        self.stats = DetectorStats()

        # Регистрируемся в engine
        engine.on_update(self._handle_book_update)

    def on_opportunity(self, fn: OppHandler) -> None:
        self._handlers.append(fn)

    async def _handle_book_update(self, book: LocalOrderBook) -> None:
        if not book.is_synced or book.is_stale:
            return

        symbol = book.symbol
        self.stats.scans += 1

        # Собираем все другие синхронизированные книги с тем же символом
        counterparts = [
            b for b in self._engine.synced_books()
            if b.symbol == symbol
            and not (b.exchange == book.exchange and b.market_type == book.market_type)
        ]

        for other in counterparts:
            self.stats.pairs_checked += 1
            # Проверяем оба направления
            self._check_direction(book, other)   # book=buy, other=sell
            self._check_direction(other, book)   # other=buy, book=sell

    def _check_direction(
        self,
        buy_book: LocalOrderBook,
        sell_book: LocalOrderBook,
    ) -> None:
        result = self._calc.compute(buy_book, sell_book, self._max_position)
        if result is None:
            return

        # Фильтр аномалий
        if result.raw_spread_bps > self._max_raw:
            self.stats.opportunities_filtered += 1
            logger.debug(
                "Аномальный спред — отфильтрован",
                symbol=buy_book.symbol,
                raw_bps=result.raw_spread_bps,
            )
            return

        # Фильтр минимального объёма
        if result.size_usdt < self._min_size:
            self.stats.opportunities_filtered += 1
            return

        # Фильтр минимального исполнимого спреда
        if result.executable_spread_bps < self._min_executable:
            return

        self.stats.opportunities_found += 1
        self.stats.last_opportunity_ms = int(time.time() * 1000)

        opp = self._calc.to_opportunity(result, buy_book, sell_book)

        log = logger.info if result.executable_spread_bps >= 0 else logger.debug
        log(
            "Арбитражная возможность",
            symbol=buy_book.symbol,
            buy_ex=buy_book.exchange.value,
            sell_ex=sell_book.exchange.value,
            executable_bps=f"{result.executable_spread_bps:.2f}",
            size_usdt=f"{result.size_usdt:.0f}",
        )

        for h in self._handlers:
            asyncio.create_task(h(opp))
