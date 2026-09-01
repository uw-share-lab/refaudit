# refaudit

A zero-dependency CLI that checks bibliography entries against eight external
indexes before you submit a paper. `src/refaudit/`, ~3,200 lines, Python 3.10+.

## The invariant

**"We could not check" and "this is wrong" must never collapse into each other.**

A resolver returns `Found`, `NotFound` or `Unavailable` (`models.py`), and only
`Found` may produce a negative verdict. `UNVERIFIED` is reported separately from
findings and is **never cached**. A tool that cries wolf when a service was
merely busy trains its users to ignore it, which is worse than not existing.

Before touching `checker.py`, `models.py` or any resolver's outcome mapping,
read `tests/test_checker.py` under `# --- the central guarantee ---`. Those
tests exist because both failure modes shipped during development.

## Constraints that are not negotiable

- **No runtime dependencies.** `pyproject.toml` `dependencies = []`, on purpose:
  this gets installed in a hurry near a deadline on a machine someone else
  administers. `defusedxml` is an optional extra and `xmlsafe.py` must keep its
  hardened stdlib fallback working without it.
- **The test suite is offline.** It drives the checker through fake resolvers
  and makes no network call, so CI cannot be flaked by a rate-limited upstream.
  Live tests are the deliberate exception: marked `live`, gated on
  `REFAUDIT_LIVE_EMAIL`, run on a schedule and never on pull requests.
- **Never guess a rate.** A resolver's `RateSpec.rationale` must cite the
  service's own documented limit. Pacing belongs to the *host*, not the
  resolver — two resolvers hitting `api.crossref.org` are one caller to
  Crossref (`resolvers/base.py`, `_HOST_PACING`).
- **Registry order encodes evidence strength.** Identifier lookups before title
  searches, curated indexes before harvested ones. See the docstring in
  `resolvers/__init__.py` before reordering `AVAILABLE`.

## Commands

```bash
pip install -e ".[dev]"
pytest -q                             # offline, must pass
ruff check .
mypy src --ignore-missing-imports
REFAUDIT_LIVE_EMAIL=you@example.com pytest -q -m live   # hits real APIs
```

CI runs the suite on 3.10–3.13 plus ruff and mypy. All four must be green.

## Workflow

`main` is protected: branch, push, open a PR, one approving review. Never
push to `main` directly.

`src/refaudit/_version.py` is the **single source of truth** for the version:
`pyproject.toml` reads it through hatch, `__init__.py` re-exports it, and the
User-Agent sent to every service is built from it. Bump it there and nowhere
else. A `v*` tag then triggers PyPI Trusted Publishing; there is no API token in
this repo. See `CONTRIBUTING.md` for the GitHub settings the release depends on,
and use the `cut-release` skill.

## Where the bugs have been

Rate limiting and retry (`ratelimit.py`, `http.py`), cross-process locking
(`filelock.py`, `cache.py`), and resolvers reading a 200-with-no-match as a
match. Changes in those files deserve the corresponding agent in
`.claude/agents/`.
