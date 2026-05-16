"""Тесты Storage Layer: репозитории, кэш, pub/sub."""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import Exchange, MarketType, Order, OrderSide, OrderStatus, OrderType, SpreadOpportunity
from execution.models import ExecutionResult, LegResult, TradeState
from storage.cache import RedisCache
from storage.pubsub import CH_SPREADS, CH_TRADES, CH_RISK_ALERTS, RedisPubSub
from storage.repository.metrics import LatencyRepository, MetricsRepository
from storage.repository.positions import PositionRepository
from storage.repository.spreads import SpreadRepository
from storage.repository.trades import TradeRepository
from storage.repository.watchdog_log import WatchdogLogRepository


# ---- вспомогательные фабрики ----

def _opp() -> SpreadOpportunity:
    return SpreadOpportunity(
        buy_exchange=Exchange.BINANCE,
        sell_exchange=Exchange.BYBIT,
        symbol="BTCUSDT",
        buy_market=MarketType.SPOT,
        sell_market=MarketType.SPOT,
        raw_spread_bps=30.0,
        effective_spread_bps=20.0,
        net_spread_bps=15.0,
        executable_spread_bps=10.0,
        buy_price=50000.0,
        sell_price=50150.0,
        max_size_usdt=500.0,
        timestamp_ms=int(time.time() * 1000),
    )


def _order(exchange: Exchange, side: OrderSide, price: float) -> Order:
    return Order(
        id="ord-001",
        client_order_id="coid-001",
        exchange=exchange,
        symbol="BTCUSDT",
        market_type=MarketType.SPOT,
        side=side,
        order_type=OrderType.IOC,
        status=OrderStatus.FILLED,
        price=price,
        qty=0.01,
        filled_qty=0.01,
        avg_fill_price=price,
    )


def _leg(exchange: Exchange, side: OrderSide, price: float) -> LegResult:
    return LegResult(
        order=_order(exchange, side, price),
        requested_qty=0.01,
        filled_qty=0.01,
        avg_price=price,
        fee_usdt=2.5,
        success=True,
    )


def _result() -> ExecutionResult:
    return ExecutionResult(
        opportunity=_opp(),
        state=TradeState.COMPLETED,
        buy_leg=_leg(Exchange.BINANCE, OrderSide.BUY, 50000.0),
        sell_leg=_leg(Exchange.BYBIT, OrderSide.SELL, 50150.0),
        execution_time_ms=45,
    )


def _mock_pool(**kwargs) -> MagicMock:
    """Создать замоканный DatabasePool."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(**kwargs)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock(return_value="OK")
    pool.fetchval = AsyncMock(return_value=0.0)
    return pool


def _mock_redis() -> MagicMock:
    """Создать замоканный RedisClient."""
    redis_client = MagicMock()
    r = MagicMock()
    r.hset = AsyncMock(return_value=1)
    r.hgetall = AsyncMock(return_value={})
    r.expire = AsyncMock(return_value=True)
    r.publish = AsyncMock(return_value=1)
    r.scan_iter = _make_async_iter([])
    redis_client.client = r
    return redis_client


def _make_async_iter(items):
    """Создать async iterator из списка."""
    async def _aiter():
        for item in items:
            yield item
    return _aiter()


# ---- TradeRepository ----

def test_trade_insert_calls_fetchrow():
    async def _run():
        pool = _mock_pool(return_value={"id": 42})
        repo = TradeRepository(pool)
        trade_id = await repo.insert("session-1", _result())
        return trade_id, pool.fetchrow.called, pool.execute.called

    trade_id, fetchrow_called, execute_called = asyncio.run(_run())
    assert trade_id == 42
    assert fetchrow_called        # INSERT INTO trades
    assert execute_called         # INSERT INTO fills (x2)


def test_trade_insert_sql_contains_table():
    async def _run():
        pool = _mock_pool(return_value={"id": 1})
        repo = TradeRepository(pool)
        await repo.insert("session-1", _result())
        return pool.fetchrow.call_args[0][0]  # первый аргумент (SQL)

    sql = asyncio.run(_run())
    assert "INSERT INTO trades" in sql
    assert "RETURNING id" in sql


def test_trade_insert_fills_for_both_legs():
    """Каждая из двух ног создаёт запись в fills."""
    async def _run():
        pool = _mock_pool(return_value={"id": 1})
        repo = TradeRepository(pool)
        await repo.insert("session-1", _result())
        return pool.execute.call_count

    count = asyncio.run(_run())
    assert count == 2  # buy fill + sell fill


def test_trade_insert_no_fill_if_no_leg():
    """Нога None → fill не создаётся."""
    async def _run():
        pool = _mock_pool(return_value={"id": 1})
        repo = TradeRepository(pool)
        result = ExecutionResult(
            opportunity=_opp(),
            state=TradeState.FAILED,
            buy_leg=None,
            sell_leg=None,
        )
        await repo.insert("session-1", result)
        return pool.execute.call_count

    count = asyncio.run(_run())
    assert count == 0


def test_trade_get_returns_none_when_missing():
    async def _run():
        pool = _mock_pool(return_value=None)
        repo = TradeRepository(pool)
        return await repo.get(999)

    result = asyncio.run(_run())
    assert result is None


def test_trade_list_recent():
    async def _run():
        fake_row = {"id": 1, "symbol": "BTCUSDT", "pnl_usdt": 3.5}
        pool = _mock_pool()
        pool.fetch = AsyncMock(return_value=[fake_row])
        repo = TradeRepository(pool)
        rows = await repo.list_recent("session-1", limit=50)
        sql = pool.fetch.call_args[0][0]
        return rows, sql

    rows, sql = asyncio.run(_run())
    assert len(rows) == 1
    assert "ORDER BY created_at DESC" in sql


def test_trade_total_pnl():
    async def _run():
        pool = _mock_pool()
        pool.fetchval = AsyncMock(return_value=123.45)
        repo = TradeRepository(pool)
        pnl = await repo.total_pnl("session-1")
        return pnl, pool.fetchval.call_args[0][0]

    pnl, sql = asyncio.run(_run())
    assert pnl == pytest.approx(123.45)
    assert "SUM(pnl_usdt)" in sql


# ---- PositionRepository ----

def test_position_upsert_sql():
    async def _run():
        pool = _mock_pool()
        repo = PositionRepository(pool)
        await repo.upsert("session-1", "binance", "BTCUSDT", 0.01, 500.0, "long")
        return pool.execute.call_args[0][0]

    sql = asyncio.run(_run())
    assert "INSERT INTO positions" in sql
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql


def test_position_get_none_when_missing():
    async def _run():
        pool = _mock_pool(return_value=None)
        repo = PositionRepository(pool)
        return await repo.get("session-1", "binance", "BTCUSDT")

    result = asyncio.run(_run())
    assert result is None


def test_position_get_all():
    async def _run():
        fake = {"exchange": "binance", "symbol": "BTCUSDT", "qty": 0.01}
        pool = _mock_pool()
        pool.fetch = AsyncMock(return_value=[fake])
        repo = PositionRepository(pool)
        rows = await repo.get_all("session-1")
        sql = pool.fetch.call_args[0][0]
        return rows, sql

    rows, sql = asyncio.run(_run())
    assert len(rows) == 1
    assert "ABS(qty)" in sql


# ---- SpreadRepository ----

def test_spread_insert_returns_id():
    async def _run():
        pool = _mock_pool(return_value={"id": 7})
        repo = SpreadRepository(pool)
        spread_id = await repo.insert(_opp())
        return spread_id, pool.fetchrow.call_args[0][0]

    spread_id, sql = asyncio.run(_run())
    assert spread_id == 7
    assert "INSERT INTO spreads" in sql


def test_spread_mark_executed():
    async def _run():
        pool = _mock_pool()
        repo = SpreadRepository(pool)
        await repo.mark_executed(7)
        return pool.execute.call_args[0]

    args = asyncio.run(_run())
    assert "UPDATE spreads SET executed=TRUE" in args[0]
    assert args[1] == 7


def test_spread_list_recent():
    async def _run():
        pool = _mock_pool()
        pool.fetch = AsyncMock(return_value=[{"id": 1, "symbol": "BTCUSDT"}])
        repo = SpreadRepository(pool)
        rows = await repo.list_recent("BTCUSDT", limit=10)
        return rows

    rows = asyncio.run(_run())
    assert len(rows) == 1


# ---- MetricsRepository / LatencyRepository ----

def test_latency_insert():
    async def _run():
        pool = _mock_pool()
        repo = LatencyRepository(pool)
        await repo.insert("binance", "ws_message", 350, "BTCUSDT")
        return pool.execute.call_args[0]

    args = asyncio.run(_run())
    assert "INSERT INTO latency" in args[0]
    assert args[1] == "binance"
    assert args[2] == "ws_message"
    assert args[3] == 350


def test_latency_percentiles_empty():
    async def _run():
        pool = _mock_pool()
        pool.fetch = AsyncMock(return_value=[])
        repo = LatencyRepository(pool)
        return await repo.percentiles("binance", "ws_message")

    result = asyncio.run(_run())
    assert result == {"p50": 0, "p95": 0, "p99": 0, "samples": 0}


def test_metrics_insert():
    async def _run():
        pool = _mock_pool()
        repo = MetricsRepository(pool)
        await repo.insert("scan_count", 1234, {"symbol": "BTCUSDT"})
        return pool.execute.call_args[0]

    args = asyncio.run(_run())
    assert "INSERT INTO metrics" in args[0]
    assert args[1] == "scan_count"
    assert args[2] == 1234
    labels = json.loads(args[3])
    assert labels["symbol"] == "BTCUSDT"


# ---- WatchdogLogRepository ----

def test_watchdog_log_insert():
    async def _run():
        pool = _mock_pool()
        repo = WatchdogLogRepository(pool)
        await repo.insert("BTCUSDT", "restart", "starting", 3, "timeout")
        return pool.execute.call_args[0]

    args = asyncio.run(_run())
    assert "INSERT INTO watchdog_logs" in args[0]
    assert args[1] == "BTCUSDT"
    assert args[2] == "restart"


def test_watchdog_log_list_recent():
    async def _run():
        pool = _mock_pool()
        pool.fetch = AsyncMock(return_value=[{"id": 1, "event_type": "failed"}])
        repo = WatchdogLogRepository(pool)
        return await repo.list_recent("BTCUSDT", limit=5)

    rows = asyncio.run(_run())
    assert rows[0]["event_type"] == "failed"


def test_watchdog_error_count():
    async def _run():
        pool = _mock_pool()
        pool.fetchval = AsyncMock(return_value=5)
        repo = WatchdogLogRepository(pool)
        count = await repo.error_count("BTCUSDT", window_s=3600)
        sql = pool.fetchval.call_args[0][0]
        return count, sql

    count, sql = asyncio.run(_run())
    assert count == 5
    assert "event_type='failed'" in sql


# ---- RedisCache ----

def test_cache_set_position():
    async def _run():
        rc = _mock_redis()
        cache = RedisCache(rc)
        await cache.set_position("sess-1", "binance", "BTCUSDT", 0.01, 500.0, "long")
        hset_call = rc.client.hset.call_args
        return hset_call

    call = asyncio.run(_run())
    assert call is not None
    key = call[0][0]
    assert key == "pos:sess-1:binance:BTCUSDT"
    mapping = call[1]["mapping"]
    assert mapping["qty"] == 0.01
    assert mapping["side"] == "long"


def test_cache_get_position_none():
    async def _run():
        rc = _mock_redis()
        rc.client.hgetall = AsyncMock(return_value={})
        cache = RedisCache(rc)
        return await cache.get_position("sess-1", "binance", "BTCUSDT")

    result = asyncio.run(_run())
    assert result is None


def test_cache_get_position_data():
    async def _run():
        rc = _mock_redis()
        rc.client.hgetall = AsyncMock(return_value={
            "qty": "0.01", "cost_usdt": "500.0", "side": "long", "updated_at": "1234567.0"
        })
        cache = RedisCache(rc)
        return await cache.get_position("sess-1", "binance", "BTCUSDT")

    result = asyncio.run(_run())
    assert result["qty"] == pytest.approx(0.01)
    assert result["side"] == "long"


def test_cache_set_pnl():
    async def _run():
        rc = _mock_redis()
        cache = RedisCache(rc)
        await cache.set_pnl("sess-1", 100.0, 120.0, 1.5, 25)
        mapping = rc.client.hset.call_args[1]["mapping"]
        return mapping

    mapping = asyncio.run(_run())
    assert mapping["realized_pnl"] == 100.0
    assert mapping["drawdown_pct"] == 1.5
    assert mapping["trade_count"] == 25


def test_cache_get_pnl_none():
    async def _run():
        rc = _mock_redis()
        rc.client.hgetall = AsyncMock(return_value={})
        cache = RedisCache(rc)
        return await cache.get_pnl("sess-1")

    result = asyncio.run(_run())
    assert result is None


def test_cache_set_balance():
    async def _run():
        rc = _mock_redis()
        cache = RedisCache(rc)
        await cache.set_balance("binance", "USDT", 9500.0, 10000.0)
        key = rc.client.hset.call_args[0][0]
        mapping = rc.client.hset.call_args[1]["mapping"]
        return key, mapping

    key, mapping = asyncio.run(_run())
    assert key == "balance:binance:USDT"
    assert mapping["available"] == 9500.0
    assert mapping["total"] == 10000.0


# ---- RedisPubSub ----

def test_pubsub_publish_spread():
    async def _run():
        rc = _mock_redis()
        pubsub = RedisPubSub(rc)
        await pubsub.publish_spread(_opp())
        channel, payload = rc.client.publish.call_args[0]
        return channel, json.loads(payload)

    channel, data = asyncio.run(_run())
    assert channel == CH_SPREADS
    assert data["symbol"] == "BTCUSDT"
    assert "executable_spread_bps" in data
    assert data["buy_exchange"] == "binance"


def test_pubsub_publish_trade():
    async def _run():
        rc = _mock_redis()
        pubsub = RedisPubSub(rc)
        await pubsub.publish_trade(_result())
        channel, payload = rc.client.publish.call_args[0]
        return channel, json.loads(payload)

    channel, data = asyncio.run(_run())
    assert channel == CH_TRADES
    assert "pnl_usdt" in data
    assert data["state"] == "completed"


def test_pubsub_publish_risk_alert():
    async def _run():
        rc = _mock_redis()
        pubsub = RedisPubSub(rc)
        await pubsub.publish_risk_alert("BTCUSDT", "max_exposure", 5000.0)
        channel, payload = rc.client.publish.call_args[0]
        return channel, json.loads(payload)

    channel, data = asyncio.run(_run())
    assert channel == CH_RISK_ALERTS
    assert data["symbol"] == "BTCUSDT"
    assert data["violation"] == "max_exposure"
    assert data["value"] == 5000.0


def test_pubsub_subscribe_creates_task():
    async def _run():
        rc = _mock_redis()
        # Мокируем pubsub.listen() чтобы сразу завершился
        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()

        async def _empty_listen():
            return
            yield  # делает функцию async generator

        mock_pubsub.listen = _empty_listen
        rc.client.pubsub = MagicMock(return_value=mock_pubsub)

        pubsub = RedisPubSub(rc)
        received = []

        async def callback(channel: str, data: dict) -> None:
            received.append(data)

        task = await pubsub.subscribe(CH_TRADES, callback)
        await asyncio.wait_for(task, timeout=1.0)
        return isinstance(task, asyncio.Task)

    is_task = asyncio.run(_run())
    assert is_task
