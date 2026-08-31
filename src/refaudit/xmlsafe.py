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
2. Otherwise the standard-library parser with expat's entity handlers wired to
   refuse any entity declaration outright.

Refusing entity declarations is stricter than defusedxml's default (which
permits harmless internal entities), and that is the right trade here: Atom
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

    def _forbid(*args: object, **kwargs: object) -> None:
        raise XmlSecurityError("XML entity declarations are not accepted")

    _DOCTYPE = _re.compile(r"<!\s*(DOCTYPE|ENTITY)\b", _re.IGNORECASE)

    def fromstring(text: str):
        # Refuse before parsing. Wiring expat's entity handlers after
        # ElementTree has built its parser proved unreliable across CPython
        # builds -- the handlers were silently not applied -- so the check that
        # actually holds is a scan for any DTD or entity declaration. Atom feeds
        # from these APIs contain neither, so this rejects nothing legitimate.
        if _DOCTYPE.search(text):
            raise XmlSecurityError("document declares a DTD or entity; refused")
        parser = _ET.XMLParser()
        expat = getattr(parser, "parser", None)
        if expat is not None:
            # Any of these firing means the document is trying something we do
            # not need and will not do.
            for handler in (
                "EntityDeclHandler",
                "UnparsedEntityDeclHandler",
                "ExternalEntityRefHandler",
                "NotationDeclHandler",
            ):
                try:
                    setattr(expat, handler, _forbid)
                except (AttributeError, TypeError):  # handler absent on this build
                    pass
            try:
                expat.DefaultHandlerExpand = None
            except (AttributeError, TypeError):
                pass
        parser.feed(text)
        return parser.close()
