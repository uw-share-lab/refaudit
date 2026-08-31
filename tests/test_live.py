"""The one test that talks to the real services. Opt-in.

Every other test here is hermetic, on purpose: CI must not fail because
Crossref was busy. The cost of that is real, though. A green suite tells you
the code behaves correctly against fixtures somebody wrote by hand, and says
nothing about whether it still parses what Crossref actually sends. Version
0.4.1 exists because a live run found something the other 127 tests could not:
OpenAlex answered with a `Retry-After` of eight hours and the client sat there
re-asking it.

So this runs only when you ask for it:

    REFAUDIT_LIVE_EMAIL=you@uni.edu pytest tests/test_live.py -v

and on a schedule, away from pull requests, where a busy upstream costs a
notification rather than a blocked merge.

The assertions are shaped around the one property that must never break. A
source being unreachable has to produce *no verdict* rather than a wrong one,
so an outage is allowed to turn any of these into UNVERIFIED. What is never
allowed is a correct entry coming back as a finding. The run must also resolve
something, or an outage everywhere would let this pass while proving nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from refaudit import Checker, Verdict, default_resolvers, parse_file
from refaudit.doi_registry import DoiExistence
from refaudit.duplicates import find_duplicates

EMAIL = os.environ.get("REFAUDIT_LIVE_EMAIL", "").strip()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not EMAIL,
        reason="set REFAUDIT_LIVE_EMAIL to run the live check against real APIs",
    ),
]

FIXTURE = Path(__file__).parent / "fixtures" / "smoke.bib"

#: What each entry in the fixture is, and therefore what a correct run may say
#: about it. UNVERIFIED is always allowed: it means a source could not be
#: reached, which is a fact about the network and not about the entry.
EXPECTED: dict[str, set[Verdict]] = {
    "ok_crossref": {Verdict.OK},
    "ok_arxiv": {Verdict.OK},
    "ok_datacite": {Verdict.OK},
    "dup_of_arxiv": {Verdict.OK},
    # A real DOI paired with an unrelated title: the failure this tool exists
    # to catch. NOT_FOUND is tolerated only in the sense that a dead network
    # cannot confirm anything; OK would mean the check is broken.
    "bad_title": {Verdict.TITLE_MISMATCH},
    "dead_doi": {Verdict.DEAD_DOI, Verdict.NOT_FOUND},
    "no_id_misc": {Verdict.SKIPPED, Verdict.NOT_FOUND},
}


@pytest.fixture(scope="module")
def results():
    entries = parse_file(FIXTURE)
    assert len(entries) == len(EXPECTED), "fixture and expectations have drifted apart"
    checker = Checker(
        default_resolvers(EMAIL),
        cache=None,  # never let a cached answer stand in for a live one
        doi_existence=DoiExistence(contact_email=EMAIL),
    )
    return {r.key: r for r in checker.check_all(entries, workers=2)}


def test_the_live_path_resolves_something(results):
    """Guards the rest: everything may legitimately be UNVERIFIED in an
    outage, so without this the whole file could pass while proving nothing."""
    resolved = [r for r in results.values() if r.verdict is not Verdict.UNVERIFIED]
    assert resolved, "no entry reached any source; the live path is not working"


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_each_entry_gets_a_defensible_verdict(results, key):
    result = results[key]
    allowed = EXPECTED[key] | {Verdict.UNVERIFIED}
    assert result.verdict in allowed, (
        f"{key}: got {result.verdict.value} from {result.source or 'no source'}; "
        f"expected one of {sorted(v.value for v in allowed)}. note={result.note!r}"
    )


def test_no_correct_entry_is_reported_as_a_finding(results):
    """The safety property. Everything else here is diagnostics."""
    correct = [k for k, v in EXPECTED.items() if Verdict.OK in v]
    wrongly_flagged = [k for k in correct if results[k].verdict.is_finding]
    assert not wrongly_flagged, (
        "correct references reported as findings: "
        + ", ".join(f"{k}={results[k].verdict.value} ({results[k].note})"
                    for k in wrongly_flagged)
    )


def test_the_planted_mismatch_is_caught_when_crossref_answers(results):
    """If Crossref answered at all, the wrong-DOI entry must not pass as OK."""
    assert results["bad_title"].verdict is not Verdict.OK


def test_duplicates_are_found_offline(results):
    """Does not touch the network, but belongs with the end-to-end shape."""
    dups = find_duplicates(parse_file(FIXTURE))
    reasons = {d.reason for d in dups}
    assert "same DOI" in reasons
    assert "same arXiv ID" in reasons
