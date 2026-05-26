"""Проверка Gate.io Futures баланса. Запуск: python3 test_gate_balance.py"""
import asyncio
import hashlib
import hmac
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv(Path(".env"))

KEY = os.environ.get("GATE_API_KEY", "")
SEC = os.environ.get("GATE_API_SECRET", "")


async def main() -> None:
    if not KEY or not SEC:
        print("GATE_API_KEY / GATE_API_SECRET не заданы в .env")
        return

    ts        = str(int(__import__("time").time()))
    path      = "/api/v4/futures/usdt/accounts"
    body_hash = hashlib.sha512(b"").hexdigest()
    msg       = f"GET\n{path}\n\n{body_hash}\n{ts}"
    sig       = hmac.new(SEC.encode(), msg.encode(), hashlib.sha512).hexdigest()
    headers   = {"KEY": KEY, "SIGN": sig, "Timestamp": ts, "Accept": "application/json"}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(f"https://api.gateio.ws{path}", headers=headers) as r:
            print(f"HTTP {r.status}")
            text = await r.text()
            print(text[:500])


if __name__ == "__main__":
    asyncio.run(main())
