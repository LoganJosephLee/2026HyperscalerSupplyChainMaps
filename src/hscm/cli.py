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

LIST_LIMIT = 40  # how many review rows to echo before pointing at the file

_ACCESSION_IN_URL = re.compile(r"(\d{10}\d{2}\d{6}|\d{10}-\d{2}-\d{6})")


# --- M1 ---------------------------------------------------------------------
def cmd_fetch(args: argparse.Namespace) -> int:
    client = EdgarClient()
    if args.tickers:
        tickers = args.tickers
    elif args.seeds_only:
        tickers = list(config.SEED_TICKERS)
    else:
        tickers = list(config.WATCHLIST)
    fetched: list[Filing] = []
    failures: list[tuple[str, str]] = []

    for ticker in tickers:
        # Honour an explicit --form; otherwise let foreign private issuers use
        # the form they actually file.
        form = args.form
        if not args.form_was_given:
            form = config.DEFAULT_FORM_BY_TICKER.get(ticker.upper(), args.form)
        try:
            filings = client.recent_filings(ticker, form, limit=args.limit)
        except Exception as exc:  # network, lookup, or schema failure
            failures.append((ticker, f"{type(exc).__name__}: {exc}"))
            print(f"  {ticker:<6} FAILED  {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        if not filings:
            failures.append((ticker, f"no {form} in submissions.recent"))
            print(f"  {ticker:<6} none    no {form} found in recent filings")
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
        will_read = {key for key, _, _ in extraction_sections(sections, form)}
        for key in EXTRACTION_KEYS.get(form.upper(), ()):
            if key not in sections:
                if will_read:
                    print(f"    -- {key} not located; extraction reads "
                          f"{', '.join(sorted(will_read))} instead")
                else:
                    print(f"    !! {key} not located — extraction would read nothing from it")
                    problems += 1
            elif sections[key].char_count < min_chars:
                # A stub is a filing convention, not a broken split, when the
                # content is somewhere else and extraction knows where.
                fallback = next((k for k in will_read if k != key and k not in
                                 EXTRACTION_KEYS.get(form.upper(), ())), None)
                if fallback:
                    print(f"    -- {key} is a {sections[key].char_count:,}-char "
                          f"cross-reference; extraction reads {fallback} instead")
                else:
                    print(f"    !! {key} is only {sections[key].char_count:,} chars "
                          f"— check the split")
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
    records = json.loads(Path(args.extractions).read_text(encoding="utf-8"))
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


def cmd_show(args: argparse.Namespace) -> int:
    """Print extracted records for a human to read.

    Verification proves a sentence is real. It cannot prove the model drew the
    right relationship from it, and that is the error class left. Reading the
    rows is the only check for it, so the rows have to be readable.
    """
    records = json.loads(Path(args.extractions).read_text(encoding="utf-8"))
    if isinstance(records, dict):
        records = records.get("relationships", [])

    wanted_conf = set(args.confidence or [])
    wanted_type = set(args.type or [])
    shown = 0

    for index, record in enumerate(records):
        confidence = record.get("extraction_confidence")
        rel = record.get("relationship_type")
        if wanted_conf and confidence not in wanted_conf:
            continue
        if wanted_type and rel not in wanted_type:
            continue
        shown += 1

        arrow = "<->" if rel == "unclear" else "->"
        head = f"[{index}] {record.get('supplier_name_raw')} {arrow} {record.get('buyer_name_raw')}"
        print(f"\n{head}")

        facts = [rel or "?", f"confidence: {confidence or '?'}"]
        pct = record.get("quantified_pct")
        if pct is not None:
            facts.append(f"{pct}% of {record.get('quantified_basis') or 'UNSTATED'}")
        if record.get("product_or_service"):
            facts.append(str(record["product_or_service"]))
        facts.append(f"{record.get('form_type', '?')} filed {record.get('filing_date', '?')}")
        print("    " + "  |  ".join(facts))

        sentence = " ".join(str(record.get("source_sentence", "")).split())
        for line in _wrap(sentence, args.width):
            print(f"    {line}")

    print(f"\n{shown} of {len(records)} record{'' if len(records) == 1 else 's'} shown")
    return 0


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


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


def cmd_check_api(args: argparse.Namespace) -> int:
    """One tiny API call, before spending real tokens on a filing."""
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set in this shell.", file=sys.stderr)
        return 1

    from .extract.anthropic_api import AnthropicExtractor

    try:
        result = AnthropicExtractor().check()
    except Exception as exc:
        print(f"FAILED  {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"model returned:   {result['model']}")
    print(f"stop_reason:      {result['stop_reason']}")
    print(f"schema accepted:  {result['schema_accepted']}")
    print(f"tokens:           {result['input_tokens']} in, {result['output_tokens']} out")
    print(f"relationships:    {len(result['relationships'])} from the sample sentence")
    for record in result["relationships"]:
        print(f"  {record.get('supplier_name_raw')} -> {record.get('buyer_name_raw')}"
              f"  [{record.get('relationship_type')}]")
        print(f"    sentence: {record.get('source_sentence', '')[:120]}")
    if not result["schema_accepted"]:
        print(f"\nraw response: {result['raw']}", file=sys.stderr)
        print("The JSON schema was not honoured — do not run a real extraction yet.",
              file=sys.stderr)
        return 1
    print("\nRequest shape works. Safe to run a real extraction.")
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

    extractor = None if args.estimate else get_extractor(args.extractor)
    if extractor is not None:
        print(f"extractor: {extractor.name}")
    else:
        print("estimate only — no API calls will be made")

    total_windows = total_chars = 0
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

        wanted_sections = {name.lower() for name in args.sections} if args.sections else None

        targets: list[tuple[str, str, str]] = list(
            extraction_sections(sections, filing.form_type)
        )
        if wanted_sections is not None:
            targets = [t for t in targets if t[0].lower() in wanted_sections]

        # Concentration passages are extracted separately: they live in the
        # financial statement notes rather than a numbered item, and they are
        # where the quantified percentages are.
        #
        # The concentration sweep searches the whole document, so a passage in
        # Item 1 is matched even though Item 1 is already being sent. Re-sending
        # it costs a second call and yields the same sentence twice, worded
        # slightly differently each time. Only passages outside everything we
        # are already reading are worth a window of their own.
        covered = [
            (sections[key].start, sections[key].end) for key, _, _ in targets if key in sections
        ]
        passages = [
            passage
            for passage in find_concentration_passages(text)
            if not any(start <= passage.start < end for start, end in covered)
        ]
        if passages and (wanted_sections is None or "concentration" in wanted_sections):
            targets.append(
                ("concentration", "Customer/supplier concentration passages",
                 "\n\n".join(p.text for p in passages))
            )

        if args.estimate:
            from .extract.anthropic_api import AnthropicExtractor

            for key, label, section_text in targets:
                count = len(AnthropicExtractor.windows(section_text))
                total_windows += count
                total_chars += len(section_text)
                print(f"  {filing.ticker:<6} {key:<13} {len(section_text):>9,} chars"
                      f" -> {count} call(s)")
            continue

        for key, label, section_text in targets:
            found = extractor.extract(
                ExtractionRequest(filing, key, label, section_text)
            )
            records.extend(found)
            print(f"  {filing.ticker:<6} {key:<13} {len(section_text):>9,} chars -> {len(found)} record(s)")

    if args.estimate:
        # Rough, and deliberately so: the point is the order of magnitude, not
        # a quote. Filing text is dense, so ~4 chars/token is a fair rule of
        # thumb, and the prompt adds a fixed overhead per call.
        prompt_tokens = total_chars / 4 + total_windows * 400
        print(f"\n{total_windows} API call(s), roughly {prompt_tokens:,.0f} input tokens")
        print(
            f"Order of magnitude at $2-3 per million input tokens: "
            f"${prompt_tokens / 1_000_000 * 2:.2f}-${prompt_tokens / 1_000_000 * 3:.2f}, "
            f"plus a little for output. Check current pricing before a large run."
        )
        print("Narrow it with e.g. --sections item1a, or by naming tickers.")
        print("Run without --estimate to extract for real.")
        return 0

    stats = getattr(extractor, "stats", None)
    if stats:
        lost = stats["refused"] + stats["truncated"] + stats["unparseable"]
        print(
            f"\nwindows sent: {stats['windows']}  "
            f"lost: {lost} (refused {stats['refused']}, truncated {stats['truncated']}, "
            f"unparseable {stats['unparseable']})"
        )
        print(f"tokens: {stats['input_tokens']:,} in, {stats['output_tokens']:,} out")
        if lost:
            print("  !! Records from lost windows are missing from this run, not empty.")

    out = Path(args.out) if args.out else config.DATA_DIR / "extractions.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(records)} record(s) written to {out}")
    print("Nothing is trustworthy until `hscm verify` has run over it.")
    return 0


# --- M5: entity resolution --------------------------------------------------
def _resolver(threshold: float | None = None):
    from .resolve import Aliases, Resolver, Spine

    return Resolver(Spine.load(), Aliases.load(), threshold)


def _ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


def cmd_review_edit(args: argparse.Namespace) -> int:
    """Work the review queue one name at a time, in the terminal.

    The CSV was the wrong shape for the job. Eighteen rows of thirteen columns
    wraps into an unreadable block in any editor, and the decisions it wants are
    a handful of keystrokes each. Nothing here is new capability — it writes the
    same aliases.yaml `review apply` writes — it just asks one question at a
    time and shows the sentence the name came from, which is the thing a person
    actually needs to see to decide.
    """
    from .resolve import Aliases, Spine, build_review_queue

    records = json.loads(Path(args.extractions).read_text(encoding="utf-8"))
    from .verify import validate_record

    usable = [record for record in records if not validate_record(record)]

    aliases = Aliases.load()
    resolver = _resolver(args.threshold)
    _, pending = build_review_queue(usable, resolver, config.REVIEW_QUEUE_PATH)
    if not pending:
        print("Nothing needs a decision. Every name resolves.")
        return 0

    examples: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for record in usable:
        for key in ("buyer_name_raw", "supplier_name_raw"):
            name = (record.get(key) or "").strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
                examples.setdefault(name, record)

    print(f"\n{len(pending)} name(s) to decide. Ctrl-C stops; everything decided so far is kept.\n")
    decided = 0

    for position, resolution in enumerate(pending, start=1):
        name = resolution.raw_name
        print("=" * 72)
        print(f"[{position}/{len(pending)}]  {name}")
        print(f"  appears in {counts.get(name, 0)} record(s)")
        example = examples.get(name, {})
        sentence = " ".join(str(example.get("source_sentence", "")).split())
        for line in _wrap(sentence, 68):
            print(f"  | {line}")
        print()

        for index, (score, title, cik) in enumerate(resolution.candidates, start=1):
            print(f"  {index}. {title}  (CIK {cik})  @ {score:.2f}")
        if not resolution.candidates:
            print("  (nothing in SEC's registry looks like this)")
        print("  n = files nothing with the SEC     x = not a company, exclude")
        print("  s = skip for now                   q = stop here and save")

        choice = _ask("  > ").lower()
        if choice == "q":
            break
        if choice in {"", "s"}:
            continue

        if choice == "n":
            canonical = _ask(f"    fold onto which name? [{name}] ", name)
            reason = _ask("    why does it not file? [Files nothing with the SEC] ",
                          "Files nothing with the SEC")
            aliases.non_filers[name] = {"canonical": canonical, "note": reason}
            decided += 1
            continue

        if choice == "x":
            reason = _ask("    why is it out of the dataset? ")
            if not reason:
                print("    an exclusion needs a reason; skipped")
                continue
            aliases.excluded[name] = reason
            decided += 1
            continue

        if choice.isdigit() and 1 <= int(choice) <= len(resolution.candidates):
            _, title, cik = resolution.candidates[int(choice) - 1]
            aliases.aliases[name] = {"cik": int(cik), "note": f"Matched to {title} by hand."}
            decided += 1
            continue

        # Anything else is read as a CIK typed in directly.
        digits = choice.replace("-", "").strip()
        if digits.isdigit():
            entry = Spine.load().by_cik(int(digits))
            if not entry:
                print(f"    no company with CIK {digits} in the registry; skipped")
                continue
            print(f"    -> {entry.title}")
            aliases.aliases[name] = {"cik": entry.cik, "note": f"Matched to {entry.title} by hand."}
            decided += 1
            continue

        print("    not understood; skipped")

    path = aliases.save()
    print(f"\n{decided} decision(s) written to {path}")
    if decided:
        print("Commit it — these decisions are the asset this project builds.")
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    """Find a company in SEC's registry, by ticker or by name.

    Working the review queue means putting CIKs in a column, and a CIK typed
    from memory is a wrong merge waiting to happen — silent, because nothing
    downstream can tell a plausible CIK from the right one. This reads the same
    spine the resolver reads.
    """
    from .resolve import Spine, normalize_name

    spine = Spine.load()
    for query in args.names:
        print(f"\n{query}")
        by_ticker = spine.by_ticker(query)
        if by_ticker:
            print(f"  {by_ticker.cik:<10} {by_ticker.ticker:<6} {by_ticker.title}   (ticker match)")
            continue

        normalized = normalize_name(query)
        exact = spine.exact(normalized)
        if exact:
            print(f"  {exact.cik:<10} {exact.ticker:<6} {exact.title}   (exact match)")
            continue

        candidates = spine.by_leading_tokens(normalized) + spine.best_fuzzy(normalized)
        if not candidates:
            print("  nothing in the registry looks like this — it may not file with the SEC")
            continue
        seen: set[int] = set()
        for score, entry in candidates:
            if entry.cik in seen:
                continue
            seen.add(entry.cik)
            print(f"  {entry.cik:<10} {entry.ticker:<6} {entry.title}   @ {score:.2f}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from .resolve import Aliases, apply_review_queue, build_review_queue

    if args.action == "edit":
        return cmd_review_edit(args)

    if args.action == "build":
        records = json.loads(Path(args.extractions).read_text(encoding="utf-8"))

        # Only ask about records that could become edges. A record the validator
        # rejects is already out of the dataset, so putting its "company" in the
        # queue asks a person to adjudicate something nothing will ever use —
        # "third-party foundries located in Taiwan" is not a name awaiting a CIK.
        from .verify import validate_record

        usable = [record for record in records if not validate_record(record)]
        dropped = len(records) - len(usable)
        if dropped:
            print(f"({dropped} record(s) skipped — the validator already rejects them)")
        path, pending = build_review_queue(usable, _resolver(args.threshold))
        print(f"{len(pending)} name(s) need a human decision -> {path}")
        if pending:
            print("\nFill in `decision` per row: accept | cik | non-filer | exclude | skip")
            print("  accept    — best_match_cik is right")
            print("  cik       — put the correct CIK in the `cik` column")
            print("  non-filer — named in a filing but files nothing itself, like")
            print("              OpenAI; becomes a node with no CIK")
            print("  exclude   — keep out of the dataset entirely; give a reason")
            print("  skip      — decide later\n")
            # Print all of them. A truncated list with nothing saying it was
            # truncated reads as the whole queue, and the rows below the cut are
            # the ones nobody looks at.
            shown = pending[:LIST_LIMIT]
            for resolution in shown:
                best = resolution.candidates[0] if resolution.candidates else None
                guess = f"{best[1]} ({best[2]}) @ {resolution.score:.2f}" if best else "no candidate"
                print(f"  {resolution.raw_name:<45} {guess}")
            if len(pending) > len(shown):
                print(f"\n  ... and {len(pending) - len(shown)} more, all of them in the file.")
        return 0

    aliases = Aliases.load()
    added, excluded, non_filers, problems = apply_review_queue(aliases)
    for problem in problems:
        print(f"  !! {problem}", file=sys.stderr)
    path = aliases.save()
    print(f"{added} alias(es), {non_filers} non-filer(s), "
          f"{excluded} exclusion(s) written to {path}")
    return 1 if problems else 0


# --- M7/M8: graph build and export -----------------------------------------
def cmd_build(args: argparse.Namespace) -> int:
    from .graph import build_graph, export
    from .resolve import Spine

    records = json.loads(Path(args.extractions).read_text(encoding="utf-8"))
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

    records = json.loads(Path(args.extractions).read_text(encoding="utf-8"))
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
    import logging

    # A refused or truncated extraction window logs a warning. Without this,
    # those warnings are discarded and a partial run looks like a clean one.
    logging.basicConfig(level=logging.WARNING, format="  !! %(message)s")

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
    fetch.add_argument("tickers", nargs="*",
                       help="default: the seed set plus the supplier watchlist")
    fetch.add_argument("--seeds-only", action="store_true",
                       help="fetch only the six buyers")
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
    extract.add_argument("--sections", nargs="+",
                         help="limit to these section keys, e.g. --sections item1a")
    extract.add_argument("--estimate", action="store_true",
                         help="count the API calls this would make, without making them")
    extract.set_defaults(func=cmd_extract)

    show = sub.add_parser("show", help="print extracted records in a readable form for hand-checking")
    show.add_argument("extractions", nargs="?", default=default_extractions)
    show.add_argument("--confidence", nargs="+", choices=["high", "medium", "low"],
                      help="only these confidence levels")
    show.add_argument("--type", nargs="+",
                      help="only these relationship types, e.g. --type unclear")
    show.add_argument("--width", type=int, default=88)
    show.set_defaults(func=cmd_show)

    check = sub.add_parser("check-api", help="one tiny API call to validate the request shape")
    check.set_defaults(func=cmd_check_api)

    review = sub.add_parser("review", help="entity resolution review queue")
    review.add_argument("action", choices=["build", "apply", "edit"])
    review.add_argument("--extractions", default=default_extractions)
    review.add_argument("--threshold", type=float, default=None)
    review.set_defaults(func=cmd_review)

    lookup = sub.add_parser("lookup", help="find a company's CIK in SEC's registry")
    lookup.add_argument("names", nargs="+", help="ticker or company name")
    lookup.set_defaults(func=cmd_lookup)

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
    # Lets cmd_fetch tell "the user asked for 10-K" from "10-K is the default".
    if getattr(args, "form", None) is not None:
        args.form_was_given = any(a == "--form" for a in (argv or sys.argv[1:]))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
