"""Тесты Watchdog System: WorkerPolicy, StrategyWorker, WorkerSupervisor, WatchdogOrchestrator."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import Exchange, MarketType, SpreadOpportunity
from execution.models import ExecutionResult, TradeState
from risk.engine import RiskEngine, RiskViolation
from watchdog.orchestrator import WatchdogOrchestrator
from watchdog.policies import RestartPolicy, WorkerPolicy
from watchdog.supervisor import WorkerSupervisor
from watchdog.worker import StrategyWorker, WorkerState


# ---- вспомогательные фабрики ----

def _opp(symbol: str = "BTCUSDT") -> SpreadOpportunity:
    return SpreadOpportunity(
        buy_exchange=Exchange.BINANCE,
        sell_exchange=Exchange.BYBIT,
        symbol=symbol,
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


def _mock_result(success: bool = True) -> ExecutionResult:
    opp = _opp()
    result = MagicMock(spec=ExecutionResult)
    result.opportunity = opp
    result.state = TradeState.COMPLETED if success else TradeState.FAILED
    result.pnl_usdt = 3.5 if success else -1.0
    result.realized_spread_bps = 10.0
    result.success = success
    return result


def _mock_risk(allow: bool = True, violation: RiskViolation = RiskViolation.OK) -> RiskEngine:
    risk = MagicMock(spec=RiskEngine)
    risk.check.return_value = (allow, violation)
    risk.on_result.return_value = None
    return risk


def _mock_execution(result: ExecutionResult | None = None) -> object:
    exc_eng = MagicMock()
    exc_eng.execute = AsyncMock(return_value=result or _mock_result())
    return exc_eng


def _worker(
    symbol: str = "BTCUSDT",
    risk: RiskEngine | None = None,
    execution: object | None = None,
    policy: WorkerPolicy | None = None,
) -> StrategyWorker:
    return StrategyWorker(
        symbol=symbol,
        risk_engine=risk or _mock_risk(),
        execution_engine=execution or _mock_execution(),
        policy=policy,
    )


# ---- WorkerPolicy ----

def test_policy_backoff_exponential():
    p = WorkerPolicy(base_backoff_s=1.0, max_backoff_s=60.0)
    assert p.backoff_for(0) == pytest.approx(1.0)
    assert p.backoff_for(1) == pytest.approx(2.0)
    assert p.backoff_for(2) == pytest.approx(4.0)
    assert p.backoff_for(3) == pytest.approx(8.0)


def test_policy_backoff_capped():
    p = WorkerPolicy(base_backoff_s=1.0, max_backoff_s=10.0)
    assert p.backoff_for(10) == pytest.approx(10.0)


def test_policy_defaults():
    p = WorkerPolicy()
    assert p.restart == RestartPolicy.ON_FAILURE
    assert p.max_restarts == 10
    assert p.heartbeat_timeout_s == 30.0


# ---- StrategyWorker — состояния ----

def test_worker_initial_state():
    w = _worker()
    assert w.state == WorkerState.STARTING
    assert w.stats.opportunities_seen == 0
    assert w.stats.trades_executed == 0


def test_worker_start_changes_state():
    async def _run():
        w = _worker()
        w.start()
        await asyncio.sleep(0.05)
        state = w.state
        await w.stop()
        return state

    state = asyncio.run(_run())
    assert state == WorkerState.RUNNING


def test_worker_stop_changes_state():
    async def _run():
        w = _worker()
        w.start()
        await asyncio.sleep(0.05)
        await w.stop()
        return w.state

    state = asyncio.run(_run())
    assert state == WorkerState.STOPPED


# ---- StrategyWorker — обработка opportunity ----

def test_worker_processes_opportunity():
    """Разрешённая opportunity должна вызвать execute и увеличить счётчик."""
    async def _run():
        risk = _mock_risk(allow=True)
        exc = _mock_execution()
        w = StrategyWorker("BTCUSDT", risk, exc)
        w.start()
        await asyncio.sleep(0.02)

        w.push_opportunity(_opp())
        await asyncio.sleep(0.1)

        trades = w.stats.trades_executed
        await w.stop()
        return trades, exc.execute.call_count

    trades, call_count = asyncio.run(_run())
    assert trades == 1
    assert call_count == 1


def test_worker_skips_on_risk_violation():
    """Отклонённая риск-проверкой opportunity увеличивает trades_skipped."""
    async def _run():
        risk = _mock_risk(allow=False, violation=RiskViolation.MAX_EXPOSURE)
        exc = _mock_execution()
        w = StrategyWorker("BTCUSDT", risk, exc)
        w.start()
        await asyncio.sleep(0.02)

        w.push_opportunity(_opp())
        await asyncio.sleep(0.1)

        skipped = w.stats.trades_skipped
        executed = w.stats.trades_executed
        await w.stop()
        return skipped, executed

    skipped, executed = asyncio.run(_run())
    assert skipped == 1
    assert executed == 0


def test_worker_calls_on_trade_done():
    """После успешной сделки вызывается колбэк on_trade_done."""
    async def _run():
        results = []

        async def callback(symbol: str, info: dict) -> None:
            results.append((symbol, info))

        risk = _mock_risk(allow=True)
        exc = _mock_execution(_mock_result(success=True))
        w = StrategyWorker("BTCUSDT", risk, exc, on_trade_done=callback)
        w.start()
        await asyncio.sleep(0.02)

        w.push_opportunity(_opp())
        await asyncio.sleep(0.1)

        await w.stop()
        return results

    results = asyncio.run(_run())
    assert len(results) == 1
    assert results[0][0] == "BTCUSDT"
    assert "pnl" in results[0][1]


def test_worker_queue_overflow():
    """push_opportunity возвращает False когда очередь (maxsize=100) переполнена."""
    w = _worker()
    opp = _opp()
    # Заполняем очередь без запуска воркера
    pushed = [w.push_opportunity(opp) for _ in range(101)]
    assert pushed[-1] is False  # последний не вошёл
    assert pushed[0] is True


def test_worker_handles_execution_exception():
    """Исключение в execute() не должно завалить воркер."""
    async def _run():
        risk = _mock_risk(allow=True)
        exc_eng = MagicMock()
        exc_eng.execute = AsyncMock(side_effect=RuntimeError("биржа упала"))

        w = StrategyWorker("BTCUSDT", risk, exc_eng)
        w.start()
        await asyncio.sleep(0.02)

        w.push_opportunity(_opp())
        await asyncio.sleep(0.1)

        state = w.state
        await w.stop()
        return state

    state = asyncio.run(_run())
    # Воркер должен остаться RUNNING — execute-ошибка не должна его убить
    assert state in (WorkerState.RUNNING, WorkerState.STOPPED)


def test_worker_failed_on_internal_exception():
    """Исключение в _run_loop (не в execute) → state = FAILED."""
    async def _run():
        risk = MagicMock(spec=RiskEngine)
        risk.check.side_effect = RuntimeError("критическая ошибка")

        exc = _mock_execution()
        w = StrategyWorker("BTCUSDT", risk, exc)
        w.start()
        await asyncio.sleep(0.02)

        w.push_opportunity(_opp())
        await asyncio.sleep(0.15)

        return w.state, w.stats.last_error

    state, error = asyncio.run(_run())
    assert state == WorkerState.FAILED
    assert "критическая ошибка" in error


def test_worker_snapshot_structure():
    w = _worker()
    snap = w.snapshot()
    assert "symbol" in snap
    assert "state" in snap
    assert "restarts" in snap
    assert "trades_executed" in snap
    assert "heartbeat_age_s" in snap


# ---- WorkerSupervisor ----

def test_supervisor_add_and_count():
    async def _run():
        sup = WorkerSupervisor()
        sup.add_worker(_worker("BTCUSDT"))
        sup.add_worker(_worker("ETHUSDT"))
        count = sup.worker_count
        await asyncio.gather(
            sup.remove_worker("BTCUSDT"),
            sup.remove_worker("ETHUSDT"),
        )
        return count

    count = asyncio.run(_run())
    assert count == 2


def test_supervisor_get_worker():
    async def _run():
        sup = WorkerSupervisor()
        w = _worker("BTCUSDT")
        sup.add_worker(w)
        found = sup.get_worker("BTCUSDT")
        missing = sup.get_worker("XRPUSDT")
        await sup.remove_worker("BTCUSDT")
        return found, missing

    found, missing = asyncio.run(_run())
    assert found is not None
    assert missing is None


def test_supervisor_remove_worker():
    async def _run():
        sup = WorkerSupervisor()
        sup.add_worker(_worker("BTCUSDT"))
        await sup.remove_worker("BTCUSDT")
        return sup.worker_count

    count = asyncio.run(_run())
    assert count == 0


def test_supervisor_restarts_failed_worker():
    """Упавший воркер должен быть перезапущен супервизором."""
    async def _run():
        # Политика: немедленный перезапуск (backoff=0)
        policy = WorkerPolicy(
            restart=RestartPolicy.ON_FAILURE,
            max_restarts=3,
            base_backoff_s=0.0,
        )
        w = _worker("BTCUSDT", policy=policy)
        sup = WorkerSupervisor()
        sup.WATCHDOG_INTERVAL_S = 0.05  # быстрый watchdog для теста
        sup.add_worker(w)

        await asyncio.sleep(0.02)

        # Принудительно переводим в FAILED
        w.state = WorkerState.FAILED

        await asyncio.sleep(0.2)  # ждём watchdog + delayed start

        state = w.state
        restarts = w.stats.restarts
        await sup.stop()
        return state, restarts

    state, restarts = asyncio.run(_run())
    assert restarts >= 1
    assert state in (WorkerState.RUNNING, WorkerState.STARTING)


def test_supervisor_disables_after_max_restarts():
    """После превышения max_restarts воркер переходит в DISABLED."""
    async def _run():
        policy = WorkerPolicy(
            restart=RestartPolicy.ON_FAILURE,
            max_restarts=2,
            base_backoff_s=0.0,
        )
        w = _worker("BTCUSDT", policy=policy)
        sup = WorkerSupervisor()
        sup.WATCHDOG_INTERVAL_S = 0.05
        sup.add_worker(w)

        await asyncio.sleep(0.02)

        # Симулируем повторные падения
        for _ in range(3):
            w.stats.restarts += 1
        w.state = WorkerState.FAILED

        await asyncio.sleep(0.2)

        state = w.state
        await sup.stop()
        return state

    state = asyncio.run(_run())
    assert state == WorkerState.DISABLED


def test_supervisor_never_policy():
    """NEVER — воркер сразу переходит в DISABLED без перезапуска."""
    async def _run():
        policy = WorkerPolicy(restart=RestartPolicy.NEVER)
        w = _worker("BTCUSDT", policy=policy)
        sup = WorkerSupervisor()
        sup.WATCHDOG_INTERVAL_S = 0.05
        sup.add_worker(w)

        await asyncio.sleep(0.02)
        w.state = WorkerState.FAILED
        await asyncio.sleep(0.2)

        state = w.state
        restarts = w.stats.restarts
        await sup.stop()
        return state, restarts

    state, restarts = asyncio.run(_run())
    assert state == WorkerState.DISABLED
    assert restarts == 0


def test_supervisor_stop_cleans_up():
    """stop() корректно завершает watchdog и все воркеры (STOPPED)."""
    async def _run():
        sup = WorkerSupervisor()
        sup.add_worker(_worker("BTCUSDT"))
        sup.add_worker(_worker("ETHUSDT"))
        await sup.stop()
        return [w.state for w in sup._workers.values()]

    states = asyncio.run(_run())
    assert all(s == WorkerState.STOPPED for s in states)


def test_supervisor_snapshot_structure():
    async def _run():
        sup = WorkerSupervisor()
        sup.add_worker(_worker("BTCUSDT"))
        snap = sup.snapshot()
        await sup.stop()
        return snap

    snap = asyncio.run(_run())
    assert "total" in snap
    assert "running" in snap
    assert "workers" in snap


# ---- WatchdogOrchestrator ----

def test_orchestrator_routes_opportunity():
    """on_opportunity() должен доставить opportunity к воркеру."""
    async def _run():
        risk = _mock_risk(allow=True)
        exc = _mock_execution()

        orch = WatchdogOrchestrator()
        w = StrategyWorker("BTCUSDT", risk, exc)
        orch.add_live_worker(w)

        await asyncio.sleep(0.02)

        orch.on_opportunity(_opp("BTCUSDT"))
        await asyncio.sleep(0.1)

        routed = orch.status()["routed_opportunities"]
        trades = w.stats.trades_executed
        await orch.stop()
        return routed, trades

    routed, trades = asyncio.run(_run())
    assert routed == 1
    assert trades == 1


def test_orchestrator_missed_no_worker():
    """Opportunity для незарегистрированного символа → missed++."""
    orch = WatchdogOrchestrator()
    orch.on_opportunity(_opp("XRPUSDT"))
    assert orch.status()["missed_opportunities"] == 1
    assert orch.status()["routed_opportunities"] == 0


def test_orchestrator_missed_queue_full():
    """Переполненная очередь → missed++."""
    async def _run():
        risk = _mock_risk(allow=True)
        exc = _mock_execution()

        # Не запускаем воркер — очередь не дренируется
        w = StrategyWorker("BTCUSDT", risk, exc)
        # Заполняем очередь до краёв
        for _ in range(100):
            w._queue.put_nowait(_opp())

        orch = WatchdogOrchestrator()
        orch._supervisor._workers["BTCUSDT"] = w

        orch.on_opportunity(_opp("BTCUSDT"))
        return orch.status()["missed_opportunities"]

    missed = asyncio.run(_run())
    assert missed == 1


def test_orchestrator_status_structure():
    async def _run():
        orch = WatchdogOrchestrator()
        w = _worker("BTCUSDT")
        orch.add_live_worker(w)
        status = orch.status()
        await orch.stop()
        return status

    status = asyncio.run(_run())
    assert "routed_opportunities" in status
    assert "missed_opportunities" in status
    assert "total" in status
    assert "workers" in status


def test_orchestrator_register_and_start():
    """register_symbol + start() должны создать и запустить воркер."""
    async def _run():
        risk = _mock_risk()
        exc = _mock_execution()

        orch = WatchdogOrchestrator()
        orch.register_symbol("BTCUSDT", risk, exc)
        await orch.start()

        await asyncio.sleep(0.05)
        total = orch.status()["total"]
        await orch.stop()
        return total

    total = asyncio.run(_run())
    assert total == 1


def test_orchestrator_multiple_symbols():
    """500 воркеров должны регистрироваться без ошибок."""
    async def _run():
        orch = WatchdogOrchestrator()
        for i in range(500):
            orch.register_symbol(f"PAIR{i}USDT", _mock_risk(), _mock_execution())
        await orch.start()
        await asyncio.sleep(0.1)
        total = orch.status()["total"]
        await orch.stop()
        return total

    total = asyncio.run(_run())
    assert total == 500
