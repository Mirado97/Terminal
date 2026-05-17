"""Тест MEXC Spot WS: подписываемся на 3 пары, смотрим сырые сообщения 10 сек."""
import asyncio
import json
import websockets

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

async def main():
    url = "wss://wbs.mexc.com/ws"
    print(f"Connecting to {url} ...")
    async with websockets.connect(url) as ws:
        print("Connected!")
        sub = json.dumps({
            "method": "SUBSCRIPTION",
            "params": [f"spot@public.bookTicker.v3.api@{s}" for s in SYMBOLS],
        })
        await ws.send(sub)
        print(f"Subscribed to: {SYMBOLS}\n")

        for _ in range(30):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2)
                print(msg[:300])
            except asyncio.TimeoutError:
                print("[timeout — no message]")

asyncio.run(main())
