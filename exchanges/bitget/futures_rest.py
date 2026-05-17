"""Bitget Futures USDT REST: ордера + баланс.
Auth: BASE64(HMAC-SHA256(secret, timestamp+method+path+body))
Требуется passphrase дополнительно к key/secret.
"""
from __future__ import annotations

import base64
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

_BASE = "https://api.bitget.com"


class BitgetFuturesRestClient:
    def __init__(self, credentials: ExchangeCredentials, passphrase: str) -> None:
        self._creds      = credentials
        self._passphrase = passphrase
        self._session: aiohttp.ClientSession | None = None
        self.contract_specs: dict[str, dict] = {}  # symbol → {size_multiplier, min_trade_num}

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300),
            timeout=aiohttp.ClientTimeout(total=10),
        )
        await self._load_contracts()

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    async def _load_contracts(self) -> None:
        assert self._session
        path = "/api/v2/mix/market/contracts?productType=USDT-FUTURES"
        async with self._session.get(f"{_BASE}{path}") as r:
            data = orjson.loads(await r.read())
        for c in data.get("data", []):
            sym = c.get("symbol", "")
            if not sym:
                continue
            self.contract_specs[sym] = {
                "size_multiplier": float(c.get("sizeMultiplier") or 1),
                "min_trade_num":   float(c.get("minTradeNum") or 1),
            }
        logger.info("Bitget контракты загружены", count=len(self.contract_specs))

    def compute_size(self, symbol: str, qty_usdt: float, price: float) -> float:
        """USDT → количество контрактов Bitget. Возвращает 0 если минимум превышает бюджет."""
        if price <= 0:
            return 0
        spec = self.contract_specs.get(symbol, {})
        sm   = float(spec.get("size_multiplier", 1.0) or 1.0)
        minn = float(spec.get("min_trade_num",   1.0) or 1.0)
        raw  = qty_usdt / (price * sm)
        size = max(minn, round(raw / minn) * minn)
        actual_usdt = size * price * sm
        if actual_usdt > qty_usdt * 3:
            logger.warning("Bitget min order too large", symbol=symbol,
                           min_usdt=round(actual_usdt, 2), budget=qty_usdt)
            return 0
        return size

    async def place_order(
        self, symbol: str, side: str, trade_side: str, size: float
    ) -> Order:
        """
        side: 'buy' | 'sell'
        trade_side: 'open' | 'close'
        size: количество контрактов
        """
        body = {
            "symbol":      symbol,
            "productType": "USDT-FUTURES",
            "marginMode":  "crossed",
            "marginCoin":  "USDT",
            "size":        str(size),
            "side":        side,
            "tradeSide":   trade_side,
            "orderType":   "market",
            "clientOid":   f"arb-{uuid.uuid4().hex[:12]}",
        }
        data = await self._request("POST", "/api/v2/mix/order/place-order", body)
        order_id = str(data.get("data", {}).get("orderId", ""))
        return Order(
            id              = order_id,
            client_order_id = body["clientOid"],
            exchange        = Exchange.BITGET,
            symbol          = symbol,
            market_type     = MarketType.PERPETUAL,
            side            = OrderSide.BUY if side == "buy" else OrderSide.SELL,
            order_type      = OrderType.MARKET,
            status          = OrderStatus.FILLED,
            price           = 0.0,
            qty             = size,
            created_at_ms   = int(time.time() * 1000),
        )

    async def get_usdt_balance(self) -> float:
        data = await self._request(
            "GET",
            "/api/v2/mix/account/accounts?productType=USDT-FUTURES",
        )
        for acct in (data.get("data") or []):
            if acct.get("marginCoin") == "USDT":
                return float(acct.get("available") or 0)
        return 0.0

    async def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        assert self._session
        body_str  = orjson.dumps(body).decode() if body else ""
        ts        = str(int(time.time() * 1000))
        msg       = ts + method + path + body_str
        sig       = base64.b64encode(
            hmac.new(self._creds.api_secret.encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()
        headers = {
            "ACCESS-KEY":        self._creds.api_key,
            "ACCESS-SIGN":       sig,
            "ACCESS-TIMESTAMP":  ts,
            "ACCESS-PASSPHRASE": self._passphrase,
            "Content-Type":      "application/json",
            "locale":            "en-US",
        }
        url = f"{_BASE}{path.split('?')[0]}"
        params = None
        if "?" in path and method == "GET":
            params = dict(p.split("=") for p in path.split("?")[1].split("&"))

        if method == "GET":
            async with self._session.get(url, params=params, headers=headers) as r:
                return await self._handle(r)
        else:
            async with self._session.post(
                url, data=body_str.encode(), headers=headers
            ) as r:
                return await self._handle(r)

    async def _handle(self, resp: aiohttp.ClientResponse) -> dict:
        raw = await resp.read()
        try:
            data = orjson.loads(raw)
        except Exception as e:
            raise RuntimeError(
                f"Bitget не JSON: status={resp.status}, raw={raw[:400]!r}"
            ) from e
        code = str(data.get("code", "0"))
        if code != "00000":
            raise RuntimeError(f"Bitget ошибка {code}: {data.get('msg', data)}")
        return data
