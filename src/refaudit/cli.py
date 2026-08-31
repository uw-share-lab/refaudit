"""Command line interface."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .bibtex import cited_keys, find_tex, parse_file
from .cache import Cache
from .checker import Checker, Thresholds
from .doi_registry import DoiExistence
from .duplicates import Duplicate, find_duplicates
from .models import Verdict
from .resolvers import AVAILABLE, default_resolvers

EPILOG = """\
examples:
  refaudit refs.bib --email you@uni.edu
  refaudit refs.bib --email you@uni.edu --tex paper/sections --only-cited
  refaudit refs.bib --email you@uni.edu --resolvers crossref:doi,openalex

exit status:
  0  no findings
  1  at least one entry needs a human look
  2  usage or input error
"""


#: Marks the handler we own, so repeated calls replace it rather than stacking
#: another copy of every line on top of the last.
_HANDLER_TAG = "refaudit-cli"


def configure_logging(verbose: bool = False) -> None:
    """Point the package logger at stderr.

    Only the CLI calls this. Importing refaudit as a library leaves logging
    entirely to the host application, which is why the package itself installs
    nothing but a NullHandler.

    Reports go to stdout and diagnostics to stderr, so ``refaudit ... > out.txt``
    still shows you what the network is doing.
    """
    log = logging.getLogger("refaudit")
    for existing in [h for h in log.handlers if getattr(h, "name", None) == _HANDLER_TAG]:
        log.removeHandler(existing)

    handler = logging.StreamHandler(sys.stderr)
    handler.name = _HANDLER_TAG
    handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.DEBUG if verbose else logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="refaudit",
        description="Verify .bib entries against Crossref, arXiv and OpenAlex.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("bib", type=Path, help="path to the .bib file")
    p.add_argument("--email", default=os.environ.get("REFAUDIT_EMAIL", ""),
                   help="contact address sent to the APIs (or set REFAUDIT_EMAIL). "
                        "Crossref and OpenAlex give identified callers a better pool.")
    p.add_argument("--tex", type=Path,
                   help="directory or file of LaTeX sources, to determine which keys are cited")
    p.add_argument("--only-cited", action="store_true",
                   help="check only keys cited in --tex (uncited entries never reach the PDF)")
    p.add_argument("--resolvers", default="",
                   help=f"comma-separated subset of: {', '.join(AVAILABLE)}")
    p.add_argument("--out", type=Path, default=Path("refaudit-out"),
                   help="output directory (default: refaudit-out)")
    p.add_argument("--cache", type=Path, help="cache file (default: <out>/cache.json)")
    p.add_argument("--no-cache", action="store_true", help="ignore and do not write the cache")
    p.add_argument("--ttl-days", type=float, default=90.0, help="cache lifetime (default: 90)")
    p.add_argument("--timeout", type=float, default=20.0, help="per-request timeout seconds")
    p.add_argument("--title-match", type=float, default=Thresholds.title_match,
                   help="similarity at or above which two titles are the same work")
    p.add_argument("--workers", type=int, default=4,
                   help="entries checked in parallel (default: 4). Each service "
                        "keeps its own rate limit regardless, so this recovers "
                        "time lost to latency rather than going faster than a "
                        "service allows")
    p.add_argument("--no-duplicates", action="store_true",
                   help="skip the offline duplicate-entry pass")
    p.add_argument("--quiet", action="store_true", help="only print the summary")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="log every request, retry and rate-limit change to stderr. "
                        "Use this when entries come back UNVERIFIED and you want "
                        "to see which service was unreachable and why")
    p.add_argument("--version", action="version", version=f"refaudit {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)

    if not args.bib.is_file():
        print(f"error: no such file: {args.bib}", file=sys.stderr)
        return 2
    if not args.email:
        print("error: --email is required (or set REFAUDIT_EMAIL).\n"
              "       Crossref and OpenAlex ask callers to identify themselves, and\n"
              "       doing so puts you in a more reliable request pool.", file=sys.stderr)
        return 2
    if args.only_cited and not args.tex:
        print("error: --only-cited requires --tex", file=sys.stderr)
        return 2

    entries = parse_file(args.bib)
    if not entries:
        print(f"error: no entries parsed from {args.bib}", file=sys.stderr)
        return 2

    cited: set[str] | None = None
    if args.tex:
        cited = cited_keys(find_tex(args.tex))
        if args.only_cited:
            entries = [e for e in entries if e.key in cited]
            if not entries:
                print("error: no cited entries found; is --tex pointing at the right place?",
                      file=sys.stderr)
                return 2

    only = [r.strip() for r in args.resolvers.split(",") if r.strip()] or None
    try:
        resolvers = default_resolvers(args.email, only=only, timeout=args.timeout)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    cache = None if args.no_cache else Cache(args.cache or (args.out / "cache.json"),
                                             ttl_days=args.ttl_days)
    checker = Checker(resolvers, cache=cache,
                      thresholds=Thresholds(title_match=args.title_match),
                      doi_existence=DoiExistence(contact_email=args.email,
                                                 timeout=args.timeout))

    results = []
    try:
        for i, result in enumerate(
                checker.check_all(entries, cited=cited,
                                  workers=max(1, args.workers)), 1):
            results.append(result)
            if not args.quiet:
                print(f"[{i}/{len(entries)}] {result.verdict.value:<15} {result.key}", flush=True)
            if cache and i % 10 == 0:
                cache.flush()
    except KeyboardInterrupt:
        print("\ninterrupted; partial results kept", file=sys.stderr)
    finally:
        if cache:
            cache.flush()

    # Offline, so it still runs when every network source refused us -- and it
    # is checked against the entries actually selected, not the whole file.
    duplicates = [] if args.no_duplicates else find_duplicates(entries)

    return _report(results, args.out, bool(args.only_cited), duplicates)


def _report(results, out: Path, only_cited: bool,
            duplicates: list[Duplicate] | None = None) -> int:
    order = list(Verdict)
    results.sort(key=lambda r: (order.index(r.verdict), r.key.lower()))

    duplicates = duplicates or []
    # A key is annotated with the entry it duplicates, so the CSV carries the
    # finding too rather than it living only in the text report.
    dup_of = {k: d.primary for d in duplicates for k in d.keys if k != d.primary}

    csv_path = out / "reference_check.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        rows = [{**r.as_row(), "duplicate_of": dup_of.get(r.key, "")} for r in results]
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.verdict.value] = counts.get(r.verdict.value, 0) + 1

    findings = [r for r in results if r.verdict.is_finding]
    unverified = [r for r in results if r.verdict is Verdict.UNVERIFIED]

    lines = ["refaudit", "=" * 72,
             f"entries checked : {len(results)}" + ("  (cited only)" if only_cited else ""),
             "counts          : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), ""]

    if unverified:
        lines += [f"{len(unverified)} entries could not be checked (a source was unreachable).",
                  "These are not findings. Re-run later or from another network.", ""]

    if duplicates:
        n = sum(len(d.keys) - 1 for d in duplicates)
        lines.append(f"--- {len(duplicates)} work(s) cited more than once "
                     f"({n} redundant entr{'y' if n == 1 else 'ies'})")
        for d in duplicates:
            lines.append(f"  {d.reason:<22} {d.detail[:60]}")
            for k in d.keys:
                lines.append(f"      {'keep  ' if k == d.primary else 'remove'} {k}")
        lines.append("")

    if findings:
        lines.append(f"--- {len(findings)} entries need a human look, worst first")
        for r in findings:
            lines.append(f"  {r.verdict.value:<15} {r.key}")
            lines.append(f"      bib   : {r.entry_title[:110]}")
            if r.found_title:
                lines.append(f"      found : {r.found_title[:110]}  [{r.source}]")
            if r.note:
                lines.append(f"      why   : {r.note}")
    else:
        lines.append("No findings.")

    text = "\n".join(lines)
    (out / "reference_check.txt").write_text(text + "\n", encoding="utf-8")
    print("\n" + text)
    print(f"\nwritten to {out}/")
    return 1 if (findings or duplicates) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
