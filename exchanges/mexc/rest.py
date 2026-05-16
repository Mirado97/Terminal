"""MEXC REST клиент: балансы и ордера (API совместим с Binance V3)."""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import aiohttp
import orjson
import structlog

from core.models import (
    Balance, Exchange, MarketType, Order, OrderSide, OrderStatus, OrderType,
)
from exchanges.rate_limiter import RateLimiter
from credentials.manager import ExchangeCredentials

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.mexc.com"


class MexcRestClient:
    def __init__(self, credentials: ExchangeCredentials, rate_limiter: RateLimiter) -> None:
        self._creds = credentials
        self._rl = rate_limiter
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=50, ttl_dns_cache=300),
            timeout=aiohttp.ClientTimeout(total=10),
            headers={
                "X-MEXC-APIKEY": self._creds.api_key,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            },
        )

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    async def get_book_tickers(self) -> list[dict]:
        """Лучший bid/ask для всех символов — один запрос на всё."""
        assert self._session
        async with self._session.get(_BASE_URL + "/api/v3/ticker/bookTicker") as resp:
            return await self._handle(resp)

    async def get_balances(self) -> list[Balance]:
        await self._rl.rest.acquire()
        data = await self._get("/api/v3/account", {}, signed=True)
        now_ms = int(time.time() * 1000)
        balances: list[Balance] = []
        for asset in data.get("balances", []):
            free   = float(asset.get("free", 0))
            locked = float(asset.get("locked", 0))
            if free + locked == 0:
                continue
            balances.append(Balance(
                exchange     = Exchange.MEXC,
                currency     = asset["asset"],
                available    = free,
                locked       = locked,
                total        = free + locked,
                updated_at_ms= now_ms,
            ))
        return balances

    async def place_order(
        self,
        symbol: str,
        market_type: MarketType,
        side: OrderSide,
        order_type: OrderType,
        qty: float,
        price: float | None = None,
        client_order_id: str | None = None,
    ) -> Order:
        await self._rl.orders.acquire()
        coid = client_order_id or f"arb{uuid.uuid4().hex[:14]}"
        params: dict[str, Any] = {
            "symbol":           symbol,
            "side":             "BUY" if side == OrderSide.BUY else "SELL",
            "type":             _order_type_to_mexc(order_type),
            "quantity":         qty,
            "newClientOrderId": coid,
        }
        if price is not None:
            params["price"] = price
        if order_type in (OrderType.LIMIT, OrderType.POST_ONLY):
            params["timeInForce"] = "GTC" if order_type == OrderType.LIMIT else "GTX"
        elif order_type == OrderType.IOC:
            params["timeInForce"] = "IOC"
        elif order_type == OrderType.FOK:
            params["timeInForce"] = "FOK"

        data  = await self._post("/api/v3/order", params, signed=True)
        now_ms = int(time.time() * 1000)

        return Order(
            id               = str(data.get("orderId", "")),
            client_order_id  = coid,
            exchange         = Exchange.MEXC,
            symbol           = symbol,
            market_type      = market_type,
            side             = side,
            order_type       = order_type,
            status           = _parse_status(data.get("status", "")),
            price            = float(data.get("price", price or 0)),
            qty              = float(data.get("origQty", qty)),
            filled_qty       = float(data.get("executedQty", 0)),
            created_at_ms    = data.get("transactTime", now_ms),
            exchange_order_id= str(data.get("orderId", "")),
        )

    async def cancel_order(self, symbol: str, order_id: str, market_type: MarketType) -> None:
        await self._rl.orders.acquire()
        await self._delete("/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)

    async def get_order(self, symbol: str, order_id: str, market_type: MarketType) -> Order:
        await self._rl.rest.acquire()
        data = await self._get("/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)
        return Order(
            id               = str(data.get("orderId", "")),
            client_order_id  = data.get("clientOrderId", ""),
            exchange         = Exchange.MEXC,
            symbol           = symbol,
            market_type      = market_type,
            side             = OrderSide.BUY if data.get("side") == "BUY" else OrderSide.SELL,
            order_type       = OrderType.LIMIT,
            status           = _parse_status(data.get("status", "")),
            price            = float(data.get("price", 0)),
            qty              = float(data.get("origQty", 0)),
            filled_qty       = float(data.get("executedQty", 0)),
            avg_fill_price   = float(data.get("cummulativeQuoteQty", 0)) / max(float(data.get("executedQty", 1) or 1), 1e-12),
            exchange_order_id= str(data.get("orderId", "")),
        )

    # ---- HTTP helpers ----

    async def _get(self, path: str, params: dict, signed: bool = False) -> dict:
        assert self._session
        if signed:
            params = self._sign(params)
        async with self._session.get(_BASE_URL + path, params=params) as resp:
            return await self._handle(resp)

    async def _post(self, path: str, params: dict, signed: bool = False) -> dict:
        assert self._session
        if signed:
            params = self._sign(params)
        async with self._session.post(_BASE_URL + path, params=params) as resp:
            return await self._handle(resp)

    async def _delete(self, path: str, params: dict, signed: bool = False) -> dict:
        assert self._session
        if signed:
            params = self._sign(params)
        async with self._session.delete(_BASE_URL + path, params=params) as resp:
            return await self._handle(resp)

    async def _handle(self, resp: aiohttp.ClientResponse) -> dict:
        raw = await resp.read()
        if resp.status == 200:
            return orjson.loads(raw)
        try:
            data = orjson.loads(raw)
            msg  = data.get("msg", raw)
        except Exception:
            msg = raw
        raise RuntimeError(f"MEXC API ошибка {resp.status}: {msg}")

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig   = hmac.new(self._creds.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params


def _order_type_to_mexc(ot: OrderType) -> str:
    return {
        "limit":     "LIMIT",
        "market":    "MARKET",
        "post_only": "LIMIT_MAKER",
        "ioc":       "LIMIT",
        "fok":       "LIMIT",
    }[ot.value]


def _parse_status(s: str) -> OrderStatus:
    return {
        "NEW":              OrderStatus.OPEN,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "FILLED":           OrderStatus.FILLED,
        "CANCELED":         OrderStatus.CANCELLED,
        "REJECTED":         OrderStatus.REJECTED,
        "EXPIRED":          OrderStatus.EXPIRED,
    }.get(s, OrderStatus.PENDING)
