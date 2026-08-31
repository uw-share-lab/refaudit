"""A DOI absent from one agency is not a dead DOI.

Every arXiv DOI (prefix 10.48550) is registered with DataCite, so Crossref
returns 404 for all of them. Reading that as DEAD_DOI reported 22 live
references in a real bibliography as broken; these tests pin that behaviour
shut.
"""

from __future__ import annotations

from refaudit.checker import Checker
from refaudit.models import Entry, Found, NotFound, Record, Unavailable, Verdict

ARXIV_DOI = "10.48550/arXiv.2502.14052"
TITLE = "A Matter of Perspectives: Contrasting Human and LLM Argumentation"


def entry(**overrides):
    fields = {"title": TITLE, "year": "2025", "author": "Aoyagui, Paula Akemi",
              "doi": ARXIV_DOI}
    fields.update(overrides)
    return Entry(key="k", entry_type="misc", fields=fields)


class StubResolver:
    def __init__(self, name, outcome):
        self.name = name
        self._outcome = outcome
        self.calls = 0

    def can_handle(self, entry):
        return True

    def resolve(self, entry):
        self.calls += 1
        return self._outcome


class StubExistence:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def exists(self, doi):
        self.calls += 1
        return self.answer


def record(title=TITLE, year=2025, surname="Aoyagui"):
    return Record(source="datacite", title=title, year=year,
                  first_author_surname=surname, doi=ARXIV_DOI, url="")


def test_crossref_404_does_not_end_the_check():
    """The bug: crossref:doi returned first and short-circuited to DEAD_DOI."""
    crossref = StubResolver("crossref:doi", NotFound("crossref:doi", "not registered"))
    datacite = StubResolver("datacite:doi", Found(record()))
    result = Checker([crossref, datacite],
                     doi_existence=StubExistence(True)).check(entry())
    assert datacite.calls == 1, "later agencies must still be consulted"
    assert result.verdict is Verdict.OK


def test_dead_doi_requires_confirmation_from_the_proxy():
    crossref = StubResolver("crossref:doi", NotFound("crossref:doi", "not registered"))
    datacite = StubResolver("datacite:doi", NotFound("datacite:doi", "not registered"))
    proxy = StubExistence(False)
    result = Checker([crossref, datacite], doi_existence=proxy).check(entry())
    assert proxy.calls == 1
    assert result.verdict is Verdict.DEAD_DOI


def test_live_doi_no_agency_indexes_is_unverified_not_dead():
    resolvers = [StubResolver("crossref:doi", NotFound("crossref:doi", "no")),
                 StubResolver("datacite:doi", NotFound("datacite:doi", "no"))]
    result = Checker(resolvers, doi_existence=StubExistence(True)).check(entry())
    assert result.verdict is Verdict.UNVERIFIED
    assert not result.verdict.is_finding


def test_unreachable_proxy_never_yields_dead_doi():
    """An unreachable confirmation service must not condemn a reference."""
    resolvers = [StubResolver("crossref:doi", NotFound("crossref:doi", "no"))]
    result = Checker(resolvers, doi_existence=StubExistence(None)).check(entry())
    assert result.verdict is Verdict.UNVERIFIED


def test_no_existence_checker_configured_cannot_report_dead():
    resolvers = [StubResolver("crossref:doi", NotFound("crossref:doi", "no"))]
    result = Checker(resolvers, doi_existence=None).check(entry())
    assert result.verdict is not Verdict.DEAD_DOI


def test_genuinely_wrong_title_still_reported():
    """The fix must not blunt real findings."""
    datacite = StubResolver("datacite:doi", Found(record(title="Something Else Entirely")))
    result = Checker([datacite], doi_existence=StubExistence(True)).check(entry())
    assert result.verdict is Verdict.TITLE_MISMATCH


def test_unavailable_agency_does_not_count_as_disowning_the_doi():
    resolvers = [StubResolver("crossref:doi", Unavailable("crossref:doi", "429")),
                 StubResolver("datacite:doi", Unavailable("datacite:doi", "timeout"))]
    proxy = StubExistence(False)
    result = Checker(resolvers, doi_existence=proxy).check(entry())
    assert proxy.calls == 0, "a 429 says nothing about whether the DOI exists"
    assert result.verdict is Verdict.UNVERIFIED


def test_a_weak_title_hit_does_not_block_a_better_source():
    """A real book stayed NOT_FOUND behind an empty Crossref candidate.

    Title searches are guesses, so an unconvincing one must not end the search
    before a better index has been asked.
    """
    junk = StubResolver("crossref:title", Found(record(title="Utterly Unrelated Work")))
    good = StubResolver("openlibrary", Found(record(title=TITLE)))
    result = Checker([junk, good], doi_existence=StubExistence(True)).check(
        entry(doi="", title=TITLE))
    assert good.calls == 1, "the later index must still be consulted"
    assert result.verdict is Verdict.OK
    assert result.source == "openlibrary"


def test_a_convincing_title_hit_stops_the_search():
    """The cascade must not cost extra requests once the answer is in hand."""
    good = StubResolver("crossref:title", Found(record(title=TITLE)))
    later = StubResolver("openlibrary", Found(record(title=TITLE)))
    result = Checker([good, later], doi_existence=StubExistence(True)).check(
        entry(doi="", title=TITLE))
    assert later.calls == 0
    assert result.verdict is Verdict.OK


def test_an_identifier_mismatch_is_never_overturned_by_a_later_source():
    """A DOI pointing at another paper is the finding, whatever else agrees."""
    wrong = StubResolver("crossref:doi", Found(record(title="A Different Paper")))
    flattering = StubResolver("crossref:title", Found(record(title=TITLE)))
    result = Checker([wrong, flattering], doi_existence=StubExistence(True)).check(entry())
    assert flattering.calls == 0
    assert result.verdict is Verdict.TITLE_MISMATCH


def test_the_closest_guess_is_reported_when_all_are_weak():
    near = StubResolver("crossref:title", Found(record(title=TITLE[:20])))
    far = StubResolver("openlibrary", Found(record(title="Nothing Like It")))
    result = Checker([near, far], doi_existence=StubExistence(True)).check(
        entry(doi="", title=TITLE))
    assert result.found_title == TITLE[:20], "the closest candidate, not the last"
