"""Bybit V5 REST клиент: подписание запросов, балансы, ордера."""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Any

import aiohttp
import orjson
import structlog

from core.models import (
    Balance, Exchange, MarketType, Order, OrderSide, OrderStatus, OrderType,
)
from exchanges.rate_limiter import RateLimiter
from credentials.manager import ExchangeCredentials

logger = structlog.get_logger(__name__)

_RECV_WINDOW = 5000  # ms — допустимое расхождение часов с биржей


class BybitRestClient:
    def __init__(self, base_url: str, credentials: ExchangeCredentials, rate_limiter: RateLimiter) -> None:
        self._base_url = base_url.rstrip("/")
        self._creds = credentials
        self._rl = rate_limiter
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=50, ttl_dns_cache=300),
            timeout=aiohttp.ClientTimeout(total=10),
            json_serialize=lambda v: orjson.dumps(v).decode(),
        )

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    # ---- public ----

    async def get_balances(self) -> list[Balance]:
        await self._rl.rest.acquire()
        data = await self._get("/v5/account/wallet-balance", {"accountType": "UNIFIED"}, signed=True)
        balances: list[Balance] = []
        now_ms = int(time.time() * 1000)
        for account in data.get("result", {}).get("list", []):
            for coin in account.get("coin", []):
                balances.append(Balance(
                    exchange=Exchange.BYBIT,
                    currency=coin["coin"],
                    available=float(coin.get("availableToWithdraw", 0)),
                    locked=float(coin.get("locked", 0)),
                    total=float(coin.get("equity", 0)),
                    updated_at_ms=now_ms,
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
        coid = client_order_id or f"arb-{uuid.uuid4().hex[:16]}"
        category = _market_type_to_category(market_type)

        body: dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": "Buy" if side == OrderSide.BUY else "Sell",
            "orderType": _order_type_to_bybit(order_type),
            "qty": str(qty),
            "orderLinkId": coid,
        }
        if price is not None:
            body["price"] = str(price)
        if order_type == OrderType.POST_ONLY:
            body["timeInForce"] = "PostOnly"
        elif order_type == OrderType.IOC:
            body["timeInForce"] = "IOC"
        elif order_type == OrderType.FOK:
            body["timeInForce"] = "FOK"

        data = await self._post("/v5/order/create", body, signed=True)
        result = data.get("result", {})
        now_ms = int(time.time() * 1000)

        return Order(
            id=result.get("orderId", ""),
            client_order_id=coid,
            exchange=Exchange.BYBIT,
            symbol=symbol,
            market_type=market_type,
            side=side,
            order_type=order_type,
            status=OrderStatus.PENDING,
            price=price or 0.0,
            qty=qty,
            created_at_ms=now_ms,
            exchange_order_id=result.get("orderId", ""),
        )

    async def get_execution_fee(self, symbol: str, order_id: str) -> float:
        """Вернуть реальную комиссию по orderId в USDT."""
        try:
            data = await self._get("/v5/execution/list", {
                "category": "linear",
                "symbol": symbol,
                "orderId": order_id,
                "limit": "10",
            }, signed=True)
            executions = data.get("result", {}).get("list", [])
            logger.info("bybit execution list", symbol=symbol, order_id=order_id,
                        count=len(executions), raw=str(data)[:300])
            total = sum(float(e.get("execFee", 0)) for e in executions)
            return round(total, 6)
        except Exception as e:
            logger.warning("bybit get_execution_fee failed", symbol=symbol, order_id=order_id, error=str(e))
            return 0.0

    async def set_leverage(self, symbol: str, leverage: int = 1) -> None:
        try:
            await self._post("/v5/position/set-leverage", {
                "category": "linear",
                "symbol": symbol,
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            }, signed=True)
        except RuntimeError as e:
            if "leverage not modified" in str(e).lower() or "110043" in str(e):
                pass  # уже стоит нужное плечо
            else:
                logger.warning("set_leverage failed", symbol=symbol, error=str(e))

    async def cancel_order(self, symbol: str, order_id: str, market_type: MarketType) -> None:
        await self._rl.orders.acquire()
        category = _market_type_to_category(market_type)
        await self._post("/v5/order/cancel", {
            "category": category,
            "symbol": symbol,
            "orderId": order_id,
        }, signed=True)

    async def get_order(self, symbol: str, order_id: str, market_type: MarketType) -> Order:
        await self._rl.rest.acquire()
        category = _market_type_to_category(market_type)
        data = await self._get("/v5/order/realtime", {
            "category": category,
            "symbol": symbol,
            "orderId": order_id,
        }, signed=True)
        items = data.get("result", {}).get("list", [])
        if not items:
            raise RuntimeError(f"Bybit: ордер не найден {order_id}")
        o = items[0]
        return Order(
            id=o.get("orderId", ""),
            client_order_id=o.get("orderLinkId", ""),
            exchange=Exchange.BYBIT,
            symbol=symbol,
            market_type=market_type,
            side=OrderSide.BUY if o.get("side") == "Buy" else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            status=_parse_bybit_status(o.get("orderStatus", "")),
            price=float(o.get("price", 0)),
            qty=float(o.get("qty", 0)),
            filled_qty=float(o.get("cumExecQty", 0)),
            avg_fill_price=float(o.get("avgPrice", 0)),
            fee=float(o.get("cumExecFee", 0)),
            exchange_order_id=o.get("orderId", ""),
        )

    # ---- HTTP helpers ----

    async def _get(self, path: str, params: dict, signed: bool = False) -> dict:
        assert self._session, "Вызовите start() перед использованием REST клиента"
        if signed:
            params = self._sign_params(params)
        async with self._session.get(self._base_url + path, params=params, headers=self._headers(params, signed)) as resp:
            return await self._handle(resp)

    async def _post(self, path: str, body: dict, signed: bool = False) -> dict:
        assert self._session, "Вызовите start() перед использованием REST клиента"
        raw_body = orjson.dumps(body).decode()
        headers = self._headers(raw_body, signed)
        async with self._session.post(self._base_url + path, data=raw_body, headers=headers) as resp:
            return await self._handle(resp)

    async def _handle(self, resp: aiohttp.ClientResponse) -> dict:
        raw = await resp.read()
        data: dict = orjson.loads(raw)
        ret_code = data.get("retCode", -1)
        if ret_code != 0:
            raise RuntimeError(f"Bybit API ошибка {ret_code}: {data.get('retMsg')}")
        return data

    def _headers(self, payload: Any, signed: bool) -> dict:
        if not signed:
            return {"Content-Type": "application/json"}
        ts = int(time.time() * 1000)
        sign_str = f"{ts}{self._creds.api_key}{_RECV_WINDOW}"
        if isinstance(payload, dict):
            sign_str += "&".join(f"{k}={v}" for k, v in sorted(payload.items()))
        else:
            sign_str += str(payload)
        sig = hmac.new(self._creds.api_secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": self._creds.api_key,
            "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-SIGN": sig,
            "X-BAPI-RECV-WINDOW": str(_RECV_WINDOW),
        }

    def _sign_params(self, params: dict) -> dict:
        ts = int(time.time() * 1000)
        params_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sign_str = f"{ts}{self._creds.api_key}{_RECV_WINDOW}{params_str}"
        sig = hmac.new(self._creds.api_secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        return {**params, "api_key": self._creds.api_key, "timestamp": ts, "sign": sig, "recv_window": _RECV_WINDOW}


def _market_type_to_category(mt: MarketType) -> str:
    return {"spot": "spot", "perpetual": "linear", "futures": "inverse"}[mt.value]


def _order_type_to_bybit(ot: OrderType) -> str:
    return {"limit": "Limit", "market": "Market", "post_only": "Limit",
            "ioc": "Limit", "fok": "Limit"}[ot.value]


def _parse_bybit_status(s: str) -> OrderStatus:
    mapping = {
        "New": OrderStatus.OPEN,
        "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
        "Filled": OrderStatus.FILLED,
        "Cancelled": OrderStatus.CANCELLED,
        "Rejected": OrderStatus.REJECTED,
        "Untriggered": OrderStatus.PENDING,
    }
    return mapping.get(s, OrderStatus.PENDING)
