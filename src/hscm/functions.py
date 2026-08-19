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
        # Not a bare "system": "liquid cooling systems" and "lithography systems"
        # are not servers, and half the companies in this industry have the word
        # in their name.
        r"contract manufactur|final products|\bservers?\b|\bracks?\b|motherboard"
        r"|\bboards?\b|\bmodules?\b|complete systems?|computing systems?",
    ),
    (
        "interconnect",
        "Interconnect, optics & networking",
        # "networking", not "network" — "network transit" and "network services"
        # are what a carrier sells, not a box someone bolts into a rack.
        r"optic|cable|interconnect|networking|switch|ethernet|\baec\b|transceiver",
    ),
    (
        "silicon",
        "Chips, IP & design",
        r"\bics?\b|semiconductor|chip|processor|\bgpus?\b|\bcpus?\b|\bsocs?\b|silicon|"
        r"intellectual property|licen[cs]|technology licen|processing unit|design",
    ),
    # Appended below: the rest of the chain. These sit after the semiconductor
    # rules on purpose — a phrase like "wafer fabrication equipment" should read
    # as fabrication first — so each one is written to catch words the rules
    # above do not use.
    (
        "equipment",
        "Chipmaking equipment",
        r"lithograph|deposition|\betch(?:ing)?\b|metrology|ion implant|"
        r"chemical mechanical|fab tool|inspection system",
    ),
    (
        "materials",
        "Materials & chemicals",
        r"substrate|photoresist|polysilicon|rare earth|specialty gas|"
        r"\bneon\b|\bargon\b|\bhelium\b|chemical|raw material",
    ),
    (
        "power",
        "Power & electrical",
        r"\bpower\b|electrical|transformer|generator|switchgear|\bups\b|"
        r"busway|substation|\benergy\b|\bgrid\b|turbine|battery",
    ),
    (
        "cooling",
        "Cooling & thermal",
        r"cooling|thermal|\bhvac\b|chiller|refrigerat|immersion|liquid cool",
    ),
    (
        "construction",
        "Construction & site works",
        r"construction|civil work|build[\s-]?out|site work|engineering,? procurement|"
        r"general contract",
    ),
    (
        "datacenter",
        "Data centre space & capacity",
        r"data cent(?:er|re)|colocation|\bcolo\b|floor space|hosting|"
        r"capacity|square feet|megawatt",
    ),
    (
        "carrier",
        "Network carriers & bandwidth",
        r"carrier|telecom|bandwidth|dark fibre|dark fiber|wavelength|\btransit\b|"
        r"network services|backhaul|long[\s-]?haul|subsea|\bfibre?\b routes?",
    ),
    (
        "logistics",
        "Freight, shipping & logistics",
        r"freight|shipping|logistic|transport|\bcargo\b|customs|warehous|"
        r"courier|air ?freight|ocean|fulfilment|fulfillment",
    ),
    (
        "software",
        "Software & tooling",
        r"software|\beda\b|firmware|operating system|middleware|compiler|"
        r"toolchain|\bapi\b",
    ),
    (
        "services",
        "Services & other",
        r"consult|staffing|maintenance|support services|installation|integration",
    ),
)

# Plain English, for a reader who does not already know how chips get made. The
# map is useless to them if every category is a term of art, and they are most of
# the audience: someone who knows what a foundry is did not need the map.
FUNCTION_DESCRIPTIONS: dict[str, str] = {
    "fabrication": "The factories that actually print chips onto silicon wafers. "
                   "Almost nobody owns one; almost everybody depends on one.",
    "packaging": "Takes finished wafers, cuts them up, wraps each chip in its casing "
                 "and tests that it works. The unglamorous step between a wafer and "
                 "a part you can solder down.",
    "memory": "The chips that hold data while the machine is working. AI training "
              "runs are limited by memory at least as often as by processors.",
    "systems": "Bolts the parts into finished machines — servers, racks, the boxes "
               "that fill a data centre hall.",
    "interconnect": "Moves data between chips, between machines, and between halls: "
                    "cables, optics, switches. At AI scale this is a bottleneck in "
                    "its own right.",
    "silicon": "Designs the chips, or owns the designs. A company here may not "
               "manufacture anything at all.",
    "equipment": "Sells the machines that fabs use to make chips. A step further "
                 "upstream than most people ever look.",
    "materials": "The physical inputs: blank wafers, gases, chemicals, the substrate "
                 "a chip is mounted on.",
    "power": "Gets electricity into the building and keeps it there — transformers, "
             "switchgear, backup generators. Increasingly the thing that decides "
             "where a data centre can be built at all.",
    "cooling": "Removes the heat. An AI hall turns nearly all the power it draws "
               "into heat that has to go somewhere.",
    "construction": "Builds the building.",
    "datacenter": "Rents out the space, power and cooling rather than the computers "
                  "— the landlords of the industry.",
    "carrier": "Carries the traffic in and out over long distances: fibre routes, "
               "bandwidth, subsea cable.",
    "logistics": "Physically moves the goods — freight, shipping, customs, "
                 "warehousing. Every other category on this list depends on it and "
                 "almost nobody names it in a filing.",
    "software": "The tools used to design, run or operate the hardware.",
    "services": "Everything else a filing described: installation, maintenance, "
                "consulting.",
}

FUNCTION_LABELS = {key: label for key, label, _ in FUNCTION_RULES}
_COMPILED = tuple((key, re.compile(pattern, re.IGNORECASE)) for key, _, pattern in FUNCTION_RULES)

UNKNOWN = "unstated"
UNKNOWN_LABEL = "Not stated in any filing"
UNKNOWN_DESCRIPTION = (
    "No filing in this dataset says what this company supplies. That is a gap in "
    "what has been disclosed, not a judgement about the company — the buyers this "
    "map is built around mostly sit here, because their suppliers name them and "
    "they do not name their suppliers."
)


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
