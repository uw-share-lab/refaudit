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
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .._version import __version__
from ..http import HttpClient
from ..models import Entry, Outcome
from ..ratelimit import CircuitBreaker, TokenBucket


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

    def can_handle(self, entry: Entry) -> bool: ...

    def resolve(self, entry: Entry) -> Outcome: ...


class HttpResolver:
    """Base class wiring a resolver to its own rate limiter and circuit breaker."""

    name: str = "base"
    rate: RateSpec = RateSpec(1.0, 1.0, "default")
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
        self.http = HttpClient(
            user_agent=f"refaudit/{__version__} "
                       f"(+https://github.com/uw-share-lab/refaudit; mailto:{contact_email})",
            bucket=TokenBucket(self.rate.per_second, self.rate.burst),
            breaker=CircuitBreaker(),
            timeout=timeout,
            api_key_header=key_header,
        )

    def can_handle(self, entry: Entry) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def resolve(self, entry: Entry) -> Outcome:  # pragma: no cover - overridden
        raise NotImplementedError
