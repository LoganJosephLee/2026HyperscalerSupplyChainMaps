# 2026 Hyperscaler Supply Chain Maps

A supply chain graph of the AI hyperscalers where every edge is traceable to a
specific sentence in a specific SEC filing.

Status: **M1 and M3's verification code are written. Neither has been run
against a real filing yet.** See "Current status" below before trusting
anything here.

## Setup

```bash
uv sync --extra dev
```

Set a contact address for SEC's fair-access policy (defaults to the repo
owner's):

```bash
export HSCM_EDGAR_CONTACT="you@example.com"
```

## Commands

```bash
uv run hscm fetch                 # M1: cache the latest 10-K for each seed company
uv run hscm fetch --form 20-F TSM # any of 10-K / 20-F / 10-Q / 8-K, any ticker
uv run hscm sections              # M1: report what the section splitter found
uv run hscm verify extractions.json --out report.json   # M3: hallucination check
uv run pytest                     # unit tests
```

`fetch` writes documents to `data/cache/filings/` and an index of them to
`data/cache/manifest.json`. It is polite by default: 5 requests/second against
SEC's limit of 10, backs off and retries when SEC throttles (403/429) or 5xxs,
and never re-downloads a cached document unless you pass `--refresh`. The
manifest accumulates across runs, so fetching 8-Ks does not unregister your
10-Ks.

Section splitting is form-aware: 10-K and 20-F by item, 10-Q by its own Part I /
Part II numbering, and 8-K whole (a current report is one event, and its 1.01 /
2.01 numbering shares nothing with the annual forms).

## Current status

| Milestone | State |
|---|---|
| M1 — EDGAR fetcher, cache, section splitter | Written, **unverified against real filings** |
| M2 — Extraction on the Microsoft 10-K | **Blocked** (no filing to extract from) |
| M3 — Hallucination check | Logic written and unit-tested; not yet run on real extractions |
| M4–M8 | Not started |

**Why M1 is unverified.** The environment this was written in blocks outbound
access to `sec.gov`, `data.sec.gov`, `efts.sec.gov` and `gleif.org` at the
network policy level (the egress proxy answers 403 to CONNECT). No filing could
be fetched, so the section splitter has only been exercised against the
synthetic 10-K in `tests/test_sections.py`. Real filing HTML is messier than any
synthetic fixture, and the splitter is the part most likely to be wrong.
Run `uv run hscm fetch && uv run hscm sections` on a machine with SEC access and
read the output before extracting anything.

**Why M2 is blocked rather than stubbed.** Doing the extraction by hand requires
the cached Microsoft 10-K. Writing a fixture without one would mean inventing
filing sentences, which the project's ground rules prohibit outright.

## Layout

```
src/hscm/config.py    seed set, form types, paths, EDGAR access policy
src/hscm/edgar.py     ticker -> CIK -> filing metadata -> cached document
src/hscm/sections.py  HTML to text; Item splitting; concentration passages
src/hscm/verify.py    M3 — structural validation and the hallucination check
src/hscm/cli.py       fetch / sections / verify
```

## Data and licensing

SEC filings are US government works in the public domain. Everything this
project ingests comes from EDGAR, so the dataset can be redistributed and
rebuilt by anyone. Commercial supply chain databases are deliberately not used.

## Known exclusions

Companies that do not file with the SEC are excluded from the dataset entirely
— no placeholder nodes. That includes Samsung Electronics and SK Hynix, which
are dominant in high-bandwidth memory. Their absence overstates Micron's
apparent share of AI memory supply. This is documented rather than corrected
for; the limitations page (M8) will name every such exclusion.
