"""Parsing contracts for the resolvers added in 0.3.0.

These exercise the shape of each service's response, which is the part that
breaks silently: a resolver that mis-parses a record does not error, it reports
a mismatch against a reference that was fine.
"""

from __future__ import annotations

from refaudit.models import Entry
from refaudit.resolvers.dblp import _record as dblp_record
from refaudit.resolvers.doi_content import _record as csl_record
from refaudit.resolvers.openlibrary import OpenLibrary
from refaudit.resolvers.openlibrary import _record as ol_record


def test_csl_title_may_be_a_string_or_a_list():
    """CSL permits both and registration agencies disagree about which."""
    assert csl_record({"title": "A Paper"}).title == "A Paper"
    assert csl_record({"title": ["A Paper"]}).title == "A Paper"


def test_csl_reads_year_and_author():
    r = csl_record({"title": "T", "issued": {"date-parts": [[2021, 5]]},
                    "author": [{"family": "Buçinca", "given": "Zana"}],
                    "DOI": "10.1145/3449287"})
    assert (r.year, r.first_author_surname, r.doi) == (2021, "Buçinca", "10.1145/3449287")


def test_csl_falls_back_to_literal_author_names():
    """Organisational authors have no family name."""
    r = csl_record({"title": "T", "author": [{"literal": "World Health Organization"}]})
    assert r.first_author_surname == "World Health Organization"


def test_dblp_strips_its_disambiguation_number():
    """DBLP writes repeated names as "Yuan Chen 0011"; the digits are not a name."""
    r = dblp_record({"title": "Understanding Pointing.", "year": "2020",
                     "authors": {"author": [{"text": "Yuan Chen 0011"}]},
                     "doi": "10.1145/3313831.3376592"})
    assert r.first_author_surname == "Chen"
    assert r.year == 2020
    assert r.title == "Understanding Pointing", "DBLP appends a full stop"


def test_dblp_handles_a_single_author_object():
    """One author comes back as an object, several as a list."""
    r = dblp_record({"title": "T", "authors": {"author": {"text": "Ada Lovelace"}}})
    assert r.first_author_surname == "Lovelace"


def test_openlibrary_skips_entries_that_have_a_doi():
    """It runs last, so it sees only what every article index declined."""
    ol = OpenLibrary(contact_email="x@y.z")
    book = Entry(key="b", entry_type="book", fields={"title": "Thinking, Fast and Slow"})
    # Monographs are routinely filed as @article; refusing on type left real
    # books reported as missing.
    book_as_article = Entry(key="a", entry_type="article",
                            fields={"title": "Fairness and Machine Learning"})
    with_doi = Entry(key="c", entry_type="book",
                     fields={"title": "T", "doi": "10.1145/3449287"})
    assert ol.can_handle(book)
    assert ol.can_handle(book_as_article)
    assert not ol.can_handle(with_doi), "a DOI is stronger evidence"


def test_openlibrary_year_is_not_used_as_evidence():
    """It reports the earliest edition, not the date of the work cited."""
    assert OpenLibrary(contact_email="x@y.z").year_is_authoritative is False


def test_openlibrary_record_takes_the_surname():
    r = ol_record({"title": "Thinking, Fast and Slow",
                   "author_name": ["Daniel Kahneman"],
                   "first_publish_year": 2011, "key": "/works/OL1W"})
    assert (r.first_author_surname, r.year) == ("Kahneman", 2011)
    assert r.url.endswith("/works/OL1W")
