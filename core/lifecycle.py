"""Async lifecycle: startup/shutdown хуки + graceful завершение по сигналу."""
from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

StartupHook = Callable[[], Coroutine[Any, Any, None]]
ShutdownHook = Callable[[], Coroutine[Any, Any, None]]


class AsyncLifecycle:
    """
    Управляет порядком запуска и остановки компонентов.
    Shutdown выполняется в обратном порядке относительно startup.
    """

    def __init__(self) -> None:
        self._startup_hooks: list[StartupHook] = []
        self._shutdown_hooks: list[ShutdownHook] = []
        self._stop_event: asyncio.Event | None = None

    def on_startup(self, hook: StartupHook) -> StartupHook:
        self._startup_hooks.append(hook)
        return hook

    def on_shutdown(self, hook: ShutdownHook) -> ShutdownHook:
        self._shutdown_hooks.append(hook)
        return hook

    async def startup(self) -> None:
        logger.info("Запуск приложения", hooks=len(self._startup_hooks))
        for hook in self._startup_hooks:
            await hook()

    async def shutdown(self) -> None:
        logger.info("Остановка приложения", hooks=len(self._shutdown_hooks))
        for hook in reversed(self._shutdown_hooks):
            try:
                await hook()
            except Exception:
                logger.exception("Ошибка в shutdown хуке", hook=getattr(hook, "__name__", repr(hook)))

    def _trigger_stop(self, sig: signal.Signals) -> None:
        logger.warning("Получен сигнал остановки", signal=sig.name)
        if self._stop_event:
            self._stop_event.set()

    def _register_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            if sys.platform == "win32":
                # Windows: add_signal_handler недоступен для SIGTERM
                signal.signal(sig, lambda s, _f, _sig=sig: self._trigger_stop(signal.Signals(s)))
            else:
                loop.add_signal_handler(sig, self._trigger_stop, sig)

    async def run_until_stopped(self) -> None:
        """Блокирует до получения SIGINT/SIGTERM."""
        self._stop_event = asyncio.Event()
        self._register_signals()
        logger.info("Приложение работает, ожидаю сигнал остановки (Ctrl+C)")
        await self._stop_event.wait()

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None, None]:
        """Context manager для использования в main()."""
        await self.startup()
        try:
            yield
        finally:
            await self.shutdown()
