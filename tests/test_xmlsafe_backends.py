"""Both XML backends, held to the same promise.

``xmlsafe`` picks its parser at import: ``defusedxml`` when it is installed,
otherwise the standard library with a hard refusal of any DTD or entity
declaration. Only one of those runs on any given machine, so a test suite run
on a developer's laptop silently exercises one and never the other.

Which is backwards, because ``defusedxml`` is an *optional* extra. The
dependency list is empty by design, so the standard-library fallback is the
path most installations actually take -- and it was the one with no test at
all.

These tests load the module both ways and put the same attacks through each.
The property is that a hostile document is refused identically whichever
backend is present, and raises `XmlSecurityError` either way rather than a type
that depends on what happens to be installed.
"""

from __future__ import annotations

import builtins
import importlib
import sys

import pytest

from refaudit.xmlsafe import XmlSecurityError

ATTACKS = {
    "internal entity": '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "a">]><l>&a;</l>',
    "billion laughs": ('<?xml version="1.0"?><!DOCTYPE z [<!ENTITY a "aa">'
                       '<!ENTITY b "&a;&a;">]><z>&b;</z>'),
    "external entity": ('<?xml version="1.0"?><!DOCTYPE d '
                        '[<!ENTITY x SYSTEM "file:///etc/passwd">]><d>&x;</d>'),
    "bare doctype": "<!DOCTYPE feed><feed><t>x</t></feed>",
    "doctype with odd spacing": '<!  DOCTYPE feed><feed><t>x</t></feed>',
    "lowercase doctype": "<!doctype feed><feed><t>x</t></feed>",
}

ATOM = ('<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><title>Just Like Me</title></entry></feed>")
NS = "{http://www.w3.org/2005/Atom}"


@pytest.fixture
def stdlib_backend(monkeypatch):
    """``xmlsafe`` as it behaves where defusedxml is not installed.

    The backend is chosen by an import at module scope, so the only honest way
    to reach the other branch is to re-import with that name unavailable. The
    real module is put back afterwards, or every later test in the session
    would keep the stubbed one.
    """
    real_import = builtins.__import__

    def without_defusedxml(name, *args, **kwargs):
        if name.startswith("defusedxml"):
            raise ImportError("defusedxml is not installed")
        return real_import(name, *args, **kwargs)

    saved = sys.modules.pop("refaudit.xmlsafe", None)
    monkeypatch.setattr(builtins, "__import__", without_defusedxml)
    try:
        yield importlib.import_module("refaudit.xmlsafe")
    finally:
        monkeypatch.undo()
        sys.modules.pop("refaudit.xmlsafe", None)
        if saved is not None:
            sys.modules["refaudit.xmlsafe"] = saved
        else:  # pragma: no cover - only if it had never been imported
            importlib.import_module("refaudit.xmlsafe")


def test_the_fixture_really_selects_the_fallback(stdlib_backend):
    """Guards every other test here: if defusedxml leaked in, they would all
    pass while testing the backend that was already covered."""
    assert stdlib_backend.fromstring.__module__ == "refaudit.xmlsafe"
    # _DOCTYPE is defined only in the ImportError branch, so its presence is
    # proof of which backend was selected. Asserting on sys.modules would not
    # be: defusedxml stays imported from earlier tests in the same session.
    assert hasattr(stdlib_backend, "_DOCTYPE"), "the defusedxml backend was loaded instead"


@pytest.mark.parametrize("name", list(ATTACKS))
def test_the_fallback_refuses_every_attack(stdlib_backend, name):
    # Its own exception class, not the one imported at the top of this file:
    # re-importing the module builds a fresh class object, so the two are not
    # the same type even though they have the same name.
    with pytest.raises(stdlib_backend.XmlSecurityError):
        stdlib_backend.fromstring(ATTACKS[name])


def test_the_fallback_still_parses_a_real_atom_feed(stdlib_backend):
    root = stdlib_backend.fromstring(ATOM)
    assert root.find(f"{NS}entry").findtext(f"{NS}title") == "Just Like Me"


def test_the_fallback_parses_the_captured_arxiv_response(stdlib_backend):
    """The document this parser actually meets in production."""
    from pathlib import Path

    xml = (Path(__file__).parent / "fixtures" / "responses" / "arxiv-entry.xml"
           ).read_text(encoding="utf-8")
    root = stdlib_backend.fromstring(xml)
    title = root.find(f"{NS}entry").findtext(f"{NS}title")
    assert " ".join(title.split()) == "Attention Is All You Need"


def test_the_fallback_raises_our_error_type_not_expats(stdlib_backend):
    """One security exception for callers to handle, whichever backend is in
    play. Without this the same hostile input raises different types on
    different machines and one of them escapes the handler.

    Asserted by name and ancestry rather than identity, because the re-import
    this fixture performs necessarily produces a distinct class object. What
    matters in production, where the module is imported once, is that both
    branches raise the same named error out of the same module.
    """
    import xml.etree.ElementTree as ET

    with pytest.raises(Exception) as caught:
        stdlib_backend.fromstring(ATTACKS["billion laughs"])

    assert type(caught.value).__name__ == XmlSecurityError.__name__
    assert type(caught.value).__module__ == XmlSecurityError.__module__
    assert not isinstance(caught.value, ET.ParseError), "not a parse failure"


def test_malformed_xml_is_a_parse_error_not_a_security_error(stdlib_backend):
    """A truncated feed is a broken response, not an attack, and the caller
    turns it into Unavailable rather than a verdict."""
    with pytest.raises(stdlib_backend.ParseError):
        stdlib_backend.fromstring("<feed><entry>unclosed")


# --- the installed backend, for comparison ---------------------------------

@pytest.mark.parametrize("name", list(ATTACKS))
def test_the_installed_backend_refuses_the_same_attacks(name):
    """Whatever is installed here must refuse exactly what the fallback does."""
    from refaudit.xmlsafe import fromstring

    with pytest.raises(XmlSecurityError):
        fromstring(ATTACKS[name])


def test_both_backends_agree_on_a_legitimate_feed(stdlib_backend):
    from refaudit import xmlsafe as installed

    a = stdlib_backend.fromstring(ATOM).find(f"{NS}entry").findtext(f"{NS}title")
    b = installed.fromstring(ATOM).find(f"{NS}entry").findtext(f"{NS}title")
    assert a == b == "Just Like Me"
