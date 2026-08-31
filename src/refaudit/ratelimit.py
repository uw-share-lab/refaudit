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

import contextlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .filelock import exclusive

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


#: Bumped when the meaning of a shared state file changes, so an older or newer
#: refaudit on the same machine starts clean instead of misreading it.
STATE_SCHEMA = 1

#: Short on purpose. This lock is taken once per request, so a stale one must
#: cost a moment rather than the ten seconds a cache flush can afford.
_PACING_LOCK_TIMEOUT = 2.0


def default_state_dir() -> Path:
    """Where shared pacing state lives, under the user's own account.

    Deliberately not a world-writable temp directory. Pacing another local user
    could edit would let them slow a run to a crawl, or speed it up into the
    ban this whole module exists to avoid.
    """
    if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        if base.startswith("~"):  # pragma: no cover - no home directory
            base = tempfile.gettempdir()
    return Path(base) / "refaudit" / "pacing"


class SharedTokenBucket(TokenBucket):
    """A token bucket whose allowance is shared by every refaudit on the machine.

    Per-process pacing is right across users -- each runs under their own
    contact address and is a separate identified caller. It is wrong for one
    person running several processes at once, because the service sees a single
    caller going at a multiple of the rate we promised.

    The state is a small JSON file per host, read and written under a lock. The
    cost is one lock, one read and one write per request, which is nothing
    beside the network call it is pacing.

    Every failure degrades to the parent class: if the file cannot be created,
    read, written or locked, this is exactly an in-process ``TokenBucket``. The
    worst case of the whole mechanism is therefore the behaviour that came
    before it.
    """

    def __init__(self, rate: float, capacity: float = 1.0, *,
                 state_dir: Path | str | None = None, host: str = "default") -> None:
        super().__init__(rate, capacity)
        # A host comes from a resolver's api_base rather than from user input,
        # but a separator in it must still not write outside the directory.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", host) or "default"
        self._dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self._state = self._dir / f"{safe}.json"
        self._lock_path = self._dir / f"{safe}.lock"
        self._shared = True
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            # Ours alone. The state files are 0600 anyway, but a directory
            # anyone can write lets another local user sit on our lock files,
            # which would cost us the coordination this exists to provide.
            with contextlib.suppress(OSError):
                os.chmod(self._dir, 0o700)
        except OSError:
            self._shared = False        # fall back to plain in-process pacing

    # -- shared state ------------------------------------------------------

    def _read(self) -> dict[str, float] | None:
        try:
            blob = json.loads(self._state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if blob.get("schema") != STATE_SCHEMA:
            return None
        try:
            return {"tokens": float(blob["tokens"]), "last": float(blob["last"]),
                    "rate": float(blob["rate"]), "ceiling": float(blob["ceiling"])}
        except (KeyError, TypeError, ValueError):
            return None

    def _write(self, state: dict[str, float]) -> None:
        payload = json.dumps({"schema": STATE_SCHEMA, **state})
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)        # nobody else's business, and nobody else's to edit
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self._state)
        finally:
            if os.path.exists(tmp):
                with contextlib.suppress(OSError):
                    os.unlink(tmp)

    def _current(self) -> dict[str, float]:
        """Shared state if there is any usable state, otherwise our own."""
        found = self._read()
        if found is None:
            return {"tokens": self._tokens, "last": time.time(),
                    "rate": self._rate, "ceiling": self._ceiling}
        # A ceiling we were built with that is lower than the stored one is
        # policy -- the most cautious resolver on a host wins -- so keep it.
        found["ceiling"] = min(found["ceiling"], self._ceiling)
        found["rate"] = min(found["rate"], found["ceiling"])
        return found

    def _refill(self, state: dict[str, float]) -> None:
        now = time.time()
        # Wall-clock, because it has to be comparable between processes, so it
        # can go backwards. A negative elapsed must never grant tokens.
        elapsed = max(0.0, now - state["last"])
        state["tokens"] = min(self._capacity, state["tokens"] + elapsed * state["rate"])
        state["last"] = now

    def _mutate(self, change) -> None:
        """Apply a change to the shared state, or locally if that is not possible."""
        if not self._shared:
            change(None)
            return
        try:
            with exclusive(self._lock_path, timeout=_PACING_LOCK_TIMEOUT):
                state = self._current()
                change(state)
                self._write(state)
                with self._lock:
                    self._rate, self._ceiling = state["rate"], state["ceiling"]
        except OSError:
            self._shared = False
            change(None)

    # -- the TokenBucket interface ----------------------------------------

    def set_rate(self, rate: float) -> None:
        def apply(state):
            if state is None:
                TokenBucket.set_rate(self, rate)
                return
            state["ceiling"] = max(min(rate, state["ceiling"]), 1e-6)
            state["rate"] = min(state["rate"], state["ceiling"])
        self._mutate(apply)

    def penalise(self) -> None:
        def apply(state):
            if state is None:
                TokenBucket.penalise(self)
                return
            state["rate"] = max(state["rate"] / 2.0,
                                state["ceiling"] / FLOOR_DIVISOR)
        self._mutate(apply)

    def recover(self) -> None:
        def apply(state):
            if state is None:
                TokenBucket.recover(self)
                return
            if state["rate"] < state["ceiling"]:
                state["rate"] = min(state["ceiling"],
                                    state["rate"] + state["ceiling"] / RECOVERY_STEPS)
        self._mutate(apply)

    @property
    def rate(self) -> float:
        if self._shared:
            found = self._read()
            if found is not None:
                return min(found["rate"], self._ceiling)
        return self._rate

    def acquire(self, tokens: float = 1.0) -> None:
        if not self._shared:
            TokenBucket.acquire(self, tokens)
            return
        while True:
            wait = 0.0
            try:
                with exclusive(self._lock_path, timeout=_PACING_LOCK_TIMEOUT):
                    state = self._current()
                    self._refill(state)
                    if state["tokens"] >= tokens:
                        state["tokens"] -= tokens
                        self._write(state)
                        with self._lock:
                            self._rate, self._ceiling = state["rate"], state["ceiling"]
                        return
                    wait = (tokens - state["tokens"]) / state["rate"]
                    self._write(state)
            except OSError:
                # Lost the ability to share part-way through a run. Keep pacing
                # ourselves rather than stopping.
                self._shared = False
                TokenBucket.acquire(self, tokens)
                return
            time.sleep(min(wait, 5.0))
