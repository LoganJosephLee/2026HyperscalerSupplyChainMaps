"""Turn a filing document into text, then into the sections we care about.

Two jobs:

1. `document_text` — HTML to plain text, with the normalisation that filing
   text always needs (non-breaking spaces, curly quotes, soft hyphens).
2. `split_items` — locate Item boundaries (10-K) or their 20-F equivalents.

The hard part of (2) is that every item header appears at least twice: once in
the table of contents and once in the body, plus any number of cross-references
("see Item 1A. Risk Factors"). Picking the last occurrence is wrong (cross-refs
run to the end of the document); picking the first is wrong (that is the TOC).

What we do instead, in three passes:

1. Drop candidates that begin mid-line — that removes inline cross-references
   without having to parse the sentence around them.
2. Drop candidates inside a detected table of contents. A TOC is recognised by
   what makes it a TOC: many *different* item headers packed into a short span
   of text. Nothing else in a filing looks like that.
3. Of what survives, choose one candidate per item such that positions strictly
   increase and the capped sum of gaps between consecutive picks is maximised.
   Items may be skipped, so filings that omit an item still parse.

Pass 3 alone is not enough, which is why pass 2 exists: a TOC entry for Item 1
sits even further from the body Item 1A than the body Item 1 does, so gap
maximisation on its own actively prefers the TOC row.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from bs4 import BeautifulSoup

# A consecutive gap larger than this is not more convincing than one exactly
# this size. Without the cap the optimiser just picks the earliest-possible
# first item and the latest-possible last item.
GAP_CAP = 20_000

# Including an item is worth this much, so the optimiser does not drop items
# merely to widen a gap.
ITEM_BONUS = 5_000

# A table of contents: at least this many distinct item headers inside a window
# this wide. Real 10-K contents pages pack ~15 items into ~2kB. The threshold is
# deliberately above the 2-3 items that legitimately cluster when a short item
# ("Item 1B. Unresolved Staff Comments — None.") sits between two real ones.
TOC_WINDOW = 2_500
TOC_MIN_DISTINCT_ITEMS = 5

_SEP = r"[\.\:\)\-–—]?\s*"


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    start: int
    end: int
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class ItemMarker:
    key: str
    label: str
    pattern: re.Pattern[str]


def _marker(key: str, label: str, regex: str) -> ItemMarker:
    return ItemMarker(key, label, re.compile(regex, re.IGNORECASE))


# Ordered as they appear in the form. Order matters: it is what makes
# "strictly increasing positions" a meaningful constraint.
TEN_K_MARKERS: tuple[ItemMarker, ...] = (
    _marker("item1", "Item 1. Business", rf"item\s*1{_SEP}business\b"),
    _marker("item1a", "Item 1A. Risk Factors", rf"item\s*1a{_SEP}risk\s*factors\b"),
    _marker("item1b", "Item 1B. Unresolved Staff Comments", rf"item\s*1b{_SEP}unresolved\s+staff\s+comments\b"),
    _marker("item2", "Item 2. Properties", rf"item\s*2{_SEP}propert"),
    _marker("item3", "Item 3. Legal Proceedings", rf"item\s*3{_SEP}legal\s+proceedings\b"),
    _marker("item5", "Item 5. Market for Registrant's Common Equity", rf"item\s*5{_SEP}market\s+for\s+"),
    _marker("item7", "Item 7. MD&A", rf"item\s*7{_SEP}management.{{0,3}}s\s+discussion\b"),
    _marker("item7a", "Item 7A. Quantitative and Qualitative Disclosures", rf"item\s*7a{_SEP}quantitative\b"),
    _marker("item8", "Item 8. Financial Statements", rf"item\s*8{_SEP}financial\s+statements\b"),
    _marker("item9", "Item 9. Changes in and Disagreements", rf"item\s*9{_SEP}changes\s+in\s+and\s+disagreements\b"),
    # Terminator. Without it the last located item runs to the end of the
    # document and swallows the exhibit index and signature pages.
    _marker("item15", "Item 15. Exhibits and Financial Statement Schedules", rf"item\s*15{_SEP}exhibit"),
)

# 10-Q numbering is not 10-K numbering, and "Item 1" means different things in
# Part I (Financial Statements) and Part II (Legal Proceedings). Listed in
# document order, which is what makes the increasing-position constraint work.
TEN_Q_MARKERS: tuple[ItemMarker, ...] = (
    _marker("part1_item1", "Part I Item 1. Financial Statements", rf"item\s*1{_SEP}financial\s+statements\b"),
    _marker("part1_item2", "Part I Item 2. MD&A", rf"item\s*2{_SEP}management.{{0,3}}s\s+discussion\b"),
    _marker("part1_item3", "Part I Item 3. Quantitative and Qualitative Disclosures", rf"item\s*3{_SEP}quantitative\b"),
    _marker("part1_item4", "Part I Item 4. Controls and Procedures", rf"item\s*4{_SEP}controls\s+and\s+procedures\b"),
    _marker("part2_item1", "Part II Item 1. Legal Proceedings", rf"item\s*1{_SEP}legal\s+proceedings\b"),
    _marker("part2_item1a", "Part II Item 1A. Risk Factors", rf"item\s*1a{_SEP}risk\s*factors\b"),
    _marker("part2_item2", "Part II Item 2. Unregistered Sales of Equity Securities", rf"item\s*2{_SEP}unregistered\s+sales\b"),
    _marker("part2_item5", "Part II Item 5. Other Information", rf"item\s*5{_SEP}other\s+information\b"),
    _marker("part2_item6", "Part II Item 6. Exhibits", rf"item\s*6{_SEP}exhibits\b"),
)

# 20-F carries the same disclosures under different numbering (Decision 1:
# TSMC is eligible because it files a 20-F, so the parser must handle it).
TWENTY_F_MARKERS: tuple[ItemMarker, ...] = (
    _marker("item3d", "Item 3.D. Risk Factors", rf"item\s*3{_SEP}(?:d{_SEP})?risk\s*factors\b"),
    _marker("item4", "Item 4. Information on the Company", rf"item\s*4{_SEP}information\s+on\s+the\s+company\b"),
    _marker("item4a", "Item 4A. Unresolved Staff Comments", rf"item\s*4a{_SEP}unresolved\s+staff\s+comments\b"),
    _marker("item5", "Item 5. Operating and Financial Review", rf"item\s*5{_SEP}operating\s+and\s+financial\s+review\b"),
    _marker("item7", "Item 7. Major Shareholders and Related Party Transactions", rf"item\s*7{_SEP}major\s+shareholders\b"),
    _marker("item8", "Item 8. Financial Information", rf"item\s*8{_SEP}financial\s+information\b"),
    _marker("item18", "Item 18. Financial Statements", rf"item\s*18{_SEP}financial\s+statements\b"),
    _marker("item19", "Item 19. Exhibits", rf"item\s*19{_SEP}exhibits\b"),
)

MARKERS_BY_FORM: dict[str, tuple[ItemMarker, ...]] = {
    "10-K": TEN_K_MARKERS,
    "10-Q": TEN_Q_MARKERS,
    "20-F": TWENTY_F_MARKERS,
    # 8-K deliberately has no markers: see split_items.
    "8-K": (),
}

# The sections extraction actually reads, per form. Used to tell "this filing
# genuinely has no risk factors" from "the splitter failed".
EXTRACTION_KEYS: dict[str, tuple[str, ...]] = {
    "10-K": ("item1", "item1a", "item8"),
    "10-Q": ("part1_item1", "part2_item1a"),
    "20-F": ("item4", "item3d", "item18"),
    "8-K": ("body",),
}


# --- HTML -> text -----------------------------------------------------------
_WS_RUN = re.compile(r"[ \t   ]+")
_BLANK_RUN = re.compile(r"\n{3,}")

_TRANSLATIONS = {
    " ": " ",  # non-breaking space — the single most common parse breaker
    " ": " ",
    " ": " ",
    "­": "",   # soft hyphen
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "−": "-",
    "﻿": "",
}


def normalize_text(raw: str) -> str:
    """Normalisation shared by the section splitter and the M3 verifier.

    Both sides must agree exactly, or a sentence that is present in the filing
    will fail to match and be reported as a hallucination.
    """
    text = unicodedata.normalize("NFKC", raw)
    for source, target in _TRANSLATIONS.items():
        text = text.replace(source, target)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_RUN.sub("\n\n", text).strip()


# Tags that end a line of text. Everything else is inline and must not break a
# sentence: filing HTML routinely wraps each styled run of a single sentence in
# its own <span>, and splitting on those shreds the sentences we need to match.
_BLOCK_TAGS = (
    "p div br tr li h1 h2 h3 h4 h5 h6 table section article ul ol hr blockquote"
).split()
_LINE_BREAK = "\x00"
_ANY_WS = re.compile(r"\s+")


def document_text(html: str | bytes) -> str:
    """Extract readable text from a filing document.

    Block elements become line breaks; table cells within a row are joined by
    spaces so a row reads as one line; all other whitespace — including the
    newlines inside a text node, which are insignificant in HTML — collapses to
    single spaces.
    """
    # Bytes are handed to BeautifulSoup undecoded so it can sniff the encoding
    # from the meta charset and the byte pattern. Assuming UTF-8 turns every
    # curly quote in a Windows-1252 filing into a replacement character, and a
    # mangled quote inside a sentence makes that sentence unverifiable.
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before(_LINE_BREAK)
        tag.insert_after(_LINE_BREAK)
    for tag in soup.find_all(["td", "th"]):
        tag.insert_after(" ")

    text = _ANY_WS.sub(" ", soup.get_text(""))
    return normalize_text(text.replace(_LINE_BREAK, "\n"))


# --- item splitting ---------------------------------------------------------
def _candidates(text: str, marker: ItemMarker) -> list[int]:
    """Positions where this item's header plausibly starts.

    If any match begins a line, only line-start matches are kept. That drops
    inline cross-references ("as described in Item 1A. Risk Factors") without
    needing to reason about the sentence around them.
    """
    matches = [m.start() for m in marker.pattern.finditer(text)]
    line_starts = [p for p in matches if p == 0 or text[p - 1] == "\n"]
    return line_starts or matches


def _dense_spans(candidate_lists: list[list[int]]) -> list[tuple[int, int]]:
    """Ranges holding headers for many different items in a short stretch of text."""
    tagged = sorted(
        (position, item)
        for item, positions in enumerate(candidate_lists)
        for position in positions
    )
    spans: list[tuple[int, int]] = []
    for i, (start, _) in enumerate(tagged):
        window = [entry for entry in tagged[i:] if entry[0] - start <= TOC_WINDOW]
        if len({item for _, item in window}) >= TOC_MIN_DISTINCT_ITEMS:
            spans.append((start, window[-1][0]))

    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# A contents row carries a page number, usually after a run of dot leaders.
# A body header is just the header. This is the only reliable difference when
# the contents page and Item 1 sit a few hundred characters apart — which they
# routinely do, so a position-only rule swallows the real Item 1.
_TOC_ROW_TAIL = re.compile(r"(?:\.{2,}|…|\s)\s*\d{1,4}\s*$")


def _drop_toc(
    text: str, candidate_lists: list[list[int]]
) -> tuple[list[list[int]], list[tuple[int, int]]]:
    """Remove contents-page rows, unless doing so would empty an item.

    The exception matters: a filing whose only Item 1A header is one this
    heuristic catches should still yield a section, and be visibly odd in the
    diagnostics, rather than silently losing Risk Factors altogether.
    """
    spans = _dense_spans(candidate_lists)
    if not spans:
        return candidate_lists, []

    def is_contents_row(position: int) -> bool:
        if not any(start <= position <= end for start, end in spans):
            return False
        line_end = text.find("\n", position)
        line = text[position : line_end if line_end != -1 else len(text)]
        return bool(_TOC_ROW_TAIL.search(line))

    filtered = []
    for positions in candidate_lists:
        kept = [p for p in positions if not is_contents_row(p)]
        filtered.append(kept or positions)
    return filtered, spans


def _choose_positions(
    candidate_lists: list[list[int]], doc_len: int
) -> list[int | None]:
    """Pick at most one candidate per item, strictly increasing, best score.

    Score is the sum over consecutive picks of min(gap, GAP_CAP) plus a bonus
    per item included, with earlier positions breaking ties — after the table of
    contents is gone, the body header is the earliest surviving candidate.
    """
    n = len(candidate_lists)
    # states[i] maps a chosen position for item i to (score, backpointer).
    best: list[dict[int, tuple[float, tuple[int, int] | None]]] = [{} for _ in range(n)]
    for i, candidates in enumerate(candidate_lists):
        for position in candidates:
            score = ITEM_BONUS - 1e-6 * position
            back: tuple[int, int] | None = None
            for j in range(i):
                for previous, (previous_score, _) in best[j].items():
                    if previous >= position:
                        continue
                    gap = min(position - previous, GAP_CAP)
                    if previous_score + gap + ITEM_BONUS > score:
                        score = previous_score + gap + ITEM_BONUS
                        back = (j, previous)
            best[i][position] = (score, back)

    # Walk back from the highest-scoring endpoint anywhere in the table.
    end: tuple[int, int] | None = None
    end_score = -1.0
    for i in range(n):
        for position, (score, _) in best[i].items():
            trailing = min(doc_len - position, GAP_CAP)
            if score + trailing > end_score:
                end_score = score + trailing
                end = (i, position)

    chosen: list[int | None] = [None] * n
    while end is not None:
        i, position = end
        chosen[i] = position
        end = best[i][position][1]
    return chosen


def split_items(
    text: str, form_type: str = "10-K", diagnostics: dict | None = None
) -> dict[str, Section]:
    """Split normalised filing text into its items.

    Returns only the items actually found. A missing key means the splitter
    could not locate that item — it never means the item is empty.

    Pass a dict as `diagnostics` to receive the candidate counts and detected
    contents-page spans, which is how you tell a genuinely absent item from a
    splitter failure.
    """
    markers = MARKERS_BY_FORM.get(form_type.upper())
    if markers is None:
        raise ValueError(f"No item markers defined for form type {form_type!r}")

    if not markers:
        # 8-K. A current report is a handful of paragraphs about one event;
        # splitting it into items would discard more context than it isolates,
        # and its numbering (1.01, 2.01, 8.01) shares nothing with the annual
        # forms. Treat the whole document as the unit of extraction.
        if diagnostics is not None:
            diagnostics["toc_spans"] = []
            diagnostics["candidates"] = {}
        text = text.strip()
        return {"body": Section("body", f"{form_type.upper()} (whole document)", 0, len(text), text)}

    raw_candidates = [_candidates(text, marker) for marker in markers]
    candidate_lists, toc_spans = _drop_toc(text, raw_candidates)
    chosen = _choose_positions(candidate_lists, len(text))

    if diagnostics is not None:
        diagnostics["toc_spans"] = toc_spans
        diagnostics["candidates"] = {
            marker.key: len(positions)
            for marker, positions in zip(markers, raw_candidates)
        }

    found = [(i, p) for i, p in enumerate(chosen) if p is not None]
    sections: dict[str, Section] = {}
    for slot, (index, start) in enumerate(found):
        end = found[slot + 1][1] if slot + 1 < len(found) else len(text)
        marker = markers[index]
        sections[marker.key] = Section(
            key=marker.key,
            label=marker.label,
            start=start,
            end=end,
            text=text[start:end].strip(),
        )
    return sections


# --- customer / supplier concentration --------------------------------------
# The 10% customer-concentration disclosures live in the financial statement
# notes, not in a numbered item, so they are found by keyword rather than by
# splitting. Everything matched here is a *candidate* passage for extraction.
CONCENTRATION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:no|one|a\s+single|two|three)\s+(?:customer|client|supplier|vendor)s?\b",
        r"(?:customer|client)s?\s+(?:that\s+)?(?:individually\s+)?account(?:ed|s)?\s+for",
        r"account(?:ed|s)?\s+for\s+(?:approximately\s+)?\d{1,3}(?:\.\d+)?\s*%\s+of\s+"
        r"(?:our\s+|total\s+|net\s+|consolidated\s+)*(?:revenue|sales|purchases|cost)",
        r"concentrations?\s+of\s+(?:credit\s+)?risk",
        r"\b(?:significant|major|largest)\s+(?:customer|supplier|vendor)s?\b",
        r"\b(?:sole|single)[\s-]source\b",
        r"limited\s+number\s+of\s+(?:supplier|vendor|manufacturer)s\b",
        r"\bthird[\s-]party\s+manufactur",
        r"\b(?:foundr|fabricat)(?:y|ies|ion)\b",
    )
)


@dataclass(frozen=True)
class Passage:
    start: int
    end: int
    text: str
    matched_patterns: tuple[str, ...]


def find_concentration_passages(
    text: str, context_lines: int = 1, min_length: int = 80
) -> list[Passage]:
    """Lines mentioning customer/supplier concentration, with surrounding context.

    Short lines are dropped because filing HTML turns table cells into one-word
    lines, and a table cell reading "Customer A" carries no extractable claim.
    """
    lines = text.split("\n")
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1

    hits: dict[int, list[str]] = {}
    for index, line in enumerate(lines):
        if len(line) < min_length:
            continue
        matched = [p.pattern for p in CONCENTRATION_PATTERNS if p.search(line)]
        if matched:
            hits[index] = matched

    passages: list[Passage] = []
    claimed: set[int] = set()
    for index in sorted(hits):
        if index in claimed:
            continue
        first = max(0, index - context_lines)
        last = min(len(lines) - 1, index + context_lines)
        claimed.update(range(first, last + 1))
        start = offsets[first]
        end = offsets[last] + len(lines[last])
        passages.append(
            Passage(
                start=start,
                end=end,
                text=text[start:end].strip(),
                matched_patterns=tuple(hits[index]),
            )
        )
    return passages
