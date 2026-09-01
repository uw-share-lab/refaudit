---
name: soak-test
description: Run refaudit against a large real bibliography and diff the verdicts against a stored baseline to catch regressions the offline suite cannot. Use before a release or after changing resolvers, matching, or pacing.
---

# Soak test

The unit suite is offline by design, which means it cannot catch a resolver that
stopped matching real records, a service that changed its response shape, or a
pacing change that quietly turns half a run into `UNVERIFIED`. The soak does.

It is a **regression** test, not a correctness test: the question is *what
changed since the last run*, not *is every verdict right*.

## Inputs

A real bibliography of a few hundred entries. Keep it **outside this repo** —
it is someone's manuscript, it is large, and it does not belong in git. Pass its
path explicitly; do not add it to the repo to make the command shorter.

You also need a baseline: the verdicts from the last known-good version.
Baselines live outside the repo alongside the corpus.

## Run it

Always record which version produced the results. Comparing a soak from one
version against a baseline from another is the mistake that has wasted a full
run here — the diff is real but it tells you nothing about your change.

```bash
refaudit --version                       # write this down
refaudit /path/to/corpus.bib \
  --email you@example.com \
  --out ./soak-$(refaudit --version | tr ' ' '-') \
  --no-cache
```

`--no-cache` is deliberate: a warm cache turns the soak into a test of the cache
rather than of the resolvers. Expect it to take a while — the run is
rate-limited on purpose and that is not a bug.

If you are soaking a pacing change specifically, run it a second time *with* the
cache to confirm the cached path agrees.

## Compare

Diff verdict-by-key against the baseline. What matters:

- **A verdict that moved from `OK` to a finding.** Either a real regression or a
  record that genuinely changed upstream. Investigate every one with the
  `triage-verdict` skill — this is the class that damages user trust.
- **A verdict that moved to `UNVERIFIED`.** Usually pacing or an outage, not
  your change. Re-run the affected entries alone before concluding anything;
  a service having a bad afternoon looks exactly like a regression.
- **A verdict that moved from a finding to `OK`.** Often the fix you intended.
  Confirm it is, rather than the invariant being weakened so nothing gets
  reported.
- **`NOT_FOUND` ↔ `UNVERIFIED` movement.** Take this seriously regardless of
  direction: it is the central invariant's boundary, and movement means an
  outcome mapping changed.

Aggregate counts per verdict are the first look; the per-key diff is where the
answer is. A run where the totals match but forty keys swapped verdicts is a
worse result than one with a small net change.

## Record the result

Note the version, the date, the corpus, the per-verdict totals, and any key that
moved with its explanation. If the run is clean, it becomes the new baseline. If
it is not, it does not — never promote a baseline you have not explained.
