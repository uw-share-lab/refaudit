"""Core types.

The central design decision in this package is that *"we could not check"* and
*"the entry is wrong"* are different outcomes and must never collapse into each
other. A checker that reports a mismatch when a resolver was merely unreachable
trains its users to ignore it. Every resolver therefore returns one of three
things -- ``Found``, ``NotFound``, ``Unavailable`` -- and only ``Found`` can
produce a negative verdict about an entry.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Entry:
    """A single bibliography entry as it appears in the .bib file."""

    key: str
    entry_type: str
    fields: dict[str, str] = field(default_factory=dict)

    def get(self, name: str, default: str = "") -> str:
        return self.fields.get(name.lower(), default)

    @property
    def title(self) -> str:
        return self.get("title")

    @property
    def doi(self) -> str:
        return self.get("doi")

    @property
    def arxiv_id(self) -> str:
        return self.get("eprint") or self.get("archiveprefix_id")

    @property
    def year(self) -> Optional[int]:
        import re

        m = re.search(r"\d{4}", self.get("year"))
        return int(m.group()) if m else None


@dataclass(frozen=True)
class Record:
    """A bibliographic record retrieved from an external source."""

    source: str
    title: str
    year: Optional[int] = None
    first_author_surname: str = ""
    doi: str = ""
    url: str = ""


# --- Resolver outcomes -----------------------------------------------------
# Deliberately three separate types rather than one nullable Record, so that
# callers cannot accidentally treat "unavailable" as "not found".


@dataclass(frozen=True)
class Found:
    record: Record


@dataclass(frozen=True)
class NotFound:
    """The source was reached and authoritatively has no such record."""

    source: str
    detail: str = ""


@dataclass(frozen=True)
class Unavailable:
    """The source could not be reached, or refused us. Says nothing about the entry."""

    source: str
    reason: str = ""
    retry_after: Optional[float] = None


Outcome = Found | NotFound | Unavailable


class Verdict(enum.Enum):
    """Ordered worst-first: iteration order is the triage order."""

    TITLE_MISMATCH = "TITLE_MISMATCH"
    DEAD_DOI = "DEAD_DOI"
    AUTHOR_MISMATCH = "AUTHOR_MISMATCH"
    YEAR_MISMATCH = "YEAR_MISMATCH"
    NOT_FOUND = "NOT_FOUND"
    UNVERIFIED = "UNVERIFIED"
    SKIPPED = "SKIPPED"
    OK = "OK"

    @property
    def is_finding(self) -> bool:
        """True if this says something about the entry, rather than about us."""
        return self in {
            Verdict.TITLE_MISMATCH,
            Verdict.DEAD_DOI,
            Verdict.AUTHOR_MISMATCH,
            Verdict.YEAR_MISMATCH,
            Verdict.NOT_FOUND,
        }


@dataclass(frozen=True)
class CheckResult:
    key: str
    verdict: Verdict
    entry_title: str = ""
    found_title: str = ""
    source: str = ""
    similarity: Optional[float] = None
    note: str = ""
    cited: Optional[bool] = None

    def as_row(self) -> dict[str, object]:
        return {
            "key": self.key,
            "verdict": self.verdict.value,
            "is_finding": self.verdict.is_finding,
            "source": self.source,
            "similarity": "" if self.similarity is None else round(self.similarity, 3),
            "cited": "" if self.cited is None else self.cited,
            "entry_title": self.entry_title,
            "found_title": self.found_title,
            "note": self.note,
        }
