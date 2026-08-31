"""Every request sent must cost a token, redirect hops included.

The bucket was drawn from once per *attempt*, but an attempt could follow up to
``MAX_REDIRECTS`` hops before returning -- so a redirect chain sent four requests
on the strength of one token and briefly ran at four times the rate the service
documents. Pacing has to count what leaves the machine, not what the caller
asked for.
"""

import pytest

from refaudit.http import MAX_REDIRECTS, HttpClient, HttpError, Response, TransportError
from refaudit.ratelimit import CircuitBreaker, TokenBucket


class _CountingBucket(TokenBucket):
    """A real bucket that also records how often it was drawn from."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.acquires = 0

    def acquire(self, tokens: float = 1.0) -> None:
        self.acquires += 1
        super().acquire(tokens)


class _Scripted(HttpClient):
    def __init__(self, outcomes, **kw):
        super().__init__(**kw)
        self._outcomes = list(outcomes)

    def _open_once(self, url, headers):
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _redirect(to: str) -> HttpError:
    return HttpError(302, f"redirect to {to}")


def _client(outcomes, **kw):
    # Fast enough that pacing costs no wall-clock; this is about the count.
    bucket = kw.pop("bucket", None) or _CountingBucket(1000.0, 1000)
    kw.setdefault("user_agent", "test")
    return _Scripted(outcomes, bucket=bucket, **kw), bucket


def test_a_direct_request_costs_one_token():
    client, bucket = _client([Response(200, b"{}", {})])
    client.get("https://example.org/a")
    assert bucket.acquires == 1


def test_each_redirect_hop_costs_its_own_token():
    client, bucket = _client([
        _redirect("https://example.org/b"),
        _redirect("https://example.org/c"),
        Response(200, b"{}", {}),
    ])
    client.get("https://example.org/a")
    assert bucket.acquires == 3, "two hops plus the final request are three requests"


def test_an_exhausted_redirect_chain_still_paid_for_every_hop():
    # One attempt only: a chain that runs out of hops is retried like any other
    # failure, and this test is about what a single attempt costs.
    client, bucket = _client([_redirect(f"https://example.org/{i}")
                              for i in range(MAX_REDIRECTS + 1)],
                             max_attempts=1)
    try:
        client.get("https://example.org/a")
    except (HttpError, TransportError):
        pass
    assert bucket.acquires == MAX_REDIRECTS + 1


def test_a_retry_still_costs_a_token():
    """The per-attempt cost must survive the change; this is the regression."""
    client, bucket = _client(
        [TransportError("connection reset"), Response(200, b"{}", {})],
        max_attempts=2,
    )
    client.get("https://example.org/a")
    assert bucket.acquires == 2


def test_a_retry_after_a_redirect_pays_for_both():
    client, bucket = _client([
        _redirect("https://example.org/b"),
        TransportError("connection reset"),
        Response(200, b"{}", {}),
    ], max_attempts=2)
    client.get("https://example.org/a")
    assert bucket.acquires == 3


# --- a chain we cannot follow is an answer, not a failure -------------------

class _AlwaysRedirects(HttpClient):
    """An endpoint stuck in a redirect loop. Counts what we actually send."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.requests = 0

    def _open_once(self, url, headers):
        self.requests += 1
        raise HttpError(302, f"redirect to https://example.org/{self.requests}")


def _looping(**kw):
    bucket = _CountingBucket(1000.0, 1000)
    breaker = CircuitBreaker(threshold=4, cooldown=60.0)
    kw.setdefault("user_agent", "test")
    return _AlwaysRedirects(bucket=bucket, breaker=breaker, **kw), bucket, breaker


def test_an_exhausted_chain_is_not_retried():
    """Retrying re-walks the same hops; four attempts cost sixteen requests."""
    client, bucket, _ = _looping(max_attempts=4)
    with pytest.raises(TransportError):
        client.get("https://example.org/a")
    assert client.requests == MAX_REDIRECTS + 1
    assert bucket.acquires == MAX_REDIRECTS + 1


def test_an_exhausted_chain_does_not_trip_the_circuit_breaker():
    """The host answered every time; it is our URL that goes nowhere."""
    client, _, breaker = _looping(max_attempts=4)
    with pytest.raises(TransportError):
        client.get("https://example.org/a")
    assert not breaker.is_open, "a redirecting host is not a failing host"


def test_an_exhausted_chain_reads_as_unreachable_to_a_resolver():
    """Resolvers turn TransportError into Unavailable, which is UNVERIFIED --
    the safe direction. It must not surface as evidence about the entry."""
    client, _, _ = _looping(max_attempts=4)
    with pytest.raises(TransportError, match="redirect"):
        client.get("https://example.org/a")


# --- a retry is a retry of what the caller asked for ------------------------

class _Recording(HttpClient):
    """Answers per URL, and records the exact order of URLs requested."""

    def __init__(self, script, **kw):
        super().__init__(**kw)
        self.script = {k: list(v) for k, v in script.items()}
        self.seen: list[str] = []

    def _open_once(self, url, headers):
        self.seen.append(url)
        outcomes = self.script.get(url)
        if not outcomes:
            raise AssertionError(f"unscripted request to {url}")
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _recording(script, **kw):
    kw.setdefault("user_agent", "test")
    kw.setdefault("bucket", _CountingBucket(1000.0, 1000))
    return _Recording(script, **kw)


A = "https://example.org/a"
B = "https://example.org/b"


def test_a_retry_starts_again_from_the_url_the_caller_asked_for():
    """A redirect followed once must not pin every later attempt to its target.

    The redirect may itself have been transient -- a load balancer bouncing us,
    a maintenance page -- and resuming from it means never asking for the thing
    the caller actually wanted again.
    """
    client = _recording({
        A: [_redirect(B), Response(200, b"{}", {})],
        B: [TransportError("connection reset")],
    }, max_attempts=2)

    resp = client.get(A)

    assert resp.status == 200
    assert client.seen == [A, B, A]


def test_the_hop_budget_is_measured_from_the_original_url_each_attempt():
    """Otherwise a chain walks max_attempts x MAX_REDIRECTS hops from the start,
    quietly multiplying the bound that keeps a redirect from taking us -- and
    the mailto identifier we send -- somewhere unexpected."""
    client = _recording({
        A: [_redirect(B), _redirect(B)],
        B: [TransportError("reset"), TransportError("reset")],
    }, max_attempts=2)

    with pytest.raises(TransportError):
        client.get(A)

    assert client.seen == [A, B, A, B]
