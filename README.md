# AI Hyperscaler Supply Chain Maps (Publicly Disclosed Data)

A supply chain graph of the AI hyperscalers where every edge is traceable to a
specific sentence in a specific SEC filing.

**Status: the pipeline runs end to end on real filings; the published dataset is
still empty.** Twenty filings are cached, and a pilot extraction over four
supplier 10-Ks produced 56 records, of which 55 were checked against the filing
text and 55 matched — a 0.0% hallucination rate. That pilot is not the dataset:
the hand-check of those records found three defects in the extraction schema
that are now fixed, so the batch has to be re-extracted before it is worth
publishing. See [Current status](#current-status) before trusting any of it.

## Setup

**macOS / Linux**

```bash
make setup
export HSCM_EDGAR_CONTACT="you@example.com"   # SEC fair-access policy wants a real contact
```

**Windows (PowerShell)** — there is no `make` on Windows, and PowerShell 5.1
does not accept `&&`, so use `run.ps1`, one command per line:

```powershell
.\run.ps1 setup
$env:HSCM_EDGAR_CONTACT = "you@example.com"
```

If PowerShell refuses to run the script ("running scripts is disabled on this
system"), allow local scripts once with
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Rebuild the dataset

```bash
make refresh    # fetch -> extract -> verify -> build, the whole pipeline
make serve      # then open http://localhost:8000
```

On Windows: `.\run.ps1 refresh` and `.\run.ps1 serve`. Every `make` target in
this README has a `.\run.ps1` equivalent of the same name; `.\run.ps1 help`
lists them.

`make refresh` is the single documented command. It is deliberately manual:
there is no scheduled workflow, so the dataset never changes underneath you.
Every page and both dataset exports carry a "data as of" stamp taken from the
newest filing date in the data — not from today's date.

To use the real extractor once you have an API key:

```bash
export ANTHROPIC_API_KEY=...
export HSCM_EXTRACTOR=anthropic
uv sync --extra anthropic
make refresh
```

## Commands

```bash
uv run hscm fetch                    # M1: cache the latest 10-K for each seed company
uv run hscm fetch --form 20-F TSM    # any of 10-K / 20-F / 10-Q / 8-K, any ticker
uv run hscm sections                 # M1: report what the section splitter found
uv run hscm extract                  # M2/M4: run the configured extractor
uv run hscm verify data/extractions.json --out report.json   # M3: the hallucination check
uv run hscm show --type unclear       # M3: read the records a human still has to judge
uv run hscm review build             # M5: queue unresolved names for a human
uv run hscm lookup TSM AMKR          # M5: find a CIK without typing one from memory
uv run hscm review apply             # M5: fold decisions into aliases.yaml
uv run hscm build                    # M7: verify, resolve, export the graph
uv run hscm neo4j-load --dry-run     # M6: print the Cypher without a database
make test
```

## Current status

| Milestone | State |
|---|---|
| M1 — EDGAR fetcher, cache, section splitter | **Done.** 20 filings cached, including a 20-F. Three splitter bugs that only real filings expose are fixed |
| M2 — Extraction on the Microsoft 10-K | **Done.** Microsoft's entire risk factors section yields one relationship, and it states no direction — which is the finding, not a failure |
| M3 — Hallucination check | **Passed.** 55 records checked against filing text, 55 matched, 0.0% failure rate. The one earlier failure was a page number our HTML-to-text left mid-sentence, not a hallucination |
| M4 — All six seeds | Supplier-side filings read as well, because naming obligations fall on suppliers. Full run not yet made |
| M5 — Entity resolution + review queue | Written and tested; **never run on real names.** `aliases.yaml` carries the non-filer decisions |
| M6 — Neo4j load + Cypher | Statements and queries written; **Neo4j never started** (Docker Hub blocked in the build environment) |
| M7 — JSON export + sigma.js front end | Built, and **verified in a real browser** against a test dataset. No real dataset exported yet |
| M8 — Limitations page, `make refresh`, date stamp | Built |

### What the first hand-check found

Verification proves a quoted sentence is really in the filing. It does not prove
the model drew the right relationship out of it, and reading all 56 records
turned up three things no automated check would have caught:

1. **`purchases_from` was the wrong shape of verb.** Roles were right — TSMC
   really was the supplier — but the edge printed as "TSMC purchases_from
   Broadcom", the relationship backwards. Every verb now takes the supplier as
   its subject.
2. **One sentence was counted as several statements.** The concentration sweep
   re-read text already inside Item 1, and the model worded
   `product_or_service` differently each pass, so whole-record deduplication
   missed it. The site counts statements to show corroboration; this claimed
   independent sources that did not exist.
3. **`quantified_basis` was too narrow.** "Approximately 95% of the wafers
   manufactured by our CMs were produced by TSMC" is a share of units, not of
   revenue or cost, and the enum forced the model to answer `null` — which the
   validator then discarded. That is the most valuable number in the batch.

The pilot records predate all three fixes and need re-extracting.

**What "verified in a real browser" means.** The graph renders, force layout
runs, clicking an edge opens the citation panel with the verbatim sentence, the
form type, the filing date and a working EDGAR link, and clicking a node lists
its disclosed counterparties. That was tested with Playwright against a
synthetic graph held outside the repo. The interaction works; the data it will
eventually show has not been checked.

**Why M1 could not run.** The egress policy here answers 403 to `sec.gov`,
`data.sec.gov`, `efts.sec.gov` and `gleif.org`. The section splitter has
therefore only met synthetic 10-K/10-Q/8-K documents. Real filing HTML is
messier than any fixture, and the splitter is the part most likely to be wrong —
run `make fetch && uv run hscm sections` and read the output before trusting an
extraction.

## Layout

```
src/hscm/config.py     seed set, form types, paths, thresholds, extractor selection
src/hscm/edgar.py      ticker -> CIK -> filing metadata -> cached document
src/hscm/sections.py   HTML to text; Item splitting; concentration passages
src/hscm/extract/      the Extractor interface, FixtureExtractor, AnthropicExtractor
src/hscm/verify.py     M3 — structural validation and the hallucination check
src/hscm/resolve.py    name -> CIK, review queue, aliases.yaml
src/hscm/graph.py      companies and edges, JSON/CSV export, Cypher statements
site/                  the static site: graph, citation panel, limitations page
cypher/queries.cypher  the Cypher worth running once the graph is loaded
aliases.yaml           human entity-resolution decisions, version controlled
```

## Design decisions worth knowing before you read the code

- **The model is never asked for `source_url`, `form_type` or `filing_date`.**
  Those are facts about the filing we fetched, so the extractor stamps them. A
  model asked to reproduce a URL eventually produces a plausible one that 404s.
- **A near-miss sentence match counts as a failure.** "Nearly verbatim" is
  precisely what a paraphrased — that is, fabricated — claim looks like. The
  closest passage is reported for prompt debugging, never accepted as evidence.
- **Edges are grouped by company pair in the site export, and kept one-per-
  sentence in Neo4j and the downloadable dataset.** A canvas cannot draw forty
  parallel lines legibly; a Cypher query about corroboration needs them separate.
- **`country`, `sector` and `lei` are null.** They are not in
  `company_tickers.json`, and GLEIF was unreachable here. Empty is honest.

## Data and licensing

SEC filings are US government works in the public domain. Everything ingested
comes from EDGAR, so the dataset can be redistributed and rebuilt by anyone.
Commercial supply chain databases are deliberately not used.

`site/vendor/sigma-bundle.js` is a vendored build of sigma.js, graphology and
graphology-layout-forceatlas2 (MIT), so the site depends on no CDN and cannot
break when someone else's host goes away. Rebuild it with esbuild from
`site/vendor/sigma-bundle.entry.js`.

## Known exclusions

Companies that do not file with the SEC are excluded from the dataset entirely —
no placeholder nodes. That includes Samsung Electronics and SK Hynix, which are
dominant in high-bandwidth memory. Their absence overstates Micron's apparent
share of AI memory supply. This is documented rather than corrected for; every
exclusion recorded in `aliases.yaml` is named on the limitations page.
