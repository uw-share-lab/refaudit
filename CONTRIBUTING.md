# Contributing

`main` is protected. Nobody pushes to it directly — changes arrive by pull
request with a review from a code owner (see `.github/CODEOWNERS`).

```bash
git checkout -b your-change
# ...
git push -u origin your-change
gh pr create --fill
```

CI runs the test suite on Python 3.10–3.13 plus `ruff` and `mypy`, and must be
green before merge.

## Running the tests

```bash
pip install -e ".[dev]"
pytest -q
```

The suite is **offline by design**: it exercises the checker through fake
resolvers and never makes a network call. This is deliberate — the services this
tool talks to rate-limit aggressively, and a test suite that depended on them
would fail for reasons that have nothing to do with the change under review.

If you add a resolver, add it to `AVAILABLE` in `src/refaudit/resolvers/__init__.py`
and give it a `RateSpec` whose `rationale` cites the service's own documented
limit. Please do not guess a rate.

## The invariant to preserve

**"We could not check" and "this is wrong" must never collapse into each other.**

A resolver returns `Found`, `NotFound` or `Unavailable`, and only `Found` may
produce a negative verdict. `UNVERIFIED` results are reported separately from
findings and are never cached. If you are tempted to simplify this, read the
tests in `tests/test_checker.py` under "the central guarantee" first — they
exist because both failure modes happened during development.

## Releasing

1. Bump `version` in `pyproject.toml` and `__version__` in `src/refaudit/__init__.py`.
2. Merge to `main`.
3. Publish a GitHub release with a tag like `v0.1.1`.

The `release` workflow builds and publishes to PyPI using Trusted Publishing.
There is no API token in this repository; PyPI verifies the workflow's identity
over OIDC, so there is no long-lived credential to leak.

### Repository settings the release depends on

These live in GitHub's settings rather than in this repo, so they are recorded
here — if you fork this, recreate the repository, or wonder why a release was
rejected, start with these.

**`pypi` environment → deployment branch policy must allow the `v*` tag.**
A release workflow is triggered by a tag, not a branch. An environment
restricted to "protected branches only" therefore rejects every release with
`Tag "vX.Y.Z" is not allowed to deploy to pypi due to environment protection
rules`. The policy is set to custom rules with a single `v*` tag pattern:

```bash
gh api -X PUT repos/uw-share-lab/refaudit/environments/pypi \
  -f 'deployment_branch_policy[protected_branches]=false' \
  -f 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/uw-share-lab/refaudit/environments/pypi/deployment-branch-policies \
  -f name='v*' -f type=tag
```

**PyPI trusted publisher.** Registered against project `refaudit`, owner
`uw-share-lab`, repository `refaudit`, workflow `release.yml`, environment
`pypi`. All five must match exactly or PyPI refuses the upload.

**Branch protection on `main`.** Pull request with one code-owner approval,
all four CI checks green, linear history, no force-push or delete. Admins can
bypass, which is intentional; everyone else goes through review.
