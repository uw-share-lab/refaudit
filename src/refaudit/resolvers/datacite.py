"""DataCite REST API.

Docs: https://support.datacite.org/docs/api

The DOI system has several registration agencies. Crossref registers most
journal and conference literature; DataCite registers preprints, datasets and
repository deposits -- notably every arXiv DOI (prefix ``10.48550``), Zenodo
(``10.5281``) and figshare. Querying only Crossref and reading its 404 as "this
DOI does not exist" reports live preprints as dead references, which is the
single most damaging thing this tool could get wrong.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..http import HttpError, TransportError
from ..models import Entry, Found, NotFound, Outcome, Record, Unavailable
from ..normalize import clean_doi
from .base import HttpResolver, RateSpec

API = "https://api.datacite.org"


def _record(attrs: dict[str, Any]) -> Record:
    titles = attrs.get("titles") or []
    title = ""
    for item in titles:
        if isinstance(item, dict) and (item.get("title") or "").strip():
            title = item["title"].strip()
            break
    creators = attrs.get("creators") or []
    surname = ""
    if creators:
        first = creators[0]
        # familyName is absent for organisational authors, where `name` is all
        # there is.
        surname = (first.get("familyName") or first.get("name") or "").strip()
        if not first.get("familyName") and "," in surname:
            surname = surname.split(",")[0].strip()
    year = attrs.get("publicationYear")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    return Record(
        source="datacite",
        title=title,
        year=year,
        first_author_surname=surname,
        doi=(attrs.get("doi") or "").lower(),
        url=attrs.get("url", "") or "",
    )


class DataCiteDoi(HttpResolver):
    """Authoritative lookup by DOI for DataCite-registered work."""

    name = "datacite:doi"
    rate = RateSpec(
        per_second=2.0,
        burst=2.0,
        rationale="DataCite asks for reasonable use and throttles heavy callers; "
                  "two per second matches the pace we use for Crossref.",
    )

    def can_handle(self, entry: Entry) -> bool:
        return bool(clean_doi(entry.doi))

    def resolve(self, entry: Entry) -> Outcome:
        doi = clean_doi(entry.doi)
        if not doi:
            return NotFound(self.name, "no usable DOI")
        url = f"{API}/dois/{urllib.parse.quote(doi, safe='')}"
        try:
            data = self.http.get(url).json().get("data") or {}
        except HttpError as e:
            if e.status == 404:
                return NotFound(self.name, "DOI not registered with DataCite")
            return Unavailable(self.name, str(e), e.retry_after)
        except (TransportError, ValueError) as e:
            return Unavailable(self.name, str(e))
        attrs = data.get("attributes") or {}
        if not attrs.get("titles"):
            return NotFound(self.name, "record has no title")
        return Found(_record(attrs))
