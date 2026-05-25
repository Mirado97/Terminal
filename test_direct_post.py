"""
Проверяет проходит ли curl_cffi (Chrome fingerprint) через Akamai напрямую с VPS.
Если POST вернёт 401 — прокси не нужен, латенси будет ~5ms.
Запуск: python3 test_direct_post.py
"""
import asyncio
import time
from curl_cffi.requests import AsyncSession

URL = "https://contract.mexc.com"


async def main() -> None:
    async with AsyncSession(impersonate="chrome", timeout=10) as s:

        print("Без прокси, Chrome fingerprint:\n")

        t0 = time.perf_counter()
        r = await s.get(f"{URL}/api/v1/contract/ticker?symbol=BTC_USDT")
        ms = (time.perf_counter() - t0) * 1000
        print(f"  GET  HTTP {r.status_code}  {ms:.1f}ms")

        t0 = time.perf_counter()
        r = await s.post(
            f"{URL}/api/v1/private/order/submit",
            data='{"test":1}',
            headers={"Content-Type": "application/json"},
        )
        ms = (time.perf_counter() - t0) * 1000
        print(f"  POST HTTP {r.status_code}  {ms:.1f}ms  | {r.text[:120]}")

        if r.status_code == 401:
            print("\n✓ Akamai пропускает — прокси не нужен!")
        elif r.status_code == 403:
            print("\n✗ Akamai блокирует — прокси необходим.")


if __name__ == "__main__":
    asyncio.run(main())
