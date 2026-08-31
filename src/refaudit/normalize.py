"""Normalisation and comparison of bibliographic strings.

Comparing titles naively produces false alarms: publishers vary capitalisation,
BibTeX carries brace protection and LaTeX accents, and subtitles are sometimes
dropped. The aim here is to be forgiving about presentation and strict about
content, so that a flagged mismatch is worth a human's attention.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

# LaTeX accent/command forms that should collapse to their letter or vanish.
_TEX_COMMAND = re.compile(r"\\[a-zA-Z]+\s*")
_TEX_MATH = re.compile(r"\$[^$]*\$")
_BRACES = re.compile(r"[{}]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")

# Words that carry no discriminating power in a title comparison.
_STOPWORDS = frozenset(
    ["a", "an", "the", "of", "for", "and", "or", "in", "on", "at", "to", "with", "without", "via", "using", "toward", "towards"]
)


def strip_tex(value: str) -> str:
    value = _TEX_MATH.sub(" ", value or "")
    value = _TEX_COMMAND.sub(" ", value)
    return _BRACES.sub("", value)


def fold(value: str) -> str:
    """Lowercase, de-accent, strip punctuation, collapse whitespace."""
    value = strip_tex(value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = _NON_ALNUM.sub(" ", value.lower())
    return _WS.sub(" ", value).strip()


def title_tokens(value: str) -> list[str]:
    return [t for t in fold(value).split() if t not in _STOPWORDS]


def similarity(a: str, b: str) -> float:
    """Similarity in [0, 1] between two titles.

    Combines a sequence ratio (sensitive to word order and typos) with a
    *prefix* check that tolerates a dropped subtitle.

    The prefix condition is what makes the second signal safe. Plain token
    containment is not: "A Study of Things" is wholly contained in "A Study of
    Other Things Entirely", which is a different paper, and containment alone
    scores that a perfect match. Requiring the shorter title to be a leading
    run of the longer one keeps the "title: subtitle" case working while
    refusing coincidental subsets.
    """
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0

    seq = difflib.SequenceMatcher(None, fa, fb).ratio()

    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return seq

    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    # Two tokens is not enough to identify a work; require a little substance
    # before letting a prefix match override the sequence ratio.
    if len(short) >= 3 and long_[:len(short)] == short:
        return max(seq, 0.95)

    return seq


def first_surname(author_field: str) -> str:
    """Surname of the first author, from either BibTeX author convention."""
    if not author_field:
        return ""
    first = re.split(r"\s+and\s+", strip_tex(author_field).strip())[0].strip()
    if not first:
        return ""
    if "," in first:                       # "Ferguson, Sharon"
        return fold(first.split(",", 1)[0])
    parts = fold(first).split()            # "Sharon Ferguson"
    return parts[-1] if parts else ""


def surnames_match(a: str, b: str) -> bool:
    """Tolerant surname comparison: handles particles, hyphens and initials."""
    if not a or not b:
        return True                        # nothing to contradict
    if a == b:
        return True
    # van der Berg / Berg, Al-Rashid / Al Rashid
    a_last, b_last = a.split()[-1], b.split()[-1]
    if a_last == b_last:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:a-z0-9<>\[\]+]+$", re.IGNORECASE)


def clean_doi(raw: str) -> str:
    """Reduce a DOI field to a bare DOI, or "" if it does not look like one.

    Validating before interpolating into a request path keeps a malformed or
    hostile field from steering the URL somewhere unintended.
    """
    if not raw:
        return ""
    value = strip_tex(raw).strip()
    value = re.sub(r"^\s*(doi:\s*)?", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)
    value = value.strip().rstrip(".,;")
    return value if _DOI_RE.match(value) else ""


_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5}|[a-z-]+(\.[A-Z]{2})?/\d{7})$", re.IGNORECASE)


def clean_arxiv_id(raw: str) -> str:
    """Bare arXiv identifier without version suffix, or "" if unrecognisable."""
    if not raw:
        return ""
    value = strip_tex(raw).strip()
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^https?://arxiv\.org/abs/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"v\d+$", "", value.strip())
    return value if _ARXIV_RE.match(value) else ""
