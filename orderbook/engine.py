"""OrderBookEngine: управляет всеми локальными стаканами."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from core.models import Exchange, MarketType, OrderBook
from orderbook.book import LocalOrderBook
from orderbook.checksum import bybit_checksum

logger = structlog.get_logger(__name__)

UpdateHandler = Callable[[LocalOrderBook], Coroutine[Any, Any, None]]
StaleHandler = Callable[[LocalOrderBook], Coroutine[Any, Any, None]]


class OrderBookEngine:
    """
    Центральный реестр стаканов.

    Принимает сырые OrderBook сообщения от адаптеров бирж,
    применяет snapshot/delta к LocalOrderBook,
    валидирует Bybit checksum,
    нотифицирует обработчики через asyncio.create_task.
    """

    def __init__(self, validate_checksum: bool = False) -> None:
        # По умолчанию checksum выключен: требует точного string repr из WS.
        # Включать только после проверки на live данных.
        self._validate_checksum = validate_checksum
        self._books: dict[str, LocalOrderBook] = {}
        self._update_handlers: list[UpdateHandler] = []
        self._checksum_errors: int = 0

    # ---- регистрация ----

    def on_update(self, fn: UpdateHandler) -> None:
        """Вызывается после каждого успешного обновления стакана."""
        self._update_handlers.append(fn)

    # ---- основной handler (подключается к exchange.on_orderbook) ----

    async def handle(self, msg: OrderBook) -> None:
        book = self._get_or_create(msg.exchange, msg.symbol, msg.market_type)

        if msg.is_snapshot:
            book.apply_snapshot(msg.bids, msg.asks, msg.sequence, msg.timestamp_ms)
            logger.debug(
                "Snapshot применён",
                exchange=msg.exchange.value,
                symbol=msg.symbol,
                levels=len(msg.bids),
            )
        else:
            if not book.is_synced:
                # Дельта пришла раньше snapshot — ждём snapshot
                logger.debug("Дельта до snapshot — пропускаем", symbol=msg.symbol)
                return
            book.apply_delta(msg.bids, msg.asks, msg.sequence, msg.timestamp_ms)

        # Валидация checksum (Bybit)
        if self._validate_checksum and msg.checksum and msg.exchange == Exchange.BYBIT:
            expected = bybit_checksum(book.top_bids(25), book.top_asks(25))
            if expected != msg.checksum:
                self._checksum_errors += 1
                logger.warning(
                    "Checksum mismatch — invalidate книги",
                    symbol=msg.symbol,
                    expected=expected,
                    got=msg.checksum,
                    total_errors=self._checksum_errors,
                )
                book.invalidate()
                return

        for h in self._update_handlers:
            asyncio.create_task(h(book))

    # ---- доступ к книгам ----

    def get(self, exchange: Exchange, symbol: str, market_type: MarketType) -> LocalOrderBook | None:
        return self._books.get(self._key(exchange, symbol, market_type))

    def all_books(self) -> list[LocalOrderBook]:
        return list(self._books.values())

    def synced_books(self) -> list[LocalOrderBook]:
        return [b for b in self._books.values() if b.is_synced and not b.is_stale]

    def stale_books(self) -> list[LocalOrderBook]:
        return [b for b in self._books.values() if b.is_stale]

    @property
    def checksum_errors(self) -> int:
        return self._checksum_errors

    # ---- stale watchdog ----

    async def run_stale_watchdog(
        self,
        on_stale: StaleHandler,
        interval_s: float = 5.0,
    ) -> None:
        """
        Фоновая задача: проверяет stale книги каждые interval_s секунд.
        При обнаружении вызывает on_stale — адаптер должен запросить ресинк.
        """
        while True:
            await asyncio.sleep(interval_s)
            for book in self.stale_books():
                logger.warning(
                    "Stale orderbook обнаружен",
                    exchange=book.exchange.value,
                    symbol=book.symbol,
                )
                asyncio.create_task(on_stale(book))

    # ---- internal ----

    def _get_or_create(
        self, exchange: Exchange, symbol: str, market_type: MarketType
    ) -> LocalOrderBook:
        key = self._key(exchange, symbol, market_type)
        if key not in self._books:
            self._books[key] = LocalOrderBook(exchange, symbol, market_type)
        return self._books[key]

    @staticmethod
    def _key(exchange: Exchange, symbol: str, market_type: MarketType) -> str:
        return f"{exchange.value}:{symbol}:{market_type.value}"
