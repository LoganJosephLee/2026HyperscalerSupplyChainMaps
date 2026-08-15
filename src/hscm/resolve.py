"""Entity resolution: raw filing names -> CIKs on SEC's spine.

Decision 4's shape, and the reasoning behind the parts that look fussy:

* SEC's `company_tickers.json` is the canonical spine. A name that cannot be
  matched to it is not a company we can put in the graph.
* Matching is deliberately conservative. Anything below `MATCH_ACCEPT_THRESHOLD`
  goes to a CSV review queue for a human, because a wrong merge is invisible
  once it is in the graph while a queue row is merely tedious.
* Human decisions are written back to `aliases.yaml`, which is version
  controlled and is the part of this project a scraper cannot reproduce.

`aliases.yaml` has two sections:

    aliases:
      "TSMC": {cik: 1046179, note: "..."}
    excluded:
      "Samsung Electronics": "Korean-listed; does not file with the SEC"

An entry under `excluded` is a recorded decision that a company is real and
outside the dataset — not a placeholder node. It keeps the name out of the
review queue on every subsequent run and feeds the limitations page.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from . import config

# --- name normalisation -----------------------------------------------------
# Suffixes carry no identity: "NVIDIA Corporation" and "NVIDIA Corp" are one
# company. Stripping them before comparison is what makes exact matching work
# on the majority of names, leaving fuzzy matching for the genuinely hard ones.
_CORPORATE_SUFFIXES = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "COMPANIES",
    "LTD", "LIMITED", "LLC", "LP", "LLP", "PLC", "AG", "NV", "SA", "SE", "AB",
    "OYJ", "KK", "GMBH", "HOLDING", "HOLDINGS", "GROUP", "THE", "AND",
}
_PUNCTUATION = re.compile(r"[^A-Z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Uppercase, strip punctuation, drop corporate suffixes and filler words."""
    text = _PUNCTUATION.sub(" ", name.upper())
    tokens = [t for t in _WHITESPACE.sub(" ", text).split() if t and t not in _CORPORATE_SUFFIXES]
    return " ".join(tokens)


# --- the spine --------------------------------------------------------------
@dataclass(frozen=True)
class SpineEntry:
    cik: int
    ticker: str
    title: str
    normalized: str


class Spine:
    """SEC's company_tickers.json, indexed for matching."""

    def __init__(self, entries: list[SpineEntry]) -> None:
        self.entries = entries
        self._by_normalized: dict[str, SpineEntry] = {}
        self._by_ticker: dict[str, SpineEntry] = {}
        self._by_cik: dict[int, SpineEntry] = {}
        for entry in entries:
            # First writer wins: company_tickers.json lists share classes
            # separately (GOOG and GOOGL both map to Alphabet), and the first
            # is as good a representative as the second.
            self._by_normalized.setdefault(entry.normalized, entry)
            self._by_ticker.setdefault(entry.ticker.upper(), entry)
            self._by_cik.setdefault(entry.cik, entry)

    @classmethod
    def from_json(cls, payload: dict) -> "Spine":
        entries = [
            SpineEntry(
                cik=int(row["cik_str"]),
                ticker=row["ticker"],
                title=row["title"],
                normalized=normalize_name(row["title"]),
            )
            for row in payload.values()
        ]
        return cls(entries)

    @classmethod
    def load(cls, path: Path | None = None) -> "Spine":
        path = path or config.CACHE_DIR / "company_tickers.json"
        if not path.exists():
            raise FileNotFoundError(
                f"No company spine at {path}. Run `hscm fetch` first — it caches "
                f"SEC's company_tickers.json."
            )
        return cls.from_json(json.loads(path.read_text()))

    def by_cik(self, cik: int) -> SpineEntry | None:
        return self._by_cik.get(cik)

    def by_ticker(self, ticker: str) -> SpineEntry | None:
        return self._by_ticker.get(ticker.upper())

    def exact(self, normalized: str) -> SpineEntry | None:
        return self._by_normalized.get(normalized)

    def best_fuzzy(self, normalized: str, limit: int = 3) -> list[tuple[float, SpineEntry]]:
        """Closest spine entries by normalised-name similarity.

        Candidates are pre-filtered on a shared first token so we compare
        against a handful of names instead of eleven thousand; names with no
        token in common are not plausible matches for the same company.
        """
        if not normalized:
            return []
        tokens = set(normalized.split())
        matcher = SequenceMatcher(autojunk=False)
        matcher.set_seq2(normalized)

        scored: list[tuple[float, SpineEntry]] = []
        for entry in self.entries:
            if not tokens & set(entry.normalized.split()):
                continue
            matcher.set_seq1(entry.normalized)
            if matcher.real_quick_ratio() < 0.5:
                continue
            scored.append((matcher.ratio(), entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:limit]


# --- alias file -------------------------------------------------------------
@dataclass
class Aliases:
    """Human decisions about raw names. The asset this project actually builds."""

    aliases: dict[str, dict] = field(default_factory=dict)
    excluded: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Aliases":
        path = path or config.ALIASES_PATH
        if not path.exists():
            return cls()
        payload = yaml.safe_load(path.read_text()) or {}
        return cls(
            aliases={str(k): dict(v) for k, v in (payload.get("aliases") or {}).items()},
            excluded={str(k): str(v) for k, v in (payload.get("excluded") or {}).items()},
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or config.ALIASES_PATH
        payload = {
            "aliases": dict(sorted(self.aliases.items())),
            "excluded": dict(sorted(self.excluded.items())),
        }
        path.write_text(
            "# Entity resolution decisions, made by hand and reviewed in version control.\n"
            "# aliases:  raw filing name -> the CIK it refers to\n"
            "# excluded: raw filing name -> why it is not in the dataset\n"
            "# Regenerate the queue with `hscm review build`; apply it with `hscm review apply`.\n"
            + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)
        )
        return path

    def lookup(self, raw_name: str) -> dict | None:
        return self.aliases.get(raw_name) or self.aliases.get(raw_name.strip())

    def is_excluded(self, raw_name: str) -> bool:
        return raw_name in self.excluded or raw_name.strip() in self.excluded


# --- resolution -------------------------------------------------------------
@dataclass(frozen=True)
class Resolution:
    raw_name: str
    status: str  # resolved | review | excluded
    cik: int | None = None
    canonical_name: str | None = None
    ticker: str | None = None
    score: float = 0.0
    method: str = ""
    candidates: tuple[tuple[float, str, int], ...] = ()
    reason: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.status == "resolved"


class Resolver:
    def __init__(self, spine: Spine, aliases: Aliases | None = None, threshold: float | None = None):
        self.spine = spine
        self.aliases = aliases or Aliases.load()
        self.threshold = threshold if threshold is not None else config.MATCH_ACCEPT_THRESHOLD

    def resolve(self, raw_name: str) -> Resolution:
        name = (raw_name or "").strip()
        if not name:
            return Resolution(raw_name, "review", reason="empty name")

        if self.aliases.is_excluded(name):
            return Resolution(
                name, "excluded", reason=self.aliases.excluded.get(name, "excluded by aliases.yaml")
            )

        decided = self.aliases.lookup(name)
        if decided and decided.get("cik") is not None:
            entry = self.spine.by_cik(int(decided["cik"]))
            return Resolution(
                raw_name=name,
                status="resolved",
                cik=int(decided["cik"]),
                canonical_name=(entry.title if entry else decided.get("canonical_name")),
                ticker=entry.ticker if entry else decided.get("ticker"),
                score=1.0,
                method="alias",
            )

        normalized = normalize_name(name)

        exact = self.spine.exact(normalized)
        if exact:
            return Resolution(
                name, "resolved", exact.cik, exact.title, exact.ticker, 1.0, "exact"
            )

        # A bare ticker in a filing ("we purchase from AMD") is common enough to
        # be worth its own check, but only for short all-caps strings — matching
        # any word against the ticker table produces nonsense.
        if 1 < len(normalized) <= 5 and normalized.isalpha():
            by_ticker = self.spine.by_ticker(normalized)
            if by_ticker:
                return Resolution(
                    name, "resolved", by_ticker.cik, by_ticker.title,
                    by_ticker.ticker, 0.99, "ticker",
                )

        candidates = self.spine.best_fuzzy(normalized)
        if candidates and candidates[0][0] >= self.threshold:
            score, entry = candidates[0]
            return Resolution(
                name, "resolved", entry.cik, entry.title, entry.ticker, score, "fuzzy",
                tuple((s, e.title, e.cik) for s, e in candidates),
            )

        return Resolution(
            raw_name=name,
            status="review",
            score=candidates[0][0] if candidates else 0.0,
            method="fuzzy",
            candidates=tuple((s, e.title, e.cik) for s, e in candidates),
            reason="below acceptance threshold" if candidates else "no candidate found",
        )


# --- review queue -----------------------------------------------------------
REVIEW_COLUMNS = [
    "raw_name",
    "occurrences",
    "best_match_name",
    "best_match_cik",
    "best_match_ticker",
    "score",
    "alternatives",
    "example_sentence",
    "example_url",
    # Filled in by hand:
    "decision",  # accept | cik | exclude | skip
    "cik",
    "reason",
]


def build_review_queue(
    records: list[dict], resolver: Resolver, path: Path | None = None
) -> tuple[Path, list[Resolution]]:
    """Write every unresolved name to a CSV for a human to decide.

    Rows are ordered by how often the name appears, so the names that matter
    most to the graph are at the top of the file.
    """
    path = path or config.REVIEW_QUEUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    counts: Counter[str] = Counter()
    examples: dict[str, dict] = {}
    for record in records:
        for key in ("buyer_name_raw", "supplier_name_raw"):
            name = (record.get(key) or "").strip()
            if not name:
                continue
            counts[name] += 1
            examples.setdefault(name, record)

    pending: list[Resolution] = []
    for name, _ in counts.most_common():
        resolution = resolver.resolve(name)
        if resolution.status == "review":
            pending.append(resolution)

    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for resolution in pending:
            best = resolution.candidates[0] if resolution.candidates else None
            example = examples.get(resolution.raw_name, {})
            writer.writerow(
                {
                    "raw_name": resolution.raw_name,
                    "occurrences": counts[resolution.raw_name],
                    "best_match_name": best[1] if best else "",
                    "best_match_cik": best[2] if best else "",
                    "best_match_ticker": "",
                    "score": f"{resolution.score:.3f}",
                    "alternatives": " | ".join(
                        f"{score:.2f} {title} ({cik})"
                        for score, title, cik in resolution.candidates[1:]
                    ),
                    "example_sentence": (example.get("source_sentence") or "")[:300],
                    "example_url": example.get("source_url", ""),
                    "decision": "",
                    "cik": "",
                    "reason": "",
                }
            )
    return path, pending


def apply_review_queue(
    aliases: Aliases, path: Path | None = None
) -> tuple[int, int, list[str]]:
    """Fold decided rows of the review CSV into aliases.yaml.

    Returns (aliases added, exclusions added, problems). Undecided rows are
    left alone so the queue can be worked through over several sittings.
    """
    path = path or config.REVIEW_QUEUE_PATH
    if not path.exists():
        raise FileNotFoundError(f"No review queue at {path}. Run `hscm review build` first.")

    added = excluded = 0
    problems: list[str] = []

    with path.open(newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            decision = (row.get("decision") or "").strip().lower()
            raw = (row.get("raw_name") or "").strip()
            if not decision or decision == "skip" or not raw:
                continue

            if decision == "exclude":
                reason = (row.get("reason") or "").strip()
                if not reason:
                    problems.append(f"line {line}: exclude needs a reason ({raw})")
                    continue
                aliases.excluded[raw] = reason
                excluded += 1
                continue

            cik_text = (row.get("cik") or "").strip() or (
                row.get("best_match_cik", "").strip() if decision == "accept" else ""
            )
            if not cik_text.isdigit():
                problems.append(f"line {line}: decision {decision!r} needs a numeric cik ({raw})")
                continue

            entry: dict = {"cik": int(cik_text)}
            if row.get("reason", "").strip():
                entry["note"] = row["reason"].strip()
            aliases.aliases[raw] = entry
            added += 1

    return added, excluded, problems
