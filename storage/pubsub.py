"""RedisPubSub: pub/sub каналы для real-time событий."""
from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

import structlog

from core.models import SpreadOpportunity
from execution.models import ExecutionResult
from storage.redis_client import RedisClient

logger = structlog.get_logger(__name__)

# Названия каналов
CH_SPREADS     = "channel:spreads"
CH_TRADES      = "channel:trades"
CH_RISK_ALERTS = "channel:risk_alerts"


class RedisPubSub:
    """
    Pub/Sub через Redis для распространения событий в реальном времени.

    Publisher:
        await pubsub.publish_spread(opp)
        await pubsub.publish_trade(result)
        await pubsub.publish_risk_alert(symbol, violation)

    Subscriber:
        task = await pubsub.subscribe(CH_TRADES, callback)
        # callback(channel: str, message: dict) -> Awaitable[None]
    """

    def __init__(self, client: RedisClient) -> None:
        self._r = client
        self._log = logger

    # ---- публикация ----

    async def publish_spread(self, opp: SpreadOpportunity) -> None:
        payload = json.dumps({
            "symbol": opp.symbol,
            "buy_exchange": opp.buy_exchange.value,
            "sell_exchange": opp.sell_exchange.value,
            "executable_spread_bps": opp.executable_spread_bps,
            "buy_price": opp.buy_price,
            "sell_price": opp.sell_price,
            "max_size_usdt": opp.max_size_usdt,
            "timestamp_ms": opp.timestamp_ms,
        })
        await self._r.client.publish(CH_SPREADS, payload)

    async def publish_trade(self, result: ExecutionResult) -> None:
        opp = result.opportunity
        payload = json.dumps({
            "symbol": opp.symbol,
            "buy_exchange": opp.buy_exchange.value,
            "sell_exchange": opp.sell_exchange.value,
            "pnl_usdt": result.pnl_usdt,
            "realized_spread_bps": result.realized_spread_bps,
            "state": result.state.value,
            "success": result.success,
            "execution_time_ms": result.execution_time_ms,
        })
        await self._r.client.publish(CH_TRADES, payload)

    async def publish_risk_alert(
        self,
        symbol: str,
        violation: str,
        value: float = 0.0,
    ) -> None:
        payload = json.dumps({
            "symbol": symbol,
            "violation": violation,
            "value": value,
        })
        await self._r.client.publish(CH_RISK_ALERTS, payload)

    # ---- подписка ----

    async def subscribe(
        self,
        channel: str,
        callback: Callable[[str, dict], Awaitable[None]],
    ) -> asyncio.Task:
        """
        Запустить подписку в фоновом Task.
        Callback вызывается для каждого сообщения: (channel, parsed_dict).
        """
        task = asyncio.create_task(
            self._listen(channel, callback),
            name=f"pubsub:{channel}",
        )
        return task

    async def _listen(
        self,
        channel: str,
        callback: Callable[[str, dict], Awaitable[None]],
    ) -> None:
        # Создаём отдельное соединение для блокирующей подписки
        pubsub = self._r.client.pubsub()
        await pubsub.subscribe(channel)
        self._log.info("Подписка на канал", channel=channel)

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    await callback(channel, data)
                except Exception as exc:
                    self._log.error("Ошибка обработки pubsub", channel=channel, error=repr(exc))
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
