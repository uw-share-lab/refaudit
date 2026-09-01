---
name: capture-fixture
description: Record a real API response into a test fixture using a single request, so offline tests match the service's actual response shape. Use when adding resolver parse tests or reproducing a parsing bug.
---

# Capturing a response fixture

Invented fixtures test your idea of a service's response, not the service. Every
resolver parsing bug this project has shipped was invisible to a handwritten
fixture that already assumed the shape the parser expected.

The constraint: these services are metered and some of them (OpenAlex) now
count usage. **One request per fixture.** Get it right the first time.

## 1. Work out the exact URL first

Read the resolver's own code for how it builds the request, rather than
reconstructing it from documentation:

```bash
grep -n "url\|urlencode\|quote" src/refaudit/resolvers/<name>.py
```

Match the real request exactly — same path, same query parameters, same
`Accept` header. A fixture captured from a slightly different URL can differ in
ways that make your test pass while production fails.

## 2. Make the one request

Always send a real contact address; that is the polite-pool convention these
services run on and refaudit always does it.

```bash
curl -sS \
  -H 'Accept: application/json' \
  -A "refaudit/dev (mailto:you@example.com)" \
  -D headers.txt \
  '<exact URL>' -o body.json
```

Keep `headers.txt` — the status line and any rate-limit headers are part of what
you are testing, especially if you are capturing a non-200.

## 3. Trim it, carefully

Full responses can be large. It is fine to cut a result list down to one or two
entries, but **do not** reshape it: keep the envelope, the field names, the
nulls, and the odd values (a missing author, a year as a string, a title with
LaTeX in it). The awkward parts are the reason you captured a real response
instead of writing one.

Never edit values to make a test convenient. If you need a different case,
capture a different record.

## 4. Capture the cases that matter, not just the happy one

The empty-result body is the single most valuable fixture — it is where "200
with zero results" gets misread as a match. Get it with a query that genuinely
matches nothing:

```bash
curl -sS -A "refaudit/dev (mailto:you@example.com)" \
  '<search endpoint>?query=zzzznonexistenttitle'
```

## 5. Check it before committing

- No credentials, tokens, or personal data. Grep for your own email — it will be
  in a `mailto` echo in some responses.
- Valid JSON/XML, and the parser actually consumes it.
- Small enough to read in a diff.

## 6. Use it offline

Add the parse test alongside the others in `tests/test_resolver_parsing.py`.
The test must not make a network call. If you want the live equivalent, that is
a separate `@pytest.mark.live` test gated on `REFAUDIT_LIVE_EMAIL`, which runs
on a schedule and never on pull requests.

## If you are rate-limited mid-capture

Stop. Wait out the window rather than retrying — retrying against a service that
just throttled you is how a short block becomes a long one. Capture the
remaining fixtures in a later session.
