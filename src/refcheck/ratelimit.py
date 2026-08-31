"""Per-host rate limiting and circuit breaking.

Two separate concerns, deliberately kept apart:

``TokenBucket`` paces *our* requests to what a service says it will tolerate.
``CircuitBreaker`` reacts when a service tells us we have got it wrong anyway.

The ad-hoc version of this tool retried blindly on 429 and kept being refused;
the fix is to treat a 429 as instruction rather than noise -- honour
``Retry-After``, slow the bucket down, and after repeated refusals stop asking
altogether so the rest of the run still completes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


class TokenBucket:
    """Classic token bucket. Thread-safe; ``acquire`` blocks until a token is free.

    ``rate`` is tokens per second and ``capacity`` the burst allowance. A service
    documenting "1 request per 3 seconds" is ``rate=1/3, capacity=1``.
    """

    def __init__(self, rate: float, capacity: float = 1.0) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = rate
        self._capacity = max(capacity, 1.0)
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        """Adjust pacing at runtime, e.g. from a service's rate-limit headers."""
        with self._lock:
            self._rate = max(rate, 1e-6)

    def acquire(self, tokens: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last) * self._rate
                )
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self._rate
            time.sleep(min(wait, 5.0))


@dataclass
class CircuitBreaker:
    """Stops hammering a host that is refusing us.

    After ``threshold`` consecutive failures the circuit opens for
    ``cooldown`` seconds; callers should treat an open circuit as *unavailable*,
    not as evidence about the item they were looking up.
    """

    threshold: int = 4
    cooldown: float = 120.0
    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if time.monotonic() - self._opened_at >= self.cooldown:
                # half-open: allow one probe through
                self._opened_at = None
                self._failures = self.threshold - 1
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold and self._opened_at is None:
                self._opened_at = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
