"""OpenAlex API.

Docs: https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication

OpenAlex allows 100,000 calls per day and up to 10 per second, and asks callers
to join the "polite pool" by supplying a ``mailto``. It indexes both DOIs and
arXiv identifiers, which makes it the useful third option when arXiv itself is
refusing a network -- the situation this package was written in response to.

We stay well under the published ceiling: this is a background job run before a
submission deadline, not something that needs to finish in seconds.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from ..http import HttpError, TransportError
from ..models import Entry, Found, NotFound, Outcome, Record, Unavailable
from ..normalize import clean_arxiv_id, clean_doi
from .base import HttpResolver, RateSpec

API = "https://api.openalex.org/works"


def _is_work(data: Any) -> bool:
    """Does this payload look like an OpenAlex work rather than something else?

    Every work carries an ``id``. Checking for it distinguishes a real record
    from an error object, a search-shaped body, or anything else served with a
    200 that we would otherwise read as a work with no title.
    """
    return isinstance(data, dict) and bool(data.get("id"))


def _record(work: dict[str, Any]) -> Record:
    authorships = work.get("authorships") or []
    surname = ""
    if authorships:
        name = ((authorships[0].get("author") or {}).get("display_name") or "").strip()
        if name:
            surname = name.split()[-1]
    doi = (work.get("doi") or "").replace("https://doi.org/", "").lower()
    year: int | None = work.get("publication_year")
    return Record(
        source="openalex",
        title=(work.get("display_name") or work.get("title") or "").strip(),
        year=int(year) if isinstance(year, int) else None,
        first_author_surname=surname,
        doi=doi,
        url=work.get("id", ""),
    )


class OpenAlex(HttpResolver):
    """Resolve by DOI, then arXiv id, then title."""

    name = "openalex"
    #: OpenAlex merges a preprint with the versions published later, so this
    #: year can be a reissue rather than the work the entry cites -- observed
    #: live reporting 2025 for a preprint posted in 2017. A year we cannot rely
    #: on must not produce YEAR_MISMATCH against a correct reference.
    #:
    #: Its arXiv landing-page index has the same character: of four preprints
    #: sampled, one resolved to an entirely unrelated paper that claims the same
    #: abs/ URL. That one the checker already handles, since a weak title match
    #: from a search is not treated as identifier evidence. This is why OpenAlex
    #: is registered as a fallback and never as the backbone.
    year_is_authoritative = False
    api_base = API
    rate = RateSpec(
        per_second=3.0,
        burst=3.0,
        rationale="OpenAlex meters against a daily budget rather than a request "
                  "ceiling -- a free caller is refused with a Retry-After of "
                  "hours once it is spent, which is why this is a fallback and "
                  "never the backbone.",
    )

    _SELECT = "id,doi,display_name,title,publication_year,authorships"

    def can_handle(self, entry: Entry) -> bool:
        return bool(clean_doi(entry.doi) or clean_arxiv_id(entry.arxiv_id) or entry.title.strip())

    def _get_json(self, url: str) -> tuple[dict[str, Any] | None, Outcome | None]:
        try:
            return self.http.get(url).json(), None
        except HttpError as e:
            if e.status == 404:
                return None, NotFound(self.name, "not in OpenAlex")
            return None, Unavailable(self.name, str(e), e.retry_after)
        except (TransportError, ValueError) as e:
            return None, Unavailable(self.name, str(e))

    def resolve(self, entry: Entry) -> Outcome:
        mailto = urllib.parse.urlencode({"mailto": self.contact_email})

        doi = clean_doi(entry.doi)
        if doi:
            url = f"{API}/https://doi.org/{urllib.parse.quote(doi, safe='/')}?{mailto}"
            data, problem = self._get_json(url)
            if data is not None and not _is_work(data):
                # A 200 carrying something that is not a work: an error object,
                # a search-shaped body, a changed response. Reading it as a
                # record yields an empty title, which scores zero against the
                # entry and lands in the report as a finding -- a service
                # anomaly turned into an accusation about somebody's
                # bibliography, which is the one thing this must never do.
                return Unavailable(self.name, "response was not a work record")
            if data:
                return Found(_record(data))
            if isinstance(problem, Unavailable):
                return problem
            # a 404 on the DOI is meaningful; fall through to other routes

        arx = clean_arxiv_id(entry.arxiv_id)
        if arx:
            # Both schemes, as an OR. OpenAlex stores the arXiv landing page as
            # `http://arxiv.org/abs/...` and this filter is an exact string
            # match, so asking only for `https://` matched nothing -- ever, for
            # any preprint. It failed silently, because an empty result set
            # looks exactly like "not indexed", and the route appeared to work
            # while never once contributing. Matching either form also means
            # this keeps working if OpenAlex normalises to https later, rather
            # than trading one silent mismatch for the opposite one.
            landing = (f"http://arxiv.org/abs/{arx}"
                       f"|https://arxiv.org/abs/{arx}")
            q = urllib.parse.urlencode(
                {"filter": f"locations.landing_page_url:{landing}",
                 "select": self._SELECT, "per-page": 1, "mailto": self.contact_email}
            )
            data, problem = self._get_json(f"{API}?{q}")
            if data and (data.get("results") or []):
                return Found(_record(data["results"][0]))
            if isinstance(problem, Unavailable):
                return problem

        title = entry.title.strip()
        if title:
            q = urllib.parse.urlencode(
                {"filter": f"title.search:{title[:200]}",
                 "select": self._SELECT, "per-page": 1, "mailto": self.contact_email}
            )
            data, problem = self._get_json(f"{API}?{q}")
            if data:
                results = data.get("results") or []
                if results:
                    return Found(_record(results[0]))
                return NotFound(self.name, "no title match")
            if isinstance(problem, Unavailable):
                return problem

        return NotFound(self.name, "nothing to query with")
