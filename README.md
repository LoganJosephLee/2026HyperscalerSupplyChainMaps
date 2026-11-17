# Who builds the AI cloud?

A learning map of the AI hyperscaler supply chain: the companies behind the data
centers of Microsoft, Google, Amazon, Meta, Oracle and CoreWeave, from chip
materials to the AI labs that buy the computing. It is written for people who are
curious about the industry and new to it.

Every connection on the map is backed by a quote, copied word for word, with a
link to where it was published, and every quote is labelled with the kind of
source it came from:

- **SEC filing**: a 10-K, 20-F, 8-K or 6-K. Most of these are found and
  extracted by the pipeline in `src/hscm/`.
- **Company announcement**: a press release or official blog post from one of the
  companies in the deal.
- **News report**: a major news organisation. Deals the companies have not
  confirmed themselves are marked as reported, not confirmed.

**Status (November 2026):** 77 companies, 100 connections and 178 quoted sources
(148 from SEC filings, 25 from company announcements, 5 from news reports). Every
SEC quote is matched against a cached copy of its filing at each build; all 37
web quotes were found on their pages when last checked. See
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
uv run hscm learn                    # merge the graph with sources/ into the learning map
uv run hscm learn --strict           # ...and fail if any web quote lacks a passing check
uv run hscm verify-web               # re-check every non-SEC quote against its live page
uv run hscm neo4j-load --dry-run     # M6: print the Cypher without a database
make test
```

## Current status

| Part | State |
|---|---|
| SEC pipeline (M1–M5, M7) | **Done.** 43 filings cached; full extraction run, 194 records; 186 of 187 checked sentences matched their filings (0.5% failure). Entity review queue cleared in November 2026: 0 names left |
| Curated sources | **Started.** 29 relationships from company announcements, news reports and SEC filings the pipeline does not read, in `sources/claims.yaml` |
| Learning map site | **Done and browser-checked.** Layered stack, company panels with every quote, five guided tours, glossary, sourcing page. `site/index.html` |
| Raw SEC network | Kept at `site/network.html` |
| Neo4j (M6) | Statements and queries written; never started |
| Hosting | GitHub Pages not yet enabled |

### November 2026 changes

- **New direction:** the sourcing rule widened from "SEC filings only" to any
  credible source, quoted word for word and labelled by type. The site became a
  learning tool built around a layered stack instead of a network diagram.
- **Entity resolution fixes.** "We", "us" and "the Company" in a filing now
  resolve to the company that filed it, which recovered relationships such as
  Arista's dependence on Broadcom for switching chips. GlobalFoundries had been
  matched to New Germany Fund through the ticker GF; fixed. The 62-name review
  queue was decided by hand.
- **Corrections** (`sources/corrections.yaml`): Entegris's top-customer table had
  been read backwards (TSMC buys from Entegris); KLA's TSMC row now shows the
  sentence above the table that explains it.
- **Off-topic records hidden from the map** (GE's trademark licence, FedEx's truck
  pilots, a nuclear joint venture), listed in `sources/companies.yaml`.

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
- **No inferred percentages.** A number appears only if a quoted sentence states it.
- **Sources beyond EDGAR, with labels (November 2026).** The SEC-only rule left
  the hyperscalers' biggest deals off the map, because buyers rarely name
  suppliers in filings. Company announcements and major news reports are now
  allowed, quoted exactly and labelled so a reader can see how strong each one is.

## Sources

The SEC pipeline produces `site/data/graph.json`. `hscm learn` merges it with the
hand-curated files in `sources/` and writes `site/data/map.json`, which the site reads:

| File | What it holds |
|---|---|
| `sources/companies.yaml` | Every company's layer, a one-line summary, and a quote that backs the summary up |
| `sources/claims.yaml` | Relationships from outside the SEC pipeline, each with one or more quotes |
| `sources/corrections.yaml` | Hand fixes to SEC records, matched by filing and sentence |
| `sources/layers.yaml` | The layers, guided tours and glossary the site teaches with |
| `sources/web_checks.json` | When each web quote was last found on its page, and how |

Rules the build enforces:

- A quote from a cached SEC filing that is not in the filing fails the build.
- A tour may only step along a cited connection.
- A correction that no longer matches any record fails the build.
- A company with no quote of its own must appear in a cited connection.

Web quotes are checked by `hscm verify-web`, which fetches each page and looks for
the exact words. Some sites block scripts; those come back `unreachable`, and the
earlier check is kept. The quotes added in November 2026 were checked against page
text returned by web search, and several were also fetched directly; run
`make verify-web` on your own machine to re-check them all.

To add a relationship: find the sentence on the company's own release, its filing
or a major outlet; copy it exactly into `sources/claims.yaml`; run `make learn`
and `make verify-web`.

## Layout

```
src/hscm/config.py     seed set, form types, paths, thresholds, extractor selection
src/hscm/edgar.py      ticker -> CIK -> filing metadata -> cached document
src/hscm/sections.py   HTML to text; Item splitting; concentration passages
src/hscm/extract/      the Extractor interface, FixtureExtractor, AnthropicExtractor
src/hscm/verify.py     M3 — structural validation and the hallucination check
src/hscm/resolve.py    name -> CIK, review queue, aliases.yaml
src/hscm/graph.py      companies and edges, JSON/CSV export, Cypher statements
src/hscm/learn.py      merges graph.json with sources/ into the learning map; web checks
sources/               curated companies, claims, corrections, layers and tours
site/                  the learning map (index.html), raw SEC network, limitations page
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

SEC filings are US government works in the public domain. Quotes from company
releases and news reports are short excerpts used to cite a source, with a link
to the original. Commercial supply chain databases are deliberately not used.

`site/vendor/sigma-bundle.js` is a vendored build of sigma.js, graphology and
graphology-layout-forceatlas2 (MIT), so the site depends on no CDN and cannot
break when someone else's host goes away. Rebuild it with esbuild from
`site/vendor/sigma-bundle.entry.js`.

## Known gaps

Companies that file nothing with the SEC (Samsung, SK hynix, Foxconn, OpenAI,
Anthropic and others) appear when another company names them, and are drawn with
a dashed outline. A missing connection does not mean two companies do not do
business: most supply relationships are never disclosed or announced. See
`site/limitations.html`.
