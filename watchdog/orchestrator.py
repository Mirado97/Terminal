"""WatchdogOrchestrator: маршрутизация opportunities к воркерам."""
from __future__ import annotations

from typing import Awaitable, Callable

import structlog

from core.models import SpreadOpportunity
from risk.engine import RiskEngine
from watchdog.policies import WorkerPolicy
from watchdog.supervisor import WorkerSupervisor
from watchdog.worker import StrategyWorker

logger = structlog.get_logger(__name__)


class WatchdogOrchestrator:
    """
    Центральный координатор системы воркеров.

    Поток данных:
      SpreadDetector.on_opportunity()
          → WatchdogOrchestrator.on_opportunity()
              → StrategyWorker.push_opportunity()
                  → RiskEngine.check()
                      → ExecutionEngine.execute()
                          → RiskEngine.on_result()

    Архитектура:
      - 1 торговая пара = 1 StrategyWorker
      - Один WorkerSupervisor управляет всем пулом
      - on_opportunity() — синхронный (non-blocking): кладёт opp в queue воркера
      - Перегруженные очереди → счётчик missed_opportunities

    Масштабируемость:
      500+ одновременных воркеров — каждый как lightweight asyncio.Task (~4KB памяти).
      Все воркеры работают в одном event loop, изолированы по очереди и exception scope.
    """

    def __init__(self) -> None:
        self._supervisor = WorkerSupervisor()
        self._missed: int = 0
        self._routed: int = 0
        self._log = logger

    # ---- конфигурация ----

    def register_symbol(
        self,
        symbol: str,
        risk_engine: RiskEngine,
        execution_engine: object,
        policy: WorkerPolicy | None = None,
        on_trade_done: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> None:
        """Зарегистрировать символ: создать воркер через фабрику."""
        def factory() -> StrategyWorker:
            return StrategyWorker(
                symbol=symbol,
                risk_engine=risk_engine,
                execution_engine=execution_engine,
                policy=policy or WorkerPolicy(),
                on_trade_done=on_trade_done,
            )

        self._supervisor.register(symbol, factory)

    def add_live_worker(self, worker: StrategyWorker) -> None:
        """Добавить готовый воркер напрямую (удобно в тестах)."""
        self._supervisor.add_worker(worker)

    # ---- маршрутизация ----

    def on_opportunity(self, opp: SpreadOpportunity) -> None:
        """
        Маршрутизировать opportunity к воркеру.
        Вызывается из SpreadDetector — синхронный, не блокирует event loop.
        """
        worker = self._supervisor.get_worker(opp.symbol)
        if not worker:
            self._missed += 1
            return

        if worker.push_opportunity(opp):
            self._routed += 1
        else:
            self._missed += 1
            self._log.debug("Очередь воркера переполнена", symbol=opp.symbol)

    # ---- lifecycle ----

    async def start(self) -> None:
        await self._supervisor.start()
        self._log.info("Orchestrator запущен", symbols=self._supervisor.worker_count)

    async def stop(self) -> None:
        await self._supervisor.stop()
        self._log.info("Orchestrator остановлен")

    # ---- статус ----

    def status(self) -> dict:
        sup = self._supervisor.snapshot()
        return {
            "routed_opportunities": self._routed,
            "missed_opportunities": self._missed,
            **sup,
        }

    @property
    def supervisor(self) -> WorkerSupervisor:
        return self._supervisor
