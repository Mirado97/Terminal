"""Тесты AsyncLifecycle."""
from __future__ import annotations

import pytest

from core.lifecycle import AsyncLifecycle


@pytest.mark.asyncio
async def test_startup_order():
    lc = AsyncLifecycle()
    log: list[str] = []

    @lc.on_startup
    async def a() -> None:
        log.append("startup_a")

    @lc.on_startup
    async def b() -> None:
        log.append("startup_b")

    await lc.startup()
    assert log == ["startup_a", "startup_b"]


@pytest.mark.asyncio
async def test_shutdown_reverse_order():
    lc = AsyncLifecycle()
    log: list[str] = []

    @lc.on_shutdown
    async def a() -> None:
        log.append("shutdown_a")

    @lc.on_shutdown
    async def b() -> None:
        log.append("shutdown_b")

    await lc.shutdown()
    # shutdown выполняется в обратном порядке
    assert log == ["shutdown_b", "shutdown_a"]


@pytest.mark.asyncio
async def test_lifespan_context_manager():
    lc = AsyncLifecycle()
    events: list[str] = []

    @lc.on_startup
    async def up() -> None:
        events.append("up")

    @lc.on_shutdown
    async def down() -> None:
        events.append("down")

    async with lc.lifespan():
        events.append("running")

    assert events == ["up", "running", "down"]


@pytest.mark.asyncio
async def test_shutdown_hook_exception_does_not_propagate():
    """Ошибка в одном shutdown хуке не должна блокировать остальные."""
    lc = AsyncLifecycle()
    log: list[str] = []

    @lc.on_shutdown
    async def broken() -> None:
        raise RuntimeError("сломан")

    @lc.on_shutdown
    async def ok() -> None:
        log.append("ok")

    # Не должно бросить исключение
    await lc.shutdown()
    assert "ok" in log
