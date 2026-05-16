"""Таблица торговых комиссий по биржам и типам рынков (в базисных пунктах)."""
from __future__ import annotations

from dataclasses import dataclass

from core.models import Exchange, MarketType


@dataclass(frozen=True)
class FeeSchedule:
    maker_bps: float
    taker_bps: float


# Стандартные комиссии VIP-0 / базовый тир
_DEFAULT_FEES: dict[tuple[Exchange, MarketType], FeeSchedule] = {
    (Exchange.BYBIT,   MarketType.SPOT):      FeeSchedule(maker_bps=10.0, taker_bps=10.0),
    (Exchange.BYBIT,   MarketType.PERPETUAL): FeeSchedule(maker_bps=2.0,  taker_bps=5.5),
    (Exchange.BYBIT,   MarketType.FUTURES):   FeeSchedule(maker_bps=2.0,  taker_bps=5.5),
    (Exchange.BINANCE, MarketType.SPOT):      FeeSchedule(maker_bps=10.0, taker_bps=10.0),
    (Exchange.BINANCE, MarketType.PERPETUAL): FeeSchedule(maker_bps=2.0,  taker_bps=4.0),
    (Exchange.BINANCE, MarketType.FUTURES):   FeeSchedule(maker_bps=2.0,  taker_bps=4.0),
    (Exchange.MEXC,    MarketType.SPOT):      FeeSchedule(maker_bps=0.0,  taker_bps=20.0),
    (Exchange.GATE,    MarketType.SPOT):      FeeSchedule(maker_bps=10.0, taker_bps=10.0),
    (Exchange.GATE,    MarketType.PERPETUAL): FeeSchedule(maker_bps=2.0,  taker_bps=5.0),
    (Exchange.MEXC,    MarketType.PERPETUAL): FeeSchedule(maker_bps=0.0,  taker_bps=6.0),
    (Exchange.OKX,     MarketType.SPOT):      FeeSchedule(maker_bps=8.0,  taker_bps=10.0),
    (Exchange.OKX,     MarketType.PERPETUAL): FeeSchedule(maker_bps=2.0,  taker_bps=5.0),
    (Exchange.BITGET,  MarketType.SPOT):      FeeSchedule(maker_bps=10.0, taker_bps=10.0),
    (Exchange.BITGET,  MarketType.PERPETUAL): FeeSchedule(maker_bps=2.0,  taker_bps=6.0),
}

_FALLBACK = FeeSchedule(maker_bps=10.0, taker_bps=10.0)


class FeeTable:
    """
    Хранит комиссии и умеет считать стоимость round-trip.
    Поддерживает переопределение (VIP тиры, BNB/BYB скидки).
    """

    def __init__(self, overrides: dict[tuple[Exchange, MarketType], FeeSchedule] | None = None) -> None:
        self._fees = dict(_DEFAULT_FEES)
        if overrides:
            self._fees.update(overrides)

    def get(self, exchange: Exchange, market_type: MarketType) -> FeeSchedule:
        return self._fees.get((exchange, market_type), _FALLBACK)

    def maker_bps(self, exchange: Exchange, market_type: MarketType) -> float:
        return self.get(exchange, market_type).maker_bps

    def taker_bps(self, exchange: Exchange, market_type: MarketType) -> float:
        return self.get(exchange, market_type).taker_bps

    def round_trip_taker_bps(
        self,
        buy_ex: Exchange, buy_mt: MarketType,
        sell_ex: Exchange, sell_mt: MarketType,
    ) -> float:
        """Суммарные taker комиссии на обе ноги сделки."""
        return self.taker_bps(buy_ex, buy_mt) + self.taker_bps(sell_ex, sell_mt)

    def round_trip_maker_bps(
        self,
        buy_ex: Exchange, buy_mt: MarketType,
        sell_ex: Exchange, sell_mt: MarketType,
    ) -> float:
        """Суммарные maker комиссии (post-only обе ноги)."""
        return self.maker_bps(buy_ex, buy_mt) + self.maker_bps(sell_ex, sell_mt)
