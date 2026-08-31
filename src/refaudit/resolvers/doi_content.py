"""DOI content negotiation -- metadata for a DOI from any registration agency.

Docs: https://citation.crosscite.org/docs.html

Asking ``doi.org`` for ``application/vnd.citationstyles.csl+json`` returns CSL
JSON for *any* registered DOI, whichever agency owns it. That makes this the
one DOI lookup that cannot be defeated by guessing the wrong agency, and the
reason it exists: querying only Crossref once reported 22 live arXiv preprints
as dead references.

It sits behind the agency-specific resolvers rather than in front of them,
because Crossref and DataCite return richer records and Crossref publishes the
rate-limit headers we pace ourselves by. This is the safety net that catches
DOIs from the agencies we do not query directly -- mEDRA, JaLC, KISTI, OP and
the rest.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..http import HttpError, TransportError
from ..models import Entry, Found, NotFound, Outcome, Record, Unavailable
from ..normalize import clean_doi
from .base import HttpResolver, RateSpec

API = "https://doi.org"
CSL_JSON = "application/vnd.citationstyles.csl+json"


def _year(msg: dict[str, Any]) -> int | None:
    for field in ("issued", "published-print", "published-online", "created"):
        parts = (msg.get(field) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _title(msg: dict[str, Any]) -> str:
    title = msg.get("title")
    # CSL allows either a string or a list; agencies disagree about which.
    if isinstance(title, list):
        title = title[0] if title else ""
    return (title or "").strip()


def _record(msg: dict[str, Any]) -> Record:
    authors = msg.get("author") or []
    surname = ""
    if authors:
        first = authors[0]
        surname = (first.get("family") or first.get("literal")
                   or first.get("name") or "").strip()
    return Record(
        source="doi.org",
        title=_title(msg),
        year=_year(msg),
        first_author_surname=surname,
        doi=(msg.get("DOI") or "").lower(),
        url=msg.get("URL", "") or "",
    )


class DoiContentNegotiation(HttpResolver):
    """Agency-agnostic DOI metadata, via the DOI proxy."""

    name = "doi:content"
    api_base = API
    rate = RateSpec(
        per_second=2.0,
        burst=2.0,
        rationale="The proxy redirects to the owning agency's server, so each "
                  "call costs a third party a real request; paced like Crossref.",
    )

    def can_handle(self, entry: Entry) -> bool:
        return bool(clean_doi(entry.doi))

    def resolve(self, entry: Entry) -> Outcome:
        doi = clean_doi(entry.doi)
        if not doi:
            return NotFound(self.name, "no usable DOI")
        url = f"{API}/{urllib.parse.quote(doi, safe='/')}"
        try:
            msg = self.http.get(url, accept=CSL_JSON).json()
        except HttpError as e:
            if e.status == 404:
                return NotFound(self.name, "DOI not registered")
            # 406: registered, but the agency serves no CSL for it. That is a
            # gap in their deposit, not evidence about the reference.
            if e.status == 406:
                return Unavailable(self.name, "agency returned no CSL metadata")
            return Unavailable(self.name, str(e), e.retry_after)
        except (TransportError, ValueError) as e:
            return Unavailable(self.name, str(e))
        if not isinstance(msg, dict) or not _title(msg):
            return NotFound(self.name, "record has no title")
        return Found(_record(msg))
