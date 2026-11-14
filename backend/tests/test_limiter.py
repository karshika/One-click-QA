import random

import pytest

from app.llm.limiter import RateLimiter, TokenBucket, TokenWindow, backoff_delay, with_retries
from app.llm.provider import RetryableLLMError


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_token_bucket_enforces_rps() -> None:
    clock = FakeClock()
    b = TokenBucket(rate=1.0, capacity=1.0, clock=clock)
    assert b.reserve() == 0.0  # first request is free
    assert b.reserve() == pytest.approx(1.0)  # second must wait 1 s
    assert b.reserve() == pytest.approx(2.0)  # third waits 2 s (serialized)
    clock.t += 10
    assert b.reserve() == 0.0  # bucket refilled (capped at capacity)


def test_token_bucket_half_rps() -> None:
    clock = FakeClock()
    b = TokenBucket(rate=0.5, clock=clock)
    b.reserve()
    assert b.reserve() == pytest.approx(2.0)


def test_token_window_limits_tpm() -> None:
    clock = FakeClock()
    w = TokenWindow(tpm=1000, clock=clock)
    assert w.reserve(600) == 0.0
    clock.t += 10
    wait = w.reserve(600)  # 1200 > 1000 → wait until first event expires (50 s more)
    assert wait == pytest.approx(50.0)


async def test_rate_limiter_sleeps() -> None:
    clock = FakeClock()
    slept: list[float] = []

    async def fake_sleep(d: float) -> None:
        slept.append(d)

    lim = RateLimiter(rps=1, tpm=100_000, clock=clock, sleep=fake_sleep)
    await lim.acquire(10)
    await lim.acquire(10)
    assert slept == [pytest.approx(1.0)]


def test_backoff_honors_retry_after_and_cap() -> None:
    rng = random.Random(1)
    for attempt in range(8):
        d = backoff_delay(attempt, rng=rng)
        assert 0 <= d <= 60
    assert backoff_delay(0, retry_after=7.5, rng=rng) >= 7.5


async def test_with_retries_succeeds_after_429() -> None:
    calls = {"n": 0}
    slept: list[float] = []

    async def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableLLMError("429", status=429, retry_after=2.0)
        return "ok"

    async def sleep(d: float) -> None:
        slept.append(d)

    result, attempts = await with_retries(fn, max_attempts=5, sleep=sleep, rng=random.Random(0))
    assert result == "ok"
    assert attempts == 3
    assert all(d >= 2.0 for d in slept)  # Retry-After respected


async def test_with_retries_gives_up_cleanly() -> None:
    async def fn() -> str:
        raise RetryableLLMError("server error (503)", status=503)

    async def sleep(_: float) -> None:
        return None

    with pytest.raises(RetryableLLMError, match="giving up after 3 attempts"):
        await with_retries(fn, max_attempts=3, sleep=sleep)
