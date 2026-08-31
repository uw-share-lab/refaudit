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
