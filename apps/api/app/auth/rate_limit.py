"""Sliding-window rate limiting for the authentication endpoints.

The store is in-process, which is sufficient for a single API instance and for
tests; it must be replaced by a Redis-backed implementation before the API is
scaled horizontally (each replica would otherwise enforce its own window).
The limiter is deliberately behind this small interface so that swap is local.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Protocol


class RateLimiter(Protocol):
    """Decides whether one more attempt is allowed for a caller-scoped key."""

    def allow(self, key: str) -> bool: ...


class SlidingWindowRateLimiter:
    """Allows at most `attempts` events per `window_seconds` for each key."""

    def __init__(
        self,
        *,
        attempts: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if attempts < 1:
            msg = "attempts must be at least 1"
            raise ValueError(msg)
        self._attempts = attempts
        self._window_seconds = window_seconds
        self._clock = clock
        self._events: defaultdict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock()
        events = self._events[key]
        cutoff = now - self._window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self._attempts:
            return False
        events.append(now)
        return True
