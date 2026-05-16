"""Тесты модуля backtesting."""
import math

import pytest

from backtesting.engine import BacktestEngine
from backtesting.latency import LatencyModel
from backtesting.metrics import BacktestMetrics
from backtesting.models import (
    BacktestConfig,
    BacktestResult,
    BacktestTrade,
    LatencyModelType,
    MonteCarloResult,
    OrderBookTick,
    SlippageModelType,
)
from backtesting.monte_carlo import MonteCarloEngine
from backtesting.replay import OrderBookReplayer
from backtesting.simulator import TradeSimulator
from backtesting.slippage import SlippageModel
from core.models import Exchange, MarketType


# ──────────────────────────── helpers ────────────────────────────

def _cfg(**kwargs) -> BacktestConfig:
    return BacktestConfig(**kwargs)


def _tick(
    exchange: Exchange,
    symbol: str = "BTC/USDT",
    best_ask: float = 100.0,
    best_bid: float = 100.0,
    ts: int = 1_000,
) -> OrderBookTick:
    return OrderBookTick(
        timestamp_ms = ts,
        exchange     = exchange,
        symbol       = symbol,
        market_type  = MarketType.SPOT,
        bids         = [(best_bid, 1.0), (best_bid - 0.1, 2.0)],
        asks         = [(best_ask, 1.0), (best_ask + 0.1, 2.0)],
    )


def _trade(pnl: float, success: bool = True) -> BacktestTrade:
    return BacktestTrade(
        timestamp_ms   = 1_000,
        symbol         = "BTC/USDT",
        buy_exchange   = "bybit",
        sell_exchange  = "binance",
        raw_spread_bps = 20.0,
        slippage_bps   = 3.0,
        fee_bps        = 10.0,
        net_spread_bps = 7.0,
        size_usdt      = 100.0,
        pnl_usdt       = pnl,
        latency_ms     = 50.0,
        success        = success,
    )


# ──────────────────────────── SlippageModel ────────────────────────────

class TestSlippageModel:

    def test_fixed_bps_ignores_size(self):
        cfg   = _cfg(slippage_model=SlippageModelType.FIXED_BPS, slippage_fixed_bps=5.0)
        model = SlippageModel(cfg)
        assert model.estimate(1000.0, [], 100.0) == 5.0
        assert model.estimate(1.0,    [], 100.0) == 5.0

    def test_linear_grows_with_size(self):
        cfg   = _cfg(slippage_model=SlippageModelType.LINEAR, slippage_factor=1.0)
        model = SlippageModel(cfg)
        levels = [(100.0, 10.0)]  # ликвидность 1000 USDT
        small = model.estimate(10.0,  levels, 100.0)
        large = model.estimate(100.0, levels, 100.0)
        assert large > small

    def test_sqrt_grows_with_size(self):
        # sqrt(ratio) монотонно растёт с размером сделки
        levels = [(100.0, 10.0)]  # ликвидность 1000 USDT
        cfg  = _cfg(slippage_model=SlippageModelType.SQRT, slippage_factor=1.0)
        model = SlippageModel(cfg)
        small = model.estimate(10.0,  levels, 100.0)
        large = model.estimate(100.0, levels, 100.0)
        assert large > small

    def test_apply_reduces_spread(self):
        cfg   = _cfg(slippage_model=SlippageModelType.FIXED_BPS, slippage_fixed_bps=3.0)
        model = SlippageModel(cfg)
        net, total = model.apply(20.0, 100.0, [(100.0, 1.0)], [(100.0, 1.0)], 100.0)
        assert total == 6.0      # 3 + 3
        assert net   == 14.0     # 20 - 6

    def test_empty_levels_uses_size_as_liquidity(self):
        # при пустых уровнях ratio = size/size = 1.0, LINEAR даёт factor * 1.0 * 10000
        cfg   = _cfg(slippage_model=SlippageModelType.LINEAR, slippage_factor=0.01, slippage_fixed_bps=2.0)
        model = SlippageModel(cfg)
        bps = model.estimate(100.0, [], 100.0)
        assert bps == pytest.approx(100.0)  # 0.01 * 1.0 * 10000

    def test_zero_liquidity_falls_back_to_fixed(self):
        cfg   = _cfg(slippage_model=SlippageModelType.LINEAR, slippage_fixed_bps=2.0, slippage_factor=1.0)
        model = SlippageModel(cfg)
        # levels с нулевым qty
        bps = model.estimate(100.0, [(0.0, 0.0)], 100.0)
        assert bps == 2.0


# ──────────────────────────── LatencyModel ────────────────────────────

class TestLatencyModel:

    def test_fixed_always_returns_mean(self):
        cfg   = _cfg(latency_model=LatencyModelType.FIXED, latency_mean_ms=50.0)
        model = LatencyModel(cfg)
        for _ in range(20):
            assert model.sample() == 50.0

    def test_normal_positive(self):
        cfg   = _cfg(latency_model=LatencyModelType.NORMAL, latency_mean_ms=50.0, latency_std_ms=10.0)
        model = LatencyModel(cfg)
        for _ in range(50):
            assert model.sample() > 0

    def test_lognormal_positive(self):
        cfg   = _cfg(latency_model=LatencyModelType.LOGNORMAL, latency_mean_ms=50.0, latency_std_ms=20.0)
        model = LatencyModel(cfg)
        for _ in range(50):
            assert model.sample() > 0

    def test_lognormal_zero_std(self):
        cfg   = _cfg(latency_model=LatencyModelType.LOGNORMAL, latency_mean_ms=50.0, latency_std_ms=0.0)
        model = LatencyModel(cfg)
        sample = model.sample()
        assert sample > 0

    def test_latency_cost_bps(self):
        cfg   = _cfg()
        model = LatencyModel(cfg)
        cost  = model.latency_cost_bps(10.0)
        assert cost == pytest.approx(1.0)  # 10ms * 0.1 bps/ms


# ──────────────────────────── TradeSimulator ────────────────────────────

class TestTradeSimulator:

    def test_profitable_trade(self):
        cfg  = _cfg(
            min_spread_bps = 5.0,
            fee_bps        = 5.0,
            slippage_model = SlippageModelType.FIXED_BPS,
            slippage_fixed_bps = 1.0,
            latency_model  = LatencyModelType.FIXED,
            latency_mean_ms = 0.0,
        )
        sim  = TradeSimulator(cfg)
        buy  = _tick(Exchange.BYBIT,   best_ask=100.0, best_bid=100.5)
        sell = _tick(Exchange.BINANCE, best_ask=101.0, best_bid=101.0)
        # raw spread: (101.0 - 100.0) / 100.0 * 10000 = 100 bps
        trade = sim.simulate(buy, sell, 100.0)
        assert trade is not None
        assert trade.success
        assert trade.pnl_usdt > 0

    def test_below_min_spread_rejected(self):
        cfg  = _cfg(
            min_spread_bps = 100.0,  # порог выше реального спреда
            slippage_model = SlippageModelType.FIXED_BPS,
            slippage_fixed_bps = 0.0,
            latency_model  = LatencyModelType.FIXED,
            latency_mean_ms = 0.0,
        )
        sim  = TradeSimulator(cfg)
        buy  = _tick(Exchange.BYBIT,   best_ask=100.0, best_bid=99.0)
        sell = _tick(Exchange.BINANCE, best_ask=100.5, best_bid=100.5)
        # raw spread 50 bps < min 100 bps → rejected
        trade = sim.simulate(buy, sell, 100.0)
        assert trade is not None
        assert not trade.success
        assert trade.reason == "spread_below_min"

    def test_empty_asks_returns_none(self):
        cfg  = _cfg()
        sim  = TradeSimulator(cfg)
        buy  = OrderBookTick(1000, Exchange.BYBIT, "BTC/USDT", MarketType.SPOT, [], [])
        sell = _tick(Exchange.BINANCE, best_ask=101.0, best_bid=101.0)
        assert sim.simulate(buy, sell, 100.0) is None

    def test_size_for_tick_capped(self):
        cfg  = _cfg(max_size_usdt=100.0)
        sim  = TradeSimulator(cfg)
        # 3 уровня asks: суммарная ликвидность 300 USDT → capped до 100
        tick = OrderBookTick(
            1000, Exchange.BYBIT, "BTC/USDT", MarketType.SPOT,
            bids=[(99.0, 10.0)],
            asks=[(100.0, 1.0), (100.1, 1.0), (100.2, 1.0)],
        )
        assert sim.size_for_tick(tick) == 100.0

    def test_negative_net_spread(self):
        cfg  = _cfg(
            min_spread_bps     = 5.0,
            fee_bps            = 20.0,  # большая комиссия
            slippage_model     = SlippageModelType.FIXED_BPS,
            slippage_fixed_bps = 5.0,
            latency_model      = LatencyModelType.FIXED,
            latency_mean_ms    = 0.0,
        )
        sim  = TradeSimulator(cfg)
        buy  = _tick(Exchange.BYBIT,   best_ask=100.0, best_bid=99.0)
        sell = _tick(Exchange.BINANCE, best_ask=100.2, best_bid=100.2)
        # raw spread = 20 bps, slip = 10 bps, fee = 20 bps → net = -10 bps
        trade = sim.simulate(buy, sell, 100.0)
        assert trade is not None
        # raw_spread < min_spread → rejected before net check
        assert trade.reason in ("spread_below_min", "negative_net_spread")


# ──────────────────────────── OrderBookReplayer ────────────────────────────

class TestOrderBookReplayer:

    def test_no_pairs_same_exchange(self):
        replayer = OrderBookReplayer()
        ticks = [
            _tick(Exchange.BYBIT, ts=1000, best_ask=100.0, best_bid=99.0),
            _tick(Exchange.BYBIT, ts=1001, best_ask=101.0, best_bid=100.5),
        ]
        pairs = list(replayer.feed(ticks))
        assert pairs == []

    def test_detects_pair_direction1(self):
        replayer = OrderBookReplayer()
        # Bybit ask=100, Binance bid=101 → купить на Bybit, продать на Binance
        ticks = [
            _tick(Exchange.BYBIT,   ts=1000, best_ask=100.0, best_bid=99.0),
            _tick(Exchange.BINANCE, ts=1001, best_ask=102.0, best_bid=101.0),
        ]
        pairs = list(replayer.feed(ticks))
        assert len(pairs) == 1
        buy_t, sell_t = pairs[0]
        assert buy_t.exchange  == Exchange.BYBIT
        assert sell_t.exchange == Exchange.BINANCE

    def test_detects_pair_direction2(self):
        replayer = OrderBookReplayer()
        ticks = [
            _tick(Exchange.BINANCE, ts=1000, best_ask=100.0, best_bid=99.0),
            _tick(Exchange.BYBIT,   ts=1001, best_ask=102.0, best_bid=101.0),
        ]
        pairs = list(replayer.feed(ticks))
        assert len(pairs) == 1
        buy_t, sell_t = pairs[0]
        assert buy_t.exchange  == Exchange.BINANCE
        assert sell_t.exchange == Exchange.BYBIT

    def test_stale_tick_ignored(self):
        replayer = OrderBookReplayer(max_age_ms=100)
        ticks = [
            _tick(Exchange.BYBIT,   ts=1000,  best_ask=100.0, best_bid=99.0),
            _tick(Exchange.BINANCE, ts=10_000, best_ask=102.0, best_bid=101.0),
        ]
        pairs = list(replayer.feed(ticks))
        assert pairs == []

    def test_reset_clears_state(self):
        replayer = OrderBookReplayer()
        ticks = [_tick(Exchange.BYBIT, ts=1000)]
        list(replayer.feed(ticks))
        replayer.reset()
        assert replayer._latest == {}

    def test_different_symbols_not_paired(self):
        replayer = OrderBookReplayer()
        ticks = [
            _tick(Exchange.BYBIT,   symbol="BTC/USDT", ts=1000, best_ask=100.0, best_bid=99.0),
            _tick(Exchange.BINANCE, symbol="ETH/USDT", ts=1001, best_ask=102.0, best_bid=101.0),
        ]
        pairs = list(replayer.feed(ticks))
        assert pairs == []


# ──────────────────────────── BacktestMetrics ────────────────────────────

class TestBacktestMetrics:

    def test_empty_trades(self):
        cfg    = _cfg()
        result = BacktestResult(config=cfg)
        BacktestMetrics.compute(result)
        assert result.total_trades == 0
        assert result.sharpe_ratio == 0.0

    def test_win_rate(self):
        cfg    = _cfg()
        result = BacktestResult(config=cfg, trades=[
            _trade(10.0),
            _trade(-5.0),
            _trade(3.0),
        ])
        BacktestMetrics.compute(result)
        assert result.win_rate == pytest.approx(2/3)
        assert result.winning_trades == 2
        assert result.losing_trades  == 1

    def test_profit_factor(self):
        cfg    = _cfg()
        result = BacktestResult(config=cfg, trades=[
            _trade(20.0),
            _trade(-10.0),
        ])
        BacktestMetrics.compute(result)
        assert result.profit_factor == pytest.approx(2.0)

    def test_max_drawdown(self):
        cfg    = _cfg(initial_capital_usdt=1000.0)
        result = BacktestResult(config=cfg, trades=[
            _trade(100.0),
            _trade(-200.0),
            _trade(50.0),
        ])
        BacktestMetrics.compute(result)
        # пик = 1100, потом 900 → dd = 200/1100 ≈ 18.18%
        assert result.max_drawdown_pct > 0

    def test_total_return(self):
        cfg    = _cfg(initial_capital_usdt=1000.0)
        result = BacktestResult(config=cfg, trades=[
            _trade(100.0),
            _trade(100.0),
        ])
        BacktestMetrics.compute(result)
        assert result.total_return_pct == pytest.approx(20.0)

    def test_sharpe_positive_for_consistent_wins(self):
        cfg    = _cfg(trades_per_year_estimate=252.0)
        result = BacktestResult(config=cfg, trades=[_trade(10.0) for _ in range(50)])
        BacktestMetrics.compute(result)
        # Все сделки одинаковые → std=0 → sharpe=0
        assert result.sharpe_ratio == 0.0

    def test_sharpe_nonzero_mixed(self):
        import random
        random.seed(42)
        cfg    = _cfg(trades_per_year_estimate=252.0)
        trades = [_trade(random.uniform(-5, 15)) for _ in range(100)]
        result = BacktestResult(config=cfg, trades=trades)
        BacktestMetrics.compute(result)
        assert isinstance(result.sharpe_ratio, float)

    def test_equity_curve_length(self):
        cfg    = _cfg()
        result = BacktestResult(config=cfg, trades=[_trade(10.0) for _ in range(5)])
        BacktestMetrics.compute(result)
        assert len(result.equity_curve.equity)     == 5
        assert len(result.equity_curve.drawdowns)  == 5
        assert len(result.equity_curve.timestamps) == 5

    def test_profit_factor_no_losses(self):
        cfg    = _cfg()
        result = BacktestResult(config=cfg, trades=[_trade(5.0), _trade(10.0)])
        BacktestMetrics.compute(result)
        assert result.profit_factor == float("inf")


# ──────────────────────────── MonteCarloEngine ────────────────────────────

class TestMonteCarloEngine:

    def _base_result(self) -> BacktestResult:
        cfg    = _cfg(initial_capital_usdt=10_000.0)
        trades = [_trade(float(i)) for i in range(1, 21)]
        result = BacktestResult(config=cfg, trades=trades)
        return BacktestMetrics.compute(result)

    def test_returns_n_simulations(self):
        mc  = MonteCarloEngine(n_simulations=50)
        res = mc.run(self._base_result())
        assert res.n_simulations == 50
        assert len(res.results)  == 50

    def test_percentiles_ordered(self):
        mc  = MonteCarloEngine(n_simulations=200)
        res = mc.run(self._base_result())
        assert res.pnl_p5 <= res.pnl_p50 <= res.pnl_p95

    def test_empty_base_returns_empty(self):
        cfg    = _cfg()
        base   = BacktestResult(config=cfg)
        mc     = MonteCarloEngine(n_simulations=10)
        res    = mc.run(base)
        assert len(res.results) == 0

    def test_noise_produces_variation(self):
        mc    = MonteCarloEngine(n_simulations=100, noise_pct=0.5)
        base  = self._base_result()
        res   = mc.run(base)
        pnls  = [r.total_return_pct for r in res.results]
        assert max(pnls) != min(pnls)


# ──────────────────────────── BacktestEngine ────────────────────────────

class TestBacktestEngine:

    def _ticks(self) -> list[OrderBookTick]:
        """Простой поток: 10 пар тиков с реальным спредом."""
        ticks = []
        for i in range(10):
            ts = 1000 + i * 100
            ticks.append(OrderBookTick(
                timestamp_ms = ts,
                exchange     = Exchange.BYBIT,
                symbol       = "BTC/USDT",
                market_type  = MarketType.SPOT,
                bids         = [(99.0, 2.0)],
                asks         = [(100.0, 2.0)],
            ))
            ticks.append(OrderBookTick(
                timestamp_ms = ts + 10,
                exchange     = Exchange.BINANCE,
                symbol       = "BTC/USDT",
                market_type  = MarketType.SPOT,
                bids         = [(101.0, 2.0)],
                asks         = [(102.0, 2.0)],
            ))
        return sorted(ticks, key=lambda t: t.timestamp_ms)

    def test_run_produces_trades(self):
        cfg    = _cfg(
            min_spread_bps     = 5.0,
            fee_bps            = 5.0,
            slippage_model     = SlippageModelType.FIXED_BPS,
            slippage_fixed_bps = 1.0,
            latency_model      = LatencyModelType.FIXED,
            latency_mean_ms    = 0.0,
        )
        engine = BacktestEngine(cfg)
        result = engine.run(self._ticks())
        assert result.total_trades > 0

    def test_run_populates_metrics(self):
        cfg    = _cfg(
            slippage_model  = SlippageModelType.FIXED_BPS,
            slippage_fixed_bps = 1.0,
            latency_model   = LatencyModelType.FIXED,
            latency_mean_ms = 0.0,
        )
        engine = BacktestEngine(cfg)
        result = engine.run(self._ticks())
        assert isinstance(result.sharpe_ratio, float)
        assert isinstance(result.max_drawdown_pct, float)
        assert len(result.equity_curve.equity) == result.total_trades

    def test_run_empty_ticks(self):
        engine = BacktestEngine(_cfg())
        result = engine.run([])
        assert result.total_trades == 0

    def test_monte_carlo_from_engine(self):
        cfg    = _cfg(
            slippage_model  = SlippageModelType.FIXED_BPS,
            slippage_fixed_bps = 1.0,
            latency_model   = LatencyModelType.FIXED,
            latency_mean_ms = 0.0,
        )
        engine = BacktestEngine(cfg)
        result = engine.run(self._ticks())
        mc     = engine.run_monte_carlo(result, n_simulations=50)
        assert mc.n_simulations == 50

    def test_single_exchange_no_trades(self):
        cfg    = _cfg()
        engine = BacktestEngine(cfg)
        ticks  = [
            _tick(Exchange.BYBIT, ts=1000 + i * 100)
            for i in range(10)
        ]
        result = engine.run(ticks)
        assert result.total_trades == 0
