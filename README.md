# refaudit

Verify the entries in a `.bib` against Crossref, arXiv and OpenAlex, and report
the ones a human needs to look at.

Written for venues that run automated checks for hallucinated or malformed
references. The failure it is built to catch is not a missing DOI — it is a DOI
that resolves to **a different paper than the entry claims**. That one is
invisible when you read the reference list, because the title and authors look
fine and only the identifier is wrong.

## Install

```bash
pip install refaudit
```

Python 3.10+. No runtime dependencies.

Latest development version:

```bash
pip install git+https://github.com/uw-share-lab/refaudit.git
```

## Quick start

It reads the whole `.bib` and checks **every** entry in it — you point it at the
file and it works through the lot, one entry at a time.

```bash
# every entry in the file
refaudit refs.bib --email you@uwaterloo.ca

# only the entries actually cited in the paper, which is usually what you want
refaudit refs.bib --email you@uwaterloo.ca --tex paper/sections --only-cited
```

`--email` is required. Crossref and OpenAlex give identified callers a separate,
more reliable request pool, and it is the courtesy their documentation asks for.
Set it once instead of typing it each time:

```bash
export REFAUDIT_EMAIL=you@uwaterloo.ca
```

Results are written to `refaudit-out/` as `reference_check.txt` (readable) and
`reference_check.csv` (sortable), and printed to stdout.

### Working from Overleaf

Download the `.bib` (Menu → Download → Source, or just the file), then point
`--tex` at the unzipped `sections/` directory so `--only-cited` can tell which
keys actually reach the PDF:

```bash
refaudit sample-base.bib --email you@uwaterloo.ca --tex sections/ --only-cited
```

## What it reports

| Verdict | Meaning |
|---|---|
| `TITLE_MISMATCH` | an identifier resolved to a different paper — check first |
| `DEAD_DOI` | the DOI is not registered |
| `AUTHOR_MISMATCH` | titles agree, first author does not |
| `YEAR_MISMATCH` | titles agree, year is off by more than one |
| `NOT_FOUND` | no identifier, and no title match anywhere |
| `UNVERIFIED` | **a source was unreachable — this says nothing about the entry** |
| `SKIPPED` | `@misc`/`@online` with no identifier; nothing to check against |
| `OK` | resolved and consistent |

Exit status is `1` if there is at least one finding, `0` if not, `2` on a usage
error — so it drops into CI or a pre-submission script.

## Options

| Flag | Effect |
|---|---|
| `--email` | contact address sent to the APIs (or `REFAUDIT_EMAIL`). Required. |
| `--tex PATH` | LaTeX file or directory, used to work out which keys are cited |
| `--only-cited` | check only cited keys; requires `--tex` |
| `--resolvers` | comma-separated subset of `crossref:doi`, `arxiv:id`, `openalex`, `crossref:title` |
| `--out DIR` | output directory (default `refaudit-out`) |
| `--cache PATH` / `--no-cache` | cache location, or disable it |
| `--ttl-days N` | how long cached results stay valid (default 90) |
| `--timeout N` | per-request timeout in seconds (default 20) |
| `--title-match N` | similarity at or above which two titles are the same work (default 0.75) |
| `--quiet` | suppress per-entry progress, print only the summary |

A run over a few hundred references takes minutes, because it is deliberately
paced. Successful lookups are cached, so it is safe to interrupt with Ctrl-C and
re-run — it picks up where it stopped.

## The one design rule

**"We could not check" and "this is wrong" are different answers and never
collapse into each other.**

This sounds obvious and is easy to get wrong. arXiv rate-limits whole networks;
when that happens, a naive checker either silently passes the entry (false
comfort, the worse failure) or falls back to a title search, finds something
loosely related, and reports a mismatch (false alarm, which trains you to ignore
it). Both are worse than saying "I could not check this one."

So every resolver returns exactly one of `Found`, `NotFound`, or `Unavailable`,
and only `Found` can produce a negative verdict. `UNVERIFIED` results are listed
separately from findings and are never cached, so a transient outage does not
get baked into later runs.

Relatedly, evidence is weighted by strength: a DOI that Crossref does not
recognise is a finding, but a title search returning something different is only
a finding when there was no identifier to go on. Otherwise every arXiv-only
workshop paper that Crossref does not index would be flagged.

## Rate limiting

Each source declares the limit its own documentation specifies, next to the code
that calls it:

| Source | Rate used | Why |
|---|---|---|
| Crossref | 2/s, then whatever the response headers say | Crossref publishes `X-Rate-Limit-Limit` / `-Interval`; the client reads and obeys them |
| arXiv | 1 per 3s | [arXiv's terms of use](https://info.arxiv.org/help/api/tou.html) specify exactly this |
| OpenAlex | 3/s | [documented](https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication) ceiling is 10/s and 100k/day; we use a third of it |

A `429` is treated as instruction rather than noise: `Retry-After` is honoured,
the token bucket is permanently halved, and after repeated refusals a circuit
breaker stops asking that host so the rest of the run still finishes. Retries use
exponential backoff with full jitter.

`--email` is required because Crossref and OpenAlex give identified callers a
separate, more reliable pool, and it is the courtesy their docs ask for.

## Security

- **HTTPS only.** Plain-`http` URLs are refused, not silently upgraded.
- **Bounded redirects**, kept on https, so a redirect cannot downgrade transport
  or forward the `mailto` identifier somewhere unexpected.
- **Response size cap and per-request timeouts** on every call.
- **XML is parsed with entity declarations refused**, blocking billion-laughs and
  XXE. Uses `defusedxml` when installed, otherwise a hardened stdlib path; both
  raise the same exception type so callers cannot miss one.
- **DOIs and arXiv ids are validated against a pattern before being interpolated
  into a request path**, so a malformed field cannot steer the URL.
- **No credentials in code.** Optional API keys come from the environment and are
  sent as headers, never query parameters, so they stay out of logs.
- **No runtime dependencies.** This gets installed in a hurry near a deadline,
  often on a machine someone else administers; that is the wrong moment to widen
  the supply chain.

## Troubleshooting

**Lots of `UNVERIFIED` results.** A source refused your network. arXiv in
particular rate-limits by IP and will 429 an entire institution or VPN
regardless of your own pace. These are not findings — the entries were simply
not checked. Try again later, from a different network, or lean on OpenAlex,
which indexes arXiv identifiers too:

```bash
refaudit refs.bib --email you@uwaterloo.ca --resolvers crossref:doi,openalex
```

**A correct entry is flagged `NOT_FOUND`.** Workshop papers, theses and
tech reports are often in no citation index. If the entry has no DOI and no
arXiv id there is nothing to verify it against; confirm it by hand and move on.

**A correct entry is flagged `TITLE_MISMATCH`.** This one is worth taking
seriously: it means the DOI or arXiv id in your `.bib` resolves to a different
paper. Usually the identifier was copied from the wrong row, or generated rather
than looked up. Check the `found` line in the report against what you meant to
cite.

**Everything is `SKIPPED`.** `@misc` and `@online` entries with no identifier
cannot be checked. That is expected for datasets, blog posts and software.

## API reference

Generated from the docstrings and published on every push to `main`:
**https://uw-share-lab.github.io/refaudit/refaudit.html**

Build it locally with `pdoc` (included in the `dev` extra):

```bash
pdoc refaudit refaudit.checker refaudit.models refaudit.resolvers -o site --docformat google
```

## Library use

```python
from refaudit import Checker, default_resolvers, parse_file

entries = parse_file("refs.bib")
checker = Checker(default_resolvers("you@university.edu"))

for result in checker.check_all(entries):
    if result.verdict.is_finding:
        print(result.key, result.verdict.value, result.note)
```

`Resolver` is a `Protocol`: implement `name`, `rate`, `can_handle` and `resolve`
to add a source, and pass it to `Checker` alongside the built-ins.

## Development

```bash
pip install -e ".[dev]"
pytest          # offline: the suite uses fake resolvers and never hits a network
ruff check .
mypy src
```

`main` is protected: changes go through a pull request with a code-owner review,
and CI must pass on Python 3.10-3.13. See [CONTRIBUTING.md](CONTRIBUTING.md).

Repository settings that releases depend on are kept as code in
[`.github/scripts/configure-repo.sh`](.github/scripts/configure-repo.sh) and are
safe to re-run.

## Licence

MIT.
