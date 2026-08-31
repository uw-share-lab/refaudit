"""The socket-facing half of the client, with the socket replaced.

``_open_once`` was the least-covered code in ``http.py``: the response size
cap, the gzip path, and the classification of urllib's exception family into
"the service answered" versus "we could not reach it". Every test elsewhere
stubs ``_open_once`` itself, so none of it ran.

That distinction is the one the whole tool rests on. An ``HttpError`` can end
up as a statement about a reference; a ``TransportError`` never can, because a
resolver turns it into ``Unavailable``. Getting the classification wrong here
is how a network problem becomes an accusation.

Nothing here opens a socket: the opener is replaced with one that returns
prepared responses, which is enough to exercise the real parsing, capping and
exception handling.
"""

from __future__ import annotations

import email.utils
import gzip
import io
import time
import urllib.error

import pytest

from refaudit.http import (
    MAX_BYTES,
    HttpClient,
    HttpError,
    TransportError,
    _parse_retry_after,
)
from refaudit.ratelimit import TokenBucket


class _Resp(io.BytesIO):
    """Stands in for what urlopen returns: a readable with status and headers."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _Opener:
    """Returns a *fresh* response per call.

    A BytesIO is consumed by the first read, so handing the same object to a
    retry would let it see an empty body and quietly succeed -- hiding exactly
    the failure the test is asserting.
    """

    def __init__(self, outcome):
        self._outcome = outcome

    def open(self, req, timeout=None):
        outcome = self._outcome() if callable(self._outcome) else self._outcome
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(outcome, **kw):
    kw.setdefault("user_agent", "test")
    kw.setdefault("bucket", TokenBucket(1000.0, 1000))
    c = HttpClient(**kw)
    c._opener = _Opener(outcome)
    return c


def _http_error(code, headers=None, body=b"", reason="reason"):
    return urllib.error.HTTPError(
        "https://example.org/x", code, reason, headers or {}, io.BytesIO(body)
    )


# --- the happy path ---------------------------------------------------------

def test_a_plain_response_is_returned_with_status_and_headers():
    c = _client(_Resp(b'{"ok":true}', 200, {"X-Thing": "1"}))
    resp = c.get("https://example.org/a")

    assert resp.status == 200
    assert resp.json() == {"ok": True}
    assert resp.headers["X-Thing"] == "1"


def test_a_gzipped_body_is_decompressed():
    """We advertise gzip in every request, so this is the normal path for the
    larger records, not an edge case."""
    packed = gzip.compress(b'{"ok":true}')
    c = _client(_Resp(packed, 200, {"Content-Encoding": "gzip"}))

    assert c.get("https://example.org/a").json() == {"ok": True}


def test_the_content_encoding_header_is_matched_case_insensitively():
    packed = gzip.compress(b'{"ok":true}')
    c = _client(_Resp(packed, 200, {"Content-Encoding": "GZIP"}))

    assert c.get("https://example.org/a").json() == {"ok": True}


def test_text_decodes_leniently_rather_than_raising():
    """A stray byte in an otherwise usable record must not fail the run."""
    c = _client(_Resp(b"caf\xff", 200))
    assert "caf" in c.get("https://example.org/a").text()


# --- the size cap -----------------------------------------------------------

def test_a_response_over_the_cap_is_refused():
    """An unbounded read is a trivial memory-exhaustion vector, and no
    bibliographic record is anywhere near this size."""
    c = _client(lambda: _Resp(b"x" * (MAX_BYTES + 1), 200), max_attempts=1)
    with pytest.raises(TransportError, match="size cap"):
        c.get("https://example.org/a")


def test_a_response_at_exactly_the_cap_is_allowed():
    c = _client(_Resp(b"x" * MAX_BYTES, 200))
    assert len(c.get("https://example.org/a").body) == MAX_BYTES


def test_the_cap_failure_is_a_transport_error_not_an_http_one():
    """It must reach the resolver as Unavailable. Treating an oversized body
    as an answer would let it become a statement about the reference."""
    c = _client(lambda: _Resp(b"x" * (MAX_BYTES + 1), 200), max_attempts=1)
    with pytest.raises(TransportError):
        c.get("https://example.org/a")


# --- classifying urllib's exceptions ---------------------------------------

def test_an_http_error_becomes_an_http_error_with_its_status():
    c = _client(_http_error(404, body=b"no such work"), max_attempts=1)
    with pytest.raises(HttpError) as caught:
        c.get("https://example.org/a")

    assert caught.value.status == 404
    assert "no such work" in str(caught.value)


def test_an_error_body_that_cannot_be_read_still_yields_the_status():
    """The body only makes the message readable. Losing it must not lose the
    status, which is the part the caller branches on."""
    class _Unreadable(urllib.error.HTTPError):
        def read(self, *a, **k):
            raise OSError("stream already closed")

    c = _client(_Unreadable("https://example.org/x", 503, "busy", {}, None),
                max_attempts=1)
    with pytest.raises(HttpError) as caught:
        c.get("https://example.org/a")
    assert caught.value.status == 503


def test_a_url_error_becomes_a_transport_error():
    c = _client(urllib.error.URLError("name resolution failed"), max_attempts=1)
    with pytest.raises(TransportError, match="name resolution"):
        c.get("https://example.org/a")


def test_a_timeout_becomes_a_transport_error():
    c = _client(TimeoutError("timed out"), max_attempts=1)
    with pytest.raises(TransportError):
        c.get("https://example.org/a")


def test_a_socket_error_becomes_a_transport_error():
    c = _client(OSError("connection reset by peer"), max_attempts=1)
    with pytest.raises(TransportError, match="connection reset"):
        c.get("https://example.org/a")


def test_a_redirect_status_carries_its_location():
    """Asserted against _open_once, because get() consumes a redirect by
    following it -- which is what test_redirect_pacing covers."""
    c = _client(_http_error(302, headers={"Location": "https://example.org/b"}))
    with pytest.raises(HttpError) as caught:
        c._open_once("https://example.org/a", {})

    assert caught.value.status == 302
    assert "https://example.org/b" in str(caught.value)


def test_a_redirect_with_no_location_still_reports_its_status():
    c = _client(_http_error(302, headers={}))
    with pytest.raises(HttpError) as caught:
        c._open_once("https://example.org/a", {})
    assert caught.value.status == 302


# --- Retry-After parsing ----------------------------------------------------

def test_a_numeric_retry_after_is_seconds():
    assert _parse_retry_after("120") == 120.0


def test_an_http_date_retry_after_is_converted_to_a_delay():
    """RFC 9110 allows either form and real services use both."""
    later = email.utils.formatdate(time.time() + 300, usegmt=True)
    delay = _parse_retry_after(later)

    assert delay is not None
    assert 240 < delay < 360


def test_an_http_date_in_the_past_is_no_delay_rather_than_negative():
    past = email.utils.formatdate(time.time() - 600, usegmt=True)
    assert _parse_retry_after(past) == 0.0


@pytest.mark.parametrize("value", [None, "", "   ", "soon", "not-a-date", "12x"])
def test_an_unusable_retry_after_falls_back_to_our_own_backoff(value):
    """A malformed header is the server's problem. Failing the request over it
    would turn their bug into a finding about somebody's bibliography."""
    assert _parse_retry_after(value) is None


def test_a_retry_after_header_reaches_the_caller_on_a_429():
    c = _client(_http_error(429, headers={"Retry-After": "90"}), max_attempts=1)
    with pytest.raises(HttpError) as caught:
        c.get("https://example.org/a")
    assert caught.value.retry_after == 90.0


# --- the rate-limit headers Crossref publishes -----------------------------

def test_a_published_allowance_lowers_our_ceiling():
    """Crossref tells us its per-caller allowance; we take half of it and never
    go faster, even if our declared rate was higher."""
    bucket = TokenBucket(50.0, 50)
    c = _client(_Resp(b"{}", 200, {"X-Rate-Limit-Limit": "10",
                                   "X-Rate-Limit-Interval": "1s"}),
                bucket=bucket)
    c.get("https://example.org/a")

    assert bucket.rate == pytest.approx(5.0)


def test_a_published_allowance_never_raises_our_rate():
    bucket = TokenBucket(1.0, 1)
    c = _client(_Resp(b"{}", 200, {"X-Rate-Limit-Limit": "100",
                                   "X-Rate-Limit-Interval": "1s"}),
                bucket=bucket)
    c.get("https://example.org/a")

    assert bucket.rate <= 1.0, "a generous allowance is not an invitation"


@pytest.mark.parametrize("headers", [
    {"X-Rate-Limit-Limit": "nonsense", "X-Rate-Limit-Interval": "1s"},
    {"X-Rate-Limit-Limit": "10", "X-Rate-Limit-Interval": "nonsense"},
    {"X-Rate-Limit-Limit": "0", "X-Rate-Limit-Interval": "1s"},
    {"X-Rate-Limit-Limit": "10"},
])
def test_unusable_rate_headers_leave_our_pacing_alone(headers):
    bucket = TokenBucket(2.0, 2)
    c = _client(_Resp(b"{}", 200, headers), bucket=bucket)
    c.get("https://example.org/a")

    assert bucket.rate == pytest.approx(2.0)


def test_the_headers_are_read_case_insensitively():
    bucket = TokenBucket(50.0, 50)
    c = _client(_Resp(b"{}", 200, {"x-rate-limit-limit": "10",
                                   "x-rate-limit-interval": "1s"}),
                bucket=bucket)
    c.get("https://example.org/a")

    assert bucket.rate == pytest.approx(5.0)


# --- transport policy -------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://example.org/insecure",
    "ftp://example.org/x",
    "example.org/no-scheme",
])
def test_only_https_is_ever_opened(url):
    """Refused loudly rather than silently upgraded, so a mistyped endpoint
    fails instead of leaking a query -- and our mailto -- in clear."""
    c = _client(_Resp(b"{}", 200), max_attempts=1)
    with pytest.raises(TransportError, match="non-https"):
        c.get(url)


def test_the_user_agent_and_accept_headers_are_sent():
    seen = {}

    class _Capturing(_Opener):
        def open(self, req, timeout=None):
            seen.update(req.headers)
            return _Resp(b"{}", 200)

    c = _client(_Resp(b"{}", 200), user_agent="refaudit/test (mailto:a@b.org)")
    c._opener = _Capturing(None)
    c.get("https://example.org/a", accept="application/xml")

    lowered = {k.lower(): v for k, v in seen.items()}
    assert lowered["User-agent".lower()] == "refaudit/test (mailto:a@b.org)"
    assert lowered["Accept".lower()] == "application/xml"
    assert "gzip" in lowered["Accept-encoding".lower()]


def test_an_api_key_is_sent_as_a_header_and_never_in_the_url():
    seen = {}

    class _Capturing(_Opener):
        def open(self, req, timeout=None):
            seen["headers"] = dict(req.headers)
            seen["url"] = req.full_url
            return _Resp(b"{}", 200)

    c = _client(_Resp(b"{}", 200), api_key_header=("X-Api-Key", "s3cret"))
    c._opener = _Capturing(None)
    c.get("https://example.org/a")

    lowered = {k.lower(): v for k, v in seen["headers"].items()}
    assert lowered["x-api-key"] == "s3cret"
    assert "s3cret" not in seen["url"], "a key in a URL ends up in logs"
