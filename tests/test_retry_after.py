"""A service that asks for hours must not be re-asked four times.

Seen against the live OpenAlex API: it answered 429 with ``Retry-After: 29895``
-- 8.3 hours -- and the client capped its sleep at 60s and retried anyway, four
times per entry. Three minutes of dead time each, spent asking a service that
had explicitly said no, and repeated for every later entry that reached it.

The circuit breaker could not help: those 60s sleeps are longer than its own
120s cooldown, so it half-opened between entries and never engaged. It logged
zero trips across eight consecutive refusals.

A ``Retry-After`` longer than this run is willing to wait is an answer, not a
delay. We stand down for the host and let the entry come back UNVERIFIED, which
is the safe direction -- no verdict rather than a wrong one.
"""

import time

import pytest

from refaudit.http import MAX_RETRY_AFTER, HttpClient, HttpError, Response, TransportError
from refaudit.ratelimit import CircuitBreaker, TokenBucket


class _Counting(HttpClient):
    def __init__(self, outcomes, **kw):
        super().__init__(**kw)
        self._outcomes = list(outcomes)
        self.requests = 0

    def _open_once(self, url, headers):
        self.requests += 1
        outcome = self._outcomes.pop(0) if self._outcomes else self._outcomes
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(outcomes, **kw):
    kw.setdefault("user_agent", "test")
    kw.setdefault("bucket", TokenBucket(1000.0, 1000))
    kw.setdefault("breaker", CircuitBreaker(threshold=4, cooldown=60.0))
    return _Counting(outcomes, **kw)


# --- the circuit breaker needs to be openable for a stated period ----------

def test_open_for_holds_the_circuit_for_the_period_given():
    cb = CircuitBreaker(threshold=4, cooldown=0.01)
    cb.open_for(30.0)
    assert cb.is_open, "must not fall back to the short default cooldown"


def test_open_for_recovers_once_the_period_has_passed():
    cb = CircuitBreaker(threshold=4, cooldown=60.0)
    cb.open_for(0.05)
    assert cb.is_open
    time.sleep(0.08)
    assert not cb.is_open, "half-open probe must be allowed through afterwards"


def test_a_success_clears_a_period_open():
    cb = CircuitBreaker(threshold=4, cooldown=60.0)
    cb.open_for(30.0)
    cb.record_success()
    assert not cb.is_open


# --- the client's behaviour -------------------------------------------------

def test_a_long_retry_after_is_not_slept_through_and_retried():
    """One request, not four; and no 60-second sleeps to get there."""
    client = _client(
        [HttpError(429, "quota exhausted", retry_after=29895.0)] * 4,
        max_attempts=4,
    )
    started = time.monotonic()
    with pytest.raises(HttpError):
        client.get("https://api.openalex.org/works")
    assert client.requests == 1, "the service said hours; asking again is not a retry"
    assert time.monotonic() - started < 1.0, "must not sleep on the way out"


def test_a_long_retry_after_stands_the_host_down_for_the_rest_of_the_run():
    """Otherwise every later entry burns its own attempts on the same refusal."""
    client = _client(
        [HttpError(429, "quota exhausted", retry_after=29895.0)],
        max_attempts=4,
    )
    with pytest.raises(HttpError):
        client.get("https://api.openalex.org/works")

    before = client.requests
    with pytest.raises(TransportError):
        client.get("https://api.openalex.org/works/other")
    assert client.requests == before, "a stood-down host must not be contacted again"


def test_a_short_retry_after_is_still_honoured_normally():
    """The regression guard: brief, ordinary throttling still retries."""
    client = _client(
        [HttpError(429, "slow down", retry_after=0.01), Response(200, b"{}", {})],
        max_attempts=3,
    )
    resp = client.get("https://api.crossref.org/works")
    assert resp.status == 200
    assert client.requests == 2


def test_the_threshold_is_the_sleep_cap_we_already_use():
    """A wait we would have sat through anyway is not a stand-down."""
    assert MAX_RETRY_AFTER == 60.0


def test_a_429_without_a_retry_after_still_backs_off_and_retries():
    """No header means no instruction; fall back to our own backoff."""
    client = _client(
        [HttpError(429, "slow down"), Response(200, b"{}", {})],
        max_attempts=3,
    )
    resp = client.get("https://api.crossref.org/works")
    assert resp.status == 200
    assert client.requests == 2
