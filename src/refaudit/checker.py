"""Orchestration: try resolvers in order, then judge the match.

Two rules govern the whole design.

**A resolver that could not be reached produces no verdict.** If every resolver
that applies to an entry came back ``Unavailable``, the result is ``UNVERIFIED``
and the report says so separately from real findings. This is what stops the
tool crying wolf when a service is rate-limiting the network.

**Identifier evidence outranks title evidence.** A DOI that Crossref says does
not exist is a finding. A title search that returns something different is only
a finding if we had no identifier to go on -- otherwise it is just a weak search
result, and treating it as a mismatch would flag every arXiv-only workshop paper
that Crossref happens not to index.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .bibtex import NON_ARCHIVAL_TYPES
from .cache import Cache
from .doi_registry import DoiExistence
from .models import (
    CheckResult,
    Entry,
    Found,
    NotFound,
    Record,
    Unavailable,
    Verdict,
)
from .normalize import clean_arxiv_id, clean_doi, first_surname, similarity, surnames_match
from .resolvers.base import Resolver


@dataclass(frozen=True)
class Thresholds:
    title_match: float = 0.75      # at or above this, the titles are the same work
    title_suspect: float = 0.45    # below this, a resolved record is clearly a different paper
    year_slack: int = 1            # preprint/publication years legitimately differ by one


DEFAULT_THRESHOLDS = Thresholds()


class Checker:
    def __init__(
        self,
        resolvers: Sequence[Resolver],
        *,
        cache: Cache | None = None,
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
        doi_existence: DoiExistence | None = None,
    ) -> None:
        if not resolvers:
            raise ValueError("at least one resolver is required")
        self.resolvers = list(resolvers)
        self.cache = cache
        self.thresholds = thresholds
        # Optional so a caller can run fully offline against a stub; when it is
        # absent no DEAD_DOI can be issued, which is the safe direction.
        self.doi_existence = doi_existence

    # -- public ------------------------------------------------------------

    def check(self, entry: Entry, *, cited: bool | None = None) -> CheckResult:
        cached = self.cache.get(self._cache_key(entry)) if self.cache else None
        if cached:
            return CheckResult(
                key=entry.key,
                verdict=Verdict(cached["verdict"]),
                entry_title=entry.title,
                found_title=cached.get("found_title", ""),
                source=cached.get("source", ""),
                similarity=cached.get("similarity"),
                note=cached.get("note", ""),
                cited=cited,
            )

        result = self._check_uncached(entry, cited)

        # Only cache outcomes that reflect the entry, not our connectivity.
        if self.cache and result.verdict is not Verdict.UNVERIFIED:
            self.cache.put(
                self._cache_key(entry),
                {
                    "verdict": result.verdict.value,
                    "found_title": result.found_title,
                    "source": result.source,
                    "similarity": result.similarity,
                    "note": result.note,
                },
            )
        return result

    def check_all(self, entries: Iterable[Entry], cited: set[str] | None = None):
        for entry in entries:
            yield self.check(entry, cited=(entry.key in cited) if cited is not None else None)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _cache_key(entry: Entry) -> str:
        # Include the fields we compare, so editing an entry re-checks it.
        return "|".join([
            entry.key,
            clean_doi(entry.doi),
            clean_arxiv_id(entry.arxiv_id),
            entry.title.strip().lower()[:200],
            str(entry.year or ""),
        ])

    def _check_uncached(self, entry: Entry, cited: bool | None) -> CheckResult:
        applicable = [r for r in self.resolvers if r.can_handle(entry)]

        has_identifier = bool(clean_doi(entry.doi) or clean_arxiv_id(entry.arxiv_id))
        if not applicable:
            if entry.entry_type in NON_ARCHIVAL_TYPES and not has_identifier:
                return CheckResult(entry.key, Verdict.SKIPPED, entry.title,
                                   note=f"@{entry.entry_type} with no identifier", cited=cited)
            return CheckResult(entry.key, Verdict.NOT_FOUND, entry.title,
                               note="no resolver could handle this entry", cited=cited)

        any_unavailable: list[str] = []
        authoritative_absence: list[str] = []
        doi_disowned_by: list[str] = []

        for resolver in applicable:
            outcome = resolver.resolve(entry)

            if isinstance(outcome, Unavailable):
                any_unavailable.append(f"{outcome.source}: {outcome.reason[:60]}")
                continue

            if isinstance(outcome, NotFound):
                # No single agency speaks for the whole DOI system, so one
                # agency's "not mine" is only a candidate for DEAD_DOI. It is
                # settled after every resolver has had its turn.
                if resolver.name.endswith(":doi") and clean_doi(entry.doi):
                    doi_disowned_by.append(resolver.name)
                authoritative_absence.append(f"{outcome.source}: {outcome.detail[:50]}")
                continue

            if isinstance(outcome, Found):
                return self._judge(entry, outcome.record, resolver, has_identifier, cited)

        # Nothing resolved the entry. If a DOI was disowned by every agency we
        # asked, ask the DOI proxy -- which answers for all of them -- before
        # calling the reference dead.
        if doi_disowned_by:
            doi = clean_doi(entry.doi)
            registered = self.doi_existence.exists(doi) if self.doi_existence else None
            if registered is False:
                return CheckResult(entry.key, Verdict.DEAD_DOI, entry.title,
                                   source="doi.org",
                                   note=f"DOI {doi} is not registered with any agency",
                                   cited=cited)
            if registered is True:
                # Real DOI, but no index we can read holds metadata for it, so
                # the reference itself is unchecked rather than wrong.
                return CheckResult(
                    entry.key, Verdict.UNVERIFIED, entry.title, source="doi.org",
                    note=f"DOI {doi} resolves but is indexed by none of: "
                         f"{', '.join(doi_disowned_by)}",
                    cited=cited)
            return CheckResult(
                entry.key, Verdict.UNVERIFIED, entry.title,
                note=f"DOI {doi} not found in {', '.join(doi_disowned_by)}; "
                     f"could not reach doi.org to confirm",
                cited=cited)

        if any_unavailable and not authoritative_absence:
            return CheckResult(entry.key, Verdict.UNVERIFIED, entry.title,
                               note="; ".join(any_unavailable)[:160], cited=cited)
        if entry.entry_type in NON_ARCHIVAL_TYPES and not has_identifier:
            return CheckResult(entry.key, Verdict.SKIPPED, entry.title,
                               note=f"@{entry.entry_type} not indexed", cited=cited)
        note = "; ".join(authoritative_absence + any_unavailable)[:160]
        return CheckResult(entry.key, Verdict.NOT_FOUND, entry.title, note=note, cited=cited)

    def _judge(
        self,
        entry: Entry,
        record: Record,
        resolver: Resolver,
        has_identifier: bool,
        cited: bool | None,
    ) -> CheckResult:
        score = similarity(entry.title, record.title)

        def result(verdict: Verdict, note: str = "") -> CheckResult:
            # Built explicitly rather than by unpacking a dict: the shared
            # fields are identical for every branch, but keyword unpacking
            # erases their types and hides genuine mistakes from the checker.
            return CheckResult(
                key=entry.key,
                verdict=verdict,
                entry_title=entry.title,
                found_title=record.title,
                source=resolver.name,
                similarity=score,
                note=note,
                cited=cited,
            )

        if score < self.thresholds.title_match:
            resolved_by_identifier = resolver.name.endswith(":doi") or "arxiv" in resolver.name
            if resolved_by_identifier:
                # The identifier points at a different paper. This is the
                # signature of a fabricated or mis-copied citation.
                return result(Verdict.TITLE_MISMATCH,
                              "identifier resolves to a different title")
            if score < self.thresholds.title_suspect:
                # A dataset or web resource with no identifier was never going
                # to be in a citation index; a stray title hit is not a finding.
                if entry.entry_type in NON_ARCHIVAL_TYPES and not has_identifier:
                    return result(Verdict.SKIPPED,
                                  f"@{entry.entry_type} with no identifier; not indexed")
                return result(Verdict.NOT_FOUND, "no close title match found")
            return result(Verdict.UNVERIFIED,
                          "only a weak title match; no identifier to confirm")

        want = first_surname(entry.get("author"))
        if (want and record.first_author_surname
                and not surnames_match(want, first_surname(record.first_author_surname))):
            return result(Verdict.AUTHOR_MISMATCH,
                          f"bib={want} vs {record.first_author_surname.lower()}")

        if entry.year and record.year and abs(entry.year - record.year) > self.thresholds.year_slack:
            return result(Verdict.YEAR_MISMATCH, f"bib={entry.year} vs {record.year}")

        return result(Verdict.OK)
