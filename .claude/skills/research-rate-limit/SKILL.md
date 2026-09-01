---
name: research-rate-limit
description: Find and cite a service's documented rate limit before writing a RateSpec. Use when adding a resolver or changing any per_second value in src/refaudit/resolvers/.
---

# Establishing a rate limit

CONTRIBUTING says: *"give it a `RateSpec` whose `rationale` cites the service's
own documented limit. Please do not guess a rate."* This skill is how you
comply.

A guessed rate is either too fast — and earns a ban that makes refaudit report
`UNVERIFIED` for everything — or too slow, and wastes the user's afternoon. It
also strips a future maintainer of any basis for changing it, because there is
nothing recorded to re-evaluate.

## Where to look, in order

1. **The service's API documentation**, specifically a rate-limit or etiquette
   section. Crossref, DataCite, OpenAlex, arXiv and DBLP all publish one.
2. **Response headers on a real request.** Many services send
   `X-Rate-Limit-Limit` / `X-Rate-Limit-Interval` or similar. One request tells
   you the live number:
   ```bash
   curl -sI -A "refaudit/dev (mailto:you@example.com)" '<endpoint>' | grep -i 'rate\|retry'
   ```
   Note that `http.py::_observe_rate_headers` already reads these at runtime to
   *lower* our rate — but it may only lower, never raise us above what the
   `RateSpec` declares, so you still need the documented number.
3. **The "polite pool" convention.** Crossref and others give better limits to
   callers who send a contact address. refaudit always does; check whether the
   documented number you found is the polite one or the anonymous one.
4. **If nothing is published**, choose conservatively — 1 request/second or
   slower — and make the rationale say explicitly that no limit is documented
   and this is a self-imposed floor. That is an honest rationale; a fabricated
   citation is not.

## Then check the host

Pacing keys on the host, not the resolver (`resolvers/base.py`, `_HOST_PACING`).
If another resolver already talks to this host, they share one bucket and the
most cautious rate wins. Note in the rationale which resolvers share it, so
nobody later "fixes" an apparently-slow rate without seeing why.

## Write it

```python
rate = RateSpec(
    per_second=<number>,
    burst=<number>,
    rationale="<Service> documents <limit> for callers sending a contact "
              "address: <URL>. Shared with <other resolver> on <host>.",
)
```

`burst` is how much idle credit may accumulate. Keep it at or below one second
of traffic unless the service explicitly permits bursting — a large burst is
how you send twenty requests in a moment and get throttled despite an average
rate that looks polite.

## Record what you found

If the documented limit was hard to find, or the service's wording is ambiguous,
put the URL and the quoted sentence in the rationale rather than a paraphrase.
The next person to touch this number should not have to redo the search.
