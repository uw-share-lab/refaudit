"""DBLP -- the computer science bibliography.

Docs: https://dblp.org/faq/How+to+use+the+dblp+search+API.html

DBLP is curated rather than harvested, so its records for CS venues are cleaner
than a general index's, and it is free, unmetered and needs no key. For an HCI
or systems bibliography it is the most reliable title lookup available, and it
usually returns the DOI as well -- which turns "we could not find this" into a
correction the author can act on.

DBLP asks callers not to hammer it and returns 429 when they do, so the pace
here is deliberately slower than the commercial APIs.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..http import HttpError, TransportError
from ..models import Entry, Found, NotFound, Outcome, Record, Unavailable
from .base import HttpResolver, RateSpec

API = "https://dblp.org/search/publ/api"


def _authors(info: dict[str, Any]) -> str:
    block = (info.get("authors") or {}).get("author") or []
    # One author comes back as an object, several as a list.
    if isinstance(block, dict):
        block = [block]
    for author in block:
        text = (author.get("text") or "").strip() if isinstance(author, dict) else ""
        if text:
            # DBLP disambiguates repeated names with a trailing number
            # ("Yuan Chen 0011"); it is not part of the name.
            parts = [p for p in text.split() if not p.isdigit()]
            return parts[-1] if parts else ""
    return ""


def _record(info: dict[str, Any]) -> Record:
    year = info.get("year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    return Record(
        source="dblp",
        title=(info.get("title") or "").strip().rstrip("."),
        year=year,
        first_author_surname=_authors(info),
        doi=(info.get("doi") or "").lower(),
        url=info.get("ee", "") or info.get("url", "") or "",
    )


class Dblp(HttpResolver):
    """Title lookup against the curated CS bibliography."""

    name = "dblp"
    rate = RateSpec(
        per_second=1.0,
        burst=1.0,
        rationale="DBLP is a small academic service that asks callers not to "
                  "hammer it and answers 429 when they do.",
    )

    def can_handle(self, entry: Entry) -> bool:
        return bool(entry.title.strip())

    def resolve(self, entry: Entry) -> Outcome:
        query = urllib.parse.urlencode(
            {"q": entry.title[:200], "format": "json", "h": 3}
        )
        try:
            payload = self.http.get(f"{API}?{query}").json()
        except HttpError as e:
            return Unavailable(self.name, str(e), e.retry_after)
        except (TransportError, ValueError) as e:
            return Unavailable(self.name, str(e))
        hits = ((payload.get("result") or {}).get("hits") or {}).get("hit") or []
        if isinstance(hits, dict):
            hits = [hits]
        for hit in hits:
            info = hit.get("info") if isinstance(hit, dict) else None
            if info and (info.get("title") or "").strip():
                # The caller scores the candidate; we return the best-ranked
                # one so the "is this the same paper?" judgement stays in one
                # place.
                return Found(_record(info))
        return NotFound(self.name, "no candidates")
