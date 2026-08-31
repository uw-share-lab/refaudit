# Changelog

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
