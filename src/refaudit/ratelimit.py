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

#: A penalty never drops the rate below ``ceiling / FLOOR_DIVISOR``. Without a
#: floor, repeated halving reaches a rate at which a single request takes
#: minutes -- indistinguishable from a hang, and never recovered from.
FLOOR_DIVISOR = 16.0

#: Successes recover the rate by ``ceiling / RECOVERY_STEPS`` each. Additive
#: increase against multiplicative decrease: quick to yield, slow to re-probe,
#: which is what keeps a shared service stable.
RECOVERY_STEPS = 20.0


class TokenBucket:
    """Classic token bucket. Thread-safe; ``acquire`` blocks until a token is free.

    ``rate`` is tokens per second and ``capacity`` the burst allowance. A service
    documenting "1 request per 3 seconds" is ``rate=1/3, capacity=1``.

    The rate moves between a floor and a *ceiling*. The ceiling is policy -- what
    the service documents, or what it published in a rate-limit header -- and
    only ``set_rate`` changes it. The current rate dips below the ceiling when we
    are refused and climbs back as requests succeed, so a burst of 429s costs a
    run seconds rather than leaving it throttled to the end.
    """

    def __init__(self, rate: float, capacity: float = 1.0) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = rate
        self._ceiling = rate
        self._capacity = max(capacity, 1.0)
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        """Set the ceiling: the fastest we will ever go for this host.

        Used for policy, not for backoff -- the rate a service documents, or the
        allowance it publishes in a header. The current rate is clamped to it,
        and recovery will never climb past it again.
        """
        with self._lock:
            self._ceiling = max(rate, 1e-6)
            self._rate = min(self._rate, self._ceiling)

    def penalise(self) -> None:
        """Halve the rate, bounded below. Call when a service refuses us."""
        with self._lock:
            self._rate = max(self._rate / 2.0, self._ceiling / FLOOR_DIVISOR)

    def recover(self) -> None:
        """Edge the rate back toward the ceiling. Call on a successful request."""
        with self._lock:
            if self._rate < self._ceiling:
                self._rate = min(self._ceiling,
                                 self._rate + self._ceiling / RECOVERY_STEPS)

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
    #: When the circuit may next be probed, rather than when it opened, so a
    #: service that names its own wait can be honoured instead of the default.
    _open_until: float | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._open_until is None:
                return False
            if time.monotonic() >= self._open_until:
                # half-open: allow one probe through
                self._open_until = None
                self._failures = self.threshold - 1
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold and self._open_until is None:
                self._open_until = time.monotonic() + self.cooldown

    def open_for(self, seconds: float) -> None:
        """Open the circuit for a period the service itself named.

        Used when a 429 carries a ``Retry-After`` longer than this run is
        willing to wait. Counting that as one more failure would let the
        default cooldown expire long before the service is ready, and every
        later entry would spend its own attempts rediscovering the same
        refusal.
        """
        with self._lock:
            self._failures = max(self._failures, self.threshold)
            self._open_until = time.monotonic() + max(seconds, 0.0)

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = None
