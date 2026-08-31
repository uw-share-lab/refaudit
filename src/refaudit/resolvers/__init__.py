"""Resolver registry.

Order matters, and encodes how much each source's answer is worth.

1. **Identifier lookups first.** A DOI or arXiv ID is a claim the author made
   that we can check exactly; a title search is a guess we then have to score.
2. **Across registration agencies, not just one.** No agency speaks for the
   whole DOI system -- Crossref registers most published literature, DataCite
   registers preprints and deposits (every ``10.48550/*`` arXiv DOI, Zenodo,
   figshare) -- and ``doi:content`` negotiates with whichever agency owns the
   DOI, covering the ones we do not query directly. Reading one agency's 404 as
   "this DOI does not exist" once reported 22 live preprints as dead.
3. **Curated title indexes before harvested ones.** DBLP is hand-curated for
   computer science and unmetered; OpenAlex is broader but noisier, and now
   meters usage, so it is a fallback rather than the backbone.
4. **Books last and only for books**, because no article index will ever hold a
   monograph, and Open Library would be noise for anything else.

No source is load-bearing on its own: every one of them can be unreachable
without the run producing a false finding.
"""

from __future__ import annotations

from .arxiv import ArxivId
from .base import HttpResolver, RateSpec, Resolver
from .crossref import CrossrefDoi, CrossrefTitle
from .datacite import DataCiteDoi
from .dblp import Dblp
from .doi_content import DoiContentNegotiation
from .openalex import OpenAlex
from .openlibrary import OpenLibrary

__all__ = [
    "AVAILABLE",
    "ArxivId",
    "CrossrefDoi",
    "CrossrefTitle",
    "DataCiteDoi",
    "Dblp",
    "DoiContentNegotiation",
    "HttpResolver",
    "OpenAlex",
    "OpenLibrary",
    "RateSpec",
    "Resolver",
    "default_resolvers",
]

AVAILABLE = {
    # identifier lookups, strongest evidence first
    "crossref:doi": CrossrefDoi,
    "datacite:doi": DataCiteDoi,
    "doi:content": DoiContentNegotiation,
    "arxiv:id": ArxivId,
    # title searches, curated before harvested
    "dblp": Dblp,
    "openalex": OpenAlex,
    "crossref:title": CrossrefTitle,
    # only ever sees monographs
    "openlibrary": OpenLibrary,
}


def default_resolvers(contact_email: str, *, only: list[str] | None = None,
                      timeout: float = 20.0) -> list[Resolver]:
    names = only or list(AVAILABLE)
    unknown = [n for n in names if n not in AVAILABLE]
    if unknown:
        raise ValueError(f"unknown resolver(s): {', '.join(unknown)}; "
                         f"available: {', '.join(AVAILABLE)}")
    # Preserve registry order regardless of the order given on the command line.
    return [AVAILABLE[n](contact_email=contact_email, timeout=timeout)
            for n in AVAILABLE if n in set(names)]
