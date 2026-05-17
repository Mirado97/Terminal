"""Gate.io Futures USDT REST: размещение ордеров и получение баланса."""
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

_BASE = "https://api.gateio.ws"
_PREFIX = "/api/v4"


class GateFuturesRestClient:
    def __init__(self, credentials: ExchangeCredentials) -> None:
        self._creds  = credentials
        self._session: aiohttp.ClientSession | None = None
        self.contract_specs: dict[str, dict] = {}  # gate_sym → {quanto_multiplier, order_size_min}

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
        async with self._session.get(f"{_BASE}{_PREFIX}/futures/usdt/contracts") as r:
            data: list[dict] = orjson.loads(await r.read())
        for c in data:
            name = c.get("name", "")
            if not name:
                continue
            self.contract_specs[name] = {
                "quanto_multiplier": float(c.get("quanto_multiplier") or 1),
                "order_size_min":    int(c.get("order_size_min") or 1),
            }
        logger.info("Gate.io контракты загружены", count=len(self.contract_specs))

    def compute_size(self, gate_sym: str, qty_usdt: float, price: float) -> int:
        """USDT → количество контрактов Gate.io."""
        if price <= 0:
            return 1
        spec = self.contract_specs.get(gate_sym, {})
        qm   = float(spec.get("quanto_multiplier", 1.0) or 1.0)
        minsz = int(spec.get("order_size_min", 1) or 1)
        return max(minsz, round(qty_usdt / (price * qm)))

    async def place_order(self, gate_sym: str, size: int, reduce_only: bool = False) -> Order:
        """size > 0 = лонг/закрыть шорт, size < 0 = шорт/закрыть лонг."""
        body = {
            "contract":    gate_sym,
            "size":        size,
            "price":       "0",
            "tif":         "ioc",
            "reduce_only": reduce_only,
            "text":        f"t-arb-{uuid.uuid4().hex[:12]}",
        }
        body_bytes = orjson.dumps(body)
        data = await self._request("POST", "/futures/usdt/orders", body_bytes)
        symbol = gate_sym.replace("_", "")
        return Order(
            id              = str(data.get("id", "")),
            client_order_id = body["text"],
            exchange        = Exchange.GATE,
            symbol          = symbol,
            market_type     = MarketType.PERPETUAL,
            side            = OrderSide.BUY if size > 0 else OrderSide.SELL,
            order_type      = OrderType.MARKET,
            status          = OrderStatus.FILLED,
            price           = float(data.get("fill_price") or 0),
            qty             = float(abs(size)),
            created_at_ms   = int(time.time() * 1000),
        )

    async def get_usdt_balance(self) -> float:
        data = await self._request("GET", "/futures/usdt/accounts", b"")
        return float(data.get("available") or 0)

    async def _request(self, method: str, path: str, body: bytes = b"") -> dict:
        assert self._session
        ts        = str(int(time.time()))
        body_hash = hashlib.sha512(body).hexdigest()
        msg       = f"{method}\n{_PREFIX}{path}\n\n{body_hash}\n{ts}"
        sig       = hmac.new(self._creds.api_secret.encode(), msg.encode(), hashlib.sha512).hexdigest()
        headers   = {
            "KEY":          self._creds.api_key,
            "SIGN":         sig,
            "Timestamp":    ts,
            "Content-Type": "application/json",
            "Accept":       "application/json",
        }
        url = f"{_BASE}{_PREFIX}{path}"
        if method == "GET":
            async with self._session.get(url, headers=headers) as r:
                return await self._handle(r)
        else:
            async with self._session.post(url, data=body, headers=headers) as r:
                return await self._handle(r)

    async def _handle(self, resp: aiohttp.ClientResponse) -> dict:
        raw = await resp.read()
        try:
            data = orjson.loads(raw)
        except Exception as e:
            raise RuntimeError(f"Gate.io не JSON: status={resp.status}, raw={raw[:400]!r}") from e
        if isinstance(data, dict) and data.get("label"):
            raise RuntimeError(f"Gate.io ошибка: {data.get('label')} — {data.get('message', '')}")
        return data
