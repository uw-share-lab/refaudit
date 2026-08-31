"""Does this DOI exist at all?

Every DOI is a Handle, and ``doi.org`` answers for all registration agencies at
once. That makes it the only place that can tell us a DOI is genuinely
unregistered, as opposed to merely absent from the one agency we happened to
ask. Reporting a reference as dead is the most consequential verdict this tool
issues, so it is the one claim we confirm against the registry itself.

Docs: https://www.doi.org/doi_handbook/3_Resolution.html
      https://www.doi.org/factsheets/DOIProxy.html#rest-api
"""

from __future__ import annotations

import urllib.parse

from .http import HttpError, TransportError
from .normalize import clean_doi
from .resolvers.base import HttpResolver, RateSpec

API = "https://doi.org/api/handles"

#: Handle protocol response codes we care about.
_RC_SUCCESS = 1
_RC_HANDLE_NOT_FOUND = 100
_RC_VALUES_NOT_FOUND = 200


class DoiExistence(HttpResolver):
    """Agency-agnostic "is this DOI registered?" check.

    Not a :class:`~refaudit.resolvers.base.Resolver`: it returns no metadata and
    so cannot verify that a reference is *correct*, only that its identifier is
    real. It is deliberately kept out of the resolver registry to stop it being
    mistaken for verification.
    """

    name = "doi.org"
    api_base = API
    rate = RateSpec(
        per_second=5.0,
        burst=5.0,
        rationale="The DOI proxy is a lightweight lookup service and we only "
                  "consult it for the handful of DOIs no agency resolved.",
    )

    def exists(self, doi: str) -> bool | None:
        """``True`` registered, ``False`` unregistered, ``None`` couldn't tell.

        The third case matters: an unreachable proxy must never be allowed to
        read as "this DOI is dead".
        """
        doi = clean_doi(doi)
        if not doi:
            return None
        url = f"{API}/{urllib.parse.quote(doi, safe='/')}"
        try:
            payload = self.http.get(url).json()
        except HttpError as e:
            # The proxy answers "no such handle" with HTTP 404. Any other
            # status is a service problem, not a statement about the DOI.
            return False if e.status == 404 else None
        except (TransportError, ValueError):
            return None
        code = payload.get("responseCode")
        if code == _RC_SUCCESS:
            return True
        if code in (_RC_HANDLE_NOT_FOUND, _RC_VALUES_NOT_FOUND):
            return False
        return None
