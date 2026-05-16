"""Модели данных бэктестинга."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.models import Exchange, MarketType


@dataclass
class OrderBookTick:
    """Один тик исторических данных стакана."""
    timestamp_ms: int
    exchange: Exchange
    symbol: str
    market_type: MarketType
    bids: list[tuple[float, float]]   # [(price, qty), ...] отсортировано по убыванию цены
    asks: list[tuple[float, float]]   # [(price, qty), ...] отсортировано по возрастанию цены


class SlippageModelType(str, Enum):
    FIXED_BPS = "fixed_bps"   # фиксированный слиппаж в bps
    LINEAR    = "linear"      # линейный от размера / ликвидности
    SQRT      = "sqrt"        # квадратный корень (более реалистично)


class LatencyModelType(str, Enum):
    FIXED     = "fixed"       # фиксированная задержка
    NORMAL    = "normal"      # нормальное распределение
    LOGNORMAL = "lognormal"   # лог-нормальное (хвосты)


@dataclass
class BacktestConfig:
    """Параметры одного прогона бэктеста."""
    # Стратегия
    min_spread_bps: float = 5.0          # минимальный исполняемый спред
    max_size_usdt: float = 500.0         # максимальный размер сделки
    initial_capital_usdt: float = 10_000.0

    # Комиссии (taker bps)
    fee_bps: float = 10.0                # suммарная комиссия в bps (обе ноги)

    # Слиппаж
    slippage_model: SlippageModelType = SlippageModelType.SQRT
    slippage_fixed_bps: float = 2.0      # для FIXED_BPS модели
    slippage_factor: float = 0.1         # для LINEAR/SQRT: масштаб

    # Latency
    latency_model: LatencyModelType = LatencyModelType.LOGNORMAL
    latency_mean_ms: float = 50.0        # среднее время исполнения
    latency_std_ms: float = 20.0         # стд. отклонение

    # Ограничения
    max_open_position_usdt: float = 5_000.0
    max_trades_per_symbol: int = 1       # 1 = не открываем пока не закрыли

    # Аннуализация для Sharpe/Sortino (trades_per_year = 24*365 для 1-минутных данных)
    trades_per_year_estimate: float = 8_760.0


@dataclass
class BacktestTrade:
    """Одна симулированная сделка."""
    timestamp_ms: int
    symbol: str
    buy_exchange: str
    sell_exchange: str

    raw_spread_bps: float          # спред до слиппажа
    slippage_bps: float            # применённый слиппаж
    fee_bps: float                 # комиссия в bps
    net_spread_bps: float          # raw - slippage - fee

    size_usdt: float
    pnl_usdt: float                # итоговый PnL
    latency_ms: float              # симулированная задержка

    success: bool = True
    reason: str = ""               # причина отказа если success=False


@dataclass
class EquityCurve:
    """Временной ряд equity."""
    timestamps: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    drawdowns: list[float] = field(default_factory=list)


@dataclass
class BacktestResult:
    """Результат одного прогона бэктеста."""
    config: BacktestConfig
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: EquityCurve = field(default_factory=EquityCurve)

    # Сводные метрики (заполняются BacktestMetrics)
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_pnl_per_trade: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0


@dataclass
class MonteCarloResult:
    """Результаты N симуляций Монте-Карло."""
    n_simulations: int
    results: list[BacktestResult] = field(default_factory=list)

    # Перцентили итоговых метрик
    pnl_p5: float = 0.0
    pnl_p50: float = 0.0
    pnl_p95: float = 0.0
    sharpe_p5: float = 0.0
    sharpe_p50: float = 0.0
    sharpe_p95: float = 0.0
    max_drawdown_p5: float = 0.0   # лучший (малый) drawdown
    max_drawdown_p95: float = 0.0  # худший (большой) drawdown
