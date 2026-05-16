"""WorkerSupervisor: управление жизненным циклом пула воркеров."""
from __future__ import annotations

import asyncio
from typing import Callable

import structlog

from watchdog.policies import RestartPolicy, WorkerPolicy
from watchdog.worker import StrategyWorker, WorkerState

logger = structlog.get_logger(__name__)


class WorkerSupervisor:
    """
    Супервизор воркеров.

    Обязанности:
    - Реестр воркеров (symbol → StrategyWorker)
    - Фоновый watchdog: каждые WATCHDOG_INTERVAL_S секунд проверяет heartbeat
      и состояние воркеров; перезапускает упавших согласно политике
    - Перезапуск неблокирующий: используется asyncio.create_task + delayed start,
      поэтому watchdog не останавливается на backoff и не пропускает другие воркеры
    - Graceful shutdown: останавливает всех воркеров параллельно
    """

    WATCHDOG_INTERVAL_S: float = 5.0

    def __init__(self) -> None:
        self._workers: dict[str, StrategyWorker] = {}
        self._factories: dict[str, Callable[[], StrategyWorker]] = {}
        self._watchdog_task: asyncio.Task | None = None
        self._log = logger

    # ---- регистрация ----

    def register(self, symbol: str, factory: Callable[[], StrategyWorker]) -> None:
        """Зарегистрировать фабрику воркера. Воркер создаётся при вызове start()."""
        self._factories[symbol] = factory

    def add_worker(self, worker: StrategyWorker) -> None:
        """Добавить готовый воркер и запустить его (минуя фабрику).
        Автоматически запускает watchdog-петлю если ещё не запущена."""
        self._workers[worker.symbol] = worker
        worker.start()
        if not self._watchdog_task or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(
                self._watchdog_loop(), name="supervisor:watchdog"
            )
        self._log.info("Воркер добавлен и запущен", symbol=worker.symbol)

    async def remove_worker(self, symbol: str) -> None:
        """Штатно остановить и удалить воркер из реестра."""
        worker = self._workers.pop(symbol, None)
        if worker:
            await worker.stop()
            self._log.info("Воркер остановлен и удалён", symbol=symbol)

    # ---- lifecycle ----

    async def start(self) -> None:
        """Создать воркеры из фабрик, запустить их и watchdog-петлю."""
        for symbol, factory in self._factories.items():
            worker = factory()
            self._workers[symbol] = worker
            worker.start()

        self._watchdog_task = asyncio.create_task(
            self._watchdog_loop(), name="supervisor:watchdog"
        )
        self._log.info("Supervisor запущен", workers=len(self._workers))

    async def stop(self) -> None:
        """Остановить watchdog и все воркеры (параллельно)."""
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        if self._workers:
            await asyncio.gather(
                *(w.stop() for w in self._workers.values()),
                return_exceptions=True,
            )
        self._log.info("Supervisor остановлен")

    # ---- доступ ----

    def get_worker(self, symbol: str) -> StrategyWorker | None:
        return self._workers.get(symbol)

    @property
    def worker_count(self) -> int:
        return len(self._workers)

    def snapshot(self) -> dict:
        workers = [w.snapshot() for w in self._workers.values()]
        counts = {s: 0 for s in ("running", "failed", "disabled", "stopped", "starting")}
        for w in self._workers.values():
            counts[w.state.value] = counts.get(w.state.value, 0) + 1
        return {"total": len(self._workers), **counts, "workers": workers}

    # ---- watchdog ----

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(self.WATCHDOG_INTERVAL_S)
            await self._check_workers()

    async def _check_workers(self) -> None:
        for worker in list(self._workers.values()):
            if worker.state in (WorkerState.DISABLED, WorkerState.STARTING):
                continue

            # Heartbeat timeout → принудительно помечаем FAILED
            if worker.state == WorkerState.RUNNING:
                age = worker.stats.heartbeat_age_s()
                if age > worker._policy.heartbeat_timeout_s:
                    self._log.warning(
                        "Heartbeat timeout — перезапуск",
                        symbol=worker.symbol,
                        age_s=round(age, 1),
                    )
                    worker.state = WorkerState.FAILED

            if worker.state == WorkerState.FAILED:
                self._schedule_restart(worker)

    def _schedule_restart(self, worker: StrategyWorker) -> None:
        """Запланировать перезапуск воркера без блокировки watchdog-петли."""
        policy = worker._policy

        if policy.restart == RestartPolicy.NEVER:
            worker.state = WorkerState.DISABLED
            self._log.warning("Воркер отключён (политика NEVER)", symbol=worker.symbol)
            return

        restarts = worker.stats.restarts
        if policy.max_restarts >= 0 and restarts >= policy.max_restarts:
            worker.state = WorkerState.DISABLED
            self._log.error(
                "Воркер отключён: превышен лимит перезапусков",
                symbol=worker.symbol,
                max_restarts=policy.max_restarts,
            )
            return

        # Помечаем STARTING сразу — следующий цикл watchdog не попытается
        # перезапустить этот же воркер пока он ждёт backoff
        worker.stats.restarts += 1
        worker.state = WorkerState.STARTING
        backoff = policy.backoff_for(restarts)

        self._log.warning(
            "Перезапуск воркера запланирован",
            symbol=worker.symbol,
            attempt=worker.stats.restarts,
            backoff_s=backoff,
        )
        asyncio.create_task(
            self._delayed_start(worker, backoff),
            name=f"restart:{worker.symbol}",
        )

    @staticmethod
    async def _delayed_start(worker: StrategyWorker, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        worker.start()
