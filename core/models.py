"""Типизированные shared-модели арбитражного терминала."""
from __future__ import annotations

from enum import Enum

import msgspec


class Exchange(str, Enum):
    BYBIT = "bybit"
    BINANCE = "binance"
    MEXC = "mexc"
    GATE = "gate"
    OKX = "okx"
    BITGET = "bitget"


class MarketType(str, Enum):
    SPOT = "spot"
    PERPETUAL = "perpetual"
    FUTURES = "futures"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"
    POST_ONLY = "post_only"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Price(msgspec.Struct, frozen=True, gc=False):
    """Уровень стакана: цена + размер."""
    price: float
    size: float


class OrderBook(msgspec.Struct, frozen=True, gc=False):
    """Локальный снимок стакана."""
    exchange: Exchange
    symbol: str
    market_type: MarketType
    bids: list[Price]
    asks: list[Price]
    timestamp_ms: int
    sequence: int = 0
    checksum: int = 0
    is_snapshot: bool = True  # False = incremental delta


class Ticker(msgspec.Struct, frozen=True, gc=False):
    """Лучший bid/ask тикер."""
    exchange: Exchange
    symbol: str
    market_type: MarketType
    bid: float
    ask: float
    last: float
    volume_24h: float
    timestamp_ms: int


class SpreadOpportunity(msgspec.Struct, frozen=True, gc=False):
    """Обнаруженная арбитражная возможность между двумя ногами."""
    buy_exchange: Exchange
    sell_exchange: Exchange
    symbol: str
    buy_market: MarketType
    sell_market: MarketType
    raw_spread_bps: float        # спред без учёта комиссий
    effective_spread_bps: float  # после вычета комиссий
    net_spread_bps: float        # после комиссий + slippage
    executable_spread_bps: float # после комиссий + slippage + latency
    buy_price: float
    sell_price: float
    max_size_usdt: float
    timestamp_ms: int


class Order(msgspec.Struct):
    """Жизненный цикл ордера."""
    id: str
    client_order_id: str
    exchange: Exchange
    symbol: str
    market_type: MarketType
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    price: float
    qty: float
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    fee: float = 0.0
    fee_currency: str = "USDT"
    created_at_ms: int = 0
    updated_at_ms: int = 0
    exchange_order_id: str = ""


class Position(msgspec.Struct):
    """Текущая позиция на бирже."""
    exchange: Exchange
    symbol: str
    market_type: MarketType
    side: PositionSide
    size: float
    entry_price: float
    unrealized_pnl: float
    realized_pnl: float
    liquidation_price: float = 0.0
    leverage: float = 1.0
    updated_at_ms: int = 0


class Balance(msgspec.Struct, frozen=True, gc=False):
    """Баланс аккаунта на бирже."""
    exchange: Exchange
    currency: str
    available: float
    locked: float
    total: float
    updated_at_ms: int


class FundingRate(msgspec.Struct, frozen=True, gc=False):
    """Ставка финансирования перпетуала."""
    exchange: Exchange
    symbol: str
    rate: float
    next_funding_ms: int
    interval_hours: int
    timestamp_ms: int


class ExchangeHealth(msgspec.Struct):
    """Состояние подключения к бирже."""
    exchange: Exchange
    ws_connected: bool
    rest_ok: bool
    last_message_ms: int
    ws_latency_us: int = 0    # микросекунды
    rest_latency_us: int = 0
    error_count: int = 0
    reconnect_count: int = 0
