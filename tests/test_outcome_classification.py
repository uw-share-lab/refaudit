"""Every resolver, on every way a request can go wrong.

This is the one property the whole tool rests on, stated once for all eight
sources: an answer about the entry and a failure to reach the service must
never be confused.

``NotFound`` says the source looked and the work is not there. It can become a
finding -- a line in somebody's report saying their reference could not be
verified. ``Unavailable`` says we never got an answer, and produces no verdict
at all. Classify a 503 as absence and a busy server becomes an accusation;
classify a 404 as unavailability and a genuinely fabricated citation slips
through as UNVERIFIED.

The interesting cases are the ones where the obvious reading is wrong. A 404
from an *identifier* lookup is a real absence, but only of that agency's
records -- which is why one agency's 404 is not a dead DOI. A 406 from content
negotiation means the DOI is registered and the agency simply serves no CSL for
it: a gap in their deposit, not evidence about the reference. A feed that
declares XML entities is refused rather than parsed, and that refusal is our
decision, not the work being missing.
"""

from __future__ import annotations

import pytest

from refaudit.http import HttpError, Response, TooManyRedirects, TransportError
from refaudit.models import Entry, Found, NotFound, Unavailable
from refaudit.resolvers import (
    ArxivId,
    CrossrefDoi,
    CrossrefTitle,
    DataCiteDoi,
    Dblp,
    DoiContentNegotiation,
    OpenAlex,
    OpenLibrary,
)

EMAIL = "test@example.org"

#: Every resolver, with an entry rich enough that each will actually try.
ALL = [CrossrefDoi, CrossrefTitle, DataCiteDoi, DoiContentNegotiation,
       ArxivId, Dblp, OpenAlex, OpenLibrary]

#: Identifier lookups: for these a 404 is a real absence from that agency.
BY_IDENTIFIER = [CrossrefDoi, DataCiteDoi, DoiContentNegotiation, ArxivId]


def _entry(**over) -> Entry:
    fields = {"title": "A Study of Things", "author": "Ferguson, Sharon",
              "year": "2024", "doi": "10.1145/1111111.1111111",
              "eprint": "1706.03762"}
    fields.update(over)
    return Entry(key="k", entry_type=over.pop("entry_type", "article"), fields=fields)


def _raising(cls, exc: Exception):
    r = cls(contact_email=EMAIL)

    def boom(*a, **k):
        raise exc
    r.http.get = boom
    return r


def _answering(cls, body: bytes):
    r = cls(contact_email=EMAIL)
    r.http.get = lambda *a, **k: Response(200, body, {})
    return r


# --- nothing reachable is ever an answer -----------------------------------

@pytest.mark.parametrize("cls", ALL, ids=lambda c: c.__name__)
@pytest.mark.parametrize("exc", [
    TransportError("connection reset"),
    TransportError("timed out"),
    TooManyRedirects("more than 3 redirects"),
], ids=["reset", "timeout", "redirect-loop"])
def test_an_unreachable_service_produces_no_verdict(cls, exc):
    assert isinstance(_raising(cls, exc).resolve(_entry()), Unavailable)


@pytest.mark.parametrize("cls", ALL, ids=lambda c: c.__name__)
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_busy_or_broken_server_produces_no_verdict(cls, status):
    """The failure that matters most. A service having a bad day must never
    put a line in somebody's report about their bibliography."""
    out = _raising(cls, HttpError(status, "busy")).resolve(_entry())
    assert isinstance(out, Unavailable), f"{cls.__name__} on {status}: {out!r}"


@pytest.mark.parametrize("cls", ALL, ids=lambda c: c.__name__)
def test_a_truncated_body_produces_no_verdict(cls):
    out = _answering(cls, b'{"message": {"it').resolve(_entry())
    assert isinstance(out, Unavailable), f"{cls.__name__}: {out!r}"


@pytest.mark.parametrize("cls", [ArxivId], ids=["ArxivId"])
def test_malformed_xml_produces_no_verdict(cls):
    out = _answering(cls, b"<feed><entry>unclosed").resolve(_entry())
    assert isinstance(out, Unavailable), f"{out!r}"


def test_a_feed_declaring_entities_is_refused_not_treated_as_absent():
    """Refusing to parse is our decision about the document, not a statement
    that the preprint does not exist."""
    hostile = (b'<?xml version="1.0"?><!DOCTYPE d '
               b'[<!ENTITY x SYSTEM "file:///etc/passwd">]><d>&x;</d>')
    out = _answering(ArxivId, hostile).resolve(_entry())

    assert isinstance(out, Unavailable)
    assert "xml" in out.reason.lower() or "refus" in out.reason.lower()


# --- a 404 means different things in different places ----------------------

@pytest.mark.parametrize("cls", [CrossrefDoi, DataCiteDoi, DoiContentNegotiation],
                         ids=lambda c: c.__name__)
def test_a_404_on_a_doi_lookup_is_a_real_absence(cls):
    """This is what DEAD_DOI rests on. One agency saying "not mine" is an
    answer -- though only about that agency, which is why the checker asks
    doi.org before calling anything dead."""
    out = _raising(cls, HttpError(404, "not found")).resolve(_entry())
    assert isinstance(out, NotFound), f"{cls.__name__}: {out!r}"


def test_a_404_from_arxiv_is_an_anomaly_not_an_absence():
    """arXiv signals "no such id" with an empty feed, or a synthetic entry
    titled "Error" -- both handled as NotFound elsewhere. It does not use 404
    for that, so a 404 here means something is wrong with the service."""
    out = _raising(ArxivId, HttpError(404, "not found")).resolve(_entry())
    assert isinstance(out, Unavailable), f"{out!r}"


def test_a_406_from_content_negotiation_is_not_an_absence():
    """The DOI is registered; the agency simply serves no CSL for it. A gap in
    their deposit, not evidence about the reference."""
    out = _raising(DoiContentNegotiation,
                   HttpError(406, "not acceptable")).resolve(_entry())

    assert isinstance(out, Unavailable), f"{out!r}"
    assert "csl" in out.reason.lower() or "metadata" in out.reason.lower()


# --- an empty result set is an answer, not a failure -----------------------

@pytest.mark.parametrize("cls,body", [
    (CrossrefTitle, b'{"message": {"items": []}}'),
    (Dblp, b'{"result": {"hits": {"@total": "0"}}}'),
    (OpenLibrary, b'{"docs": []}'),
    (ArxivId, b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'),
], ids=lambda x: getattr(x, "__name__", "body"))
def test_no_results_is_not_found_rather_than_unavailable(cls, body):
    """A search that ran and found nothing is a fact about the work. Reporting
    it as unavailability would let a fabricated citation pass as UNVERIFIED."""
    out = _answering(cls, body).resolve(_entry(title="A Work Nobody Wrote"))
    assert isinstance(out, NotFound), f"{cls.__name__}: {out!r}"


def test_openalex_no_results_is_not_found():
    """Given no DOI, so the search route is the one exercised."""
    r = _answering(OpenAlex, b'{"meta": {"count": 0}, "results": []}')
    out = r.resolve(Entry(key="k", entry_type="article",
                          fields={"title": "A Work Nobody Wrote"}))
    assert isinstance(out, NotFound), f"{out!r}"


@pytest.mark.parametrize("body", [
    b'{"meta": {"count": 0}, "results": []}',      # a search shape, not a work
    b'{"error": "Rate limit exceeded", "message": "..."}',
    b'{}',
    b'[]',
    b'null',
], ids=["search-shape", "error-object", "empty-object", "list", "null"])
def test_a_doi_response_that_is_not_a_work_produces_no_verdict(body):
    """The DOI route returns a work object, and anything else must not be read
    as one. Building a Record from a body with no title yields an empty title,
    which scores zero against the entry and lands in somebody's report as a
    finding -- a service anomaly turned into an accusation, which is the one
    outcome this package exists to prevent.
    """
    r = _answering(OpenAlex, body)
    out = r.resolve(_entry(title="A Study of Things"))

    assert not isinstance(out, Found), f"read a non-work as a record: {out!r}"


# --- nothing to ask with -----------------------------------------------------

@pytest.mark.parametrize("cls", BY_IDENTIFIER, ids=lambda c: c.__name__)
def test_an_entry_with_no_identifier_is_declined_without_a_request(cls):
    """can_handle should have stopped this, but a resolver asked anyway must
    not invent a lookup -- and must not report an absence it never checked."""
    r = cls(contact_email=EMAIL)

    def must_not_be_called(*a, **k):
        raise AssertionError(f"{cls.__name__} made a request with nothing to query")
    r.http.get = must_not_be_called

    out = r.resolve(Entry(key="k", entry_type="article",
                          fields={"title": "No Identifiers Here"}))
    assert isinstance(out, NotFound)


# --- the retry_after a caller needs to pace itself -------------------------

@pytest.mark.parametrize("cls", ALL, ids=lambda c: c.__name__)
def test_a_throttled_lookup_passes_the_retry_after_along(cls):
    """The checker reports it, and a human reading UNVERIFIED wants to know
    whether to try again in a minute or tomorrow."""
    out = _raising(cls, HttpError(429, "slow", retry_after=90.0)).resolve(_entry())

    assert isinstance(out, Unavailable)
    assert out.retry_after == 90.0, f"{cls.__name__} dropped it"


# --- how far each source's year can be trusted ------------------------------

def test_openalexs_year_is_not_authoritative():
    """OpenAlex merges a preprint with the versions published later, so its
    `publication_year` can be the reissue rather than the work the entry cites.
    Observed live: it reports 2025 for a preprint posted in 2017.

    A year we cannot rely on must not produce YEAR_MISMATCH, which is a finding
    against a reference that is perfectly correct. Same reasoning as Open
    Library, which reports an edition rather than the work.
    """
    assert OpenAlex.year_is_authoritative is False


def test_the_curated_sources_keep_an_authoritative_year():
    """This is not a blanket retreat. A registration agency's year is the
    deposited date for the exact DOI in the entry, and a real disagreement
    there is worth reporting."""
    for cls in (CrossrefDoi, DataCiteDoi, ArxivId, Dblp):
        assert cls.year_is_authoritative is True, cls.__name__


def test_a_year_difference_from_openalex_is_not_a_finding():
    """Stated through the checker, because that is where it matters."""
    from refaudit.checker import Checker
    from refaudit.models import Found, Record, Verdict

    class FakeOpenAlex:
        name = "openalex"
        year_is_authoritative = False

        def can_handle(self, e):
            return True

        def resolve(self, e):
            return Found(Record(source="openalex", title=e.title, year=2025,
                                first_author_surname="Vaswani"))

    entry = Entry(key="k", entry_type="article",
                  fields={"title": "Attention Is All You Need",
                          "author": "Vaswani, Ashish", "year": "2017"})
    result = Checker([FakeOpenAlex()]).check(entry)

    assert result.verdict is Verdict.OK, f"{result.verdict}: {result.note}"
