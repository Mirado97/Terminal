"""Диагностика Bitget — полный вывод запроса + spot endpoint."""
import asyncio
import base64
import hashlib
import hmac
import os
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv(Path(".env"))

KEY = os.environ.get("BITGET_API_KEY", "")
SEC = os.environ.get("BITGET_API_SECRET", "")
PP  = os.environ.get("BITGET_PASSPHRASE", "")

print(f"KEY:        {KEY[:8]}...{KEY[-4:]}")
print(f"SEC длина:  {len(SEC)}  первые 8: {SEC[:8]}")
print(f"PP:         [{PP}]  длина: {len(PP)}")


def sign(method: str, path: str) -> tuple[dict, str]:
    ts  = str(int(time.time() * 1000))
    msg = ts + method + path
    raw = hmac.new(SEC.encode(), msg.encode(), hashlib.sha256).digest()
    sig = base64.b64encode(raw).decode()
    print(f"\n  prehash: {msg[:80]}")
    print(f"  sig:     {sig}")
    return {
        "ACCESS-KEY":        KEY,
        "ACCESS-SIGN":       sig,
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-PASSPHRASE": PP,
        "Content-Type":      "application/json",
        "locale":            "en-US",
    }, ts


async def get(s: aiohttp.ClientSession, label: str, path: str) -> None:
    hdrs, ts = sign("GET", path)
    url = f"https://api.bitget.com{path}"
    async with s.get(url, headers=hdrs) as r:
        text = await r.text()
        print(f"  [{label}] {r.status}: {text[:300]}")


async def main() -> None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        # Spot — самый простой приватный endpoint
        await get(s, "spot assets",    "/api/v2/spot/account/assets")
        # Futures USDT
        await get(s, "USDT-FUTURES",   "/api/v2/mix/account/account?productType=USDT-FUTURES&marginCoin=USDT")
        # Список всех фьюч-аккаунтов (без productType)
        await get(s, "account list",   "/api/v2/mix/account/accounts?productType=USDT-FUTURES")


asyncio.run(main())
