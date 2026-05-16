"""OrderBookReplayer: воспроизведение исторических тиков стакана."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Iterator

from backtesting.models import OrderBookTick
from core.models import Exchange


class OrderBookReplayer:
    """
    Воспроизводит исторические тики попарно для поиска арбитражных моментов.

    Хранит последний тик по (exchange, symbol) и при каждом новом тике
    проверяет все другие биржи с тем же символом — ищет пару с положительным спредом.

    Использование:
        replayer = OrderBookReplayer()
        for buy_tick, sell_tick in replayer.feed(ticks):
            trade = simulator.simulate(buy_tick, sell_tick, size)
    """

    def __init__(self, max_age_ms: int = 1_000) -> None:
        # последний тик по (exchange, symbol)
        self._latest: dict[tuple[Exchange, str], OrderBookTick] = {}
        # max_age_ms: тики старше этого порога не спариваем (устарели)
        self._max_age_ms = max_age_ms

    def feed(self, ticks: Iterable[OrderBookTick]) -> Iterator[tuple[OrderBookTick, OrderBookTick]]:
        """
        Принять поток тиков и генерировать пары (buy_tick, sell_tick).

        buy_tick  — биржа с более низкой ценой ask (покупаем здесь)
        sell_tick — биржа с более высокой ценой bid (продаём здесь)
        """
        for tick in ticks:
            key = (tick.exchange, tick.symbol)

            # Проверяем все имеющиеся тики того же символа с других бирж
            for (ex, sym), other in list(self._latest.items()):
                if sym != tick.symbol or ex == tick.exchange:
                    continue

                # Пропускаем устаревшие тики
                age = abs(tick.timestamp_ms - other.timestamp_ms)
                if age > self._max_age_ms:
                    continue

                if not tick.asks or not other.asks:
                    continue
                if not tick.bids or not other.bids:
                    continue

                tick_ask   = tick.asks[0][0]
                other_ask  = other.asks[0][0]
                tick_bid   = tick.bids[0][0]
                other_bid  = other.bids[0][0]

                # Направление 1: купить на tick, продать на other
                if other_bid > tick_ask:
                    yield tick, other

                # Направление 2: купить на other, продать на tick
                elif tick_bid > other_ask:
                    yield other, tick

            self._latest[key] = tick

    def reset(self) -> None:
        """Сбросить состояние (для нового прогона)."""
        self._latest.clear()

    @staticmethod
    def from_dict_stream(
        raw: list[dict],
    ) -> list[OrderBookTick]:
        """
        Вспомогательный метод: построить список OrderBookTick из сырых dict.

        Ожидаемый формат dict:
        {
          "timestamp_ms": int,
          "exchange": "bybit" | "binance" | ...,
          "symbol": "BTC/USDT",
          "market_type": "spot" | "linear",
          "bids": [[price, qty], ...],
          "asks": [[price, qty], ...],
        }
        """
        from core.models import MarketType

        ticks = []
        for d in raw:
            ticks.append(OrderBookTick(
                timestamp_ms = d["timestamp_ms"],
                exchange     = Exchange(d["exchange"]),
                symbol       = d["symbol"],
                market_type  = MarketType(d["market_type"]),
                bids         = [tuple(level) for level in d["bids"]],
                asks         = [tuple(level) for level in d["asks"]],
            ))
        return sorted(ticks, key=lambda t: t.timestamp_ms)
