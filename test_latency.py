"""Замер латентности до MEXC через прокси. Запуск: python3 test_latency.py"""
import asyncio
import time
from pathlib import Path

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
import os

load_dotenv(Path(".env"))

PROXY = os.environ.get("MEXC_PROXY", "")
URL   = "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT"


async def main() -> None:
    proxies = {"https": PROXY} if PROXY else None
    label   = f"через прокси ({PROXY.split('@')[-1]})" if PROXY else "напрямую (без прокси)"
    print(f"Замер латентности MEXC {label}\n")

    async with AsyncSession(impersonate="chrome", proxies=proxies, timeout=15) as s:
        await s.get(URL)  # прогрев

        times = []
        for i in range(10):
            t0 = time.perf_counter()
            r  = await s.get(URL)
            ms = (time.perf_counter() - t0) * 1000
            times.append(ms)
            print(f"  #{i+1:2d}  {ms:6.1f}ms  HTTP {r.status_code}")
            await asyncio.sleep(0.2)

    print(f"\n  min={min(times):.1f}ms  avg={sum(times)/len(times):.1f}ms  max={max(times):.1f}ms")


if __name__ == "__main__":
    asyncio.run(main())
