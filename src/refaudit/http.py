"""A small, deliberately conservative HTTP client.

Security posture, in order of what actually bites:

* **HTTPS only.** Plain-http URLs are refused rather than silently upgraded, so
  a mistyped resolver endpoint fails loudly instead of leaking a query in clear.
* **Redirects are bounded and kept on https**, so a redirect cannot downgrade
  the transport or send our ``mailto`` identifier somewhere unexpected.
* **Every request has a timeout.** A hung socket would otherwise stall a whole
  run behind the rate limiter.
* **Responses are size-capped while streaming.** We parse JSON and XML from
  these endpoints; an unbounded read is a trivial memory-exhaustion vector.
* **No credentials are ever hard-coded.** API keys come from the environment and
  are sent as headers, never as query parameters, so they stay out of logs.

We stay on the standard library on purpose: this package is run against a
manuscript shortly before submission, and a zero-dependency install is one less
supply-chain surface at exactly the wrong moment.
"""

from __future__ import annotations

import gzip
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .ratelimit import CircuitBreaker, TokenBucket

MAX_BYTES = 5 * 1024 * 1024  # generous for a bibliographic record, bounded all the same
DEFAULT_TIMEOUT = 20.0
MAX_REDIRECTS = 3


class HttpError(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.retry_after = retry_after


class TransportError(Exception):
    """Network-level failure: DNS, TLS, timeout, connection reset."""


@dataclass
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str]

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8", "replace"))

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Handle redirects ourselves so we can enforce https and a hop limit."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:  # HTTP-date form
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)
        delta = dt.timestamp() - time.time()
        return max(delta, 0.0)
    except (TypeError, ValueError, OverflowError):
        # A malformed Retry-After is the server's problem, not ours; fall back
        # to our own backoff rather than failing the request.
        return None


class HttpClient:
    """Rate-limited, retrying HTTP client scoped to one host."""

    def __init__(
        self,
        *,
        user_agent: str,
        bucket: TokenBucket,
        breaker: CircuitBreaker | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = 4,
        api_key_header: tuple[str, str] | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.bucket = bucket
        self.breaker = breaker or CircuitBreaker()
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._api_key_header = api_key_header
        self._opener = urllib.request.build_opener(_NoRedirect)

    # -- internals ---------------------------------------------------------

    def _open_once(self, url: str, headers: Mapping[str, str]) -> Response:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https":
            raise TransportError(f"refusing non-https URL: {parsed.scheme or '(none)'}")

        req = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise TransportError("response exceeded size cap")
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return Response(resp.status, raw, dict(resp.headers))
        except urllib.error.HTTPError as e:
            retry_after = _parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
            body = b""
            try:
                body = e.read(4096)
            except (OSError, ValueError):
                # The error body is only used to make the message readable;
                # losing it must not mask the HTTP status we actually care about.
                body = b""
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location", "") if e.headers else ""
                raise HttpError(e.code, f"redirect to {loc[:120]}")
            raise HttpError(e.code, body.decode("utf-8", "replace")[:200] or e.reason,
                            retry_after)
        except urllib.error.URLError as e:
            raise TransportError(str(e.reason)) from e
        except (TimeoutError, OSError) as e:
            raise TransportError(str(e)) from e

    # -- public ------------------------------------------------------------

    def get(self, url: str, *, accept: str = "application/json") -> Response:
        """GET with rate limiting, bounded redirects and retry-with-backoff.

        Raises ``HttpError`` for a definitive HTTP status and ``TransportError``
        for anything that means "we could not reach it".
        """
        if self.breaker.is_open:
            raise TransportError("circuit open for this host (repeated refusals)")

        headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Accept-Encoding": "gzip",
        }
        if self._api_key_header:
            headers[self._api_key_header[0]] = self._api_key_header[1]

        current = url
        last_exc: Exception | None = None

        for attempt in range(self.max_attempts):
            self.bucket.acquire()
            try:
                for hop in range(MAX_REDIRECTS + 1):
                    try:
                        resp = self._open_once(current, headers)
                    except HttpError as e:
                        if e.status in (301, 302, 303, 307, 308) and hop < MAX_REDIRECTS:
                            loc = str(e).split("redirect to ", 1)[-1]
                            current = urllib.parse.urljoin(current, loc)
                            continue
                        raise
                    self.breaker.record_success()
                    self._observe_rate_headers(resp.headers)
                    return resp
                raise TransportError("too many redirects")

            except HttpError as e:
                last_exc = e
                # 4xx other than 429 is a definitive answer; do not retry it.
                if e.status != 429 and 400 <= e.status < 500:
                    self.breaker.record_success()  # the host is healthy, our request wasn't
                    raise
                self.breaker.record_failure()
                delay = e.retry_after if e.retry_after is not None else self._backoff(attempt)
                if e.status == 429:
                    # The service is telling us our pace is wrong: slow down for good,
                    # not just for this attempt.
                    self.bucket.set_rate(self.bucket.rate / 2)
                if attempt < self.max_attempts - 1:
                    time.sleep(min(delay, 60.0))
                    continue
                raise
            except TransportError as e:
                last_exc = e
                self.breaker.record_failure()
                if attempt < self.max_attempts - 1:
                    time.sleep(self._backoff(attempt))
                    continue
                raise

        raise last_exc or TransportError("request failed")

    def _observe_rate_headers(self, headers: Mapping[str, str]) -> None:
        """Crossref publishes its current allowance; obey it rather than guess."""
        limit = headers.get("X-Rate-Limit-Limit") or headers.get("x-rate-limit-limit")
        interval = headers.get("X-Rate-Limit-Interval") or headers.get("x-rate-limit-interval")
        if not (limit and interval):
            return
        try:
            n = float(str(limit).strip())
            secs = float(str(interval).strip().rstrip("s") or "1")
            if n > 0 and secs > 0:
                # Use a fraction of the published allowance; we are a background job.
                self.bucket.set_rate(min(self.bucket.rate, (n / secs) * 0.5))
        except ValueError:
            return

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with full jitter, to avoid synchronised retries."""
        return random.uniform(0, min(2.0 * (2 ** attempt), 30.0))
