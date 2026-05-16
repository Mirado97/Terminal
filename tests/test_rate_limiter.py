"""Тесты TokenBucket и RateLimiter."""
from __future__ import annotations

import asyncio
import time

import pytest

from exchanges.rate_limiter import RateLimiter, TokenBucket


@pytest.mark.asyncio
async def test_token_bucket_basic():
    bucket = TokenBucket(rate=100.0, capacity=10.0)
    # Должен мгновенно выдать 10 токенов (полный запас)
    start = time.monotonic()
    for _ in range(10):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1, f"Слишком долго: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_token_bucket_rate_limited():
    bucket = TokenBucket(rate=10.0, capacity=1.0)
    await bucket.acquire()  # берём 1 токен
    start = time.monotonic()
    await bucket.acquire()  # следующий должен ждать ~100ms
    elapsed = time.monotonic() - start
    assert elapsed >= 0.08, f"Слишком быстро: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_token_bucket_available():
    bucket = TokenBucket(rate=10.0, capacity=5.0)
    assert bucket.available == pytest.approx(5.0, abs=0.1)
    await bucket.acquire(3.0)
    assert bucket.available == pytest.approx(2.0, abs=0.1)


@pytest.mark.asyncio
async def test_token_bucket_refills():
    bucket = TokenBucket(rate=100.0, capacity=5.0)
    await bucket.acquire(5.0)  # опустошаем
    await asyncio.sleep(0.05)  # ждём 50ms → должно вернуться ~5 токенов
    assert bucket.available >= 4.5


def test_rate_limiter_creates_buckets():
    rl = RateLimiter(requests_per_second=10.0, orders_per_second=5.0)
    assert isinstance(rl.rest, TokenBucket)
    assert isinstance(rl.orders, TokenBucket)
    assert rl.rest.available == pytest.approx(20.0, abs=0.1)   # capacity = rate * 2
    assert rl.orders.available == pytest.approx(10.0, abs=0.1)


@pytest.mark.asyncio
async def test_concurrent_acquire():
    """Параллельные acquire не должны давать больше токенов чем есть."""
    bucket = TokenBucket(rate=1000.0, capacity=5.0)
    results = await asyncio.gather(*[bucket.acquire() for _ in range(5)])
    assert all(r is None for r in results)
    # после 5 acquire остаток ≈ 0
    assert bucket.available < 1.0
