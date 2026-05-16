"""
demo.py — запуск API сервера с симулированными данными.

Поднимает:
  - TerminalApiServer на http://localhost:8080
  - Фоновую задачу, которая генерирует случайные трейды/латентность

Открыть фронтенд: http://localhost:3000 (нужен отдельный `npm run dev`)
"""
from __future__ import annotations

import asyncio
import math
import random
import time

from api.server import TerminalApiServer
from core.logging import setup_logging
from monitoring.latency import LatencyTracker
from risk.engine import RiskEngine
from risk.inventory import InventoryManager
from risk.limits import RiskLimits
from watchdog.orchestrator import WatchdogOrchestrator


SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
EXCHANGES = ["bybit", "binance"]


async def simulate_data(
    inventory: InventoryManager,
    latency: LatencyTracker,
    orchestrator: WatchdogOrchestrator,
) -> None:
    """Генерирует реалистичные фейковые данные каждую секунду."""
    tick = 0
    base_pnl = 0.0

    while True:
        await asyncio.sleep(1.0)
        tick += 1

        # Симулируем PnL: случайное блуждание с небольшим положительным дрейфом
        pnl_delta = random.gauss(0.8, 4.0)
        base_pnl += pnl_delta

        # Напрямую обновляем внутреннее состояние inventory через приватное поле
        inventory._realized_pnl = round(base_pnl, 2)
        inventory._peak_pnl = max(inventory._peak_pnl, base_pnl)
        inventory._trade_count = tick

        # Симулируем WS latency (логнормальное ~300-800 мкс)
        for ex in EXCHANGES:
            ws_lat = int(random.lognormvariate(math.log(400), 0.4))
            latency.record_ws(ex, ws_lat)

        # Симулируем REST latency (~3-15 мс)
        for ex in EXCHANGES:
            rest_lat = int(random.lognormvariate(math.log(6000), 0.5))
            latency.record_rest(ex, "place_order", rest_lat)

        # Симулируем execution latency (~30-100 мс)
        exec_lat = int(random.lognormvariate(math.log(55), 0.3))
        latency.record_execution(exec_lat)


async def run() -> None:
    setup_logging(level="INFO", json_output=False)

    inventory    = InventoryManager(initial_capital_usdt=10_000.0)
    limits       = RiskLimits()
    risk_engine  = RiskEngine(limits=limits, inventory=inventory)
    latency      = LatencyTracker()
    orchestrator = WatchdogOrchestrator()

    server = TerminalApiServer(
        inventory      = inventory,
        risk_engine    = risk_engine,
        orchestrator   = orchestrator,
        latency_tracker= latency,
        host           = "0.0.0.0",
        port           = 8080,
        push_interval_s= 1.0,
    )

    await server.start()
    print("\n" + "="*55)
    print("  Arbitrage Terminal API запущен")
    print("  http://localhost:8080/api/status")
    print("  ws://localhost:8080/ws")
    print("  Фронтенд: http://localhost:3000")
    print("  Остановить: Ctrl+C")
    print("="*55 + "\n")

    sim_task = asyncio.create_task(
        simulate_data(inventory, latency, orchestrator),
        name="demo:sim",
    )

    try:
        await asyncio.gather(sim_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        sim_task.cancel()
        try:
            await sim_task
        except asyncio.CancelledError:
            pass
        await server.stop()
        print("\nОстановлен.")


if __name__ == "__main__":
    asyncio.run(run())
