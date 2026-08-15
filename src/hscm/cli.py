"""Command line entry points for the milestones built so far.

    hscm fetch     M1 — cache the most recent filing of a form type per seed
    hscm sections  M1 — show what the section splitter found, per cached filing
    hscm verify    M3 — string-match extracted sentences back into the filings
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import config
from .edgar import EdgarClient, Filing, read_manifest, write_manifest
from .sections import document_text, find_concentration_passages, split_items
from .verify import verify_records, write_report

_ACCESSION_IN_URL = re.compile(r"(\d{10}\d{2}\d{6}|\d{10}-\d{2}-\d{6})")


# --- M1 ---------------------------------------------------------------------
def cmd_fetch(args: argparse.Namespace) -> int:
    client = EdgarClient()
    tickers = args.tickers or list(config.SEED_TICKERS)
    fetched: list[Filing] = []
    failures: list[tuple[str, str]] = []

    for ticker in tickers:
        try:
            filings = client.recent_filings(ticker, args.form, limit=args.limit)
        except Exception as exc:  # network, lookup, or schema failure
            failures.append((ticker, f"{type(exc).__name__}: {exc}"))
            print(f"  {ticker:<6} FAILED  {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        if not filings:
            failures.append((ticker, f"no {args.form} in submissions.recent"))
            print(f"  {ticker:<6} none    no {args.form} found in recent filings")
            continue

        for filing in filings:
            try:
                path = client.fetch_document(filing, refresh=args.refresh)
            except Exception as exc:
                failures.append((ticker, f"{type(exc).__name__}: {exc}"))
                print(f"  {ticker:<6} FAILED  {exc}", file=sys.stderr)
                continue
            fetched.append(filing)
            size_kb = path.stat().st_size / 1024
            print(
                f"  {ticker:<6} {filing.form_type:<5} filed {filing.filing_date} "
                f"({filing.report_date})  {size_kb:>8,.0f} KB  {filing.accession}"
            )

    if fetched:
        write_manifest(fetched)
        print(f"\nmanifest: {config.MANIFEST_PATH.relative_to(config.REPO_ROOT)}")
    print(f"fetched {len(fetched)}/{len(tickers)} requested; {len(failures)} failure(s)")
    return 1 if failures else 0


def cmd_sections(args: argparse.Namespace) -> int:
    rows = read_manifest()
    if not rows:
        print("Cache is empty — run `hscm fetch` first.", file=sys.stderr)
        return 1
    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        rows = [r for r in rows if r["ticker"] in wanted]

    problems = 0
    for row in rows:
        path = config.REPO_ROOT / row["cache_path"]
        if not path.exists():
            print(f"{row['ticker']}: cached file missing at {path}", file=sys.stderr)
            problems += 1
            continue

        text = document_text(path.read_bytes())
        sections = split_items(text, row["form_type"])
        passages = find_concentration_passages(text)

        print(f"\n{row['ticker']}  {row['company_name']}")
        print(f"  {row['form_type']} filed {row['filing_date']}  {len(text):,} chars of text")
        print(f"  {row['document_url']}")
        for key, section in sections.items():
            preview = section.text[:90].replace("\n", " ")
            print(f"    {key:<7} {section.char_count:>9,} chars  @{section.start:<9,} {preview}")

        # These two are what extraction actually reads; call out their absence.
        for required in ("item1", "item1a", "item3d", "item4"):
            if required in sections and sections[required].char_count < 2000:
                print(f"    !! {required} is suspiciously short — check the split")
                problems += 1
        if not ({"item1", "item1a"} & set(sections)) and not ({"item3d", "item4"} & set(sections)):
            print("    !! neither Business nor Risk Factors located — split failed")
            problems += 1

        print(f"    concentration passages: {len(passages)}")
        for passage in passages[: args.show_passages]:
            snippet = passage.text[:220].replace("\n", " ")
            print(f"      - {snippet}...")

    print(f"\n{len(rows)} filing(s) inspected, {problems} problem(s) flagged")
    return 1 if problems else 0


# --- M3 ---------------------------------------------------------------------
def _document_resolver():
    """Map an extracted record to the text of the filing it cites."""
    manifest = read_manifest()
    by_accession: dict[str, Path] = {}
    for row in manifest:
        by_accession[row["accession"].replace("-", "")] = config.REPO_ROOT / row["cache_path"]

    def resolve(record: dict) -> str | None:
        url = str(record.get("source_url", ""))
        match = _ACCESSION_IN_URL.search(url)
        if match:
            path = by_accession.get(match.group(1).replace("-", ""))
            if path and path.exists():
                return document_text(path.read_bytes())
        return None

    return resolve


def cmd_verify(args: argparse.Namespace) -> int:
    records = json.loads(Path(args.extractions).read_text())
    if isinstance(records, dict):
        records = records.get("relationships", [])

    report = verify_records(records, _document_resolver())
    print(report.summary())

    for result in report.results:
        if result.status == "supported":
            continue
        print(f"\n[{result.index}] {result.status.upper()}  {result.supplier} -> {result.buyer}")
        for error in result.errors:
            print(f"    {error}")
        if result.level:
            print(f"    match level: {result.level} (best ratio {result.ratio:.3f})")
        if result.closest_text:
            print(f"    claimed:  {records[result.index]['source_sentence'][:200]}")
            print(f"    filing:   {result.closest_text[:200]}")

    if args.out:
        path = write_report(report, Path(args.out))
        print(f"\nreport written to {path}")

    if not report.checked:
        print("\nNothing was checked — this is not a pass.", file=sys.stderr)
        return 1
    return 0 if report.failure_rate <= args.max_failure_rate else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hscm", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="cache filings for the seed companies")
    fetch.add_argument("tickers", nargs="*", help="default: the seed set")
    fetch.add_argument("--form", default="10-K", choices=config.FORM_TYPES)
    fetch.add_argument("--limit", type=int, default=1, help="filings per company")
    fetch.add_argument("--refresh", action="store_true", help="re-download cached documents")
    fetch.set_defaults(func=cmd_fetch)

    sections = sub.add_parser("sections", help="report what the section splitter found")
    sections.add_argument("tickers", nargs="*")
    sections.add_argument("--show-passages", type=int, default=3)
    sections.set_defaults(func=cmd_sections)

    verify = sub.add_parser("verify", help="check extracted sentences against the filings")
    verify.add_argument("extractions", help="JSON file of extracted relationships")
    verify.add_argument("--out", help="write a JSON report here")
    verify.add_argument("--max-failure-rate", type=float, default=0.0)
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
