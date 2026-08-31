"""Crossref REST API.

Docs: https://api.crossref.org  /  https://www.crossref.org/documentation/retrieve-metadata/rest-api/

Crossref asks callers to identify themselves with a mailto in the User-Agent;
doing so puts us in the "polite pool", which is both faster and less likely to
be throttled. It publishes ``X-Rate-Limit-Limit`` / ``X-Rate-Limit-Interval``
headers, which :class:`~refaudit.http.HttpClient` reads and obeys, so the value
below is only the starting pace.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..http import HttpError, TransportError
from ..models import Entry, Found, NotFound, Outcome, Record, Unavailable
from ..normalize import clean_doi
from .base import HttpResolver, RateSpec

API = "https://api.crossref.org"


def _year(msg: dict[str, Any]) -> int | None:
    for field in ("issued", "published-print", "published-online", "created"):
        parts = (msg.get(field) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _record(msg: dict[str, Any]) -> Record:
    authors = msg.get("author") or []
    surname = ""
    if authors:
        surname = (authors[0].get("family") or "").strip()
    titles = msg.get("title") or []
    return Record(
        source="crossref",
        title=(titles[0] if titles else "").strip(),
        year=_year(msg),
        first_author_surname=surname,
        doi=(msg.get("DOI") or "").lower(),
        url=msg.get("URL", ""),
    )


class CrossrefDoi(HttpResolver):
    """Authoritative lookup by DOI. The strongest signal we have."""

    name = "crossref:doi"
    rate = RateSpec(
        per_second=2.0,
        burst=2.0,
        rationale="Crossref publishes a per-caller allowance in response headers; "
                  "we start conservatively and follow whatever it reports.",
    )

    def can_handle(self, entry: Entry) -> bool:
        return bool(clean_doi(entry.doi))

    def resolve(self, entry: Entry) -> Outcome:
        doi = clean_doi(entry.doi)
        if not doi:
            return NotFound(self.name, "no usable DOI")
        url = f"{API}/works/{urllib.parse.quote(doi, safe='')}"
        try:
            msg = self.http.get(url).json().get("message") or {}
        except HttpError as e:
            if e.status == 404:
                # Definitive: Crossref has no record for this DOI.
                return NotFound(self.name, "DOI not registered with Crossref")
            return Unavailable(self.name, str(e), e.retry_after)
        except (TransportError, ValueError) as e:
            return Unavailable(self.name, str(e))
        if not msg:
            return NotFound(self.name, "empty record")
        return Found(_record(msg))


class CrossrefTitle(HttpResolver):
    """Best-effort lookup by bibliographic string, for entries with no identifier."""

    name = "crossref:title"
    rate = RateSpec(2.0, 2.0, "same service as crossref:doi")

    def can_handle(self, entry: Entry) -> bool:
        return bool(entry.title.strip())

    def resolve(self, entry: Entry) -> Outcome:
        query = urllib.parse.urlencode(
            {
                "query.bibliographic": entry.title[:300],
                "rows": 3,
                "select": "DOI,title,author,issued,published-print,published-online,created,URL",
            }
        )
        try:
            items = (self.http.get(f"{API}/works?{query}").json()
                     .get("message", {}).get("items") or [])
        except HttpError as e:
            return Unavailable(self.name, str(e), e.retry_after)
        except (TransportError, ValueError) as e:
            return Unavailable(self.name, str(e))
        if not items:
            return NotFound(self.name, "no candidates")
        # The caller scores the candidate; returning the best-ranked one keeps
        # the "is this the same paper?" judgement in one place.
        return Found(_record(items[0]))
