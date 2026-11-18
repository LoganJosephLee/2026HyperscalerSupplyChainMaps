"""The learning map: SEC graph + curated sources -> site/data/map.json.

The SEC pipeline (fetch -> extract -> verify -> build) produces graph.json,
where every edge is a sentence from a cached filing. This module layers the
hand-curated sources on top of it:

  sources/companies.yaml    which layer each company sits in, a plain summary,
                            and a cited sentence backing the summary up
  sources/claims.yaml       relationships from company announcements, major
                            news outlets and SEC filings we do not cache
  sources/corrections.yaml  hand fixes to SEC records the model read wrongly
  sources/layers.yaml       the stack, tours and glossary the site teaches with
  sources/web_checks.json   when each web quote was last found on its page

The rule is the same as for the SEC data: nothing reaches the page without a
quote and a link. What changes is where a quote may come from, and the site
shows a reader which kind of source each one is.

Quotes are checked two ways:

* A quote from an SEC filing we have cached is string-matched against that
  filing at build time. A miss fails the build.
* Any other quote is matched against its live web page by `hscm verify-web`,
  which records the result in web_checks.json. The build reports quotes with
  no passing check; `--strict` fails on them.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from . import config

SOURCES_DIR = config.REPO_ROOT / "sources"
SITE_DATA_DIR = config.REPO_ROOT / "site" / "data"
WEB_CHECKS_PATH = SOURCES_DIR / "web_checks.json"

SOURCE_TYPES = {
    "sec_filing": "SEC filing",
    "company": "Company announcement",
    "press": "News report",
}
RELATIONSHIPS = {
    "supplies": "supplies",
    "manufactures_for": "manufactures for",
    "leases_capacity_to": "rents capacity to",
    "licenses_to": "licenses to",
    "invests_in": "invests in",
    "finances": "helps finance",
    "partners_with": "partners with",
    "unclear": "has a relationship with",
}
# Relationship types whose arrow is not a flow of goods or services. The site
# draws them differently so "invests in" never reads as "supplies".
MONEY_TYPES = {"invests_in", "finances"}
UNDIRECTED_TYPES = {"partners_with", "unclear"}

_ACCESSION = re.compile(r"/edgar/data/\d+/(\d{18})/")
_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "‑": "-", "−": "-",
    " ": " ", "​": "", "‌": "", "‍": "", "﻿": "",
})
_WS = re.compile(r"\s+")


class LearnError(ValueError):
    """The curated sources are inconsistent. The message says how to fix it."""


def normalize_quote_text(text: str) -> str:
    """Comparison form for quotes: Unicode-normalised, punctuation folded, spaces collapsed.

    Pages and filings disagree about typography — curly or straight quotes,
    non-breaking spaces, the HTML's line breaks — and none of that changes what
    a sentence says. Wording, numbers and letter case all still have to match.
    """
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES)
    return _WS.sub(" ", text).strip()


_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?)'])")


def match_form(text: str) -> str:
    """normalize_quote_text, plus no space before closing punctuation.

    Pulling text out of HTML puts a space wherever a tag ends, so a link or a
    tagged company name followed by punctuation comes out as "Kairos Power ."
    or "Google 's" although the page reads "Kairos Power." and "Google's".
    Used only for matching; citation keys still use normalize_quote_text.
    """
    return _SPACE_BEFORE_PUNCT.sub(r"\1", normalize_quote_text(text))


def quote_key(url: str, quote: str) -> str:
    digest = hashlib.sha256(f"{url}\n{normalize_quote_text(quote)}".encode("utf-8")).hexdigest()
    return digest[:16]


def accession_of(url: str) -> str | None:
    match = _ACCESSION.search(url or "")
    return match.group(1) if match else None


# --- loading ----------------------------------------------------------------
def _load_yaml(name: str) -> dict:
    path = SOURCES_DIR / name
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise LearnError(f"{path} is not valid YAML: {exc}") from exc


def load_web_checks(path: Path | None = None) -> dict[str, dict]:
    path = path or WEB_CHECKS_PATH
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row["key"]: row for row in rows}


def save_web_checks(checks: dict[str, dict], path: Path | None = None) -> Path:
    path = path or WEB_CHECKS_PATH
    rows = sorted(checks.values(), key=lambda r: (r["url"], r["key"]))
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


class FilingTexts:
    """Normalised text of every cached filing, keyed by accession number."""

    def __init__(self) -> None:
        self._paths: dict[str, Path] = {}
        self._text: dict[str, str] = {}
        manifest = config.CACHE_DIR / "manifest.json"
        if manifest.exists():
            for row in json.loads(manifest.read_text(encoding="utf-8")):
                self._paths[row["accession"].replace("-", "")] = config.REPO_ROOT / row["cache_path"]

    def has(self, url: str) -> bool:
        acc = accession_of(url)
        return bool(acc and acc in self._paths and self._paths[acc].exists())

    def contains(self, url: str, quote: str) -> bool:
        from .sections import document_text

        acc = accession_of(url)
        if acc not in self._text:
            self._text[acc] = normalize_quote_text(document_text(self._paths[acc].read_bytes()))
        return normalize_quote_text(quote) in self._text[acc]


# --- the build --------------------------------------------------------------
@dataclass
class Report:
    sec_checked: int = 0
    web_found: int = 0
    web_unchecked: list[dict] = field(default_factory=list)
    web_failed: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"quotes matched in cached SEC filings: {self.sec_checked}",
            f"web quotes found on their pages:      {self.web_found}",
            f"web quotes not yet checked:           {len(self.web_unchecked)}",
            f"web quotes NOT found on last check:   {len(self.web_failed)}",
        ]
        for row in self.web_failed:
            lines.append(f"  not found: {row['url']}\n    {row['quote'][:100]}")
        return "\n".join(lines)


def _verify(cite: dict, filings: FilingTexts, checks: dict, report: Report, where: str) -> dict:
    """Check one citation and return the verification record the site shows."""
    url, quote = cite.get("url"), cite.get("quote")
    if not url or not quote:
        raise LearnError(f"{where}: every citation needs both `url` and `quote`.")
    if cite.get("type") not in SOURCE_TYPES:
        raise LearnError(
            f"{where}: source type {cite.get('type')!r} is not one of {sorted(SOURCE_TYPES)}."
        )
    if filings.has(url):
        for text in filter(None, [quote, cite.get("context_quote")]):
            if not filings.contains(url, text):
                raise LearnError(
                    f"{where}: quote not found in the cached filing {url}.\n  {text[:160]}\n"
                    f"Copy the sentence from the filing exactly, or fix the URL."
                )
        report.sec_checked += 1
        return {"method": "Matched word for word in the cached filing", "status": "found"}

    row = checks.get(quote_key(url, quote))
    if row is None:
        report.web_unchecked.append({"url": url, "quote": quote, "where": where})
        return {"method": "Not yet checked against the live page", "status": "unchecked"}
    if row["status"] != "found":
        report.web_failed.append({"url": url, "quote": quote, "where": where})
        return {"method": row.get("method", ""), "status": row["status"], "checked": row.get("checked")}
    report.web_found += 1
    return {"method": row.get("method", ""), "status": "found", "checked": row.get("checked")}


def _cite(cite: dict, verification: dict) -> dict:
    return {
        "quote": cite["quote"],
        "url": cite["url"],
        "publisher": cite.get("publisher", ""),
        "type": cite["type"],
        "date": str(cite.get("date") or ""),
        "context": cite.get("context"),
        "note": cite.get("note"),
        "verified": verification,
    }


def _sec_evidence_matches(evidence: dict, match: dict) -> bool:
    url = evidence.get("source_url") or ""
    sentence = evidence.get("source_sentence") or ""
    if match.get("url_contains") and match["url_contains"] not in url:
        return False
    if match.get("sentence_starts") and not normalize_quote_text(sentence).startswith(
        normalize_quote_text(match["sentence_starts"])
    ):
        return False
    return True


def build_map(graph: dict, strict: bool = False) -> tuple[dict, Report]:
    report = Report()
    filings = FilingTexts()
    checks = load_web_checks()

    companies_doc = _load_yaml("companies.yaml")
    claims_doc = _load_yaml("claims.yaml")
    corrections_doc = _load_yaml("corrections.yaml")
    teaching = _load_yaml("layers.yaml")

    layers = teaching.get("layers") or []
    layer_keys = {layer["key"] for layer in layers}
    hidden_tickers = set((companies_doc.get("hidden") or {}).keys())

    # Companies ---------------------------------------------------------------
    by_ref: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for entry in companies_doc.get("companies") or []:
        where = f"companies.yaml {entry.get('ref')}"
        if entry.get("layer") not in layer_keys:
            raise LearnError(f"{where}: layer {entry.get('layer')!r} is not defined in layers.yaml.")
        if not entry.get("summary"):
            raise LearnError(f"{where}: needs a one-line `summary`.")
        node = {
            "id": entry["id"],
            "ref": entry["ref"],
            "name": entry["name"],
            "legal_name": None,
            "ticker": None,
            "cik": entry.get("cik"),
            "files_with_sec": entry.get("cik") is not None,
            "layer": entry["layer"],
            "summary": entry["summary"],
            "about": None,
            "is_seed": False,
        }
        about = entry.get("about")
        if isinstance(about, dict):
            node["about"] = _cite(about, _verify(about, filings, checks, report, where))
        elif about != "FROM_CLAIMS":
            raise LearnError(f"{where}: `about` must be a citation or FROM_CLAIMS.")
        by_ref[entry["ref"]] = node
        by_id[entry["id"]] = node

    hidden_ids = set()
    for sec_node in graph["nodes"]:
        if sec_node.get("ticker") in hidden_tickers:
            hidden_ids.add(sec_node["id"])
            continue
        node = by_id.get(sec_node["id"])
        if node is None:
            raise LearnError(
                f"{sec_node['canonical_name']} ({sec_node['id']}) is in the SEC graph but has no "
                f"entry in sources/companies.yaml. Add it, or list its ticker under `hidden`."
            )
        node["legal_name"] = sec_node["canonical_name"]
        node["ticker"] = sec_node.get("ticker")
        node["is_seed"] = bool(sec_node.get("is_seed"))
        node["files_with_sec"] = bool(sec_node.get("has_sec_filings"))

    seed_tickers = set(config.SEED_TICKERS)
    for node in by_id.values():
        if node["ticker"] is None and node["ref"].isupper() and node["cik"]:
            node["ticker"] = node["ref"]
        if node["ticker"] in seed_tickers:
            node["is_seed"] = True

    # Edges -------------------------------------------------------------------
    edges: dict[tuple[str, str], dict] = {}

    def edge(supplier: str, buyer: str) -> dict:
        key = (supplier, buyer)
        if key not in edges:
            edges[key] = {"source": supplier, "target": buyer, "deals": []}
        return edges[key]

    corrections = corrections_doc.get("corrections") or []
    used_corrections: set[int] = set()

    for sec_edge in graph["edges"]:
        if sec_edge["source"] in hidden_ids or sec_edge["target"] in hidden_ids:
            continue
        for ev in sec_edge["evidence"]:
            supplier, buyer = sec_edge["source"], sec_edge["target"]
            rel = ev.get("relationship_type") or "unclear"
            context = None
            quantified = ev.get("quantified_pct")
            basis = ev.get("quantified_basis")
            correction_note = None
            dropped = False
            for i, fix in enumerate(corrections):
                if not _sec_evidence_matches(ev, fix.get("match") or {}):
                    continue
                used_corrections.add(i)
                action = fix.get("action")
                if action == "drop":
                    dropped = True
                elif action == "flip":
                    supplier, buyer = buyer, supplier
                if fix.get("relationship"):
                    rel = fix["relationship"]
                if "quantified_basis" in fix:
                    basis = fix["quantified_basis"]
                context = fix.get("context_quote") or context
                correction_note = fix.get("reason")
            if dropped:
                continue
            cite = {
                "quote": ev["source_sentence"],
                "url": ev["source_url"],
                "publisher": f"{ev.get('form_type', 'SEC')} filing",
                "type": "sec_filing",
                "date": ev.get("filing_date"),
                "context": context,
                "context_quote": context,
                "note": correction_note,
            }
            verification = _verify(cite, filings, checks, report, f"SEC record {ev['source_url']}")
            amount = (
                f"{quantified:g}% of {_basis_owner(basis, supplier, buyer, by_id)}"
                if quantified is not None and basis else None
            )
            # One deal per relationship type per edge: several sentences in
            # one filing describing the same tie are corroboration, not five
            # separate deals.
            deals = edge(supplier, buyer)["deals"]
            deal = next((d for d in deals if d["origin"] == "sec_pipeline" and d["relationship"] == rel), None)
            if deal is None:
                deal = {
                    "relationship": rel,
                    "what": ev.get("product_or_service"),
                    "what_is_label": True,
                    "amount": amount,
                    "confirmed": True,
                    "origin": "sec_pipeline",
                    "evidence": [],
                }
                deals.append(deal)
            elif amount and not deal["amount"]:
                deal["amount"] = amount
            deal["evidence"].append(_cite(cite, verification))

    for i, fix in enumerate(corrections):
        if i not in used_corrections:
            raise LearnError(
                f"corrections.yaml entry {i + 1} ({fix.get('reason', '')[:60]}) matched no SEC "
                f"record. The record may have changed; update or remove the correction."
            )

    for claim in claims_doc.get("claims") or []:
        where = f"claims.yaml {claim.get('id')}"
        supplier = by_ref.get(claim.get("supplier"))
        buyer = by_ref.get(claim.get("buyer"))
        if supplier is None or buyer is None:
            missing = claim.get("supplier") if supplier is None else claim.get("buyer")
            raise LearnError(f"{where}: {missing!r} is not a `ref` in companies.yaml.")
        if claim.get("relationship") not in RELATIONSHIPS:
            raise LearnError(f"{where}: unknown relationship {claim.get('relationship')!r}.")
        evidence = claim.get("evidence") or []
        if not evidence:
            raise LearnError(f"{where}: a claim needs at least one quoted source.")
        cites = []
        for j, cite in enumerate(evidence):
            cites.append(_cite(cite, _verify(cite, filings, checks, report, f"{where} source {j + 1}")))
        edge(supplier["id"], buyer["id"])["deals"].append({
            "id": claim["id"],
            "relationship": claim["relationship"],
            "what": claim.get("what"),
            "what_is_label": False,
            "amount": claim.get("amount"),
            "confirmed": claim.get("confirmed", True),
            "origin": "curated",
            "evidence": cites,
        })

    # Companies whose evidence is only their relationships ---------------------
    connected = {e["source"] for e in edges.values()} | {e["target"] for e in edges.values()}
    for node in by_id.values():
        if node["about"] is None and node["id"] not in connected:
            raise LearnError(
                f"companies.yaml {node['ref']}: `about: FROM_CLAIMS` but no cited relationship "
                f"mentions it. Give it its own citation."
            )

    # Summarise edges ---------------------------------------------------------
    strength = {"sec_filing": 3, "company": 2, "press": 1}
    out_edges = []
    for (supplier, buyer), e in edges.items():
        types = sorted({c["type"] for d in e["deals"] for c in d["evidence"]}, key=lambda t: -strength[t])
        rels = [d["relationship"] for d in e["deals"]]
        e["id"] = f"{supplier}--{buyer}"
        e["source_types"] = types
        e["strongest_source"] = types[0]
        e["kind"] = (
            "money" if all(r in MONEY_TYPES for r in rels)
            else "undirected" if all(r in UNDIRECTED_TYPES for r in rels)
            else "supply"
        )
        e["confirmed"] = any(d["confirmed"] for d in e["deals"])
        e["latest_date"] = max((c["date"] for d in e["deals"] for c in d["evidence"]), default="")
        out_edges.append(e)
    out_edges.sort(key=lambda e: e["id"])

    # Tours must point at edges that exist ------------------------------------
    edge_ids = {e["id"] for e in out_edges}
    tours = teaching.get("tours") or []
    for tour in tours:
        for step in tour["steps"]:
            a, b = by_ref.get(step["from"]), by_ref.get(step["to"])
            if a is None or b is None:
                raise LearnError(f"layers.yaml tour {tour['id']}: unknown company in step {step}.")
            step["from_id"], step["to_id"] = a["id"], b["id"]
            step["edge"] = f"{a['id']}--{b['id']}"
            if step["edge"] not in edge_ids:
                raise LearnError(
                    f"layers.yaml tour {tour['id']}: no cited relationship from {a['name']} to "
                    f"{b['name']}. A tour may only walk along cited edges."
                )

    nodes = sorted(by_id.values(), key=lambda n: n["name"].lower())
    for node in nodes:
        node["degree"] = sum(1 for e in out_edges if node["id"] in (e["source"], e["target"]))

    all_dates = [c["date"] for e in out_edges for d in e["deals"] for c in d["evidence"] if c["date"]]
    counts = {t: sum(1 for e in out_edges for d in e["deals"] for c in d["evidence"] if c["type"] == t)
              for t in SOURCE_TYPES}
    payload = {
        "meta": {
            "built": date.today().isoformat(),
            "newest_source_date": max(all_dates) if all_dates else None,
            "sec_data_as_of": graph["meta"].get("data_as_of"),
            "company_count": len(nodes),
            "relationship_count": len(out_edges),
            "citation_count": sum(counts.values()),
            "citations_by_type": counts,
            "source_types": SOURCE_TYPES,
            "relationship_labels": RELATIONSHIPS,
            "unverified_web_quotes": len(report.web_unchecked) + len(report.web_failed),
        },
        "layers": layers,
        "nodes": nodes,
        "edges": out_edges,
        "tours": tours,
        "glossary": teaching.get("glossary") or [],
    }
    if strict and (report.web_unchecked or report.web_failed):
        raise LearnError(
            "--strict: some web quotes have no passing check.\n" + report.summary()
            + "\nRun `hscm verify-web` on a machine with internet access."
        )
    return payload, report


def _basis_owner(basis: str | None, supplier: str, buyer: str, by_id: dict) -> str:
    """Whose revenue or cost a disclosed percentage is a share of."""
    supplier_name = by_id.get(supplier, {}).get("name", "the supplier")
    buyer_name = by_id.get(buyer, {}).get("name", "the buyer")
    return {
        "revenue": f"{supplier_name}'s revenue",
        "cost": f"{buyer_name}'s costs",
        "units": "the units described in the quote",
    }.get(basis or "", "a disclosed total")


def write_map(payload: dict, path: Path | None = None) -> Path:
    path = path or SITE_DATA_DIR / "map.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    # A copy as a script, so the site also works opened straight from disk,
    # where browsers refuse to fetch() a local JSON file.
    (path.parent / "map.js").write_text(
        "window.MAP_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8"
    )
    return path


# --- web verification -------------------------------------------------------
def web_citations() -> list[dict]:
    """Every citation in the curated files that is not checkable against the cache."""
    filings = FilingTexts()
    out = []
    for entry in _load_yaml("companies.yaml").get("companies") or []:
        about = entry.get("about")
        if isinstance(about, dict) and not filings.has(about["url"]):
            out.append({"url": about["url"], "quote": about["quote"], "where": f"company {entry['ref']}"})
    for claim in _load_yaml("claims.yaml").get("claims") or []:
        for cite in claim.get("evidence") or []:
            if not filings.has(cite["url"]):
                out.append({"url": cite["url"], "quote": cite["quote"], "where": f"claim {claim['id']}"})
    return out


def page_text(html: str) -> str:
    """The words a reader sees on the page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_quote_text(soup.get_text(" "))


def embedded_text(html: str) -> str:
    """Article text a page ships as data for its own JavaScript to render.

    Many company newsrooms send the article inside a JSON block (structured
    data for search engines, or a framework's page data) and draw it in the
    browser. The words are the publisher's own article, so a quote found there
    counts; ordinary scripts are still ignored.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    chunks = []
    for tag in soup.find_all("script"):
        kind = (tag.get("type") or "").lower()
        if "json" in kind or tag.get("id") == "__NEXT_DATA__":
            raw = tag.string or ""
            try:
                strings = _json_strings(json.loads(raw))
            except ValueError:
                strings = [raw]
            for value in strings:
                # Values are often HTML fragments themselves.
                chunks.append(BeautifulSoup(value, "lxml").get_text(" ") if "<" in value else value)
    return normalize_quote_text(" ".join(chunks))


def _json_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _json_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _json_strings(v)]
    return []


_WORD = re.compile(r"[A-Za-z0-9$%.,-]{4,}")


def word_coverage(quote: str, text: str) -> float:
    """Share of the quote's longer words that appear anywhere in `text`."""
    words = {w.strip(".,").lower() for w in _WORD.findall(normalize_quote_text(quote))}
    words.discard("")
    if not words:
        return 0.0
    haystack = text.lower()
    return sum(1 for w in words if w in haystack) / len(words)


# Below this share of the quote's words, the page we were served is not the
# article: a bot check, a cookie wall, or a shell that JavaScript fills in.
ARTICLE_LOADED_THRESHOLD = 0.6
DEBUG_DIR = config.REPO_ROOT / "data" / "web_debug"


def verify_web(fetch, today: str | None = None, debug_dir: Path | None = None) -> dict[str, dict]:
    """Fetch each cited page and record whether its quote is there.

    `fetch(url) -> str` is injected so tests need no network. Four outcomes:

    found        the exact words are on the page (or in its embedded article data)
    not_found    the article loaded and most of the quote's words are on it, but
                 not as quoted. This is the result to act on: the wording or the
                 page changed.
    unreadable   the page came back, but it is not the article: too few of the
                 quote's words appear to judge.
    unreachable  the request failed.

    `unreadable` and `unreachable` say nothing about whether the sentence is
    there, so they never overwrite an earlier `found`; the page should be
    checked by eye. The HTML of every page that was not `found` is saved to
    data/web_debug/ so a person can see what the script actually received.
    """
    today = today or date.today().isoformat()
    debug_dir = debug_dir or DEBUG_DIR
    checks = load_web_checks()
    pages: dict[str, tuple[str, str] | None] = {}
    raw_html: dict[str, str] = {}
    citations = web_citations()
    # A quote that was edited or removed leaves its old check behind; drop it so
    # the report only ever describes quotes the site actually shows.
    current = {quote_key(c["url"], c["quote"]) for c in citations}
    checks = {k: v for k, v in checks.items() if k in current}
    for cite in citations:
        url = cite["url"]
        if url not in pages:
            try:
                html = fetch(url)
                raw_html[url] = html
                pages[url] = (match_form(page_text(html)), match_form(embedded_text(html)))
            except Exception:  # noqa: BLE001 — any failure is "could not read"
                pages[url] = None
        key = quote_key(url, cite["quote"])
        quote = match_form(cite["quote"])
        method = "Fetched the page and matched the quote word for word"
        if pages[url] is None:
            status = "unreachable"
        else:
            visible, embedded = pages[url]
            if quote in visible:
                status = "found"
            elif quote in embedded:
                status = "found"
                method = "Fetched the page and matched the quote word for word in its article data"
            elif word_coverage(cite["quote"], visible + " " + embedded) >= ARTICLE_LOADED_THRESHOLD:
                status = "not_found"
            else:
                status = "unreadable"
        if status != "found" and url in raw_html:
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / f"{key}.html").write_text(raw_html[url], encoding="utf-8")
        if status in ("unreachable", "unreadable") and checks.get(key, {}).get("status") == "found":
            checks[key]["last_attempt"] = {"status": status, "date": today}
            continue  # keep the last good check rather than erase it
        checks[key] = {
            "key": key, "url": url, "quote": cite["quote"], "status": status,
            "method": method, "checked": today,
        }
    save_web_checks(checks)
    return checks
