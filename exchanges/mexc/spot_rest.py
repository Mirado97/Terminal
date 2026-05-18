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
        self._precision_cache: dict[str, int] = {}  # symbol → decimal places

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300),
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "Mozilla/5.0"},
        )

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    async def _get_base_precision(self, symbol: str) -> int:
        """Получить кол-во знаков после запятой для базового актива."""
        if symbol in self._precision_cache:
            return self._precision_cache[symbol]
        assert self._session
        async with self._session.get(
            f"{BASE_URL}/api/v3/exchangeInfo",
            params={"symbol": symbol},
        ) as r:
            data = orjson.loads(await r.read())
        symbols = data.get("symbols", [])
        precision = int(symbols[0]["baseAssetPrecision"]) if symbols else 8
        self._precision_cache[symbol] = precision
        return precision

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
        token_qty = float(data.get("executedQty") or 0)
        if token_qty <= 0:
            # MEXC не возвращает executedQty для market+quoteOrderQty
            # считаем из цены исполнения
            price_f = float(data.get("price") or 0)
            if price_f > 0:
                precision = await self._get_base_precision(symbol)
                token_qty = round(usdt_amount / price_f, precision)
        if token_qty <= 0:
            raise RuntimeError(f"MEXC Spot: не удалось получить qty из ответа: {data}")
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

    async def get_token_balance(self, asset: str) -> float:
        """Вернуть свободный баланс токена на споте."""
        assert self._session
        params = {"timestamp": str(int(time.time() * 1000))}
        qs  = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(self._creds.api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        async with self._session.get(
            f"{BASE_URL}/api/v3/account?{qs}&signature={sig}",
            headers={"X-MEXC-APIKEY": self._creds.api_key},
        ) as r:
            data = orjson.loads(await r.read())
        for b in data.get("balances", []):
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    async def sell_market(self, symbol: str, token_qty: float) -> Order:
        """Продать token_qty токенов → получить USDT."""
        precision = await self._get_base_precision(symbol)
        # берём реальный баланс чтобы продать всё без остатка
        asset = symbol.replace("USDT", "")
        actual_qty = await self.get_token_balance(asset)
        if actual_qty > 0:
            token_qty = actual_qty
        qty_str = f"{token_qty:.{precision}f}"
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
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
        async with self._session.post(
            f"{BASE_URL}{path}?{qs}&signature={sig}",
            data=b"",
            headers={
                "X-MEXC-APIKEY": self._creds.api_key,
                "Content-Type": "application/json",
            },
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
