"""Тест баланса Bitget — печатает сырой ответ API."""
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

KEY  = os.environ.get("BITGET_API_KEY", "")
SEC  = os.environ.get("BITGET_API_SECRET", "")
PP   = os.environ.get("BITGET_PASSPHRASE", "")

print(f"KEY длина:        {len(KEY)}")
print(f"SECRET длина:     {len(SEC)}")
print(f"PASSPHRASE длина: {len(PP)}")

async def main() -> None:
    path = "/api/v2/mix/account/account?productType=USDT-FUTURES&marginCoin=USDT"
    ts   = str(int(time.time() * 1000))
    msg  = ts + "GET" + path
    sig  = base64.b64encode(
        hmac.new(SEC.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "ACCESS-KEY":        KEY,
        "ACCESS-SIGN":       sig,
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-PASSPHRASE": PP,
        "Content-Type":      "application/json",
        "locale":            "en-US",
    }
    url    = f"https://api.bitget.com{path.split('?')[0]}"
    params = {"productType": "USDT-FUTURES", "marginCoin": "USDT"}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(url, params=params, headers=headers) as r:
            text = await r.text()
            print(f"\nHTTP status: {r.status}")
            print(f"Response:    {text}")

asyncio.run(main())
