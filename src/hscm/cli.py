"""Command line entry points.

    hscm fetch       M1 — cache the most recent filing of a form type per seed
    hscm sections    M1 — show what the section splitter found, per cached filing
    hscm extract     M2/M4 — run the configured extractor over cached filings
    hscm verify      M3 — string-match extracted sentences back into the filings
    hscm review      M5 — build / apply the entity resolution review queue
    hscm build       M7 — verify, resolve, and export the graph for the site
    hscm neo4j-load  M6 — load the graph into Neo4j for Cypher work
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import config
from .edgar import EdgarClient, Filing, read_manifest, write_manifest
from .sections import (
    EXTRACTION_KEYS,
    candidate_report,
    document_text,
    extraction_sections,
    find_concentration_passages,
    split_items,
)
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

        form = row["form_type"]
        text = document_text(path.read_bytes())
        diagnostics: dict = {}
        sections = split_items(text, form, diagnostics)
        passages = find_concentration_passages(text)

        print(f"\n{row['ticker']}  {row['company_name']}")
        print(f"  {form} filed {row['filing_date']}  {len(text):,} chars of text")
        print(f"  {row['document_url']}")
        if diagnostics.get("toc_spans"):
            spans = ", ".join(f"{s:,}-{e:,}" for s, e in diagnostics["toc_spans"])
            print(f"  contents page(s) detected at {spans}")
        for key, section in sections.items():
            found = diagnostics.get("candidates", {}).get(key, 1)
            preview = section.text[:80].replace("\n", " ")
            print(
                f"    {key:<13} {section.char_count:>9,} chars  @{section.start:<9,} "
                f"({found} candidate{'s' if found != 1 else ''})  {preview}"
            )

        # Sections extraction actually reads. Their absence is a split failure,
        # not an empty filing, and it has to be loud.
        # A one-event 8-K of a few hundred characters is normal; a 500-character
        # Item 1A is a broken split.
        min_chars = 0 if form.upper() == "8-K" else 2000
        for key in EXTRACTION_KEYS.get(form.upper(), ()):
            if key not in sections:
                print(f"    !! {key} not located — extraction would read nothing from it")
                problems += 1
            elif sections[key].char_count < min_chars:
                print(f"    !! {key} is only {sections[key].char_count:,} chars — check the split")
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


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Show every header candidate and how the splitter judged it.

    A summary of the result cannot explain a wrong split; this can.
    """
    rows = read_manifest()
    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        rows = [r for r in rows if r["ticker"] in wanted]
    if not rows:
        print("No matching cached filing. Run `hscm fetch` first.", file=sys.stderr)
        return 1

    for row in rows:
        path = config.REPO_ROOT / row["cache_path"]
        if not path.exists():
            continue
        text = document_text(path.read_bytes())
        print(f"\n{row['ticker']}  {row['form_type']} filed {row['filing_date']}  "
              f"{len(text):,} chars")
        print(f"{'item':<8} {'position':>10}  {'span':>5} {'tail#':>5} {'dropped':>7} {'chosen':>6}  line")
        for entry in candidate_report(text, row["form_type"]):
            if args.item and entry["key"] != args.item:
                continue
            print(
                f"{entry['key']:<8} {entry['position']:>10,}  "
                f"{str(entry['in_dense_span']):>5} "
                f"{str(entry['tail_looks_like_page_number']):>5} "
                f"{str(entry['dropped_as_contents_row']):>7} "
                f"{str(entry['chosen']):>6}  "
                f"[len {entry['line_length']}] {entry['line']!r}"
            )
    print(
        "\nspan    = inside a detected contents-page cluster\n"
        "tail#   = the line ends with something that looks like a page number\n"
        "dropped = discarded as a contents row\n"
        "chosen  = this position became the section start"
    )
    return 0


# --- M2/M4: extraction ------------------------------------------------------
def cmd_extract(args: argparse.Namespace) -> int:
    from .extract import ExtractionRequest, get_extractor

    rows = read_manifest()
    if not rows:
        print("Cache is empty — run `hscm fetch` first.", file=sys.stderr)
        return 1
    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        rows = [r for r in rows if r["ticker"] in wanted]

    extractor = get_extractor(args.extractor)
    print(f"extractor: {extractor.name}")

    records: list[dict] = []
    for row in rows:
        path = config.REPO_ROOT / row["cache_path"]
        if not path.exists():
            print(f"{row['ticker']}: cached file missing", file=sys.stderr)
            continue

        filing = Filing(
            cik=row["cik"], ticker=row["ticker"], company_name=row["company_name"],
            form_type=row["form_type"], filing_date=row["filing_date"],
            report_date=row["report_date"], accession=row["accession"],
            primary_document=row["primary_document"],
        )
        text = document_text(path.read_bytes())
        sections = split_items(text, filing.form_type)

        targets: list[tuple[str, str, str]] = list(
            extraction_sections(sections, filing.form_type)
        )
        # Concentration passages are extracted separately: they live in the
        # financial statement notes rather than a numbered item, and they are
        # where the quantified percentages are.
        passages = find_concentration_passages(text)
        if passages:
            targets.append(
                ("concentration", "Customer/supplier concentration passages",
                 "\n\n".join(p.text for p in passages))
            )

        for key, label, section_text in targets:
            found = extractor.extract(
                ExtractionRequest(filing, key, label, section_text)
            )
            records.extend(found)
            print(f"  {filing.ticker:<6} {key:<13} {len(section_text):>9,} chars -> {len(found)} record(s)")

    out = Path(args.out) if args.out else config.DATA_DIR / "extractions.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=2) + "\n")
    print(f"\n{len(records)} record(s) written to {out}")
    print("Nothing is trustworthy until `hscm verify` has run over it.")
    return 0


# --- M5: entity resolution --------------------------------------------------
def _resolver(threshold: float | None = None):
    from .resolve import Aliases, Resolver, Spine

    return Resolver(Spine.load(), Aliases.load(), threshold)


def cmd_review(args: argparse.Namespace) -> int:
    from .resolve import Aliases, apply_review_queue, build_review_queue

    if args.action == "build":
        records = json.loads(Path(args.extractions).read_text())
        path, pending = build_review_queue(records, _resolver(args.threshold))
        print(f"{len(pending)} name(s) need a human decision -> {path}")
        if pending:
            print("\nFill in `decision` per row: accept | cik | exclude | skip")
            print("  accept  — best_match_cik is right")
            print("  cik     — put the correct CIK in the `cik` column")
            print("  exclude — company is real but does not file with the SEC; give a reason")
            print("  skip    — decide later\n")
            for resolution in pending[:10]:
                best = resolution.candidates[0] if resolution.candidates else None
                guess = f"{best[1]} ({best[2]}) @ {resolution.score:.2f}" if best else "no candidate"
                print(f"  {resolution.raw_name:<45} {guess}")
        return 0

    aliases = Aliases.load()
    added, excluded, problems = apply_review_queue(aliases)
    for problem in problems:
        print(f"  !! {problem}", file=sys.stderr)
    path = aliases.save()
    print(f"{added} alias(es), {excluded} exclusion(s) written to {path}")
    return 1 if problems else 0


# --- M7/M8: graph build and export -----------------------------------------
def cmd_build(args: argparse.Namespace) -> int:
    from .graph import build_graph, export
    from .resolve import Spine

    records = json.loads(Path(args.extractions).read_text())
    report = verify_records(records, _document_resolver())
    print(report.summary())

    supported = report.supported_records(records)
    if not supported:
        print(
            "\nNo record survived verification — refusing to build a graph with no evidence.",
            file=sys.stderr,
        )
        return 1

    spine = Spine.load()
    seed_ciks = {
        entry.cik for entry in (spine.by_ticker(t) for t in config.SEED_TICKERS) if entry
    }
    graph = build_graph(supported, _resolver(), seed_ciks)

    print(f"\nnodes: {len(graph.companies)}  edges: {len(graph.edges)}")
    print(f"evidence records on edges: {sum(len(e.evidence) for e in graph.edges.values())}")
    print(f"records dropped to unresolved names: {len(graph.unresolved)}")
    print(f"data as of: {graph.data_as_of}")

    for path in export(graph, supported):
        print(f"  wrote {path.relative_to(config.REPO_ROOT)}")
    return 0


def cmd_neo4j_load(args: argparse.Namespace) -> int:
    from .graph import build_graph, cypher_statements, export  # noqa: F401
    from .resolve import Spine

    records = json.loads(Path(args.extractions).read_text())
    report = verify_records(records, _document_resolver())
    supported = report.supported_records(records)

    spine = Spine.load()
    seed_ciks = {
        entry.cik for entry in (spine.by_ticker(t) for t in config.SEED_TICKERS) if entry
    }
    statements = cypher_statements(build_graph(supported, _resolver(), seed_ciks))

    if args.dry_run:
        for statement, params in statements:
            print(f"{statement};\n  -- {json.dumps(params, default=str)[:160]}")
        print(f"\n{len(statements)} statement(s); not executed (--dry-run)")
        return 0

    try:
        from neo4j import GraphDatabase
    except ImportError:
        print(
            "neo4j driver not installed. `uv sync --extra neo4j`, or use --dry-run.",
            file=sys.stderr,
        )
        return 1

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    with driver.session(database=args.database) as session:
        for statement, params in statements:
            session.run(statement, params)
    driver.close()
    print(f"loaded {len(statements)} statement(s) into {args.uri}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page, which mangles the em
    # dashes and quotation marks in filing text and in our own output.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - non-reconfigurable stream
                pass

    parser = argparse.ArgumentParser(prog="hscm", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="cache filings for the seed companies")
    fetch.add_argument("tickers", nargs="*", help="default: the seed set")
    fetch.add_argument("--form", default="10-K", choices=config.FORM_TYPES)
    fetch.add_argument("--limit", type=int, default=1, help="filings per company")
    fetch.add_argument("--refresh", action="store_true", help="re-download cached documents")
    fetch.set_defaults(func=cmd_fetch)

    diagnose = sub.add_parser("diagnose", help="show every header candidate and how it was judged")
    diagnose.add_argument("tickers", nargs="*")
    diagnose.add_argument("--item", help="limit to one item key, e.g. item1")
    diagnose.set_defaults(func=cmd_diagnose)

    sections = sub.add_parser("sections", help="report what the section splitter found")
    sections.add_argument("tickers", nargs="*")
    sections.add_argument("--show-passages", type=int, default=3)
    sections.set_defaults(func=cmd_sections)

    verify = sub.add_parser("verify", help="check extracted sentences against the filings")
    verify.add_argument("extractions", help="JSON file of extracted relationships")
    verify.add_argument("--out", help="write a JSON report here")
    verify.add_argument("--max-failure-rate", type=float, default=0.0)
    verify.set_defaults(func=cmd_verify)

    default_extractions = str(config.DATA_DIR / "extractions.json")

    extract = sub.add_parser("extract", help="run the configured extractor over cached filings")
    extract.add_argument("tickers", nargs="*")
    extract.add_argument("--extractor", choices=["fixture", "anthropic"],
                         help=f"override HSCM_EXTRACTOR (currently {config.EXTRACTOR!r})")
    extract.add_argument("--out")
    extract.set_defaults(func=cmd_extract)

    review = sub.add_parser("review", help="entity resolution review queue")
    review.add_argument("action", choices=["build", "apply"])
    review.add_argument("--extractions", default=default_extractions)
    review.add_argument("--threshold", type=float, default=None)
    review.set_defaults(func=cmd_review)

    build = sub.add_parser("build", help="verify, resolve, and export the graph")
    build.add_argument("--extractions", default=default_extractions)
    build.set_defaults(func=cmd_build)

    neo = sub.add_parser("neo4j-load", help="load the graph into Neo4j")
    neo.add_argument("--extractions", default=default_extractions)
    neo.add_argument("--uri", default="bolt://localhost:7687")
    neo.add_argument("--user", default="neo4j")
    neo.add_argument("--password", default="neo4jneo4j")
    neo.add_argument("--database", default="neo4j")
    neo.add_argument("--dry-run", action="store_true", help="print statements, touch no database")
    neo.set_defaults(func=cmd_neo4j_load)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
