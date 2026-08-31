# refcheck

Verify the entries in a `.bib` against Crossref, arXiv and OpenAlex, and report
the ones a human needs to look at.

Written for venues that run automated checks for hallucinated or malformed
references. The failure it is built to catch is not a missing DOI — it is a DOI
that resolves to **a different paper than the entry claims**. That one is
invisible when you read the reference list, because the title and authors look
fine and only the identifier is wrong.

```
pip install refcheck

refcheck refs.bib --email you@university.edu
refcheck refs.bib --email you@university.edu --tex paper/sections --only-cited
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

## Library use

```python
from refcheck import Checker, default_resolvers, parse_file

entries = parse_file("refs.bib")
checker = Checker(default_resolvers("you@university.edu"))

for result in checker.check_all(entries):
    if result.verdict.is_finding:
        print(result.key, result.verdict.value, result.note)
```

`Resolver` is a `Protocol`: implement `name`, `rate`, `can_handle` and `resolve`
to add a source, and pass it to `Checker` alongside the built-ins.

## Development

```
pip install -e ".[dev]"
pytest          # offline: the suite uses fake resolvers and never hits a network
ruff check .
mypy src
```

## Licence

MIT.
