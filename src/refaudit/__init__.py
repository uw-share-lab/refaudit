"""refaudit -- verify bibliography entries against external indexes.

Built for the case where a venue runs an automated check for hallucinated or
malformed references and a false entry costs you a desk reject.

    from refaudit import Checker, parse_file, default_resolvers

    entries = parse_file("refs.bib")
    checker = Checker(default_resolvers("you@example.org"))
    for result in checker.check_all(entries):
        print(result.key, result.verdict.value)
"""

from .bibtex import cited_keys, find_tex, parse_file, parse_string
from .cache import Cache
from .checker import Checker, Thresholds
from .models import CheckResult, Entry, Found, NotFound, Record, Unavailable, Verdict
from .resolvers import default_resolvers

__version__ = "0.1.0"

__all__ = [
    "Cache",
    "CheckResult",
    "Checker",
    "Entry",
    "Found",
    "NotFound",
    "Record",
    "Thresholds",
    "Unavailable",
    "Verdict",
    "__version__",
    "cited_keys",
    "default_resolvers",
    "find_tex",
    "parse_file",
    "parse_string",
]
