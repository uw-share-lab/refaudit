"""Open Library -- books.

Docs: https://openlibrary.org/dev/docs/api/search

Monographs are the one entry type the article indexes genuinely cannot help
with: a ``@book`` with no DOI is invisible to Crossref, so it was reported as
NOT_FOUND however real it was. Open Library covers exactly that gap, is free
and needs no key. It only ever sees ``@book``-shaped entries, because for
articles it would be noise.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..http import HttpError, TransportError
from ..models import Entry, Found, NotFound, Outcome, Record, Unavailable
from ..normalize import clean_doi
from .base import HttpResolver, RateSpec

API = "https://openlibrary.org/search.json"

def _record(doc: dict[str, Any]) -> Record:
    names = doc.get("author_name") or []
    surname = ""
    if names:
        surname = (names[0] or "").strip().split()[-1] if names[0] else ""
    year = doc.get("first_publish_year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    return Record(
        source="openlibrary",
        title=(doc.get("title") or "").strip(),
        year=year,
        first_author_surname=surname,
        doi="",
        url=f"https://openlibrary.org{doc['key']}" if doc.get("key") else "",
    )


class OpenLibrary(HttpResolver):
    """Book lookup by title, for entries no article index will ever hold."""

    name = "openlibrary"
    # Open Library reports the earliest edition it knows about, which for a
    # reissued or self-published book is years from the edition being cited.
    # Comparing that against the bibliography would manufacture findings.
    year_is_authoritative = False
    rate = RateSpec(
        per_second=1.0,
        burst=1.0,
        rationale="Open Library is donation-funded and asks for a descriptive "
                  "User-Agent and modest request rates.",
    )

    def can_handle(self, entry: Entry) -> bool:
        # Registered last, so by the time this runs every article index has
        # already declined the entry. That is why it is not restricted to
        # @book: monographs are routinely filed as @article, and refusing on
        # type alone left real books reported as missing. A DOI-bearing entry
        # is left to the DOI resolvers, which are stronger evidence.
        return bool(entry.title.strip()) and not clean_doi(entry.doi)

    def resolve(self, entry: Entry) -> Outcome:
        query = urllib.parse.urlencode({
            "q": entry.title[:200],
            "limit": 3,
            "fields": "title,author_name,first_publish_year,key",
        })
        try:
            docs = self.http.get(f"{API}?{query}").json().get("docs") or []
        except HttpError as e:
            return Unavailable(self.name, str(e), e.retry_after)
        except (TransportError, ValueError) as e:
            return Unavailable(self.name, str(e))
        for doc in docs:
            if (doc.get("title") or "").strip():
                return Found(_record(doc))
        return NotFound(self.name, "no candidates")
