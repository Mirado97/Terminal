"""MEXC Futures REST клиент: аутентификация, ордера, контракты."""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from typing import Any

import orjson
import structlog
from curl_cffi.requests import AsyncSession

from core.models import (
    Exchange, MarketType, Order, OrderSide, OrderStatus, OrderType,
)
from credentials.manager import ExchangeCredentials

logger = structlog.get_logger(__name__)

BASE_URL = "https://contract.mexc.com"

# Коды стороны ордера MEXC Futures
_OPEN_LONG   = 1  # купить (открыть лонг)
_CLOSE_SHORT = 2  # купить (закрыть шорт)
_OPEN_SHORT  = 3  # продать (открыть шорт)
_CLOSE_LONG  = 4  # продать (закрыть лонг)

# Коды типа ордера MEXC Futures
_TYPE_LIMIT    = 1
_TYPE_POST_ONLY = 2
_TYPE_IOC      = 3
_TYPE_FOK      = 4
_TYPE_MARKET   = 5


def _to_mexc(symbol: str) -> str:
    """BTCUSDT → BTC_USDT"""
    if symbol.endswith("USDT") and "_" not in symbol:
        return symbol[:-4] + "_USDT"
    return symbol


class MexcFuturesRestClient:
    """
    MEXC Futures REST.
    place_order принимает qty_usdt + ref_price → конвертирует в контракты.
    curl_cffi имитирует TLS-отпечаток Chrome для обхода Akamai WAF.
    """

    def __init__(self, credentials: ExchangeCredentials) -> None:
        self._creds = credentials
        self._session: AsyncSession | None = None      # с прокси — для auth запросов
        self._pub_session: AsyncSession | None = None  # без прокси — для публичных GET
        self._contract_sizes: dict[str, float] = {}    # symbol → размер контракта

    async def start(self) -> None:
        proxy_url = os.environ.get("MEXC_PROXY", "")
        proxies = {"https": proxy_url, "http": proxy_url} if proxy_url else None
        if proxy_url:
            logger.info("MEXC Futures REST: прокси подключён", proxy=proxy_url.split("@")[-1])
        self._session = AsyncSession(impersonate="chrome", proxies=proxies, timeout=10)
        self._pub_session = AsyncSession(impersonate="chrome", timeout=30)
        await self._load_contract_sizes()

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
        if self._pub_session:
            await self._pub_session.close()

    async def _load_contract_sizes(self) -> None:
        try:
            assert self._pub_session
            r = await self._pub_session.get(f"{BASE_URL}/api/v1/contract/detail")
            data = orjson.loads(r.content)
            for c in data.get("data", []):
                sym = c.get("symbol", "").replace("_", "")  # BTC_USDT → BTCUSDT
                size = float(c.get("contractSize", 1) or 1)
                self._contract_sizes[sym] = size
            logger.info("MEXC: загружены контракты", count=len(self._contract_sizes))
        except Exception as e:
            logger.warning("MEXC: не удалось загрузить контракты", error=str(e))

    def _usdt_to_vol(self, symbol: str, qty_usdt: float, ref_price: float) -> int:
        """Конвертирует USDT → количество контрактов (минимум 1)."""
        size = self._contract_sizes.get(symbol, 1.0)
        if size <= 0 or ref_price <= 0:
            return 1
        return max(1, int(qty_usdt / (size * ref_price)))

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        qty_usdt: float,
        ref_price: float,
        close_position: bool = False,
        client_order_id: str | None = None,
    ) -> Order:
        assert self._session
        vol = self._usdt_to_vol(symbol, qty_usdt, ref_price)

        if not close_position:
            mexc_side = _OPEN_LONG if side == OrderSide.BUY else _OPEN_SHORT
        else:
            mexc_side = _CLOSE_LONG if side == OrderSide.SELL else _CLOSE_SHORT

        body: dict[str, Any] = {
            "symbol":   _to_mexc(symbol),
            "side":     mexc_side,
            "openType": 2,          # cross margin
            "type":     _TYPE_MARKET,
            "vol":      vol,
            "leverage": 1,
        }
        data = await self._post("/api/v1/private/order/submit", body)
        order_id = str(data.get("data", "") or "")
        now_ms = int(time.time() * 1000)

        return Order(
            id=order_id,
            client_order_id=client_order_id or f"arb-{uuid.uuid4().hex[:12]}",
            exchange=Exchange.MEXC,
            symbol=symbol,
            market_type=MarketType.PERPETUAL,
            side=side,
            order_type=OrderType.MARKET,
            status=OrderStatus.PENDING,
            price=ref_price,
            qty=float(vol),
            created_at_ms=now_ms,
            exchange_order_id=order_id,
        )

    async def cancel_order(self, symbol: str, order_id: str) -> None:
        assert self._session
        await self._post("/api/v1/private/order/cancel",
                         {"symbol": _to_mexc(symbol), "orderId": int(order_id)})

    async def get_order(self, symbol: str, order_id: str) -> Order:
        assert self._session
        data = await self._get(f"/api/v1/private/order/get/{order_id}",
                               {"symbol": _to_mexc(symbol)})
        o = data.get("data", {})
        state_map = {1: OrderStatus.PENDING, 2: OrderStatus.OPEN,
                     3: OrderStatus.FILLED, 4: OrderStatus.CANCELLED, 5: OrderStatus.CANCELLED}
        side_code = o.get("side", 1)
        is_buy = side_code in (_OPEN_LONG, _CLOSE_SHORT)
        return Order(
            id=str(o.get("orderId", "")),
            client_order_id="",
            exchange=Exchange.MEXC,
            symbol=symbol,
            market_type=MarketType.PERPETUAL,
            side=OrderSide.BUY if is_buy else OrderSide.SELL,
            order_type=OrderType.MARKET,
            status=state_map.get(o.get("state", 1), OrderStatus.PENDING),
            price=float(o.get("price", 0)),
            qty=float(o.get("vol", 0)),
            filled_qty=float(o.get("dealVol", 0)),
            avg_fill_price=float(o.get("dealAvgPrice", 0)),
            fee=float(o.get("fee", 0)),
            created_at_ms=int(o.get("createTime", 0)),
        )

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _sign(self, ts: str, payload: str) -> str:
        msg = self._creds.api_key + ts + payload
        return hmac.new(self._creds.api_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def _auth_headers(self, ts: str, sig: str) -> dict:
        return {
            "ApiKey":       self._creds.api_key,
            "Request-Time": ts,
            "Signature":    sig,
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: dict) -> dict:
        assert self._session
        ts = str(int(time.time() * 1000))
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        r = await self._session.get(
            BASE_URL + path, params=params,
            headers=self._auth_headers(ts, self._sign(ts, qs)),
        )
        return self._handle(r)

    async def _post(self, path: str, body: dict) -> dict:
        assert self._session
        ts  = str(int(time.time() * 1000))
        raw = orjson.dumps(body).decode()
        r = await self._session.post(
            BASE_URL + path, data=raw,
            headers=self._auth_headers(ts, self._sign(ts, raw)),
        )
        return self._handle(r)

    def _handle(self, resp: Any) -> dict:
        raw = resp.content
        try:
            data: dict = orjson.loads(raw)
        except Exception as e:
            raise RuntimeError(
                f"MEXC не JSON: status={resp.status_code}, raw={raw[:400]!r}"
            ) from e
        if not data.get("success", True):
            raise RuntimeError(f"MEXC Futures API: {data.get('message', data)}")
        return data
