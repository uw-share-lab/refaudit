"""A throttled run must be able to speed back up.

The bucket could only ever slow down: every 429 halved it and nothing raised it
again, so a transient burst of refusals early in a run left the remaining
entries crawling at a fraction of the documented rate for the life of the
process. Decrease stays multiplicative -- that is what makes backoff safe -- but
recovery is additive and bounded by whatever ceiling the service last implied.
"""

import pytest

from refaudit.http import HttpClient, HttpError
from refaudit.ratelimit import TokenBucket


def test_penalty_halves_the_rate():
    b = TokenBucket(rate=2.0, capacity=2)
    b.penalise()
    assert b.rate == pytest.approx(1.0)


def test_penalty_stops_at_a_floor_instead_of_approaching_zero():
    """Fifty refusals must not leave a rate that takes minutes per request."""
    b = TokenBucket(rate=2.0, capacity=2)
    for _ in range(50):
        b.penalise()
    assert b.rate >= 2.0 / 16


def test_recovery_climbs_back_to_the_declared_rate():
    b = TokenBucket(rate=2.0, capacity=2)
    b.penalise()
    b.penalise()
    assert b.rate < 2.0
    for _ in range(100):
        b.recover()
    assert b.rate == pytest.approx(2.0)


def test_recovery_never_exceeds_the_declared_rate():
    b = TokenBucket(rate=2.0, capacity=2)
    for _ in range(100):
        b.recover()
    assert b.rate == pytest.approx(2.0)


def test_recovery_respects_a_ceiling_the_service_asked_for():
    """A rate the service published is a ceiling, not a dip to recover from."""
    b = TokenBucket(rate=2.0, capacity=2)
    b.set_rate(0.5)                      # e.g. Crossref's X-Rate-Limit headers
    b.penalise()
    for _ in range(100):
        b.recover()
    assert b.rate == pytest.approx(0.5)


def test_lowering_the_ceiling_also_lowers_the_floor():
    b = TokenBucket(rate=2.0, capacity=2)
    b.set_rate(0.5)
    for _ in range(50):
        b.penalise()
    assert b.rate >= 0.5 / 16
    assert b.rate < 0.5


# --- wired through the client ---------------------------------------------

class _Scripted(HttpClient):
    """Replays a fixed sequence of outcomes without touching the network."""

    def __init__(self, outcomes, **kw):
        super().__init__(**kw)
        self._outcomes = list(outcomes)

    def _open_once(self, url, headers):
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok():
    from refaudit.http import Response
    return Response(200, b"{}", {})


def test_a_429_slows_the_bucket_and_later_successes_restore_it():
    # A rate high enough that pacing costs no real time; this test is about
    # what the client does to the bucket, not about how the bucket waits.
    bucket = TokenBucket(rate=1000.0, capacity=1000)
    client = _Scripted(
        [HttpError(429, "slow down", retry_after=0.0), _ok()],
        user_agent="test", bucket=bucket, max_attempts=2,
    )
    client.get("https://example.org/a")
    # Halved by the 429, then one recovery step back because the retry itself
    # succeeded: 1000 -> 500 -> 550.
    throttled = bucket.rate
    assert throttled == pytest.approx(550.0)
    assert throttled < 1000.0

    for _ in range(40):
        client._outcomes.append(_ok())
        client.get("https://example.org/a")
    assert bucket.rate == pytest.approx(1000.0)
