"""Resolver contract.

A resolver knows how to look one entry up in one external index. The contract is
deliberately narrow:

* ``can_handle`` decides whether this resolver has anything to offer for an
  entry (a DOI resolver has nothing to say about a preprint with no DOI).
* ``resolve`` returns ``Found`` / ``NotFound`` / ``Unavailable`` and must never
  raise for an ordinary network problem -- an unreachable service is a normal,
  expected outcome that the caller has to distinguish from a real answer.

Each resolver also declares the rate limit its service documents, so the pacing
policy lives next to the thing it describes instead of in a constants file.
"""

from __future__ import annotations

import os
import threading
import urllib.parse
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .._version import __version__
from ..http import HttpClient
from ..models import Entry, Outcome
from ..ratelimit import CircuitBreaker, SharedTokenBucket, TokenBucket


@dataclass(frozen=True)
class RateSpec:
    """Requests per second we will allow ourselves, and why."""

    per_second: float
    burst: float
    rationale: str


@runtime_checkable
class Resolver(Protocol):
    name: str
    rate: RateSpec
    #: Whether this source's year can be compared against the bibliography.
    #: False for indexes that report an edition or printing rather than the
    #: date of the work, where a difference is not evidence of an error.
    year_is_authoritative: bool

    def can_handle(self, entry: Entry) -> bool: ...

    def resolve(self, entry: Entry) -> Outcome: ...


# Pacing belongs to the *host*, not to the resolver. Two resolvers that call
# api.crossref.org are still one caller as far as Crossref is concerned, so
# giving each its own bucket would let us send the sum of their rates and earn
# exactly the throttling the buckets exist to avoid. Shared per process, and
# the most cautious rate any resolver declares for a host wins.
_HOST_PACING: dict[str, tuple[TokenBucket, CircuitBreaker]] = {}
_HOST_PACING_LOCK = threading.Lock()


def _new_bucket(host: str, rate: RateSpec) -> TokenBucket:
    """A bucket shared with every other refaudit this user is running.

    Per-process pacing is right across *users*, since each runs under their own
    contact address and is a separate identified caller. It is wrong for one
    person running several at once -- two terminals, a job array -- because the
    service sees a single caller at a multiple of the promised rate. The state
    lives under the user's own cache directory, so different people on a shared
    machine stay independent, which is what we want.

    ``REFAUDIT_NO_SHARED_PACING=1`` opts out, for a filesystem where this is a
    bad idea or anyone who would rather refaudit wrote nothing outside its
    output directory. Sharing also degrades to this on any error, so the opt-out
    is a preference rather than a safety valve.
    """
    if os.environ.get("REFAUDIT_NO_SHARED_PACING", "").strip():
        return TokenBucket(rate.per_second, rate.burst)
    return SharedTokenBucket(rate.per_second, rate.burst, host=host)


def _pacing_for(host: str, rate: RateSpec) -> tuple[TokenBucket, CircuitBreaker]:
    with _HOST_PACING_LOCK:
        existing = _HOST_PACING.get(host)
        if existing is None:
            pacing = (_new_bucket(host, rate), CircuitBreaker())
            _HOST_PACING[host] = pacing
            return pacing
        bucket, breaker = existing
        if rate.per_second < bucket.rate:
            bucket.set_rate(rate.per_second)
        return bucket, breaker


def reset_pacing() -> None:
    """Forget every shared bucket. For tests; not part of the public API."""
    with _HOST_PACING_LOCK:
        _HOST_PACING.clear()


class HttpResolver:
    """Base class wiring a resolver to the shared pacing for its host."""

    name: str = "base"
    rate: RateSpec = RateSpec(1.0, 1.0, "default")
    year_is_authoritative: bool = True
    #: The endpoint this resolver calls. Only its host is used, to decide which
    #: resolvers must share a rate limit.
    api_base: str = ""
    api_key_env: str | None = None
    api_key_header: str | None = None

    def __init__(self, *, contact_email: str, timeout: float = 20.0) -> None:
        if not contact_email:
            raise ValueError(
                "contact_email is required: Crossref and OpenAlex give identified "
                "callers a separate, more reliable pool, and it is the courtesy "
                "these services ask for in their documentation."
            )
        self.contact_email = contact_email
        key_header = None
        if self.api_key_env:
            value = os.environ.get(self.api_key_env, "").strip()
            if value and self.api_key_header:
                # Sent as a header, never as a query parameter, so it stays out
                # of any URL that might be logged.
                key_header = (self.api_key_header, value)
        host = urllib.parse.urlsplit(self.api_base).netloc or self.name
        bucket, breaker = _pacing_for(host, self.rate)
        self.host = host
        self.http = HttpClient(
            user_agent=f"refaudit/{__version__} "
                       f"(+https://github.com/uw-share-lab/refaudit; mailto:{contact_email})",
            bucket=bucket,
            breaker=breaker,
            timeout=timeout,
            api_key_header=key_header,
        )

    def can_handle(self, entry: Entry) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def resolve(self, entry: Entry) -> Outcome:  # pragma: no cover - overridden
        raise NotImplementedError
