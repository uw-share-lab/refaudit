"""Each resolver, against bytes the real service actually sent.

These were the least-covered files in the package: the code that turns a
service's JSON or XML into a ``Record`` sat between 27% and 66%, which is the
wrong place to be thin. It is the part most exposed to somebody else changing
their response shape, and a misparse here does not crash -- it produces a
confident wrong answer about somebody's bibliography.

The fixtures in ``fixtures/responses/`` are captured verbatim from Crossref,
DataCite, arXiv, DBLP, OpenAlex, Open Library and the DOI proxy. Nothing is
hand-written, so a test passing means the parser handles what the service
sends rather than what we imagined it sends. The expected values were read out
of the raw fixtures independently of refaudit's own parsing code.

Two of the captures are failures, kept deliberately: DBLP answered 503 with an
HTML body, and OpenAlex answered with a rate-limit JSON object. Both are shapes
a parser can plausibly mistake for data, so both get a test.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest

from refaudit.http import HttpError, Response, TransportError
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

RESPONSES = Path(__file__).parent / "fixtures" / "responses"
EMAIL = "test@example.org"


def _serve(resolver, fixture: str | bytes, status: int = 200):
    """Answer this resolver's next request with a captured response."""
    body = (RESPONSES / fixture).read_bytes() if isinstance(fixture, str) else fixture
    resolver.http.get = lambda *a, **k: Response(status, body, {})
    return resolver


def _fail(resolver, exc: Exception):
    def boom(*a, **k):
        raise exc
    resolver.http.get = boom
    return resolver


def _entry(**fields) -> Entry:
    return Entry(key=fields.pop("key", "k"),
                 entry_type=fields.pop("entry_type", "article"),
                 fields=fields)


# --- identifier lookups -----------------------------------------------------

def test_crossref_doi_parses_a_real_record():
    r = _serve(CrossrefDoi(contact_email=EMAIL), "crossref-doi.json")
    out = r.resolve(_entry(doi="10.1145/3313831.3376727", title="What is AI Literacy?"))

    assert isinstance(out, Found)
    assert out.record.title == "What is AI Literacy? Competencies and Design Considerations"
    assert out.record.first_author_surname == "Long"
    assert out.record.year == 2020
    assert out.record.doi == "10.1145/3313831.3376727"


def test_datacite_doi_parses_a_real_record():
    r = _serve(DataCiteDoi(contact_email=EMAIL), "datacite-doi.json")
    out = r.resolve(_entry(doi="10.48550/arXiv.2005.14165",
                           title="Language Models are Few-Shot Learners"))

    assert isinstance(out, Found)
    assert out.record.title == "Language Models are Few-Shot Learners"
    assert out.record.first_author_surname == "Brown"
    assert out.record.year == 2020


def test_arxiv_parses_a_real_atom_entry():
    r = _serve(ArxivId(contact_email=EMAIL), "arxiv-entry.xml")
    out = r.resolve(_entry(eprint="1706.03762", title="Attention Is All You Need"))

    assert isinstance(out, Found)
    assert out.record.title == "Attention Is All You Need"
    assert "Vaswani" in out.record.first_author_surname


def test_doi_content_negotiation_parses_real_csl():
    r = _serve(DoiContentNegotiation(contact_email=EMAIL), "doi-content-csl.json")
    out = r.resolve(_entry(doi="10.1145/3313831.3376727", title="What is AI Literacy?"))

    assert isinstance(out, Found)
    assert out.record.title.startswith("What is AI Literacy?")
    assert out.record.first_author_surname == "Long"


# --- title searches ---------------------------------------------------------
#
# Every one of these fixtures has a *wrong* paper as its first hit, which is
# what these APIs really return. The resolver's job is to parse the candidate
# faithfully; deciding whether it is the same work belongs to the checker, and
# keeping that split is why a weak hit does not become a finding.

def test_crossref_title_returns_the_top_candidate_verbatim():
    r = _serve(CrossrefTitle(contact_email=EMAIL), "crossref-title.json")
    out = r.resolve(_entry(title="What is AI Literacy?"))

    assert isinstance(out, Found)
    assert out.record.title == "1. What is Generative AI?"
    assert out.record.first_author_surname == "Rao"


def test_dblp_returns_the_top_candidate_verbatim():
    r = _serve(Dblp(contact_email=EMAIL), "dblp-search.json")
    out = r.resolve(_entry(title="Attention Is All You Need"))

    assert isinstance(out, Found)
    assert out.record.title.startswith("Attentional Transfer is All You Need")
    assert out.record.year == 2021


def test_openlibrary_returns_the_top_candidate_verbatim():
    r = _serve(OpenLibrary(contact_email=EMAIL),
               "openlibrary-search.json")
    out = r.resolve(_entry(entry_type="book",
                           title="Sapiens: A Brief History of Humankind"))

    assert isinstance(out, Found)
    assert "Sapiens" in out.record.title


# --- shapes that are not data ----------------------------------------------

def test_an_html_error_page_is_not_mistaken_for_results():
    """DBLP answered 503 with HTML during a real run. Parsing it as JSON must
    report the service as unavailable, never as an absence of the work."""
    r = _serve(Dblp(contact_email=EMAIL), "dblp-503.html")
    out = r.resolve(_entry(title="Attention Is All You Need"))

    assert isinstance(out, Unavailable), f"got {out!r}"


def test_a_rate_limit_body_is_not_mistaken_for_a_record():
    """OpenAlex answers 429 with a JSON object. It parses as JSON perfectly
    well and contains no work, so the danger is reading it as an empty result
    and concluding the reference does not exist."""
    r = _serve(OpenAlex(contact_email=EMAIL), "openalex-rate-limited.json")
    out = r.resolve(_entry(title="What is AI Literacy?"))

    assert not isinstance(out, Found)


@pytest.mark.parametrize("resolver_cls,fixture", [
    (CrossrefTitle, "crossref-title.json"),
    (Dblp, "dblp-search.json"),
    (OpenAlex, "openalex-rate-limited.json"),
    (OpenLibrary, "openlibrary-search.json"),
])
def test_an_empty_result_set_is_not_found_rather_than_unavailable(resolver_cls, fixture):
    """No candidates is an answer about the search. It must not read as an
    unreachable service, or a genuine absence would be reported UNVERIFIED."""
    empty = json.dumps({"message": {"items": []}, "result": {"hits": {"hit": []}},
                        "results": [], "docs": []}).encode()
    r = _serve(resolver_cls(contact_email=EMAIL), empty)
    out = r.resolve(_entry(title="A Work That Does Not Exist", entry_type="book"))

    assert isinstance(out, NotFound), f"got {out!r}"


@pytest.mark.parametrize("resolver_cls", [
    CrossrefDoi, CrossrefTitle, DataCiteDoi, Dblp, OpenAlex, OpenLibrary,
    DoiContentNegotiation,
])
def test_malformed_json_is_reported_as_unavailable(resolver_cls):
    """Truncated bodies happen. None of them may crash a run, and none may
    look like evidence about the entry."""
    r = _serve(resolver_cls(contact_email=EMAIL), b'{"message": {"it')
    out = r.resolve(_entry(doi="10.1145/3313831.3376727",
                           title="What is AI Literacy?", entry_type="book"))

    assert isinstance(out, Unavailable), f"got {out!r}"


@pytest.mark.parametrize("resolver_cls", [
    CrossrefDoi, CrossrefTitle, DataCiteDoi, ArxivId, Dblp, OpenAlex,
    OpenLibrary, DoiContentNegotiation,
])
def test_an_unreachable_service_never_produces_a_verdict(resolver_cls):
    r = _fail(resolver_cls(contact_email=EMAIL), TransportError("connection reset"))
    out = r.resolve(_entry(doi="10.1145/3313831.3376727", eprint="1706.03762",
                           title="What is AI Literacy?", entry_type="book"))

    assert isinstance(out, Unavailable), f"got {out!r}"


@pytest.mark.parametrize("resolver_cls", [CrossrefDoi, DataCiteDoi])
def test_a_404_from_an_identifier_lookup_is_a_definitive_absence(resolver_cls):
    """This is the distinction the DEAD_DOI logic rests on: one agency saying
    'not mine' is an answer, not a failure to reach it."""
    r = _fail(resolver_cls(contact_email=EMAIL), HttpError(404, "not found"))
    out = r.resolve(_entry(doi="10.1145/9999999.9999998", title="Nothing"))

    assert isinstance(out, NotFound), f"got {out!r}"


@pytest.mark.parametrize("resolver_cls", [CrossrefDoi, DataCiteDoi])
def test_a_429_from_an_identifier_lookup_is_not_an_absence(resolver_cls):
    """Being throttled says nothing about whether the DOI exists."""
    r = _fail(resolver_cls(contact_email=EMAIL),
              HttpError(429, "slow down", retry_after=30.0))
    out = r.resolve(_entry(doi="10.1145/3313831.3376727", title="Whatever"))

    assert isinstance(out, Unavailable), f"got {out!r}"


# --- the arXiv route through OpenAlex --------------------------------------

def _capture_urls(resolver):
    """Record every URL a resolver asks for, answering nothing."""
    seen: list[str] = []

    def capture(url, **kw):
        seen.append(url)
        raise TransportError("stop here")

    resolver.http.get = capture
    return seen


def test_the_openalex_arxiv_filter_uses_the_scheme_openalex_stores():
    """OpenAlex records arXiv landing pages as `http://arxiv.org/abs/...`, and
    the filter is an exact string match, so a query built with `https://`
    matched nothing -- ever, for any preprint.

    It failed silently: an empty result set is indistinguishable from "not
    indexed", so the route looked like it worked and simply never contributed.
    That matters because the README tells people to lean on OpenAlex when arXiv
    itself is rate-limiting them, which is exactly when this route is needed.
    """
    r = OpenAlex(contact_email=EMAIL)
    seen = _capture_urls(r)
    r.resolve(_entry(eprint="1706.03762", title=""))

    assert seen, "no request was made at all"
    query = urllib.parse.unquote("".join(seen))
    assert "http://arxiv.org/abs/1706.03762" in query, (
        f"queried only https, which OpenAlex never matches: {query}"
    )


def test_the_openalex_arxiv_filter_accepts_either_scheme():
    """Written as an OR so it keeps working if OpenAlex normalises to https
    later, rather than trading one silent mismatch for the opposite one."""
    r = OpenAlex(contact_email=EMAIL)
    seen = _capture_urls(r)
    r.resolve(_entry(eprint="1706.03762", title=""))

    query = urllib.parse.unquote("".join(seen))
    assert "https://arxiv.org/abs/1706.03762" in query
    assert "|" in query, "the two forms must be an OR, not two round trips"


def test_openalex_parses_a_real_doi_response():
    r = _serve(OpenAlex(contact_email=EMAIL), "openalex-doi.json")
    out = r.resolve(_entry(title="What is AI Literacy?"))

    assert isinstance(out, Found)
    assert out.record.title.startswith("What is AI Literacy?")
    assert out.record.first_author_surname == "Long"
    assert out.record.year == 2020


def test_openalex_parses_a_real_arxiv_response():
    """Captured with the corrected filter; before the fix this response could
    not be obtained at all."""
    r = _serve(OpenAlex(contact_email=EMAIL), "openalex-arxiv.json")
    out = r.resolve(_entry(eprint="1706.03762", title="Attention Is All You Need"))

    assert isinstance(out, Found)
    assert out.record.title == "Attention Is All You Need"
    assert out.record.first_author_surname == "Vaswani"
