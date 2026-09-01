---
name: add-resolver
description: Add a new bibliographic source to refaudit — RateSpec with a cited limit, outcome mapping, identifier handling, registry placement, offline tests, and docs. Use when adding or replacing a resolver in src/refaudit/resolvers/.
---

# Adding a resolver

A resolver knows how to look one entry up in one external index. Adding one
touches seven places, and the ones people forget are the registry entry and the
README line — a resolver that is written but not registered is dead code, and
one that is registered but undocumented is invisible.

Read `src/refaudit/resolvers/base.py` and the registry docstring in
`src/refaudit/resolvers/__init__.py` first. Use an existing resolver of the same
shape as your model: `crossref.py` for a JSON API, `arxiv.py` for XML/Atom,
`dblp.py` for a title search.

## 1. Establish the rate limit before writing code

Use the `research-rate-limit` skill. You need the service's **documented**
limit and a URL, because `RateSpec.rationale` has to cite it and CONTRIBUTING
explicitly asks contributors not to guess. If no limit is published, that fact
plus the conservative number you chose is itself the rationale — say so.

If the service shares a host with an existing resolver, note it: pacing is
per-host, and the most cautious declared rate for that host wins.

## 2. Decide where it belongs in the registry

Placement encodes how much the source's answer is worth:

1. Identifier lookups (a DOI or arXiv ID is a checkable claim).
2. Curated title indexes (DBLP).
3. Harvested title indexes (OpenAlex, Crossref title search).
4. Books, last, and only for books.

Write down the argument for the position you chose. `resolver-reviewer` will
ask.

## 3. Write the resolver

```python
@dataclass(frozen=True)  # or a plain class following the local pattern
class NewSource:
    name = "newsource"
    rate = RateSpec(per_second=..., burst=..., rationale="...cite the doc URL...")
    year_is_authoritative = True  # False if it reports editions/printings
```

- `can_handle(entry)` — return False when this source has nothing to offer.
  A True that leads to a guaranteed miss spends a rate-limit token for nothing.
- `resolve(entry)` — returns `Found` / `NotFound` / `Unavailable`, and **never
  raises for an ordinary network problem**.

Outcome mapping, which is the part that matters:

| Situation | Outcome |
|---|---|
| Record found and parsed | `Found` |
| 200, valid body, **zero results** | `NotFound` |
| 404 on an identifier lookup | `NotFound` |
| 404 where the service should have answered | `Unavailable` |
| 429, 5xx, timeout, connection error | `Unavailable` |
| Malformed body, truncated read, refused XML entity | `Unavailable` |

Parse identifiers through `normalize.py` helpers rather than hand-rolling
string surgery — schemes, versions, prefixes and case all vary in the wild.
Parse XML through `xmlsafe.fromstring`, never raw ElementTree.

## 4. Register it

In `src/refaudit/resolvers/__init__.py`: import it, add it to `__all__`, and add
it to `AVAILABLE` at the position you argued for in step 2.

## 5. Tests — offline, always

Add parse tests covering at minimum: a good response, an **empty result set**,
a malformed body, and each status code you map. Follow `tests/test_resolver_parsing.py`.
Use `capture-fixture` to record a real response body once rather than inventing
one that does not match the service's actual shape.

Add the outcome cases to `tests/test_outcome_classification.py` so the mapping
is locked down where the other resolvers' mappings live.

Nothing you add may make a network call. If you want a live check, mark it
`@pytest.mark.live` and gate it on `REFAUDIT_LIVE_EMAIL`.

## 6. Docs

- README: add the source to the list of what refaudit checks.
- CHANGELOG: an entry describing the new coverage in user terms.

## 7. Verify

```bash
pytest -q && ruff check . && mypy src --ignore-missing-imports
```

Then run the `resolver-reviewer` and `invariant-guard` agents on the diff
before opening the PR.
