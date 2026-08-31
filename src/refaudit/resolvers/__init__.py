"""Resolver registry.

Order matters: identifier-based resolvers run before title search, so a strong
signal is used when one exists and a weak one only when nothing better is
available. DOI lookups are spread across registration agencies -- Crossref for
most published literature, DataCite for preprints and deposits -- because
neither one alone can speak for the whole DOI system.
"""

from __future__ import annotations

from .arxiv import ArxivId
from .base import HttpResolver, RateSpec, Resolver
from .crossref import CrossrefDoi, CrossrefTitle
from .datacite import DataCiteDoi
from .openalex import OpenAlex

__all__ = [
    "AVAILABLE",
    "ArxivId",
    "CrossrefDoi",
    "CrossrefTitle",
    "DataCiteDoi",
    "HttpResolver",
    "OpenAlex",
    "RateSpec",
    "Resolver",
    "default_resolvers",
]

AVAILABLE = {
    "crossref:doi": CrossrefDoi,
    "datacite:doi": DataCiteDoi,
    "arxiv:id": ArxivId,
    "openalex": OpenAlex,
    "crossref:title": CrossrefTitle,
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
