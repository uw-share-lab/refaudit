"""The awkward shapes a real .bib and a real API response take.

The BibTeX parser reads the file before any resolver sees it, so a mistake here
drops or mangles an entry silently: nothing downstream can tell the difference
between a field that was absent and one that was misread. The uncovered paths
were all the ones a hand-written bibliography reaches and a tidy one does not --
escaped braces, quoted values, `@comment`, an unreadable file in a `--tex` tree.

The resolver paths are the guards and fallbacks: a lookup declining an entry it
cannot help with, and a record whose date or author sits in a field other than
the usual one.
"""

from __future__ import annotations

from refaudit.bibtex import cited_keys, find_tex, parse_file, parse_string
from refaudit.models import Entry, Found, NotFound
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


def _entry(**fields) -> Entry:
    return Entry(key=fields.pop("key", "k"),
                 entry_type=fields.pop("entry_type", "article"), fields=fields)


# --- BibTeX the way people actually write it -------------------------------

def test_an_escaped_brace_does_not_end_the_value():
    """`\\{` inside a title is a literal brace, not the close of the field."""
    entries = parse_string(r"@article{k, title = {A \{Curly\} Title}, year = {2024}}")
    assert len(entries) == 1
    assert "Curly" in entries[0].title


def test_a_quoted_value_is_read():
    """Both delimiters are legal BibTeX and both appear in exported files."""
    entries = parse_string('@article{k, title = "A Quoted Title", year = "2024"}')
    assert entries[0].title == "A Quoted Title"
    assert entries[0].year == 2024


def test_an_escaped_quote_does_not_end_a_quoted_value():
    entries = parse_string(r'@article{k, title = "A \"Quoted\" Title"}')
    assert "Quoted" in entries[0].title


def test_nested_braces_are_kept_together():
    entries = parse_string("@article{k, title = {A {Nested} Title}}")
    assert "Nested" in entries[0].title


def test_comment_preamble_and_string_entries_are_skipped():
    """They are not references, and treating them as such would put junk keys
    in the report."""
    entries = parse_string("""
        @comment{ this is ignored }
        @preamble{ "\\newcommand{\\x}{y}" }
        @string{ acm = "ACM" }
        @article{real, title = {A Real One}, year = {2024}}
    """)
    assert [e.key for e in entries] == ["real"]


def test_an_entry_with_a_key_and_nothing_else():
    """`@misc{bare}` with no comma yields nothing, while `@misc{bare,}` parses.

    Documented rather than fixed: such an entry has no title and no identifier,
    so it could only ever be SKIPPED, and loosening the scanner to catch it
    risks swallowing the malformed text around a genuinely broken entry.
    """
    assert parse_string("@misc{bare}") == []
    assert [e.key for e in parse_string("@misc{bare,}")] == ["bare"]


def test_a_trailing_comma_is_tolerated():
    entries = parse_string("@article{k, title = {T}, year = {2024},}")
    assert entries[0].year == 2024


def test_a_truncated_entry_does_not_lose_the_whole_file():
    """A file being edited when it is read must not silently yield nothing."""
    entries = parse_string("@article{good, title = {Fine}}\n@article{trunc, title = {Uncl")
    assert "good" in [e.key for e in entries]


def test_field_names_are_case_insensitive():
    entries = parse_string("@article{k, TITLE = {T}, DOI = {10.1/x}, Year = {2024}}")
    assert entries[0].title == "T"
    assert entries[0].doi == "10.1/x"
    assert entries[0].year == 2024


# --- --tex handling ---------------------------------------------------------

def test_find_tex_accepts_a_single_file(tmp_path):
    """`--tex paper.tex` is as reasonable an invocation as a directory."""
    f = tmp_path / "paper.tex"
    f.write_text(r"\cite{a}", encoding="utf-8")
    assert find_tex(f) == [f]


def test_find_tex_walks_a_directory_in_a_stable_order(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "b.tex").write_text("", encoding="utf-8")
    (tmp_path / "a.tex").write_text("", encoding="utf-8")
    (tmp_path / "sub" / "c.tex").write_text("", encoding="utf-8")

    found = find_tex(tmp_path)
    assert [p.name for p in found] == sorted(p.name for p in found)
    assert len(found) == 3


def test_an_unreadable_tex_file_is_skipped_rather_than_fatal(tmp_path, monkeypatch):
    """One unreadable file in a big tree must not abort the run."""
    good = tmp_path / "good.tex"
    good.write_text(r"\cite{keeper}", encoding="utf-8")
    bad = tmp_path / "bad.tex"
    bad.write_text("", encoding="utf-8")

    real = type(good).read_text

    def selective(self, *a, **k):
        if self.name == "bad.tex":
            raise OSError("permission denied")
        return real(self, *a, **k)

    monkeypatch.setattr(type(good), "read_text", selective)
    assert "keeper" in cited_keys([good, bad])


def test_commented_out_citations_do_not_count(tmp_path):
    """A `%`-commented \\cite never reaches the PDF, so --only-cited must not
    treat it as cited."""
    f = tmp_path / "p.tex"
    f.write_text("% \\cite{ghost}\n\\cite{real}\n", encoding="utf-8")
    keys = cited_keys([f])

    assert "real" in keys
    assert "ghost" not in keys


def test_parse_file_reads_from_disk(tmp_path):
    p = tmp_path / "r.bib"
    p.write_text("@article{k, title = {T}}", encoding="utf-8")
    assert [e.key for e in parse_file(p)] == ["k"]


# --- resolvers declining what they cannot help with ------------------------

def test_doi_resolvers_decline_an_entry_with_no_doi():
    """can_handle is what stops a DOI lookup being asked about a preprint that
    has none, and a NotFound there would read as a real absence."""
    e = _entry(title="Something", author="A")
    for cls in (CrossrefDoi, DataCiteDoi, DoiContentNegotiation):
        assert not cls(contact_email=EMAIL).can_handle(e), cls.__name__


def test_the_arxiv_resolver_declines_an_entry_with_no_identifier():
    assert not ArxivId(contact_email=EMAIL).can_handle(_entry(title="Something"))


def test_title_only_resolvers_decline_an_entry_with_no_title():
    e = _entry(doi="10.1145/1111111.1111111", title="")
    for cls in (CrossrefTitle, Dblp):
        assert not cls(contact_email=EMAIL).can_handle(e), cls.__name__


def test_openalex_takes_an_identifier_or_a_title():
    """Not a title-only resolver: it can look an entry up three ways, and only
    declines when it has none of them."""
    r = OpenAlex(contact_email=EMAIL)
    assert r.can_handle(_entry(doi="10.1145/1111111.1111111", title=""))
    assert r.can_handle(_entry(eprint="1706.03762", title=""))
    assert r.can_handle(_entry(title="Just A Title"))
    assert not r.can_handle(_entry(title="", author="Nobody"))


def test_open_library_is_not_restricted_by_entry_type():
    """Deliberately not `@book` only. It is registered last, so every article
    index has already declined the entry by the time it runs, and monographs
    are routinely filed as @article -- refusing on type left real books
    reported as missing. A DOI-bearing entry is left to the DOI resolvers,
    which are stronger evidence.
    """
    r = OpenLibrary(contact_email=EMAIL)
    assert r.can_handle(_entry(entry_type="book", title="A Monograph"))
    assert r.can_handle(_entry(entry_type="article", title="A Monograph Filed Wrong"))
    assert not r.can_handle(_entry(entry_type="book", title="X",
                                   doi="10.1145/1111111.1111111"))
    assert not r.can_handle(_entry(entry_type="book", title=""))


def test_a_doi_resolver_asked_anyway_reports_no_usable_doi():
    """can_handle is advice, not enforcement; the resolver still has to cope."""
    out = CrossrefDoi(contact_email=EMAIL).resolve(_entry(title="No DOI Here"))
    assert isinstance(out, NotFound)


# --- record fields that sit somewhere other than the usual place -----------

def test_crossref_falls_back_through_its_date_fields():
    """`issued` is missing often enough on newer deposits that reading only it
    would report a year mismatch against a perfectly good record."""
    from refaudit.http import Response

    r = CrossrefDoi(contact_email=EMAIL)
    r.http.get = lambda *a, **k: Response(200, b'''{"message": {
        "title": ["A Work"], "DOI": "10.1145/1111111.1111111",
        "author": [{"family": "Ferguson"}],
        "published-online": {"date-parts": [[2023]]}}}''', {})

    out = r.resolve(_entry(doi="10.1145/1111111.1111111", title="A Work"))
    assert isinstance(out, Found)
    assert out.record.year == 2023


def test_an_unparseable_date_does_not_sink_the_record():
    from refaudit.http import Response

    r = CrossrefDoi(contact_email=EMAIL)
    r.http.get = lambda *a, **k: Response(200, b'''{"message": {
        "title": ["A Work"], "DOI": "10.1145/1111111.1111111",
        "issued": {"date-parts": [["not-a-year"]]}}}''', {})

    out = r.resolve(_entry(doi="10.1145/1111111.1111111", title="A Work"))
    assert isinstance(out, Found)
    assert out.record.year is None


def test_a_record_with_no_title_is_still_returned_for_the_checker_to_judge():
    """Deciding it is not the same work belongs in one place, and this is not
    that place."""
    from refaudit.http import Response

    r = CrossrefDoi(contact_email=EMAIL)
    r.http.get = lambda *a, **k: Response(
        200, b'{"message": {"DOI": "10.1145/1111111.1111111", "title": []}}', {})

    out = r.resolve(_entry(doi="10.1145/1111111.1111111", title="A Work"))
    assert isinstance(out, Found)
    assert out.record.title == ""


def test_an_empty_crossref_message_is_not_found():
    from refaudit.http import Response

    r = CrossrefDoi(contact_email=EMAIL)
    r.http.get = lambda *a, **k: Response(200, b'{"message": {}}', {})

    assert isinstance(r.resolve(_entry(doi="10.1145/1111111.1111111", title="A Work")), NotFound)
