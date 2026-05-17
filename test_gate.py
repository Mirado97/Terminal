"""Тест Gate.io: WS book_ticker + REST баланс."""
import asyncio
import hashlib
import hmac
import os
import time

import aiohttp
import orjson
import websockets
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(".env"))

API_KEY = os.environ.get("GATE_API_KEY", "")
API_SEC = os.environ.get("GATE_API_SECRET", "")
SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]


async def test_ws():
    print("\n=== Gate.io Futures WS ===")
    url = "wss://fx-ws.gateio.ws/v4/ws/usdt"
    print(f"Connecting to {url} ...")
    async with websockets.connect(url, open_timeout=10) as ws:
        print("Connected!")
        sub = orjson.dumps({
            "time":    int(time.time()),
            "channel": "futures.book_ticker",
            "event":   "subscribe",
            "payload": SYMBOLS,
        })
        await ws.send(sub.decode())
        print(f"Subscribed to: {SYMBOLS}\n")
        count = 0
        for _ in range(30):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
                msg = orjson.loads(raw)
                if msg.get("event") == "update" and msg.get("channel") == "futures.book_ticker":
                    r = msg["result"]
                    print(f"  {r['s']:15s}  bid={r['b']:>12}  ask={r['a']:>12}  t={r['t']}")
                    count += 1
                    if count >= 9:
                        break
            except asyncio.TimeoutError:
                print("  [timeout]")
    print(f"\nWS OK — получено {count} тиков")


async def test_rest():
    print("\n=== Gate.io Futures REST ===")
    base = "https://api.gateio.ws"
    path = "/api/v4/futures/usdt/accounts"

    ts        = str(int(time.time()))
    body_hash = hashlib.sha512(b"").hexdigest()
    msg       = f"GET\n{path}\n\n{body_hash}\n{ts}"
    sig       = hmac.new(API_SEC.encode(), msg.encode(), hashlib.sha512).hexdigest()

    headers = {
        "KEY":       API_KEY,
        "SIGN":      sig,
        "Timestamp": ts,
        "Accept":    "application/json",
    }
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{base}{path}", headers=headers) as r:
            data = await r.json(content_type=None)

    if isinstance(data, dict) and data.get("label"):
        print(f"  ОШИБКА: {data.get('label')} — {data.get('message')}")
    else:
        print(f"  total:     {data.get('total')}")
        print(f"  available: {data.get('available')}")
        print(f"  unrealised_pnl: {data.get('unrealised_pnl')}")
        print("  REST OK")


async def main():
    await test_ws()
    await test_rest()

asyncio.run(main())
