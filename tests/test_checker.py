"""Verdict logic, exercised with fake resolvers so the suite never hits a network.

The property these tests exist to protect: an unreachable service must never
produce a statement about an entry. Everything else is secondary.
"""

import pytest

from refcheck.cache import Cache
from refcheck.checker import Checker
from refcheck.models import Entry, Found, NotFound, Record, Unavailable, Verdict


def entry(key="k", **fields):
    fields.setdefault("title", "A Study of Things")
    fields.setdefault("author", "Ferguson, Sharon")
    fields.setdefault("year", "2024")
    return Entry(key=key, entry_type=fields.pop("entry_type", "article"), fields=fields)


class Fake:
    """Resolver stub. ``outcome`` may be a value or a callable of the entry."""

    def __init__(self, name, outcome, handles=True):
        self.name = name
        self._outcome = outcome
        self._handles = handles
        self.calls = 0

    def can_handle(self, e):
        return self._handles

    def resolve(self, e):
        self.calls += 1
        return self._outcome(e) if callable(self._outcome) else self._outcome


def rec(title="A Study of Things", year=2024, surname="Ferguson", source="fake"):
    return Record(source=source, title=title, year=year, first_author_surname=surname)


# --- the central guarantee -------------------------------------------------

def test_unavailable_never_becomes_a_finding():
    c = Checker([Fake("crossref:doi", Unavailable("crossref", "HTTP 429"))])
    r = c.check(entry(doi="10.1145/1234567.1234568"))
    assert r.verdict is Verdict.UNVERIFIED
    assert not r.verdict.is_finding


def test_all_resolvers_unavailable_is_unverified():
    c = Checker([
        Fake("crossref:doi", Unavailable("crossref", "timeout")),
        Fake("arxiv:id", Unavailable("arxiv", "HTTP 429")),
    ])
    assert c.check(entry(doi="10.1145/1.2")).verdict is Verdict.UNVERIFIED


def test_unavailable_result_is_not_cached(tmp_path):
    cache = Cache(tmp_path / "c.json")
    flaky = Fake("crossref:doi", Unavailable("crossref", "429"))
    e = entry(doi="10.1145/1.2")
    assert Checker([flaky], cache=cache).check(e).verdict is Verdict.UNVERIFIED
    # a later run with a working resolver must not be shadowed by the failure
    good = Fake("crossref:doi", Found(rec()))
    assert Checker([good], cache=cache).check(e).verdict is Verdict.OK
    assert good.calls == 1


# --- real findings ---------------------------------------------------------

def test_doi_not_registered_is_dead_doi():
    c = Checker([Fake("crossref:doi", NotFound("crossref", "not registered"))])
    r = c.check(entry(doi="10.1145/0000000.0000000"))
    assert r.verdict is Verdict.DEAD_DOI and r.verdict.is_finding


def test_identifier_resolving_to_another_paper_is_title_mismatch():
    c = Checker([Fake("crossref:doi", Found(rec(title="An Entirely Different Paper")))])
    r = c.check(entry(doi="10.1145/1.2"))
    assert r.verdict is Verdict.TITLE_MISMATCH
    assert "different title" in r.note


def test_author_mismatch_when_titles_agree():
    c = Checker([Fake("crossref:doi", Found(rec(surname="Vasconcelos")))])
    assert c.check(entry(doi="10.1145/1.2")).verdict is Verdict.AUTHOR_MISMATCH


def test_year_off_by_more_than_one_is_flagged():
    c = Checker([Fake("crossref:doi", Found(rec(year=2015)))])
    assert c.check(entry(doi="10.1145/1.2")).verdict is Verdict.YEAR_MISMATCH


def test_preprint_year_off_by_one_is_tolerated():
    c = Checker([Fake("crossref:doi", Found(rec(year=2025)))])
    assert c.check(entry(doi="10.1145/1.2")).verdict is Verdict.OK


# --- weak evidence must stay weak -----------------------------------------

def test_weak_title_search_without_identifier_is_not_a_hard_finding():
    # Title search returned something loosely related; with no identifier to
    # confirm, this is not evidence the entry is wrong.
    c = Checker([Fake("crossref:title", Found(rec(title="A Study of Other Things Entirely")))])
    r = c.check(entry())
    assert r.verdict in {Verdict.UNVERIFIED, Verdict.NOT_FOUND}
    assert r.verdict is not Verdict.TITLE_MISMATCH


def test_wildly_different_title_search_is_not_found():
    c = Checker([Fake("crossref:title", Found(rec(title="Photosynthesis in Arctic Lichens")))])
    assert c.check(entry()).verdict is Verdict.NOT_FOUND


# --- housekeeping ----------------------------------------------------------

def test_non_archival_entry_without_identifier_is_skipped():
    c = Checker([Fake("crossref:doi", NotFound("crossref", "x"), handles=False)])
    e = Entry(key="d", entry_type="misc",
              fields={"title": "A Dataset", "howpublished": "\\url{https://example.org}"})
    assert c.check(e).verdict is Verdict.SKIPPED


def test_first_successful_resolver_wins_and_stops():
    first = Fake("crossref:doi", Found(rec()))
    second = Fake("openalex", Found(rec(title="Should Not Be Consulted")))
    c = Checker([first, second])
    assert c.check(entry(doi="10.1145/1.2")).verdict is Verdict.OK
    assert second.calls == 0


def test_falls_through_to_next_resolver_when_first_is_unavailable():
    first = Fake("arxiv:id", Unavailable("arxiv", "429"))
    second = Fake("openalex", Found(rec()))
    r = Checker([first, second]).check(entry(eprint="2404.12558"))
    assert r.verdict is Verdict.OK and r.source == "openalex"


def test_requires_at_least_one_resolver():
    with pytest.raises(ValueError):
        Checker([])


def test_cache_key_changes_when_entry_is_edited(tmp_path):
    cache = Cache(tmp_path / "c.json")
    resolver = Fake("crossref:doi", Found(rec()))
    c = Checker([resolver], cache=cache)
    c.check(entry(doi="10.1145/1.2"))
    c.check(entry(doi="10.1145/1.2"))
    assert resolver.calls == 1                      # second call served from cache
    c.check(entry(doi="10.1145/9.9"))               # edited entry must be re-checked
    assert resolver.calls == 2


def test_weak_title_hit_on_a_dataset_is_skipped_not_a_finding():
    # A @misc dataset with no DOI cannot be in a citation index. A stray title
    # match must not be reported as though the entry were wrong.
    c = Checker([Fake("crossref:title", Found(rec(title="Figure 1: A Reddit Post")))])
    e = Entry(key="ds", entry_type="misc",
              fields={"title": "Reddit Dataset for AmItheAsshole Subreddit",
                      "howpublished": "\\url{https://kaggle.com/x}", "year": "2022"})
    r = c.check(e)
    assert r.verdict is Verdict.SKIPPED
    assert not r.verdict.is_finding
