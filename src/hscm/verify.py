"""M3 — the hallucination check.

Every extracted relationship claims a verbatim `source_sentence` from a filing.
This module string-matches that sentence back into the filing text and drops
anything that is not there. It never repairs a record: a sentence that does not
appear is evidence the extraction is unreliable, and quietly fixing it destroys
exactly the signal we are trying to measure.

Two independent gates:

* `validate_record` — structural. Missing `source_sentence` or `source_url`,
  bad enum values, a percentage without a basis. Per Decision 3 these are
  discarded at ingest, not fixed.
* `verify_sentence` — evidentiary. Is this sentence actually in the filing?

Match levels, in decreasing strictness:

* `exact`      — substring of the normalised document text.
* `normalized` — substring once case, line breaks and punctuation spacing are
                 collapsed. Still a pass: a sentence broken across two lines of
                 filing HTML is a formatting artefact, not a fabrication.
* `fuzzy`      — close but not equal. Reported as a FAILURE with the closest
                 passage attached, because "nearly verbatim" is precisely how a
                 model paraphrases a claim the filing did not make. The closest
                 passage is there to debug the prompt, not to be accepted.
* `not_found`  — no similar passage at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import urlparse

from .sections import normalize_text

REQUIRED_FIELDS = ("buyer_name_raw", "supplier_name_raw", "source_sentence", "source_url")

# Every verb here takes the supplier as its subject, so an edge always reads
# supplier -> buyer in the same direction as the verb. "purchases_from" used to
# be in this set and was the one verb whose subject was the buyer, which made
# "TSMC purchases_from Broadcom" the printed form of "Broadcom buys from TSMC".
# Do not add a buyer-subject verb back.
VALID_RELATIONSHIP_TYPES = {
    "supplies",
    "manufactures_for",
    "leases_capacity_to",
    "licenses_to",
    "unclear",
}
VALID_FORM_TYPES = {"10-K", "20-F", "10-Q", "8-K"}
# A percentage is only meaningful with its denominator. "other" exists so that a
# real number with an unusual denominator survives instead of being thrown away;
# the sentence still has to name what it is a share of.
QUANTIFIED_BASES = {"revenue", "cost", "units", "other"}
VALID_BASES = QUANTIFIED_BASES | {None}
VALID_CONFIDENCE = {"high", "medium", "low"}

FUZZY_REPORT_THRESHOLD = 0.80  # below this we do not bother showing a "closest" passage
MIN_SENTENCE_CHARS = 25  # a "sentence" shorter than this cannot support a claim

_NON_ALNUM = re.compile(r"[^a-z0-9%]+")


def is_sec_url(url: str) -> bool:
    """True only if the host itself is sec.gov.

    Checked on the parsed host, not as a substring: "https://example.com/sec.gov/"
    contains the string and is not a filing.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "sec.gov" or host.endswith(".sec.gov")


def collapse(text: str) -> str:
    """Case-, whitespace- and punctuation-insensitive form used for matching."""
    return _NON_ALNUM.sub(" ", text.lower()).strip()


# --- structural validation --------------------------------------------------
def validate_record(record: dict) -> list[str]:
    """Return the reasons this record cannot be ingested. Empty means valid."""
    errors: list[str] = []
    for field_name in REQUIRED_FIELDS:
        value = record.get(field_name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty {field_name}")

    sentence = record.get("source_sentence")
    if isinstance(sentence, str) and 0 < len(sentence.strip()) < MIN_SENTENCE_CHARS:
        errors.append(f"source_sentence shorter than {MIN_SENTENCE_CHARS} characters")

    url = record.get("source_url")
    if isinstance(url, str) and url and not is_sec_url(url):
        errors.append(f"source_url {url!r} is not an sec.gov link")

    if record.get("relationship_type") not in VALID_RELATIONSHIP_TYPES:
        errors.append(f"relationship_type {record.get('relationship_type')!r} not recognised")
    if record.get("form_type") not in VALID_FORM_TYPES:
        errors.append(f"form_type {record.get('form_type')!r} not recognised")
    if record.get("extraction_confidence") not in VALID_CONFIDENCE:
        errors.append(f"extraction_confidence {record.get('extraction_confidence')!r} not recognised")

    filing_date = record.get("filing_date")
    if not isinstance(filing_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", filing_date or ""):
        errors.append(f"filing_date {filing_date!r} is not YYYY-MM-DD")

    pct = record.get("quantified_pct")
    basis = record.get("quantified_basis")
    if pct is not None:
        if not isinstance(pct, (int, float)) or isinstance(pct, bool):
            errors.append("quantified_pct is not a number")
        elif not 0 < pct <= 100:
            errors.append(f"quantified_pct {pct} outside (0, 100]")
        if basis not in QUANTIFIED_BASES:
            errors.append("quantified_pct given without a quantified_basis")
    if basis not in VALID_BASES:
        errors.append(f"quantified_basis {basis!r} not recognised")

    return errors


# --- evidentiary verification ----------------------------------------------
@dataclass(frozen=True)
class SentenceCheck:
    level: str  # exact | normalized | fuzzy | not_found
    ratio: float
    closest_text: str | None = None

    @property
    def supported(self) -> bool:
        return self.level in {"exact", "normalized"}


def _shingles(tokens: Sequence[str], size: int) -> list[tuple[int, str]]:
    return [(i, " ".join(tokens[i : i + size])) for i in range(0, max(1, len(tokens) - size + 1))]


class PreparedDocument:
    """A filing normalised once and reused for every sentence checked against it.

    Normalising and tokenising a 2 MB 10-K per record is the difference between
    a report that takes seconds and one that takes minutes; the token index is
    built lazily because it is only needed when a sentence fails to match
    outright.
    """

    __slots__ = ("normalized", "collapsed", "_tokens", "_positions")

    def __init__(self, text: str) -> None:
        self.normalized = normalize_text(text)
        self.collapsed = collapse(self.normalized)
        self._tokens: list[str] | None = None
        self._positions: dict[str, list[int]] | None = None

    @property
    def tokens(self) -> list[str]:
        if self._tokens is None:
            self._tokens = self.collapsed.split()
        return self._tokens

    @property
    def positions(self) -> dict[str, list[int]]:
        if self._positions is None:
            index: dict[str, list[int]] = {}
            for position, token in enumerate(self.tokens):
                index.setdefault(token, []).append(position)
            self._positions = index
        return self._positions


def _closest_window(
    sentence_collapsed: str, document: PreparedDocument, max_probes: int = 200
) -> tuple[float, str]:
    """Best similarity between the sentence and any same-length window of the doc.

    Anchored on shared word runs so we compare a bounded number of windows
    instead of sliding across the whole filing.
    """
    sentence_tokens = sentence_collapsed.split()
    if not sentence_tokens:
        return 0.0, ""

    doc_tokens = document.tokens
    if not doc_tokens:
        return 0.0, ""

    positions = document.positions
    width = len(sentence_tokens)
    anchors: list[int] = []
    for size in (8, 5, 3, 1):
        for offset, shingle in _shingles(sentence_tokens, size):
            head = shingle.split()[0]
            for start in positions.get(head, ())[:max_probes]:
                if doc_tokens[start : start + size] == shingle.split():
                    anchors.append(max(0, start - offset))
            if len(anchors) >= max_probes:
                break
        if anchors:
            break

    if not anchors:
        return 0.0, ""

    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(sentence_collapsed)
    best_ratio = 0.0
    best_text = ""
    for start in sorted(set(anchors))[:max_probes]:
        window = " ".join(doc_tokens[start : start + width])
        matcher.set_seq1(window)
        if matcher.real_quick_ratio() <= best_ratio or matcher.quick_ratio() <= best_ratio:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best_ratio, best_text = ratio, window
    return best_ratio, best_text


def verify_sentence(sentence: str, document: str | PreparedDocument) -> SentenceCheck:
    """Check one claimed verbatim sentence against one filing's text."""
    prepared = document if isinstance(document, PreparedDocument) else PreparedDocument(document)

    normalized_sentence = normalize_text(sentence)
    if normalized_sentence and normalized_sentence in prepared.normalized:
        return SentenceCheck("exact", 1.0)

    collapsed_sentence = collapse(normalized_sentence)
    if collapsed_sentence and collapsed_sentence in prepared.collapsed:
        return SentenceCheck("normalized", 1.0)

    ratio, closest = _closest_window(collapsed_sentence, prepared)
    if ratio >= FUZZY_REPORT_THRESHOLD:
        return SentenceCheck("fuzzy", ratio, closest)
    return SentenceCheck("not_found", ratio, closest or None)


# --- batch reporting --------------------------------------------------------
@dataclass
class RecordResult:
    index: int
    buyer: str
    supplier: str
    source_url: str
    status: str  # supported | unsupported | invalid | undocumented
    level: str | None
    ratio: float
    errors: list[str] = field(default_factory=list)
    closest_text: str | None = None


@dataclass
class VerificationReport:
    results: list[RecordResult]

    @property
    def total(self) -> int:
        return len(self.results)

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def checked(self) -> int:
        """Records that reached the sentence check (i.e. were structurally valid)."""
        return self.count("supported") + self.count("unsupported")

    @property
    def failure_rate(self) -> float:
        """Share of checked records whose sentence is not in the filing."""
        return self.count("unsupported") / self.checked if self.checked else 0.0

    def supported_records(self, records: Sequence[dict]) -> list[dict]:
        return [records[r.index] for r in self.results if r.status == "supported"]

    def summary(self) -> str:
        lines = [
            f"records in:            {self.total}",
            f"invalid (dropped):     {self.count('invalid')}",
            f"document unavailable:  {self.count('undocumented')}",
            f"checked:               {self.checked}",
            f"  supported:           {self.count('supported')}",
            f"  unsupported:         {self.count('unsupported')}",
        ]
        if self.checked:
            lines.append(f"failure rate:          {self.failure_rate:.1%}")
        return "\n".join(lines)


def verify_records(
    records: Iterable[dict],
    document_for: Callable[[dict], str | None],
) -> VerificationReport:
    """Validate and verify a batch of extracted relationships.

    `document_for` maps a record to the text of the filing it cites, or None if
    that filing is not available locally. A record whose filing cannot be
    retrieved is reported as `undocumented` — never as supported.
    """
    # None is cached too: a filing we could not resolve once will not resolve on
    # the next record citing the same URL, and re-reading it from disk to learn
    # that again is wasted work.
    cache: dict[str, PreparedDocument | None] = {}
    results: list[RecordResult] = []

    for index, record in enumerate(records):
        buyer = str(record.get("buyer_name_raw", ""))
        supplier = str(record.get("supplier_name_raw", ""))
        url = str(record.get("source_url", ""))

        errors = validate_record(record)
        if errors:
            results.append(
                RecordResult(index, buyer, supplier, url, "invalid", None, 0.0, errors)
            )
            continue

        if url not in cache:
            document = document_for(record)
            cache[url] = PreparedDocument(document) if document is not None else None

        prepared = cache[url]
        if prepared is None:
            results.append(
                RecordResult(
                    index, buyer, supplier, url, "undocumented", None, 0.0,
                    ["cited filing not available locally"],
                )
            )
            continue

        check = verify_sentence(record["source_sentence"], prepared)
        results.append(
            RecordResult(
                index=index,
                buyer=buyer,
                supplier=supplier,
                source_url=url,
                status="supported" if check.supported else "unsupported",
                level=check.level,
                ratio=check.ratio,
                closest_text=check.closest_text,
            )
        )

    return VerificationReport(results)


def write_report(report: VerificationReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "total": report.total,
        "checked": report.checked,
        "supported": report.count("supported"),
        "unsupported": report.count("unsupported"),
        "invalid": report.count("invalid"),
        "undocumented": report.count("undocumented"),
        "failure_rate": report.failure_rate,
        "results": [
            {
                "index": r.index,
                "buyer_name_raw": r.buyer,
                "supplier_name_raw": r.supplier,
                "source_url": r.source_url,
                "status": r.status,
                "match_level": r.level,
                "ratio": round(r.ratio, 4),
                "errors": r.errors,
                "closest_text_in_filing": r.closest_text,
            }
            for r in report.results
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path
