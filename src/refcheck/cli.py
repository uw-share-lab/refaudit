"""Command line interface."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from . import __version__
from .bibtex import cited_keys, find_tex, parse_file
from .cache import Cache
from .checker import Checker, Thresholds
from .models import Verdict
from .resolvers import AVAILABLE, default_resolvers

EPILOG = """\
examples:
  refcheck refs.bib --email you@uni.edu
  refcheck refs.bib --email you@uni.edu --tex paper/sections --only-cited
  refcheck refs.bib --email you@uni.edu --resolvers crossref:doi,openalex

exit status:
  0  no findings
  1  at least one entry needs a human look
  2  usage or input error
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="refcheck",
        description="Verify .bib entries against Crossref, arXiv and OpenAlex.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("bib", type=Path, help="path to the .bib file")
    p.add_argument("--email", default=os.environ.get("REFCHECK_EMAIL", ""),
                   help="contact address sent to the APIs (or set REFCHECK_EMAIL). "
                        "Crossref and OpenAlex give identified callers a better pool.")
    p.add_argument("--tex", type=Path,
                   help="directory or file of LaTeX sources, to determine which keys are cited")
    p.add_argument("--only-cited", action="store_true",
                   help="check only keys cited in --tex (uncited entries never reach the PDF)")
    p.add_argument("--resolvers", default="",
                   help=f"comma-separated subset of: {', '.join(AVAILABLE)}")
    p.add_argument("--out", type=Path, default=Path("refcheck-out"),
                   help="output directory (default: refcheck-out)")
    p.add_argument("--cache", type=Path, help="cache file (default: <out>/cache.json)")
    p.add_argument("--no-cache", action="store_true", help="ignore and do not write the cache")
    p.add_argument("--ttl-days", type=float, default=90.0, help="cache lifetime (default: 90)")
    p.add_argument("--timeout", type=float, default=20.0, help="per-request timeout seconds")
    p.add_argument("--title-match", type=float, default=Thresholds.title_match,
                   help="similarity at or above which two titles are the same work")
    p.add_argument("--quiet", action="store_true", help="only print the summary")
    p.add_argument("--version", action="version", version=f"refcheck {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.bib.is_file():
        print(f"error: no such file: {args.bib}", file=sys.stderr)
        return 2
    if not args.email:
        print("error: --email is required (or set REFCHECK_EMAIL).\n"
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
                      thresholds=Thresholds(title_match=args.title_match))

    results = []
    try:
        for i, result in enumerate(checker.check_all(entries, cited=cited), 1):
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

    return _report(results, args.out, bool(args.only_cited))


def _report(results, out: Path, only_cited: bool) -> int:
    order = list(Verdict)
    results.sort(key=lambda r: (order.index(r.verdict), r.key.lower()))

    csv_path = out / "reference_check.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        rows = [r.as_row() for r in results]
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.verdict.value] = counts.get(r.verdict.value, 0) + 1

    findings = [r for r in results if r.verdict.is_finding]
    unverified = [r for r in results if r.verdict is Verdict.UNVERIFIED]

    lines = ["refcheck", "=" * 72,
             f"entries checked : {len(results)}" + ("  (cited only)" if only_cited else ""),
             "counts          : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), ""]

    if unverified:
        lines += [f"{len(unverified)} entries could not be checked (a source was unreachable).",
                  "These are not findings. Re-run later or from another network.", ""]

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
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
