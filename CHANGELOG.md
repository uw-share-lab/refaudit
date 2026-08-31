# Changelog

## 0.3.2

### Fixed

- **arXiv identifiers are now found wherever the entry keeps them.** BibTeX
  exported from Google Scholar writes `journal={arXiv preprint arXiv:2506.08872}`
  rather than filling in `eprint`, and refaudit only read `eprint`. Real,
  findable preprints were therefore reported `NOT_FOUND` -- on the most common
  shape of entry there is for recent work. The identifier is now also read from
  `journal`, `note`, `howpublished`, `booktitle`, `url` and the
  `10.48550/arXiv.*` DOI form. An explicit `eprint` still wins.

  Duplicate detection inherits this, so the same preprint cited once with an
  `eprint` and once with a Scholar-style `journal` string is now recognised as
  one work.


## 0.3.1

### Fixed

- **Rate limits are now enforced per host, not per resolver.** Two resolvers
  calling the same service each held their own token bucket, so the pace we
  actually sent was the sum of their declared rates: `api.crossref.org` was
  being called at 4/s rather than 2/s, and `doi.org` at up to 7/s. Resolvers on
  the same host now share one bucket and one circuit breaker, and the most
  cautious rate any of them declares wins. A host that starts refusing us now
  backs off every resolver that calls it, rather than only the one that noticed.


## 0.3.0

Robustness pass: no single source is load-bearing any more, and the same work
cited twice is now caught.

### Added

- **Three more sources, so no service is a single point of failure.**
  `doi:content` negotiates metadata via `doi.org` and therefore works for *any*
  registration agency, including ones we do not query directly. `dblp` is
  hand-curated for computer science, free and unmetered, and usually returns the
  DOI as well. `openlibrary` covers monographs, which no article index holds.
  A full run with OpenAlex removed entirely now resolves *more* entries than
  0.2.0 did with it.
- **Duplicate detection.** The same work under two keys is invisible per-entry —
  both copies resolve and report `OK`. Matching on DOI, arXiv ID (including the
  arXiv DOI form of the same ID) and near-identical titles found two real
  duplicates in the bibliography that prompted it. Runs offline, so it works
  when every network source is refusing us. `--no-duplicates` opts out.
- **`--workers`** checks entries in parallel, about 3x faster on a 124-entry
  bibliography. Politeness does not depend on it: each service keeps its own
  token bucket, shared across threads.

### Fixed

- **A weak title hit no longer ends the search.** The first index to return
  anything at all masked better answers from the next — a real book stayed
  `NOT_FOUND` behind an empty Crossref candidate. Title searches now yield to a
  better match; identifier lookups still stop the search, because an identifier
  resolving to a different paper *is* the finding.
- **`Cache` was not thread-safe.** `flush()` runs periodically during a run, so
  under `--workers` it serialised a dict other threads were writing to.
- Open Library is no longer restricted by entry type — monographs are routinely
  filed as `@article`, which left real books reported as missing — and its year
  is not used as evidence, since it reports the earliest edition rather than the
  work being cited.


## 0.2.0

Found by running 0.1.0 against a real 124-entry bibliography.

### Fixed

- **26 live references were reported `DEAD_DOI`.** 22 of them were arXiv DOIs.
  No registration agency speaks for the whole DOI system — Crossref registers
  most published literature, DataCite registers preprints and deposits,
  including every `10.48550/*` arXiv DOI — so Crossref's 404 was never evidence
  that a DOI did not exist. `DEAD_DOI` now requires that every agency disowns
  the DOI *and* that `doi.org`, which answers for all of them, reports it
  unregistered. A live-but-unindexed DOI is `UNVERIFIED`; an unreachable
  `doi.org` is also `UNVERIFIED`. Neither is a finding.
- **A DOI written as `doi.org/10.x`, with no scheme, was silently discarded**,
  downgrading the entry to a title search that still produced a verdict — in one
  case a wrong `AUTHOR_MISMATCH`. Scheme, `www.` and `dx.` prefixes are now all
  optional.
- `@phdthesis` and `@mastersthesis` with no identifier are `SKIPPED` rather than
  `NOT_FOUND`. Universities rarely register DOIs, so absence proved nothing.

### Added

- `datacite:doi` resolver, so preprints are positively verified against title
  and author rather than merely excused.

### Changed

- The version has one source (`_version.py`) and the User-Agent reads from it,
  so what we report to the services we rate-limit against cannot go stale.

## 0.1.0

Initial release.
