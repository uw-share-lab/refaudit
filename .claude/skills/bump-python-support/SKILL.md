---
name: bump-python-support
description: Add or drop a Python version across the five places that must agree — requires-python, classifiers, the CI matrix, mypy's python_version and ruff's target-version. Use when changing which Python versions refaudit supports.
---

# Changing supported Python versions

Five settings encode this, in three files. Updating three of them and shipping
is the standard outcome, so work the list.

## The five places

| What | Where |
|---|---|
| `requires-python` | `pyproject.toml` `[project]` |
| `Programming Language :: Python :: 3.x` classifiers | `pyproject.toml` `[project] classifiers` |
| CI test matrix | `.github/workflows/ci.yml` → `strategy.matrix.python-version` |
| `python_version` | `pyproject.toml` `[tool.mypy]` |
| `target-version` | `pyproject.toml` `[tool.ruff]` |

Check them all at once:

```bash
grep -n 'requires-python\|Python :: 3\|python_version\|target-version' pyproject.toml
grep -n 'python-version' .github/workflows/ci.yml
```

Also check `.github/workflows/docs.yml`, `live.yml` and `release.yml` — they pin
a Python version too, and a version dropped from support should not still be
building your docs.

## Adding a newer version

1. Add it to the CI matrix **first** and push. Let CI tell you whether the code
   actually works there before you promise it does — a new release often breaks
   something in `urllib`, `ssl`, or a deprecation you are relying on.
2. Only once it is green: add the classifier. Leave `requires-python`,
   `python_version` and `target-version` alone — those track the **oldest**
   supported version, not the newest.
3. CHANGELOG entry.

## Dropping an older version

This is a compatibility break for anyone on it, so it is a minor bump with a
loud CHANGELOG entry, not a patch.

1. Raise `requires-python` to the new minimum. This is what stops pip from
   installing the new release on an unsupported interpreter — without it, users
   get a confusing runtime error instead of a clean resolution failure.
2. Remove the classifier.
3. Remove it from the CI matrix.
4. Raise `[tool.mypy] python_version` and `[tool.ruff] target-version` to the
   new minimum. Forgetting these means both tools keep checking against syntax
   and stdlib you are no longer supporting, so they will not tell you when you
   start using newer features — you lose the guard rail silently.
5. Now you may use newer syntax. Do that in a separate commit from the support
   change so the drop is reviewable on its own.

## Verify

```bash
pytest -q && ruff check . && mypy src --ignore-missing-imports
python -m build && python -m twine check dist/*
```

`twine check` confirms the metadata is coherent. Then let CI run the full matrix
— your local interpreter only tests one row of it.
