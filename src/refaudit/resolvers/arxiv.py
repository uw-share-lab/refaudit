"""arXiv Atom API.

Docs: https://info.arxiv.org/help/api/user-manual.html
Terms: https://info.arxiv.org/help/api/tou.html

arXiv's terms of use ask for **no more than one request every three seconds**
and a single connection at a time. That is the slowest limit of any source here
and it is not negotiable, so the bucket is set to it exactly. arXiv also
responds 429 to networks it considers noisy regardless of the individual
caller's pace; when that happens the correct outcome is ``Unavailable``, never a
verdict about the entry.
"""

from __future__ import annotations

import re
import urllib.parse

from ..http import HttpError, TransportError
from ..models import Entry, Found, NotFound, Outcome, Record, Unavailable
from ..normalize import clean_arxiv_id
from ..xmlsafe import ParseError, XmlSecurityError, fromstring
from .base import HttpResolver, RateSpec

API = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"


class ArxivId(HttpResolver):
    name = "arxiv:id"
    rate = RateSpec(
        per_second=1.0 / 3.0,
        burst=1.0,
        rationale="arXiv's terms of use specify one request per three seconds.",
    )

    def can_handle(self, entry: Entry) -> bool:
        return bool(clean_arxiv_id(entry.arxiv_id))

    def resolve(self, entry: Entry) -> Outcome:
        ident = clean_arxiv_id(entry.arxiv_id)
        if not ident:
            return NotFound(self.name, "no usable arXiv id")
        url = f"{API}?{urllib.parse.urlencode({'id_list': ident, 'max_results': 1})}"
        try:
            xml = self.http.get(url, accept="application/atom+xml").text()
        except HttpError as e:
            return Unavailable(self.name, str(e), e.retry_after)
        except TransportError as e:
            return Unavailable(self.name, str(e))

        try:
            root = fromstring(xml)
        except ParseError as e:
            return Unavailable(self.name, f"malformed Atom: {e}")
        except XmlSecurityError as e:
            # A feed that declares entities is not something we will parse.
            return Unavailable(self.name, f"refused XML: {e}")

        entries = root.findall(f"{_ATOM}entry")
        if not entries:
            return NotFound(self.name, "no such arXiv id")
        node = entries[0]

        # arXiv returns a synthetic entry with this title for unknown ids.
        title = (node.findtext(f"{_ATOM}title") or "").strip()
        if title.lower() == "error":
            return NotFound(self.name, "no such arXiv id")

        published = (node.findtext(f"{_ATOM}published") or "")[:4]
        year: int | None = int(published) if published.isdigit() else None

        surname = ""
        author = node.find(f"{_ATOM}author")
        if author is not None:
            name = (author.findtext(f"{_ATOM}name") or "").strip()
            if name:
                surname = name.split()[-1]

        doi = (node.findtext("{http://arxiv.org/schemas/atom}doi") or "").strip()

        return Found(
            Record(
                source="arxiv",
                title=re.sub(r"\s+", " ", title),
                year=year,
                first_author_surname=surname,
                doi=doi.lower(),
                url=(node.findtext(f"{_ATOM}id") or "").strip(),
            )
        )
