"""Find the same work cited under more than one key.

Duplicates are worth their own pass because they are invisible to the
per-entry check: both copies resolve, both are correct, and both are reported
``OK``. Only by comparing entries with each other does the problem appear. In
the bibliography that prompted this, one paper was cited under two keys -- one
carrying the DOI, one the bare arXiv ID -- and it surfaced only by accident.

This runs entirely offline. It costs nothing, cannot be rate-limited, and works
when every network source is refusing us, so there is no reason to make it
optional.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Entry
from .normalize import clean_arxiv_id, clean_doi, fold, similarity

#: Titles at or above this similarity are treated as the same work. Set high:
#: a false duplicate tells an author to delete a reference they need, so the
#: cost of a wrong answer is asymmetric and this errs towards silence.
TITLE_DUPLICATE = 0.93


@dataclass(frozen=True)
class Duplicate:
    """A set of keys that appear to cite the same work."""

    keys: tuple[str, ...]
    reason: str
    detail: str = ""

    @property
    def primary(self) -> str:
        """The key to keep, by convention the first in file order."""
        return self.keys[0]


def _arxiv_of(entry: Entry) -> str:
    """arXiv ID from either the eprint field or a DataCite arXiv DOI."""
    direct = clean_arxiv_id(entry.arxiv_id)
    if direct:
        return direct.lower()
    doi = clean_doi(entry.doi).lower()
    prefix = "10.48550/arxiv."
    return doi[len(prefix):] if doi.startswith(prefix) else ""


def find_duplicates(entries: list[Entry]) -> list[Duplicate]:
    """Group entries that cite the same work, strongest evidence first.

    An entry is reported at most once, under the strongest reason that applies,
    so a pair sharing both a DOI and a title is one finding rather than two.
    """
    claimed: set[str] = set()
    found: list[Duplicate] = []

    def collect(buckets: dict[str, list[str]], reason: str) -> None:
        for value, keys in buckets.items():
            fresh = [k for k in keys if k not in claimed]
            if len(fresh) > 1:
                claimed.update(fresh)
                found.append(Duplicate(tuple(fresh), reason, value))

    by_doi: dict[str, list[str]] = {}
    by_arxiv: dict[str, list[str]] = {}
    for entry in entries:
        doi = clean_doi(entry.doi).lower()
        if doi:
            by_doi.setdefault(doi, []).append(entry.key)
        arxiv = _arxiv_of(entry)
        if arxiv:
            by_arxiv.setdefault(arxiv, []).append(entry.key)

    collect(by_doi, "same DOI")
    collect(by_arxiv, "same arXiv ID")

    # Title comparison is quadratic, so only entries not already matched by an
    # identifier reach it -- and it is skipped entirely on large bibliographies
    # where the cost would be noticeable and identifiers are the better signal.
    remaining = [e for e in entries if e.key not in claimed and fold(e.title)]
    if len(remaining) > 400:
        return found
    for i, a in enumerate(remaining):
        if a.key in claimed:
            continue
        group = [a.key]
        for b in remaining[i + 1:]:
            if b.key in claimed:
                continue
            if similarity(a.title, b.title) >= TITLE_DUPLICATE:
                group.append(b.key)
                claimed.add(b.key)
        if len(group) > 1:
            claimed.add(a.key)
            found.append(Duplicate(tuple(group), "near-identical title",
                                   a.title.strip()[:80]))
    return found
