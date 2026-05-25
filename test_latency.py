"""Замер латентности до MEXC и Bybit. Запуск: python3 test_latency.py"""
import asyncio
import os
import time
from pathlib import Path

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv

load_dotenv(Path(".env"))

PROXY       = os.environ.get("MEXC_PROXY", "")
URL_MEXC    = "https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT"
URL_BYBIT   = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"


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
    proxies     = {"https": PROXY} if PROXY else None
    proxy_label = f"прокси ({PROXY.split('@')[-1]})" if PROXY else "без прокси"

    async with AsyncSession(impersonate="chrome", proxies=proxies, timeout=15) as s_proxy:
        async with AsyncSession(impersonate="chrome", timeout=15) as s_direct:

            print(f"── MEXC Futures [{proxy_label}] ──────────────────")
            await measure(s_proxy, "MEXC", URL_MEXC)

            print(f"── Bybit [напрямую] ──────────────────────────────")
            await measure(s_direct, "Bybit", URL_BYBIT)


if __name__ == "__main__":
    asyncio.run(main())
