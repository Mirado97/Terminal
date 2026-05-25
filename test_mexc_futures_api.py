"""
Проверка MEXC Futures API — публичные и приватные эндпоинты.
Запуск: python3 test_mexc_futures_api.py
"""
import asyncio
import hashlib
import hmac
import os
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv(Path(".env"))


async def main() -> None:
    key = os.environ.get("MEXC_API_KEY", "")
    sec = os.environ.get("MEXC_API_SECRET", "")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:

        # ── 1. Публичный REST — тикер BTC (ключи не нужны) ───────────────
        print("1. Публичный REST (тикер BTC_USDT)...")
        async with s.get("https://contract.mexc.com/api/v1/contract/ticker?symbol=BTC_USDT") as r:
            data = await r.json(content_type=None)
        ticker = data.get("data", {})
        print(f"   bid={ticker.get('bid1')}, ask={ticker.get('ask1')}, "
              f"last={ticker.get('lastPrice')}  →  ", end="")
        print("OK" if ticker.get("bid1") else f"FAIL: {data}")

        # ── 2. Публичный REST — список контрактов ────────────────────────
        print("2. Список контрактов MEXC Futures...")
        async with s.get("https://contract.mexc.com/api/v1/contract/detail") as r:
            data = await r.json(content_type=None)
        contracts = data.get("data", [])
        usdt = [c["symbol"] for c in contracts if c.get("symbol", "").endswith("_USDT")]
        print(f"   Найдено USDT-контрактов: {len(usdt)}  →  {'OK' if usdt else 'FAIL'}")

        # ── 3. Приватный REST — баланс (нужны ключи) ─────────────────────
        print("3. Приватный REST (баланс аккаунта)...")
        if not key or not sec:
            print("   ПРОПУЩЕНО — MEXC_API_KEY / MEXC_API_SECRET не заданы в .env")
        else:
            ts  = str(int(time.time() * 1000))
            sig = hmac.new(sec.encode(), (key + ts).encode(), hashlib.sha256).hexdigest()
            async with s.get(
                "https://contract.mexc.com/api/v1/private/account/assets",
                headers={"ApiKey": key, "Request-Time": ts, "Signature": sig},
            ) as r:
                data = await r.json(content_type=None)
            if data.get("success"):
                assets = data.get("data", [])
                usdt_asset = next((a for a in assets if a.get("currency") == "USDT"), None)
                if usdt_asset:
                    avail  = usdt_asset.get("availableBalance", "?")
                    frozen = usdt_asset.get("frozenBalance", "0")
                    print(f"   USDT: available={avail}, frozen={frozen}  →  OK")
                else:
                    print(f"   Активы получены, USDT не найден (пустой аккаунт?)  →  OK")
            else:
                print(f"   FAIL: {data}")

        # ── 4. Приватный REST — открытые позиции ─────────────────────────
        print("4. Приватный REST (открытые позиции)...")
        if not key or not sec:
            print("   ПРОПУЩЕНО — ключи не заданы")
        else:
            ts  = str(int(time.time() * 1000))
            sig = hmac.new(sec.encode(), (key + ts).encode(), hashlib.sha256).hexdigest()
            async with s.get(
                "https://contract.mexc.com/api/v1/private/position/open_positions",
                headers={"ApiKey": key, "Request-Time": ts, "Signature": sig},
            ) as r:
                data = await r.json(content_type=None)
            if data.get("success"):
                positions = data.get("data", [])
                print(f"   Открытых позиций: {len(positions)}  →  OK")
            else:
                print(f"   FAIL: {data}")

    print("\nГотово.")


if __name__ == "__main__":
    asyncio.run(main())
