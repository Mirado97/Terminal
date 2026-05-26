"""Замер латентности до Gate.io Futures и Bybit. Запуск: python3 test_latency_gate.py"""
import asyncio
import os
import time
from pathlib import Path

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv

load_dotenv(Path(".env"))

URL_GATE  = "https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=BTC_USDT"
URL_BYBIT = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"


async def measure(s: AsyncSession, label: str, url: str) -> None:
    await s.get(url)  # прогрев
    times = []
    for i in range(10):
        t0 = time.perf_counter()
        r  = await s.get(url)
        ms = (time.perf_counter() - t0) * 1000
        times.append(ms)
        print(f"  #{i+1:2d}  {ms:6.1f}ms  HTTP {r.status_code}")
        await asyncio.sleep(0.2)
    print(f"  min={min(times):.1f}ms  avg={sum(times)/len(times):.1f}ms  max={max(times):.1f}ms\n")


async def main() -> None:
    async with AsyncSession(impersonate="chrome", timeout=15) as s:

        print("── Gate.io Futures [напрямую] ────────────────────")
        await measure(s, "Gate", URL_GATE)

        print("── Bybit [напрямую] ──────────────────────────────")
        await measure(s, "Bybit", URL_BYBIT)


if __name__ == "__main__":
    asyncio.run(main())
