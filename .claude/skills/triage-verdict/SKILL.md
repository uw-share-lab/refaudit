---
name: triage-verdict
description: Debug a single bibliography entry that got the wrong verdict, in a fixed order — resolver selection, raw response, normalization, then the match rule. Use when refaudit reports a finding that looks wrong or misses one it should have caught.
---

# Triaging a wrong verdict

Follow the order below. The temptation is to jump to the match thresholds
because they are the easiest thing to change, and doing that has repeatedly
"fixed" a symptom by loosening a rule that was correct — which then hides real
findings across every other entry.

Reproduce the single entry in isolation first. Put it in a one-entry `.bib`:

```bash
refaudit one.bib --email you@example.com -v --no-cache --out ./triage
```

`--no-cache` matters — you may be looking at a stale cached result rather than
current behaviour.

## Step 1 — Which resolvers even ran?

```bash
refaudit one.bib --email you@example.com -v --no-cache 2>&1 | grep -i resolver
```

Check each resolver's `can_handle` decision. An entry with a DOI that no DOI
resolver claimed, or an arXiv preprint that `arxiv:id` declined, is a
`can_handle` bug — and note that `Entry.arxiv_id` deliberately looks in
`journal`, `note`, `howpublished`, `booktitle`, `url` and `doi`, not just
`eprint`, because Scholar exports scatter it.

Narrow to one source to isolate: `--resolvers crossref:doi`.

## Step 2 — What did the service actually return?

Do not infer this. Reproduce the exact request (see the `capture-fixture` skill
for building the URL from the resolver's own code) and look at the body.

Three different things look identical from the outside and need different fixes:
the service returned nothing; the service returned a record that genuinely
differs from the bibliography; the service returned a record we failed to parse.

If the outcome was `UNVERIFIED`, stop here — that is a reachability answer, not
a matching one. Check status codes, rate limiting, and the circuit breaker
before anything else.

## Step 3 — Normalization

Compare the normalized forms, not the raw strings. `src/refaudit/normalize.py`
holds the helpers; run them directly on the two values:

```bash
python -c "from refaudit.normalize import *; print(repr(<fn>('<value>')))"
```

Most surprising mismatches live here:
- LaTeX in titles (`{\"o}`, `\&`, math mode, protective braces).
- Unicode: accents, en/em dashes, curly quotes, non-breaking spaces.
- Subtitles after a colon, present in one source and not the other.
- DOI case and prefix (`https://doi.org/` vs bare, upper vs lower).
- arXiv IDs: scheme, `abs/` path, version suffix.
- Author surnames: particles (`van der`), hyphenation, initials-only records.

## Step 4 — Only now, the match rule

`checker.py::_judge` and `Thresholds`. Compute the actual score for this pair
before touching anything:

- If the score is near the threshold and the entry is genuinely a match, that is
  a **normalization** problem from step 3, not a threshold problem.
- If the score is far off and the entry is a match, the resolver returned the
  wrong record — a title-search collision. Tightening the threshold is right
  here; loosening it never is.
- If it is a year mismatch, check `year_is_authoritative` for that source. An
  index reporting an edition or printing date must not produce year findings.

## Before you change anything

Any fix here needs a test with this entry's real values, and needs the
`invariant-guard` agent run on the diff — most one-entry fixes are threshold or
outcome-mapping changes, which are exactly the changes that affect every other
entry too. Then re-run `soak-test` and confirm you fixed one verdict without
moving forty others.
