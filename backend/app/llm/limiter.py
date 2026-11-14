"""Process-wide rate limiting (requests/second token bucket + tokens/minute window) and
retry with exponential backoff + full jitter that honors Retry-After.

The limiter is thread-safe (the worker thread and any API thread share one instance) and
uses reservation semantics: each caller reserves the next free slot under a lock and then
sleeps outside it, so concurrent callers are serialized fairly.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.llm.provider import RetryableLLMError

T = TypeVar("T")
Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class TokenBucket:
    """Classic token bucket: `rate` tokens/sec, burst `capacity`."""

    def __init__(self, rate: float, capacity: float = 1.0, clock: Clock = time.monotonic) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.rate = rate
        self.capacity = max(1.0, capacity)
        self._tokens = self.capacity
        self._clock = clock
        self._last = clock()
        self._lock = threading.Lock()

    def reserve(self) -> float:
        """Take one token; return how long the caller must wait before proceeding."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
            self._last = now
            self._tokens -= 1.0
            if self._tokens >= 0:
                return 0.0
            return -self._tokens / self.rate


class TokenWindow:
    """Sliding 60 s window limiting estimated tokens per minute."""

    def __init__(self, tpm: int, clock: Clock = time.monotonic, window: float = 60.0) -> None:
        self.tpm = max(1, tpm)
        self.window = window
        self._clock = clock
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def reserve(self, tokens: int) -> float:
        tokens = min(max(0, tokens), self.tpm)
        with self._lock:
            now = self._clock()
            while self._events and now - self._events[0][0] >= self.window:
                self._events.popleft()
            used = sum(t for _, t in self._events)
            wait = 0.0
            if used + tokens > self.tpm:
                # wait until enough old events expire
                need = used + tokens - self.tpm
                freed = 0
                for ts, t in self._events:
                    freed += t
                    if freed >= need:
                        wait = ts + self.window - now
                        break
            self._events.append((now + wait, tokens))
            return max(0.0, wait)


class RateLimiter:
    def __init__(self, rps: float, tpm: int, clock: Clock = time.monotonic,
                 sleep: Sleeper = asyncio.sleep) -> None:
        self.bucket = TokenBucket(rps, capacity=1.0, clock=clock)
        self.window = TokenWindow(tpm, clock=clock)
        self._sleep = sleep
        self.total_wait = 0.0

    async def acquire(self, estimated_tokens: int = 0) -> float:
        wait = max(self.bucket.reserve(), self.window.reserve(estimated_tokens))
        if wait > 0:
            self.total_wait += wait
            await self._sleep(wait)
        return wait


def backoff_delay(attempt: int, retry_after: float | None = None, *, base: float = 1.0, cap: float = 60.0,
                  rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter; Retry-After (if given) is a lower bound."""
    r = rng or random
    exp = min(cap, base * (2 ** attempt))
    delay = r.uniform(0, exp)
    if retry_after is not None:
        delay = max(delay, min(retry_after, cap * 2))
    return delay


async def with_retries(fn: Callable[[], Awaitable[T]], *, max_attempts: int = 5, sleep: Sleeper = asyncio.sleep,
                       rng: random.Random | None = None,
                       on_retry: Callable[[int, RetryableLLMError, float], None] | None = None) -> tuple[T, int]:
    """Call fn, retrying RetryableLLMError. Returns (result, attempts). Re-raises the last error."""
    attempt = 0
    while True:
        try:
            return await fn(), attempt + 1
        except RetryableLLMError as exc:
            attempt += 1
            if attempt >= max_attempts:
                raise RetryableLLMError(
                    f"{exc} - giving up after {attempt} attempts", status=exc.status, retry_after=exc.retry_after
                ) from exc
            delay = backoff_delay(attempt - 1, exc.retry_after, rng=rng)
            if on_retry:
                on_retry(attempt, exc, delay)
            await sleep(delay)


_shared: RateLimiter | None = None
_shared_lock = threading.Lock()


def shared_limiter() -> RateLimiter:
    """The single process-wide limiter."""
    global _shared
    with _shared_lock:
        if _shared is None:
            from app.config import get_settings

            s = get_settings()
            _shared = RateLimiter(s.mistral_rps, s.mistral_tpm)
        return _shared
