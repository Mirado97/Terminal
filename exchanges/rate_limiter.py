"""Token bucket rate limiter для защиты от превышения лимитов бирж."""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """
    Классический token bucket.
    acquire() блокирует до появления свободного токена.
    Потокобезопасен через asyncio.Lock.
    """

    def __init__(self, rate: float, capacity: float) -> None:
        """
        rate     — токенов в секунду (пополнение),
        capacity — максимальный запас токенов (burst limit).
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self._rate
                await asyncio.sleep(wait)

    def _refill(self) -> None:
        now = time.monotonic()
        added = (now - self._last_refill) * self._rate
        self._tokens = min(self._capacity, self._tokens + added)
        self._last_refill = now

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens


class RateLimiter:
    """Набор token bucket'ов по категориям запросов (REST, orders)."""

    def __init__(self, requests_per_second: float, orders_per_second: float) -> None:
        self.rest = TokenBucket(rate=requests_per_second, capacity=requests_per_second * 2)
        self.orders = TokenBucket(rate=orders_per_second, capacity=orders_per_second * 2)
