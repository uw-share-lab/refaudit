"""BibTeX parsing and string normalisation."""

from refaudit.bibtex import cited_keys, parse_string
from refaudit.normalize import (
    clean_arxiv_id,
    clean_doi,
    first_surname,
    similarity,
    surnames_match,
)

BIB = r"""
% a commented-out entry that must be ignored by the cite scanner, not the parser
@inproceedings{nested,
  title     = {The {LLM} Era: {A} Study of {Nested {Braces}} in Titles},
  author    = {Ferguson, Sharon and Aoyagui, Paula Akemi},
  booktitle = {Proceedings of Something},
  year      = {2024},
  doi       = {10.1145/1234567.1234568}
}

@article{quoted,
  title  = "A Quoted Title, with a comma",
  author = "Someone Else",
  year   = 2021
}

@misc{preprint,
  title  = {A Preprint},
  eprint = {2404.12558},
  archivePrefix = {arXiv},
  year   = {2024}
}
"""


def test_parses_all_entry_types():
    entries = {e.key: e for e in parse_string(BIB)}
    assert set(entries) == {"nested", "quoted", "preprint"}
    assert entries["nested"].entry_type == "inproceedings"
    assert entries["quoted"].entry_type == "article"


def test_nested_braces_survive():
    e = {x.key: x for x in parse_string(BIB)}["nested"]
    # braces are retained in the raw field; normalisation strips them later
    assert "Nested" in e.title and "Braces" in e.title
    assert e.title.count("{") == e.title.count("}")


def test_quoted_value_with_comma():
    e = {x.key: x for x in parse_string(BIB)}["quoted"]
    assert e.title == "A Quoted Title, with a comma"
    assert e.year == 2021


def test_bare_numeric_year_and_eprint():
    e = {x.key: x for x in parse_string(BIB)}["preprint"]
    assert e.year == 2024
    assert clean_arxiv_id(e.arxiv_id) == "2404.12558"


def test_cited_keys_ignores_comments(tmp_path):
    tex = tmp_path / "s.tex"
    tex.write_text(
        "Live \\cite{alpha, beta} text.\n"
        "% \\cite{commented_out}\n"
        "Trailing \\citep{gamma} % \\cite{also_commented}\n"
        "Escaped 50\\% \\cite{delta}\n",
        encoding="utf-8",
    )
    assert cited_keys([tex]) == {"alpha", "beta", "gamma", "delta"}


def test_clean_doi_accepts_forms_and_rejects_junk():
    assert clean_doi("10.1145/3706598.3713248") == "10.1145/3706598.3713248"
    assert clean_doi("https://doi.org/10.1145/3706598.3713248") == "10.1145/3706598.3713248"
    assert clean_doi("doi: 10.1145/3706598.3713248") == "10.1145/3706598.3713248"
    # anything that is not a DOI must not reach a request path
    assert clean_doi("not a doi") == ""
    assert clean_doi("../../etc/passwd") == ""
    assert clean_doi("") == ""


def test_clean_arxiv_id_strips_version_and_rejects_junk():
    assert clean_arxiv_id("arXiv:2404.12558v2") == "2404.12558"
    assert clean_arxiv_id("https://arxiv.org/abs/2404.12558") == "2404.12558"
    assert clean_arxiv_id("cs.HC/0501001") == "cs.HC/0501001"
    assert clean_arxiv_id("nonsense") == ""


def test_similarity_tolerates_presentation_but_not_content():
    a = "Does the Whole Exceed its Parts? The Effect of AI Explanations"
    assert similarity(a, a.lower()) == 1.0
    assert similarity(a, "Does the whole exceed its parts? the effect of ai explanations") > 0.95
    # a dropped subtitle should still read as the same work
    assert similarity("Just Like Me: The Role of Opinions", "Just Like Me") > 0.75
    # genuinely different papers must not
    assert similarity(a, "Explanations Can Reduce Overreliance on AI Systems") < 0.5


def test_similarity_handles_latex_and_accents():
    assert similarity(r"Caf\'{e} Interaction", "Café Interaction") > 0.9
    assert similarity("The {LLM} Era", "The LLM Era") > 0.95


def test_first_surname_both_conventions():
    assert first_surname("Ferguson, Sharon and Aoyagui, Paula") == "ferguson"
    assert first_surname("Sharon Ferguson and Paula Aoyagui") == "ferguson"
    assert first_surname("") == ""


def test_surnames_match_is_tolerant_but_not_blind():
    assert surnames_match("ferguson", "ferguson")
    assert surnames_match("van der berg", "berg")
    assert surnames_match("", "anything")          # nothing to contradict
    assert not surnames_match("ferguson", "vasconcelos")


def test_clean_doi_accepts_schemeless_doi_org_prefix():
    """.bib files carry "doi.org/10.x" as often as the full URL.

    Dropping it downgraded the entry to a title search, which still produced a
    verdict -- so the weaker check was invisible rather than loud.
    """
    from refaudit.normalize import clean_doi

    expected = "10.48550/arXiv.2410.07304"
    for raw in (
        "doi.org/10.48550/arXiv.2410.07304",
        "www.doi.org/10.48550/arXiv.2410.07304",
        "dx.doi.org/10.48550/arXiv.2410.07304",
        "https://doi.org/10.48550/arXiv.2410.07304",
        "DOI: https://doi.org/10.48550/arXiv.2410.07304",
    ):
        assert clean_doi(raw) == expected, raw


def test_clean_doi_still_rejects_non_dois():
    from refaudit.normalize import clean_doi

    for raw in ("not a doi", "ftp://evil.example/10.1/x", "doi.org/nope", ""):
        assert clean_doi(raw) == ""


def test_arxiv_id_is_found_in_the_journal_field():
    """Google Scholar exports put it there instead of in `eprint`.

    Only reading `eprint` reported real, findable preprints as missing -- the
    most common shape of entry there is for recent work.
    """
    from refaudit.models import Entry
    from refaudit.normalize import clean_arxiv_id

    e = Entry(key="k", entry_type="article",
              fields={"title": "T", "journal": "arXiv preprint arXiv:2506.08872"})
    assert clean_arxiv_id(e.arxiv_id) == "2506.08872"


def test_arxiv_id_is_found_in_other_free_text_fields():
    from refaudit.models import Entry
    from refaudit.normalize import clean_arxiv_id

    for field, value in [
        ("note", "arXiv:2310.13548"),
        ("url", "https://arxiv.org/abs/2405.10632"),
        ("howpublished", "\\url{https://arxiv.org/pdf/2505.13995v2}"),
        ("doi", "10.48550/arXiv.2502.14052"),
    ]:
        e = Entry(key="k", entry_type="misc", fields={"title": "T", field: value})
        assert clean_arxiv_id(e.arxiv_id), f"{field}={value!r} yielded nothing"


def test_an_explicit_eprint_field_still_wins():
    from refaudit.models import Entry
    from refaudit.normalize import clean_arxiv_id

    e = Entry(key="k", entry_type="misc",
              fields={"title": "T", "eprint": "2310.13548",
                      "journal": "arXiv preprint arXiv:9999.99999"})
    assert clean_arxiv_id(e.arxiv_id) == "2310.13548"


def test_free_text_without_an_arxiv_id_yields_nothing():
    from refaudit.models import Entry

    for value in ("Nature Human Behaviour", "Proceedings of CHI 2020", ""):
        e = Entry(key="k", entry_type="article", fields={"title": "T", "journal": value})
        assert e.arxiv_id == "", f"{value!r} should not look like an arXiv id"


def test_a_journal_merely_mentioning_arxiv_is_not_an_id():
    """"arXiv" as a word must not be mistaken for an identifier."""
    from refaudit.models import Entry

    e = Entry(key="k", entry_type="article",
              fields={"title": "T", "journal": "arXiv e-prints"})
    assert e.arxiv_id == ""
