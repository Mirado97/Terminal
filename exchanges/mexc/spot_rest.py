"""MEXC Spot REST: market buy/sell через api.mexc.com."""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid

import aiohttp
import orjson
import structlog

from core.models import Exchange, MarketType, Order, OrderSide, OrderStatus, OrderType
from credentials.manager import ExchangeCredentials

logger = structlog.get_logger(__name__)

BASE_URL = "https://api.mexc.com"


class MexcSpotRestClient:
    def __init__(self, credentials: ExchangeCredentials) -> None:
        self._creds = credentials
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300),
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "Mozilla/5.0"},
        )

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    async def buy_market(self, symbol: str, usdt_amount: float) -> Order:
        """Потратить usdt_amount USDT → получить токены."""
        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": f"{usdt_amount:.2f}",
            "newClientOrderId": f"arb-{uuid.uuid4().hex[:12]}",
            "timestamp": str(int(time.time() * 1000)),
        }
        data = await self._signed_post("/api/v3/order", params)
        token_qty = float(data.get("executedQty") or data.get("origQty") or 0)
        if token_qty <= 0:
            # запасной вариант — оценка
            spent = float(data.get("cummulativeQuoteQty") or usdt_amount)
            last_price = float(data.get("price") or 0)
            if last_price > 0:
                token_qty = spent / last_price
        return Order(
            id=str(data.get("orderId", "")),
            client_order_id=params["newClientOrderId"],
            exchange=Exchange.MEXC,
            symbol=symbol,
            market_type=MarketType.SPOT,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            price=0.0,
            qty=token_qty,
            created_at_ms=int(time.time() * 1000),
        )

    async def sell_market(self, symbol: str, token_qty: float) -> Order:
        """Продать token_qty токенов → получить USDT."""
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": f"{token_qty:.8f}".rstrip("0").rstrip("."),
            "newClientOrderId": f"arb-{uuid.uuid4().hex[:12]}",
            "timestamp": str(int(time.time() * 1000)),
        }
        data = await self._signed_post("/api/v3/order", params)
        return Order(
            id=str(data.get("orderId", "")),
            client_order_id=params["newClientOrderId"],
            exchange=Exchange.MEXC,
            symbol=symbol,
            market_type=MarketType.SPOT,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            price=0.0,
            qty=token_qty,
            created_at_ms=int(time.time() * 1000),
        )

    async def _signed_post(self, path: str, params: dict) -> dict:
        assert self._session
        qs  = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(
            self._creds.api_secret.encode(),
            qs.encode(),
            hashlib.sha256,
        ).hexdigest()
        url = f"{BASE_URL}{path}?{qs}&signature={sig}"
        async with self._session.post(
            url,
            headers={"X-MEXC-APIKEY": self._creds.api_key},
        ) as r:
            return await self._handle(r)

    async def _handle(self, resp: aiohttp.ClientResponse) -> dict:
        raw = await resp.read()
        try:
            data: dict = orjson.loads(raw)
        except Exception as e:
            raise RuntimeError(
                f"MEXC Spot не JSON: status={resp.status}, raw={raw[:400]!r}"
            ) from e
        if "msg" in data and "code" in data:
            raise RuntimeError(f"MEXC Spot ошибка {data.get('code')}: {data.get('msg')}")
        return data
