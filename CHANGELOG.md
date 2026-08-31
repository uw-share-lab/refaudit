# Changelog

## 0.4.4

No behaviour change. More of the coverage 0.4.3 started, on the two places it
did not reach.

### Added

- **The socket-facing half of the HTTP client is tested.** `_open_once` was the
  least-covered code in `http.py` at 71%: the response size cap, the gzip path,
  and the classification of urllib's exception family. Every other test in the
  suite stubs `_open_once` itself, so none of it ran.

  That classification is the one the whole tool rests on. An `HttpError` can
  become a statement about a reference; a `TransportError` never can, because a
  resolver turns it into `Unavailable`. Getting it wrong is how a network
  problem becomes an accusation. Both directions are now pinned, along with the
  size cap at and over the limit, gzip, `Retry-After` in both its numeric and
  HTTP-date forms, the rate-limit headers Crossref publishes, https-only
  enforcement, and an API key going in a header rather than a URL. `http.py`
  is now at 99%.

- **Both XML backends are held to the same promise.** `xmlsafe` uses
  `defusedxml` when installed and a hardened standard-library parser otherwise.
  Only one runs on a given machine, so the suite silently exercised whichever
  the developer happened to have -- and since `defusedxml` is an *optional*
  extra and the dependency list is empty by design, the untested one was the
  path most installations actually take. The same entity attacks now go through
  both.

Coverage 89% to 92% overall, `http.py` 71% to 99%.

### Notes

- `xmlsafe.py` still reports 63%. Nine of its ten uncovered statements are the
  expat entity-handler block, which cannot execute on any supported Python:
  `xml.etree.ElementTree.XMLParser` no longer exposes a `.parser` attribute, so
  the `if expat is not None` guard is always false. The comment beside it
  already records that those handlers "were silently not applied" and that the
  DTD scan is "the check that actually holds", which the new tests confirm --
  every attack is refused by the scan, on both backends. The block is dead
  rather than untested and is left in place for now rather than removed as a
  side effect of a test release.
- The tenth is the `except ImportError` line, which the new tests do execute;
  coverage cannot attribute it because reaching that branch requires
  re-importing the module.


## 0.4.3

No behaviour change. This is the test coverage that should have existed before
any of 0.4.x was written, plus the packaging metadata and two documentation
claims that had quietly stopped being true.

### Added

- **Every resolver is now tested against bytes the real service sent.** The
  response parsers were the least-covered code in the package, between 27% and
  66%, which is the wrong place to be thin: they are the part most exposed to
  somebody else changing their response shape, and a misparse there does not
  crash, it produces a confident wrong answer about a bibliography.

  `tests/fixtures/responses/` holds captures from Crossref, DataCite, arXiv,
  DBLP, Open Library and the DOI proxy. Nothing is hand-written, and the
  expected values were read out of the raw fixtures independently of refaudit's
  own parsing. Two of the captures are failures, kept on purpose: DBLP answered
  503 with an HTML body and OpenAlex answered with a rate-limit JSON object,
  and both are shapes a parser can mistake for data.

  Every title-search fixture happens to have the *wrong* paper as its first
  hit, which is what these APIs really return. That is now documented by tests:
  a resolver parses its candidate faithfully and the checker decides whether it
  is the same work.

- **The command line is tested end to end.** `main()` was at 22%, an odd place
  to be thin given it is the only part every user touches. Exit status is the
  contract a pre-submission hook keys off, so all three cases are now covered,
  along with argument validation, `--only-cited`, `--quiet`, `-v`, the report
  files, and duplicates being enough on their own to warrant a look.

- **`DoiExistence` is tested for all three of its states.** It decides between
  `DEAD_DOI` and `UNVERIFIED`, so returning "not registered" when the honest
  answer is "could not tell" is the failure that puts a false accusation in
  somebody's report. Every non-404 status and every unrecognised response code
  is now pinned to "could not tell".

Coverage went from 69% to 89%; `cli.py` from 22% to 98%.

### Fixed

- **The README described OpenAlex's limits as they no longer are.** It cited a
  documented ceiling of 10 req/s and 100k/day. A live run was refused with
  `Retry-After: 29895` and a body explaining that the request cost $0.001
  against a remaining balance of $0.0003, resetting at midnight UTC. OpenAlex
  meters against a daily budget rather than a request ceiling, and a free
  caller exhausts it quickly, so the README and the resolver's own rationale
  now say that.

- **Three GitHub Pages actions in `docs.yml` were behind**, missed when the
  other workflows were brought forward in 0.4.2: `configure-pages` v5 to v6,
  `upload-pages-artifact` v3 to v5, `deploy-pages` v4 to v5.

- **PyPI classifiers were three lines long.** Added the supported Python
  versions, the licence, development status, operating system and `Typing ::
  Typed`, so the package can be filtered for on PyPI.


## 0.4.2

### Added

- **An opt-in check against the real services.** The suite is hermetic so CI
  cannot be failed by a busy upstream, which is the right trade but hides
  something: a green run proves the code works against fixtures somebody wrote
  by hand, not that it still parses what Crossref sends. 0.4.1 exists because a
  live run found what 127 hermetic tests could not.

  `tests/test_live.py` checks seven references against Crossref, arXiv,
  DataCite, DBLP, OpenAlex and doi.org, and runs only when
  `REFAUDIT_LIVE_EMAIL` is set. It asserts the property that must never break:
  a correct reference is never reported as a finding. An unreachable source may
  turn any entry `UNVERIFIED`, since that is a fact about the network rather
  than the entry, so an outage does not produce a false failure. One assertion
  requires that something resolved, or a total outage would let the file pass
  while proving nothing.

  It also runs weekly from `.github/workflows/live.yml`, away from pull
  requests, so a bad day upstream costs a notification instead of a blocked
  merge.

### Fixed

- **The cache race is closed rather than narrowed.** 0.4.0 made `flush()` merge
  what was on disk instead of overwriting it, which stopped two runs erasing
  each other wholesale. A smaller window survived: another run could finish an
  entire flush between our read and our replace, and be overwritten by it. The
  read, the merge and the replace now happen under an advisory lock held on a
  `.lock` file beside the cache — beside it, because locking the cache itself
  would be undone by the atomic replace that swaps the file out.

  The lock is best effort. Where a filesystem will not honour it, some network
  mounts among them, the merge still runs and the worst case is the old, small
  race. Refusing to write at all would be a worse outcome than the problem
  being fixed. `fcntl` and `msvcrt` both come from the standard library, so
  this remains a zero-dependency package.

- **Workflow actions no longer run on a deprecated Node.** `upload-artifact`
  and `download-artifact` were pinned to versions that GitHub was force-running
  on Node 24 while warning about Node 20. Bumped to v7 and v8, with `checkout`
  and `setup-python` brought to v7 alongside them.

- **`configure-repo.sh` no longer reverts branch protection.** It defaulted
  `CODE_OWNER_REVIEWS` to true, so re-running it would have silently restored a
  rule that makes every pull request from the sole code owner unmergeable
  except by admin override. It now defaults to false and says why, and the
  README and CONTRIBUTING no longer describe a code-owner review that is not
  required.


## 0.4.1

### Fixed

- **A service that asks us to come back in hours is now believed.** Found by
  running 0.4.0 against the live APIs: OpenAlex answered `429` with
  `Retry-After: 29895` -- 8.3 hours -- and the client capped its sleep at 60
  seconds and retried anyway, four times, then repeated the whole thing for
  every later entry that reached OpenAlex. Roughly three minutes of dead time
  per entry, spent asking a service that had already said no.

  The circuit breaker could not help, because those 60-second sleeps are longer
  than its own 120-second cooldown: it half-opened between entries and never
  engaged, logging zero trips across eight consecutive refusals.

  A `Retry-After` longer than `MAX_RETRY_AFTER` (60s, the same cap a single
  backoff already used) is now treated as an answer rather than a delay. The
  request fails immediately and the host is stood down for the period it asked
  for, capped at an hour, so the rest of the run skips it instead of
  rediscovering the same refusal. Entries that needed that source come back
  `UNVERIFIED` -- no verdict rather than a wrong one. In the observed case this
  turns 12 requests and about nine minutes of sleeping into one request and no
  waiting.

  `CircuitBreaker` gained `open_for(seconds)` for this, and now tracks when it
  may next be probed rather than when it opened.


## 0.4.0

### Added

- **`-v` / `--verbose`, and a real logger.** The package now logs through the
  standard `logging` module under the `refaudit` name: one `DEBUG` line per
  request, and `WARNING` for the things worth acting on -- rate-limit penalties,
  retries, and a circuit breaker giving up on a host. Previously the only output
  was a progress line per entry, so a run that came back with forty `UNVERIFIED`
  entries gave you no way to see which service had refused you or why; the reason
  was captured inside an `Unavailable` and went nowhere. Diagnostics go to stderr,
  the report to stdout, so `refaudit ... -v 2> refaudit.log` separates them.

  Importing refaudit as a library still configures nothing and prints nothing --
  it installs a `NullHandler` and leaves level and destination to the host
  application.

- **`py.typed`.** The package has always been typed and checked with mypy, but
  shipped no PEP 561 marker, so downstream users got none of it.

### Fixed

- **A throttled run can now speed back up.** The token bucket could only ever
  slow down: every `429` halved it and nothing ever raised it again. A burst of
  refusals early in a run -- roughly six halvings before the circuit breaker
  intervenes -- left Crossref's 2/s at about 0.03/s, and the remaining entries
  crawled at that pace for the rest of the process. On a few hundred references
  that is hours, and it looks like a hang rather than a slowdown.

  Decrease stays multiplicative, because that is what makes backoff safe, but it
  is now a penalty the run works off: each success edges the rate back toward the
  host's ceiling by a twentieth, and a penalty never drops below a sixteenth of
  it. Rates a service publishes in its own headers are treated as a ceiling
  rather than a dip, so recovery never climbs past what the service asked for.

- **Redirects are paced.** The token bucket was drawn from once per attempt, but
  an attempt could follow up to three redirect hops before returning, so a
  redirecting endpoint sent four requests on the strength of one token and
  briefly ran at four times the documented rate. Every hop now pays the bucket,
  because every hop is a real request to a real server.

- **A retry now re-requests the URL that was asked for.** The redirect target
  was carried across attempts, so once a hop had been followed every later
  attempt started from wherever the redirect had pointed -- and if that redirect
  was itself transient, a load balancer bouncing us or a maintenance page, the
  thing actually wanted was never requested again. It also handed each attempt a
  fresh hop budget, so a chain could walk `max_attempts x MAX_REDIRECTS` hops
  from the original URL rather than `MAX_REDIRECTS`, weakening the bound that
  keeps a redirect from taking us -- and the `mailto` identifier we send --
  somewhere unexpected.

- **A redirect chain we cannot follow is now a definitive answer.** Exhausting
  the hop limit raised a `302`, which is not a `4xx`, so it fell through to the
  retry branch: four attempts re-walked the identical chain for sixteen requests
  where four would do, and four `record_failure` calls opened the circuit
  breaker on a host that had answered every single time. Because the breaker is
  shared per host, that backed off every resolver calling it. It now raises
  `TooManyRedirects` -- a `TransportError` subclass, so resolvers still report
  `Unavailable` and the entry stays `UNVERIFIED` rather than becoming a finding
  -- and is neither retried nor counted against the host. This also removes a
  line of genuinely unreachable code (`raise TransportError("too many
  redirects")`), which the hop loop could never arrive at.

- **Two runs sharing a cache file no longer erase each other.** `flush()` wrote
  the whole cache from memory loaded at startup, so of two runs in the same
  directory -- two terminals, a shared lab machine, a cluster job array -- the
  second to finish replaced the first one's entries with its own. Writes were
  always atomic, so nothing ever corrupted; the entries simply vanished and got
  re-fetched from services we are trying to be polite to. Flush now merges what
  is on disk, with the newer timestamp winning a conflict.

- **Corrected the rate-limit table in the README**, which listed DataCite and
  doi.org twice with conflicting figures and gave doi.org as 5/s. Every rate
  there now matches the `RateSpec` the code declares. doi.org is 2/s.


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
