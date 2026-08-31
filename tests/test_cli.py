"""The command line, end to end, with the network stubbed out.

``main()`` was the least-covered code in the package at 22%, which is an odd
place to be thin: it is the only part every user touches. Argument validation,
the ``--only-cited`` filter, the exit status a CI job branches on and the files
written to ``--out`` had no test between them.

Exit status is the contract that matters most here, because it is what a
pre-submission hook or a CI step keys off: 0 clean, 1 something to look at,
2 the invocation itself was wrong.
"""

from __future__ import annotations

import csv

import pytest

from refaudit.cli import main
from refaudit.models import Found, NotFound, Record, Unavailable

BIB = """\
@article{good,
  title = {A Study of Things},
  author = {Ferguson, Sharon},
  year = {2024},
  doi = {10.1145/1111111.1111111}
}

@article{uncited,
  title = {Something Nobody Cited},
  author = {Nobody, A.},
  year = {2020},
  doi = {10.1145/2222222.2222222}
}
"""


class Fake:
    """A resolver that answers from a fixed script, touching no network."""

    year_is_authoritative = True

    def __init__(self, outcome, name="fake:title"):
        # The name is not cosmetic. A resolver whose name ends in ":doi" is an
        # identifier lookup, so its NotFound means "this agency disowns the
        # DOI" and routes through the DEAD_DOI logic instead of being a plain
        # absence. Default to a title resolver and opt in to the other.
        self.name = name
        self._outcome = outcome

    def can_handle(self, entry):
        return True

    def resolve(self, entry):
        return self._outcome(entry) if callable(self._outcome) else self._outcome


@pytest.fixture(autouse=True)
def _restore_logging():
    """main() configures the package logger, which is global state.

    Leaving it configured leaks into any test that asserts refaudit stays
    silent on import, so every test here puts it back.
    """
    import logging

    log = logging.getLogger("refaudit")
    handlers, level = list(log.handlers), log.level
    try:
        yield
    finally:
        log.handlers[:] = handlers
        log.setLevel(level)


@pytest.fixture
def bib(tmp_path):
    p = tmp_path / "refs.bib"
    p.write_text(BIB, encoding="utf-8")
    return p


@pytest.fixture
def stub(monkeypatch):
    """Replace resolver construction and the DOI proxy for the whole CLI."""
    def install(outcome, name="fake:title"):
        monkeypatch.setattr("refaudit.cli.default_resolvers",
                            lambda *a, **k: [Fake(outcome, name)])
        monkeypatch.setattr("refaudit.cli.DoiExistence",
                            lambda *a, **k: None)
    return install


def _match(entry):
    """An answer that agrees with the entry in every field we compare."""
    from refaudit.normalize import first_surname

    return Found(Record(source="fake", title=entry.title, year=entry.year,
                        first_author_surname=first_surname(entry.get("author"))))


# --- exit status ------------------------------------------------------------

def test_a_clean_run_exits_zero(bib, stub, tmp_path):
    stub(_match)
    assert main([str(bib), "--email", "a@b.org", "--out", str(tmp_path / "o"),
                 "--no-cache", "--quiet"]) == 0


def test_a_run_with_findings_exits_one(bib, stub, tmp_path):
    stub(Found(Record(source="fake", title="An Entirely Different Paper",
                      year=2024, first_author_surname="Ferguson")))
    assert main([str(bib), "--email", "a@b.org", "--out", str(tmp_path / "o"),
                 "--no-cache", "--quiet"]) == 1


def test_an_unreachable_source_is_not_a_finding(bib, stub, tmp_path):
    """The central guarantee, asserted through the exit code a script sees."""
    stub(Unavailable("fake", "HTTP 429"))
    assert main([str(bib), "--email", "a@b.org", "--out", str(tmp_path / "o"),
                 "--no-cache", "--quiet"]) == 0


# --- invocation errors, all exit 2 -----------------------------------------

def test_a_missing_file_exits_two(tmp_path, capsys):
    assert main([str(tmp_path / "nope.bib"), "--email", "a@b.org"]) == 2
    assert "no such file" in capsys.readouterr().err


def test_a_missing_email_exits_two(bib, monkeypatch, capsys):
    monkeypatch.delenv("REFAUDIT_EMAIL", raising=False)
    assert main([str(bib)]) == 2
    assert "--email is required" in capsys.readouterr().err


def test_only_cited_without_tex_exits_two(bib, capsys):
    assert main([str(bib), "--email", "a@b.org", "--only-cited"]) == 2
    assert "--only-cited requires --tex" in capsys.readouterr().err


def test_an_unknown_resolver_name_exits_two(bib, tmp_path, capsys):
    assert main([str(bib), "--email", "a@b.org", "--resolvers", "not-a-source",
                 "--out", str(tmp_path / "o")]) == 2
    assert "unknown resolver" in capsys.readouterr().err


def test_a_bib_with_no_entries_exits_two(tmp_path, capsys):
    empty = tmp_path / "empty.bib"
    empty.write_text("% nothing here\n", encoding="utf-8")
    assert main([str(empty), "--email", "a@b.org"]) == 2
    assert "no entries parsed" in capsys.readouterr().err


def test_the_email_can_come_from_the_environment(bib, stub, tmp_path, monkeypatch):
    monkeypatch.setenv("REFAUDIT_EMAIL", "env@b.org")
    stub(_match)
    assert main([str(bib), "--out", str(tmp_path / "o"), "--no-cache", "--quiet"]) == 0


# --- what lands in --out ----------------------------------------------------

def test_both_reports_are_written(bib, stub, tmp_path):
    out = tmp_path / "o"
    stub(_match)
    main([str(bib), "--email", "a@b.org", "--out", str(out), "--no-cache", "--quiet"])

    assert (out / "reference_check.txt").exists()
    rows = list(csv.DictReader((out / "reference_check.csv").open()))
    assert {r["key"] for r in rows} == {"good", "uncited"}
    assert "duplicate_of" in rows[0]


def test_no_cache_leaves_no_cache_behind(bib, stub, tmp_path):
    out = tmp_path / "o"
    stub(_match)
    main([str(bib), "--email", "a@b.org", "--out", str(out), "--no-cache", "--quiet"])
    assert not (out / "cache.json").exists()


def test_a_cache_is_written_by_default(bib, stub, tmp_path):
    out = tmp_path / "o"
    stub(_match)
    main([str(bib), "--email", "a@b.org", "--out", str(out), "--quiet"])
    assert (out / "cache.json").exists()


# --- --tex and --only-cited -------------------------------------------------

def test_only_cited_checks_just_the_keys_the_paper_uses(bib, stub, tmp_path):
    tex = tmp_path / "sections"
    tex.mkdir()
    (tex / "intro.tex").write_text(r"Text \cite{good} more text.", encoding="utf-8")
    out = tmp_path / "o"
    stub(_match)

    main([str(bib), "--email", "a@b.org", "--tex", str(tex), "--only-cited",
          "--out", str(out), "--no-cache", "--quiet"])

    rows = list(csv.DictReader((out / "reference_check.csv").open()))
    assert {r["key"] for r in rows} == {"good"}, "uncited entries must not be checked"


def test_only_cited_with_nothing_cited_exits_two(bib, stub, tmp_path, capsys):
    tex = tmp_path / "sections"
    tex.mkdir()
    (tex / "intro.tex").write_text("No citations at all.", encoding="utf-8")
    stub(_match)

    assert main([str(bib), "--email", "a@b.org", "--tex", str(tex), "--only-cited",
                 "--out", str(tmp_path / "o"), "--no-cache"]) == 2
    assert "no cited entries found" in capsys.readouterr().err


# --- output volume ----------------------------------------------------------

def test_quiet_suppresses_the_per_entry_lines(bib, stub, tmp_path, capsys):
    stub(_match)
    main([str(bib), "--email", "a@b.org", "--out", str(tmp_path / "o"),
          "--no-cache", "--quiet"])
    assert "[1/2]" not in capsys.readouterr().out


def test_without_quiet_each_entry_is_reported_as_it_finishes(bib, stub, tmp_path, capsys):
    stub(_match)
    main([str(bib), "--email", "a@b.org", "--out", str(tmp_path / "o"), "--no-cache"])
    assert "[1/2]" in capsys.readouterr().out


def test_verbose_puts_diagnostics_on_stderr_not_in_the_report(bib, stub, tmp_path, capsys):
    """The report is piped to a file often enough that this matters."""
    import logging

    stub(_match)
    main([str(bib), "--email", "a@b.org", "--out", str(tmp_path / "o"),
          "--no-cache", "--quiet", "-v"])
    assert logging.getLogger("refaudit").level == logging.DEBUG
    assert "WARNING" not in capsys.readouterr().out, "diagnostics must not land in the report"


# --- duplicates -------------------------------------------------------------

def test_duplicates_alone_are_enough_to_exit_one(tmp_path, stub):
    """A bibliography citing one work twice needs a human, even if every
    entry in it verifies perfectly."""
    p = tmp_path / "dup.bib"
    p.write_text("""\
@article{a, title={A Study of Things}, author={Ferguson, S.}, year={2024},
  doi={10.1145/1111111.1111111}}
@article{b, title={A Study of Things}, author={Ferguson, S.}, year={2024},
  doi={10.1145/1111111.1111111}}
""", encoding="utf-8")
    stub(_match)

    assert main([str(p), "--email", "a@b.org", "--out", str(tmp_path / "o"),
                 "--no-cache", "--quiet"]) == 1


def test_no_duplicates_flag_skips_that_pass(tmp_path, stub):
    p = tmp_path / "dup.bib"
    p.write_text("""\
@article{a, title={A Study of Things}, author={Ferguson, S.}, year={2024},
  doi={10.1145/1111111.1111111}}
@article{b, title={A Study of Things}, author={Ferguson, S.}, year={2024},
  doi={10.1145/1111111.1111111}}
""", encoding="utf-8")
    stub(_match)

    assert main([str(p), "--email", "a@b.org", "--out", str(tmp_path / "o"),
                 "--no-cache", "--quiet", "--no-duplicates"]) == 0


# --- a resolver that finds nothing -----------------------------------------

def test_nothing_found_is_a_finding(bib, stub, tmp_path):
    """A title search coming back empty is a real absence."""
    stub(NotFound("fake", "no record"))
    assert main([str(bib), "--email", "a@b.org", "--out", str(tmp_path / "o"),
                 "--no-cache", "--quiet"]) == 1


def test_a_doi_disowned_by_one_agency_is_not_a_finding(bib, stub, tmp_path):
    """No single agency speaks for the whole DOI system. With no proxy to
    confirm it, one agency's "not mine" leaves the entry unchecked rather than
    wrong -- this is what stopped 22 live preprints being called dead."""
    stub(NotFound("fake", "not registered here"), name="fake:doi")
    assert main([str(bib), "--email", "a@b.org", "--out", str(tmp_path / "o"),
                 "--no-cache", "--quiet"]) == 0
