# AI Hyperscaler Supply Chain Maps (Publicly Disclosed Data)

A supply chain graph of the AI hyperscalers where every edge is traceable to a
specific sentence in a specific SEC filing.

**Status: the pipeline runs end to end on real filings and has produced a real
dataset.** 43 filings cached across the whole chain — chips, equipment,
materials, power, cooling, data centre space, a carrier and four freight
companies. A pilot extraction over four supplier 10-Ks produced 33 records;
verification matched **32 of 32** checked sentences back into their filings, a
0.0% hallucination rate, and the one dropped record was a filing describing a
category of supplier rather than naming one. Entity resolution has been run by
hand and the decisions are in `aliases.yaml`. The exported graph is 17 companies,
19 relationships and 32 cited sentences, with nothing dropped to unresolved
names.

**What is not done:** the full 43-filing extraction (~537 API calls, roughly
$7–10), Neo4j has never been started, and GitHub Pages is not enabled. See
[Current status](#current-status).

## Setup

**macOS / Linux**

```bash
make setup     # installs everything, including the real extractor
export HSCM_EDGAR_CONTACT="you@example.com"   # SEC fair-access policy wants a real contact
```

Setting up a Mac from nothing — including the shell config that makes the
environment variables survive a reboot — is written out step by step in
[docs/macos-setup.md](docs/macos-setup.md).

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
make refresh
```

## Commands

```bash
uv run hscm fetch                    # M1: cache the latest 10-K for each seed company
uv run hscm fetch --form 20-F TSM    # any of 10-K / 20-F / 10-Q / 8-K, any ticker
uv run hscm sections                 # M1: report what the section splitter found
uv run hscm extract                  # M2/M4: run the configured extractor
uv run hscm extract --estimate       # what it would cost, before it costs it
                                     # interrupted? run it again — it resumes
uv run hscm verify data/extractions.json --out report.json   # M3: the hallucination check
uv run hscm show --type unclear       # M3: read the records a human still has to judge
uv run hscm review edit              # M5: decide unresolved names one at a time
uv run hscm review build             # M5: same queue as a CSV, if you prefer
uv run hscm lookup TSM AMKR          # M5: find a CIK without typing one from memory
uv run hscm review apply             # M5: fold decisions into aliases.yaml
uv run hscm build                    # M7: verify, resolve, export the graph
uv run hscm neo4j-load --dry-run     # M6: print the Cypher without a database
make test
```

## Current status

| Milestone | State |
|---|---|
| M1 — fetcher, cache, section splitter | **Done.** 43 filings, 0 failures. Handles 10-K, 20-F, and industries as different as a freight forwarder and a data centre REIT. Known gap: ASML's 20-F (below) |
| M2 — extraction on a real filing | **Done.** Microsoft's entire risk factors section yields one relationship, stating no direction — the finding, not a failure |
| M3 — hallucination check | **Passed on real data.** 32 of 32 sentences matched their filings, 0.0% failure |
| M4 — supplier-side reading | **Done.** Naming obligations fall on suppliers, so their filings are read too. Full 43-filing run not yet made |
| M5 — entity resolution | **Done for the pilot batch.** 18 names decided by hand; `hscm review edit` asks one at a time |
| M6 — Neo4j + Cypher | Statements and queries written; **Neo4j has never been started** |
| M7 — export | **Done.** 17 companies, 19 relationships, 32 statements, 0 dropped |
| M8 — site | **Done and browser-verified.** Clustered by function, citation panel, limitations page, primer |

### Known defects

**ASML's 20-F splits wrong.** The document is 1,397,350 characters and the
splitter finds one 38,307-character section at 97% through it — the 20-F
cross-reference table at the back of the annual report, not the report. ASML
presents its annual report in its own structure with a mapping table bolted on,
a convention nothing else in the set uses. `sections` now measures what share of
each filing gets read and flags anything under a tenth; `extract` skips those
filings rather than paying to read the wrong text. Run `hscm diagnose ASML` to
see every heading candidate before attempting a fix.

**Lumen loses `item8`.** Its `item7a` runs 231,922 characters, straight through
the financial statements. Extraction falls back to Business and Risk Factors and
the concentration sweep covers the whole document, so the cost is small.

### What the hand-checks have found

Verification proves a quoted sentence is really in the filing. It cannot prove
the model drew the right relationship out of it, and only reading the records
catches that. Four things no automated check would have found:

1. **`purchases_from` was the wrong shape of verb.** The roles were right — TSMC
   really was the supplier — but the edge printed as "TSMC purchases_from
   Broadcom", the relationship backwards, on roughly forty of fifty-six records.
   Every verb now takes the supplier as its subject.
2. **One sentence was counted as several statements.** The concentration sweep
   re-read text already inside Item 1 and the model worded `product_or_service`
   differently each pass, so whole-record deduplication missed it — claiming
   independent sources that did not exist.
3. **`quantified_basis` was too narrow.** "Approximately 95% of the wafers
   manufactured by our CMs were produced by TSMC" is a share of units, not of
   revenue or cost, and the enum forced the model to answer `null` — which the
   validator then discarded. The most valuable number in the batch.
4. **An edge with nobody on one end.** "Most of our products are manufactured by
   third-party foundries located in Taiwan" is true, a real dependency, and not a
   relationship. Verification can never catch it, because the sentence really is
   in the filing.

### Things decided along the way

- **Samsung and SK Hynix are in the graph, as non-filers.** They file nothing
  with the SEC, but NVIDIA's 10-K names them outright and a citable sentence is
  the test this project applies. Excluding them was what forced Micron to stand
  in for the entire memory supply.
- **Colour does not encode what a company does.** A network graph puts any two
  nodes side by side, so a palette must hold across every pair; no fourth hue
  tested cleared the colour-blind separation floor. Position and a label carry
  the job; colour answers only "can this company's word be checked?"
- **No inferred percentages, and no sources beyond EDGAR.** Both were considered
  and both would put numbers on the page that no sentence supports.

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
