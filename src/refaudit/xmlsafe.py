"""XML parsing hardened against entity-expansion and external-entity attacks.

``xml.etree.ElementTree`` will happily process a document that declares
entities, which makes it vulnerable to "billion laughs" expansion and, with some
parsers, to external entity resolution. We only ever parse Atom feeds from a
service we reached over TLS, so this is defence in depth rather than a live
threat -- but a library that advertises a security posture should not leave the
obvious parser hole open, and the mitigation costs nothing.

Order of preference:

1. ``defusedxml`` if it happens to be installed, since it is the maintained
   answer to this problem.
2. Otherwise the standard-library parser, with any document that declares a DTD
   or an entity refused before it is parsed at all.

Refusing entity declarations outright is stricter than defusedxml's default,
which permits harmless internal ones, and that is the right trade here: Atom
responses from arXiv contain none, so anything that does is a reason to stop.
"""

from __future__ import annotations

import re as _re
import xml.etree.ElementTree as _ET

__all__ = ["ParseError", "XmlSecurityError", "fromstring"]

ParseError = _ET.ParseError


class XmlSecurityError(Exception):
    """Raised when a document declares entities, which we do not accept."""


try:  # pragma: no cover - exercised only where defusedxml is installed
    from defusedxml.common import DefusedXmlException
    from defusedxml.ElementTree import fromstring as _defused_fromstring

    def fromstring(text: str):
        # Normalise defusedxml's exception family onto our own, so callers have
        # exactly one security exception to handle regardless of which backend
        # is installed. Without this, the same hostile input raises different
        # types on different machines and one of them escapes the handler.
        try:
            return _defused_fromstring(text)
        except DefusedXmlException as e:
            raise XmlSecurityError(str(e)) from e

except ImportError:

    _DOCTYPE = _re.compile(r"<!\s*(DOCTYPE|ENTITY)\b", _re.IGNORECASE)

    def fromstring(text: str):
        # Refuse before parsing, rather than by configuring the parser.
        #
        # The obvious approach is to wire expat's entity handlers to reject
        # declarations, but they can only be reached through
        # ``XMLParser.parser``, which CPython no longer exposes. Code that
        # tried sat behind ``if expat is not None`` on a value that is always
        # None -- it looked like a defence and was never once executed. It has
        # been removed; ``test_element_tree_exposes_no_expat_parser`` fails if
        # a future Python ever brings the attribute back.
        #
        # So this scan is the whole defence, and it is a stronger one: it acts
        # before the document reaches a parser at all, and refuses any DTD or
        # entity declaration outright rather than permitting the harmless ones.
        # Atom feeds from these APIs contain neither, so nothing legitimate is
        # rejected.
        if _DOCTYPE.search(text):
            raise XmlSecurityError("document declares a DTD or entity; refused")
        return _ET.fromstring(text)
