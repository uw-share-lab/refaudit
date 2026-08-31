"""Duplicate detection.

Duplicates are invisible to the per-entry check: both copies resolve, both are
correct, both report OK. Only comparing entries with each other finds them.
"""

from __future__ import annotations

from refaudit.duplicates import find_duplicates
from refaudit.models import Entry


def e(key, **fields):
    return Entry(key=key, entry_type=fields.pop("entry_type", "article"), fields=fields)


def test_same_doi_under_two_keys():
    dups = find_duplicates([e("a", title="X", doi="10.1145/3449287"),
                            e("b", title="X", doi="10.1145/3449287")])
    assert len(dups) == 1
    assert set(dups[0].keys) == {"a", "b"}
    assert dups[0].reason == "same DOI"
    assert dups[0].primary == "a", "file order decides which to keep"


def test_arxiv_id_matches_the_equivalent_datacite_doi():
    """The real case: one entry carried the DOI, the other the bare eprint."""
    dups = find_duplicates([
        e("cheng2025social", title="Social Sycophancy", doi="10.48550/arXiv.2505.13995"),
        e("cheng2025socialsycophancy", title="Social Sycophancy", eprint="2505.13995"),
    ])
    assert len(dups) == 1
    assert dups[0].reason == "same arXiv ID"


def test_near_identical_titles_without_identifiers():
    dups = find_duplicates([
        e("a", title="To Trust or to Think: Cognitive Forcing Functions"),
        e("b", title="To trust or to think: cognitive forcing functions"),
    ])
    assert len(dups) == 1
    assert dups[0].reason == "near-identical title"


def test_distinct_papers_are_not_duplicates():
    dups = find_duplicates([
        e("a", title="A Study of Things", doi="10.1145/1111111"),
        e("b", title="A Study of Other Things Entirely", doi="10.1145/2222222"),
        e("c", title="Something Unrelated"),
    ])
    assert dups == []


def test_an_entry_is_reported_once_under_the_strongest_reason():
    """Sharing a DOI *and* a title is one finding, not two."""
    dups = find_duplicates([e("a", title="Same Title Here", doi="10.1145/3449287"),
                            e("b", title="Same Title Here", doi="10.1145/3449287")])
    assert len(dups) == 1
    assert dups[0].reason == "same DOI"


def test_three_way_duplicate_groups_together():
    dups = find_duplicates([e("a", title="T", doi="10.1145/3449287"),
                            e("b", title="T", doi="10.1145/3449287"),
                            e("c", title="T", doi="10.1145/3449287")])
    assert len(dups) == 1 and len(dups[0].keys) == 3


def test_entries_without_identifiers_or_titles_are_ignored():
    assert find_duplicates([e("a"), e("b"), e("c", title="")]) == []
