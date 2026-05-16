"""StrategyWorker: независимый воркер для одной торговой пары."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable

import structlog

from core.models import SpreadOpportunity
from risk.engine import RiskEngine, RiskViolation
from watchdog.policies import WorkerPolicy

logger = structlog.get_logger(__name__)


class WorkerState(str, Enum):
    STARTING  = "starting"
    RUNNING   = "running"
    STOPPED   = "stopped"   # штатная остановка
    FAILED    = "failed"    # упал с исключением
    DISABLED  = "disabled"  # превышен лимит перезапусков


@dataclass
class WorkerStats:
    restarts: int = 0
    opportunities_seen: int = 0
    trades_executed: int = 0
    trades_skipped: int = 0
    last_heartbeat_ts: float = field(default_factory=time.monotonic)
    last_error: str = ""

    def heartbeat(self) -> None:
        self.last_heartbeat_ts = time.monotonic()

    def heartbeat_age_s(self) -> float:
        return time.monotonic() - self.last_heartbeat_ts


class StrategyWorker:
    """
    Независимый воркер для одной торговой пары.

    Жизненный цикл:
      start() → asyncio.Task → _run_loop() (читает из queue) → stop()

    Изолирован: любое исключение внутри run() не распространяется наружу —
    фиксируется в stats.last_error и переводит воркер в состояние FAILED,
    откуда supervisor его перезапускает согласно политике.

    Принимает ExecutionEngine как Any чтобы не создавать circular imports
    и не требовать реального движка в тестах (достаточно duck-typing).
    """

    def __init__(
        self,
        symbol: str,
        risk_engine: RiskEngine,
        execution_engine: object,          # ExecutionEngine (duck-type)
        policy: WorkerPolicy | None = None,
        on_trade_done: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> None:
        self.symbol = symbol
        self._risk = risk_engine
        self._execution = execution_engine
        self._policy = policy or WorkerPolicy()
        self._on_trade_done = on_trade_done

        self._queue: asyncio.Queue[SpreadOpportunity | None] = asyncio.Queue(maxsize=100)
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        self.state = WorkerState.STARTING
        self.stats = WorkerStats()

        self._log = logger.bind(worker=symbol)

    # ---- публичный интерфейс ----

    def start(self) -> None:
        """Запустить воркер как asyncio.Task."""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self.state = WorkerState.STARTING
        self._task = asyncio.create_task(self._run_safe(), name=f"worker:{self.symbol}")

    async def stop(self) -> None:
        """Штатная остановка: подождать завершения текущей задачи (до 5с)."""
        self._stop_event.set()
        await self._queue.put(None)  # разбудить заблокированный get()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self.state = WorkerState.STOPPED

    def push_opportunity(self, opp: SpreadOpportunity) -> bool:
        """Поставить opportunity в очередь. Возвращает False если очередь переполнена."""
        try:
            self._queue.put_nowait(opp)
            return True
        except asyncio.QueueFull:
            return False

    def snapshot(self) -> dict:
        return {
            "symbol": self.symbol,
            "state": self.state.value,
            "restarts": self.stats.restarts,
            "opportunities_seen": self.stats.opportunities_seen,
            "trades_executed": self.stats.trades_executed,
            "trades_skipped": self.stats.trades_skipped,
            "heartbeat_age_s": round(self.stats.heartbeat_age_s(), 1),
            "last_error": self.stats.last_error,
        }

    # ---- внутренние методы ----

    async def _run_safe(self) -> None:
        """Обёртка: перехватывает исключения, выставляет итоговый state."""
        try:
            self.state = WorkerState.RUNNING
            await self._run_loop()
            if self.state != WorkerState.DISABLED:
                self.state = WorkerState.STOPPED
        except asyncio.CancelledError:
            self.state = WorkerState.STOPPED
            raise
        except Exception as exc:
            self.stats.last_error = repr(exc)
            self.state = WorkerState.FAILED
            self._log.error("Воркер упал с ошибкой", error=repr(exc), exc_info=True)

    async def _run_loop(self) -> None:
        """Основной цикл: читает opportunities из очереди и исполняет."""
        while not self._stop_event.is_set():
            try:
                opp = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                self.stats.heartbeat()
                continue

            if opp is None:  # сигнал остановки
                break

            self.stats.heartbeat()
            self.stats.opportunities_seen += 1
            await self._process(opp)

    async def _process(self, opp: SpreadOpportunity) -> None:
        """Проверить риски и выполнить сделку."""
        allowed, violation = self._risk.check(opp)
        if not allowed:
            self.stats.trades_skipped += 1
            if violation not in (RiskViolation.STALE_SIGNAL,):
                self._log.debug("Opportunity отклонена", violation=violation.value)
            return

        try:
            result = await self._execution.execute(opp)
            self._risk.on_result(result)
            self.stats.trades_executed += 1

            if self._on_trade_done:
                await self._on_trade_done(self.symbol, {
                    "pnl": result.pnl_usdt,
                    "success": result.success,
                    "spread_bps": result.realized_spread_bps,
                })
        except Exception as exc:
            self._log.error("Ошибка исполнения", error=repr(exc))
