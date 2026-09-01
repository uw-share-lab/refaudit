---
name: cut-release
description: Cut a refaudit release end to end — version bump, changelog, PR, tag, and PyPI publish via Trusted Publishing. Use when releasing a new version of refaudit.
---

# Cutting a release

A version number is spent the moment it uploads to PyPI; there is no
unpublishing. Everything verifiable happens before the tag.

## 1. Pre-flight

Run the `release-captain` agent on the current `main`. Do not proceed past a
blocking finding. At minimum it confirms: the tree is clean, CI is green on the
commit you are about to tag, and the version you intend is not already taken.

## 2. Bump the version

`src/refaudit/_version.py` is the single source of truth. Edit it and nothing
else:

```bash
$EDITOR src/refaudit/_version.py    # __version__ = "X.Y.Z"
```

`pyproject.toml` reads it through hatch, `__init__.py` re-exports it, and the
User-Agent is built from it — that indirection is deliberate, because a
hardcoded User-Agent silently misreports us to the services whose rate limits we
are trying to respect. Confirm the wiring is intact rather than editing it:

```bash
grep -n 'tool.hatch.version' -A 1 pyproject.toml
grep -n '_version import' src/refaudit/__init__.py
python -c "import refaudit; print(refaudit.__version__)"
```

Version choice: patch for fixes, minor for a new resolver or flag. The project
is `Development Status :: 4 - Beta`, so a breaking CLI change is a minor bump
with a loud CHANGELOG entry, not a major.

## 3. Changelog

Add a dated entry to `CHANGELOG.md` describing what a **user** would notice.
"Fixed a redirect that skipped rate-limit accounting, which could get you
throttled on large bibliographies" — not "refactor http.py".

## 4. Build and check locally

`build` and `twine` are release-time tools and are deliberately **not** in the
`dev` extra, so install them if this is a fresh environment:

```bash
pip install build twine
rm -rf dist/ && python -m build && python -m twine check dist/*
python -m zipfile -l dist/*.whl | grep py.typed    # PEP 561 marker must ship
```

`twine check` is what catches a README that will not render on PyPI.

## 5. PR

`main` is protected — one approving review, no direct pushes.

```bash
git checkout -b release-vX.Y.Z
git commit -am "Release vX.Y.Z"
git push -u origin release-vX.Y.Z
gh pr create --fill
```

Wait for all four CI checks (pytest 3.10–3.13, ruff, mypy) and the review.
Merge.

## 6. Tag and publish

```bash
git checkout main && git pull
git tag vX.Y.Z && git push origin vX.Y.Z
gh release create vX.Y.Z --generate-notes
```

The `release` workflow builds and uploads over OIDC Trusted Publishing. There
is no API token in this repository, so there is nothing to rotate and nothing
to leak.

Watch it: `gh run watch`.

## 7. Verify what actually landed

Use the `verify-published` skill. Do not skip this — a release that builds is
not the same as a release that installs and runs, and a version mismatch
between the tag and the artifact has cost debugging time here before.

## If the release workflow fails

The usual cause is a repository setting rather than the code. `CONTRIBUTING.md`
records them, and `repo-settings-audit` checks them. The common one:

> `Tag "vX.Y.Z" is not allowed to deploy to pypi due to environment protection rules`

means the `pypi` environment's deployment policy does not allow the `v*` tag
pattern. A release is triggered by a tag, not a branch, so an environment set to
"protected branches only" rejects every release. The fixing `gh api` calls are
in CONTRIBUTING.

A failed upload does **not** free the version number if any file already
uploaded. If PyPI has partially accepted the release, bump to the next patch
rather than fighting it.
