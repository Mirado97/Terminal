"""Диагностика Bitget auth — пробуем несколько endpoints."""
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

print(f"KEY длина:        {len(KEY)}")
print(f"SECRET длина:     {len(SEC)}")
print(f"PASSPHRASE длина: {len(PP)}")


def sign(method: str, path: str) -> dict:
    ts  = str(int(time.time() * 1000))
    msg = ts + method + path
    sig = base64.b64encode(
        hmac.new(SEC.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "ACCESS-KEY":        KEY,
        "ACCESS-SIGN":       sig,
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-PASSPHRASE": PP,
        "Content-Type":      "application/json",
        "locale":            "en-US",
    }


async def get(s: aiohttp.ClientSession, label: str, path: str) -> None:
    url = f"https://api.bitget.com{path}"
    hdrs = sign("GET", path)
    async with s.get(url, headers=hdrs) as r:
        text = await r.text()
        print(f"\n[{label}]  status={r.status}  {text[:300]}")


async def main() -> None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        # 1. Общая информация об аккаунте (не требует futures)
        await get(s, "account/info", "/api/v2/account/info")
        # 2. USDT-FUTURES баланс
        await get(s, "USDT-FUTURES", "/api/v2/mix/account/account?productType=USDT-FUTURES&marginCoin=USDT")
        # 3. COIN-FUTURES баланс (вдруг другой тип)
        await get(s, "COIN-FUTURES", "/api/v2/mix/account/account?productType=COIN-FUTURES&marginCoin=BTC")


asyncio.run(main())
