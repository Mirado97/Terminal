"""
Проверка выставления ордера на MEXC Futures.
Ставим лимитный BUY по $1 (никогда не исполнится), сразу отменяем.
Запуск: python3 test_mexc_futures_order.py
"""
import asyncio
import hashlib
import hmac
import os
import time
from pathlib import Path

import aiohttp
import orjson
from dotenv import load_dotenv

load_dotenv(Path(".env"))

BASE = "https://contract.mexc.com"


def _sign(key: str, sec: str, ts: str, payload: str) -> dict:
    sig = hmac.new(sec.encode(), (key + ts + payload).encode(), hashlib.sha256).hexdigest()
    return {"ApiKey": key, "Request-Time": ts, "Signature": sig, "Content-Type": "application/json"}


async def post(s: aiohttp.ClientSession, key: str, sec: str, path: str, body: dict) -> dict:
    ts  = str(int(time.time() * 1000))
    raw = orjson.dumps(body).decode()
    async with s.post(BASE + path, data=raw, headers=_sign(key, sec, ts, raw)) as r:
        text = await r.text()
        print(f"   HTTP {r.status}, тело: {text[:500] or '(пустое)'}")
        if not text.strip():
            return {"success": False, "message": f"пустой ответ HTTP {r.status} — скорее всего WAF/Akamai блокирует POST с этого IP"}
        import json
        return json.loads(text)


async def delete(s: aiohttp.ClientSession, key: str, sec: str, path: str, body: dict) -> dict:
    ts  = str(int(time.time() * 1000))
    raw = orjson.dumps(body).decode()
    async with s.delete(BASE + path, data=raw, headers=_sign(key, sec, ts, raw)) as r:
        return await r.json(content_type=None)


async def main() -> None:
    key = os.environ.get("MEXC_API_KEY", "")
    sec = os.environ.get("MEXC_API_SECRET", "")
    if not key or not sec:
        print("MEXC_API_KEY / MEXC_API_SECRET не заданы в .env")
        return

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:

        # Текущая цена BTC
        async with s.get(f"{BASE}/api/v1/contract/ticker?symbol=BTC_USDT") as r:
            ticker = (await r.json(content_type=None)).get("data", {})
        last = float(ticker.get("lastPrice", 50000))
        limit_price = round(last * 0.5, 1)  # 50% от рынка — никогда не исполнится
        print(f"BTC last={last}, лимитный ордер по={limit_price} (50% от рынка)\n")

        # ── Выставить ордер ───────────────────────────────────────────────
        print("1. Выставляем лимитный BUY 1 контракт BTC_USDT...")
        body = {
            "symbol":   "BTC_USDT",
            "side":     1,          # OPEN_LONG
            "openType": 2,          # cross margin
            "type":     1,          # LIMIT
            "vol":      1,          # 1 контракт
            "leverage": 1,
            "price":    limit_price,
        }
        resp = await post(s, key, sec, "/api/v1/private/order/submit", body)
        print(f"   Ответ: {resp}")

        if not resp.get("success"):
            msg = resp.get("message", "")
            print()
            if any(x in msg.lower() for x in ("insufficient", "balance", "margin", "funds")):
                print("✓ API РАБОТАЕТ — ордер отклонён из-за недостаточного баланса.")
                print("  Пополни счёт и бот будет торговать.")
            elif any(x in msg.lower() for x in ("permission", "auth", "sign", "key", "forbidden")):
                print("✗ ПРОБЛЕМА С КЛЮЧАМИ — нет прав на торговлю фьючерсами.")
                print("  Проверь настройки API ключа на MEXC: нужно включить Futures Trading.")
            else:
                print(f"✗ Неизвестная ошибка: {msg}")
            return

        order_id = resp.get("data")
        print(f"   Ордер создан! id={order_id}")

        # ── Отменить ордер ────────────────────────────────────────────────
        print("2. Отменяем ордер...")
        cancel_resp = await post(s, key, sec, "/api/v1/private/order/cancel",
                                 {"symbol": "BTC_USDT", "orderId": order_id})
        print(f"   Ответ: {cancel_resp}")

        print()
        if cancel_resp.get("success"):
            print("✓ API ПОЛНОСТЬЮ РАБОТАЕТ — ордер выставлен и отменён без ошибок.")
        else:
            print(f"  Ордер выставился, но отмена вернула: {cancel_resp.get('message')}")


if __name__ == "__main__":
    asyncio.run(main())
