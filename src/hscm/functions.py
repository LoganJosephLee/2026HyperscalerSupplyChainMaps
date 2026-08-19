"""What a company does, taken from the words filings use for it.

The map is much more useful when you can see at a glance that TSMC fabricates
wafers, Amkor packages them and Micron sells memory. The risk is inventing that
knowledge: assigning roles from what I happen to know about these companies
would put unsourced claims on the page, which is the one thing this project does
not do.

So a company's function is derived only from `product_or_service` — the phrase
the extractor pulled out of a filing sentence, already verified to appear in that
filing. TSMC is a foundry here because Broadcom's 10-K says "front-end wafer
manufacturing" and Credo's says "semiconductor wafer production", not because
everyone knows TSMC is a foundry. A company nobody described gets no function,
and the map says so rather than guessing.

The keyword rules below are a judgement about which phrases mean the same job.
That judgement is presentation, not evidence: the phrases themselves are shown
on the node, so a reader can always see what the grouping was made from.
"""

from __future__ import annotations

import re
from collections import Counter

# Ordered: the first pattern that matches a phrase wins, so put the specific
# ones above the general. "wafer foundry" must beat "manufacturing".
FUNCTION_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "fabrication",
        "Wafer fabrication",
        r"wafer|foundry|foundries|fabricat|front[\s-]?end|\bic manufactur"
        r"|process(?:ing)? at the foundry",
    ),
    (
        "packaging",
        "Assembly, test & packaging",
        r"packag|assembl|\btest(?:ing)?\b|back[\s-]?end",
    ),
    (
        "memory",
        "Memory",
        r"\bmemory\b|\bdram\b|\bhbm\b|\bnand\b|storage",
    ),
    (
        "systems",
        "Systems & contract manufacturing",
        r"contract manufactur|final products|server|rack|system|board|module",
    ),
    (
        "interconnect",
        "Interconnect, optics & networking",
        r"optic|cable|interconnect|network|switch|ethernet|\baec\b|transceiver",
    ),
    (
        "silicon",
        "Chips, IP & design",
        r"\bics?\b|semiconductor|chip|processor|\bgpus?\b|\bcpus?\b|\bsocs?\b|silicon|"
        r"intellectual property|licen[cs]|technology licen|processing unit|design",
    ),
)

FUNCTION_LABELS = {key: label for key, label, _ in FUNCTION_RULES}
_COMPILED = tuple((key, re.compile(pattern, re.IGNORECASE)) for key, _, pattern in FUNCTION_RULES)

UNKNOWN = "unstated"
UNKNOWN_LABEL = "Not stated in any filing"


def classify_phrase(phrase: str | None) -> str | None:
    """The function a single product_or_service phrase describes, if any."""
    if not phrase:
        return None
    for key, pattern in _COMPILED:
        if pattern.search(phrase):
            return key
    return None


def classify_phrases(phrases: list[str | None]) -> tuple[str, list[str]]:
    """A company's function, and the phrases that decided it.

    Filings describe a company more than one way — TSMC both fabricates wafers
    and, in Broadcom's list of assembly contractors, tests them. The most common
    reading wins, and every phrase is returned so the node can show its working.
    """
    kept = [phrase for phrase in phrases if phrase]
    votes = Counter(key for key in (classify_phrase(p) for p in kept) if key)
    if not votes:
        return UNKNOWN, kept

    # Ties go to the earlier rule, which is the more specific one.
    order = [key for key, _, _ in FUNCTION_RULES]
    best = max(votes.items(), key=lambda pair: (pair[1], -order.index(pair[0])))
    return best[0], kept
