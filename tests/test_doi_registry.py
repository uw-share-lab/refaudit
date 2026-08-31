"""The check that decides whether a reference gets called dead.

``DoiExistence`` is consulted only after every registration agency we query has
disowned a DOI, and its answer is the difference between reporting `DEAD_DOI` --
an accusation that somebody invented a citation -- and `UNVERIFIED`, which
merely says we could not confirm it.

So the three states are not equally important. Getting `True` or `False` wrong
is a bug; returning `False` when the honest answer is "could not tell" is the
one that puts a false accusation in somebody's report, and every test here is
shaped around that asymmetry.
"""

from __future__ import annotations

import json

import pytest

from refaudit.doi_registry import DoiExistence
from refaudit.http import HttpError, Response, TransportError

EMAIL = "test@example.org"


def _answering(payload, status: int = 200):
    r = DoiExistence(contact_email=EMAIL)
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    r.http.get = lambda *a, **k: Response(status, body, {})
    return r


def _raising(exc: Exception):
    r = DoiExistence(contact_email=EMAIL)

    def boom(*a, **k):
        raise exc
    r.http.get = boom
    return r


# --- the two confident answers ---------------------------------------------

def test_a_registered_doi_is_true():
    r = _answering({"responseCode": 1, "handle": "10.1145/3313831.3376727"})
    assert r.exists("10.1145/3313831.3376727") is True


@pytest.mark.parametrize("code", [100, 200])
def test_the_proxys_not_found_codes_are_false(code):
    """100 is "handle not found", 200 "values not found". Both mean the DOI is
    not registered with anybody, which is what DEAD_DOI rests on."""
    assert _answering({"responseCode": code}).exists("10.1145/9999999.9999998") is False


def test_a_404_from_the_proxy_is_false():
    assert _raising(HttpError(404, "not found")).exists("10.1145/9999999.9999998") is False


# --- everything else must be "could not tell" ------------------------------

def test_an_unreachable_proxy_is_none_not_dead():
    """The one that matters. A network problem must never read as a verdict."""
    assert _raising(TransportError("connection reset")).exists("10.1145/1.1") is None


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_any_status_other_than_404_is_none(status):
    """Being throttled or meeting a broken server says nothing about the DOI."""
    assert _raising(HttpError(status, "busy")).exists("10.1145/1.1") is None


def test_an_unrecognised_response_code_is_none():
    """The proxy has codes beyond the three we know. An unfamiliar one is not
    evidence of absence."""
    assert _answering({"responseCode": 42}).exists("10.1145/1.1") is None


def test_a_response_with_no_code_at_all_is_none():
    assert _answering({}).exists("10.1145/1.1") is None


def test_malformed_json_is_none():
    assert _answering(b'{"responseCo').exists("10.1145/1.1") is None


def test_an_unusable_doi_is_none_without_asking():
    """Nothing to look up is not the same as looked up and absent."""
    r = DoiExistence(contact_email=EMAIL)

    def must_not_be_called(*a, **k):
        raise AssertionError("asked the proxy about a DOI that is not one")
    r.http.get = must_not_be_called

    assert r.exists("") is None
    assert r.exists("not-a-doi") is None


def test_a_doi_url_is_accepted_and_normalised():
    """.bib files carry the full URL as often as the bare DOI."""
    r = _answering({"responseCode": 1})
    assert r.exists("https://doi.org/10.1145/3313831.3376727") is True


def test_it_is_kept_out_of_the_resolver_registry():
    """It returns no metadata, so it cannot verify a reference is correct --
    only that the identifier is real. Registering it would let it be mistaken
    for verification."""
    from refaudit.resolvers import AVAILABLE

    assert DoiExistence not in AVAILABLE.values()
    assert not hasattr(DoiExistence, "resolve") or "resolve" not in vars(DoiExistence)
