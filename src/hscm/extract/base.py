"""The extractor interface and the relationship schema both implementations share.

One design decision worth stating, because it deviates from the spec's record
shape: **the model is never asked for `source_url`, `form_type`, or
`filing_date`.** Those are facts about the filing we just fetched, so the
extractor stamps them from the `Filing` object. A model asked to reproduce a
URL will eventually produce a plausible one that 404s, and a citation that
points at the wrong filing is worse than no citation.

The resulting records have exactly the shape Decision 3 specifies. What changes
is who fills in which field, and therefore what the M3 check is actually
testing: `source_sentence` — the one field that can be fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..edgar import Filing

# --- what the model is asked to produce -------------------------------------
# Structured outputs reject most JSON Schema constraints (no minLength, no
# maximum, no recursion). Every object needs additionalProperties: false and a
# required list naming every property; nullable fields are spelled as an anyOf
# against null rather than a type union.
RELATIONSHIP_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "relationships": {
            "type": "array",
            "description": "Every supply relationship stated in the text. Empty if none.",
            "items": {
                "type": "object",
                "properties": {
                    "buyer_name_raw": {
                        "type": "string",
                        "description": (
                            "The purchasing party, spelled exactly as the filing "
                            "spells it. Do not expand abbreviations or add Inc./Corp."
                        ),
                    },
                    "supplier_name_raw": {
                        "type": "string",
                        "description": (
                            "The supplying party, spelled exactly as the filing "
                            "spells it. If the filing says 'a limited number of "
                            "suppliers' without naming them, do not invent a name — "
                            "skip the relationship entirely."
                        ),
                    },
                    "relationship_type": {
                        "type": "string",
                        "enum": [
                            "supplies",
                            "manufactures_for",
                            "leases_capacity_to",
                            "licenses_to",
                            "unclear",
                        ],
                        "description": (
                            "The subject of this verb is ALWAYS supplier_name_raw and "
                            "the object is ALWAYS buyer_name_raw: read it as "
                            "'<supplier> supplies <buyer>'. A filing written from the "
                            "buyer's side ('we purchase wafers from TSMC') is still "
                            "'supplies', with TSMC as the supplier. Use 'unclear' when "
                            "the filing states a relationship without saying which way "
                            "goods or services flow; 'strategic partnership' is unclear, "
                            "not supplies."
                        ),
                    },
                    "product_or_service": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "What is supplied, if the filing says. Otherwise null.",
                    },
                    "quantified_pct": {
                        "anyOf": [{"type": "number"}, {"type": "null"}],
                        "description": (
                            "The percentage stated in the filing, e.g. 19 for '19% of "
                            "revenue'. Null unless the filing states a number."
                        ),
                    },
                    "quantified_basis": {
                        "anyOf": [
                            {
                                "type": "string",
                                "enum": ["revenue", "cost", "units", "other"],
                            },
                            {"type": "null"},
                        ],
                        "description": (
                            "What the percentage is a share of. 'revenue' for a share "
                            "of sales, 'cost' for a share of purchases or expense, "
                            "'units' for a share of a physical quantity such as wafers, "
                            "chips or capacity, 'other' for any denominator that fits "
                            "none of those. Null ONLY when quantified_pct is null: a "
                            "number with no stated denominator is not usable, so if you "
                            "cannot tell what the percentage is a share of, use 'other' "
                            "and make sure the sentence you quote contains it."
                        ),
                    },
                    "source_sentence": {
                        "type": "string",
                        "description": (
                            "The sentence supporting this relationship, copied "
                            "VERBATIM from the text. Character for character, "
                            "including punctuation. Do not summarise, join two "
                            "sentences, correct typos, or trim words. This is "
                            "checked against the filing and the record is discarded "
                            "if it does not appear."
                        ),
                    },
                    "extraction_confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": (
                            "high: the sentence names both parties and the "
                            "relationship. medium: one party or the direction is "
                            "inferred from nearby context. low: a reading of the "
                            "sentence rather than a statement in it."
                        ),
                    },
                },
                "required": [
                    "buyer_name_raw",
                    "supplier_name_raw",
                    "relationship_type",
                    "product_or_service",
                    "quantified_pct",
                    "quantified_basis",
                    "source_sentence",
                    "extraction_confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["relationships"],
    "additionalProperties": False,
}

# Fields the extractor stamps rather than the model producing them.
PROVENANCE_FIELDS = ("source_url", "form_type", "filing_date")

SYSTEM_PROMPT = """\
You extract supply chain relationships from SEC filings for a dataset where \
every edge must be traceable to a specific sentence.

Rules, in order of importance:

1. Only report a relationship that the text states. Do not use knowledge you \
have about these companies from anywhere else. If you know NVIDIA supplies \
Microsoft but this filing does not say so, that relationship does not exist \
for our purposes.
2. `source_sentence` must be copied verbatim from the text. It is string-matched \
back against the filing and discarded if it does not appear. A paraphrase is \
worse than no extraction, because it looks like evidence and is not.
3. Both parties must be named in the text. Filings often say "a limited number \
of suppliers" or "certain third-party manufacturers" without naming anyone. \
Those sentences describe a real dependency but support no edge — skip them.
4. Prefer `unclear` over a guess. "We have strategic relationships with leading \
semiconductor providers" states that a relationship exists and nothing about \
its direction.
5. Never answer with a word the filing uses for itself. A 10-K says "we \
outsource manufacturing to TSMC"; the answer is not that the buyer is "we", it \
is the company filing the document, whose name appears at the top of the text. \
The same goes for "us", "our", "the Company" and "the Registrant".
6. `relationship_type` always reads supplier first. Most filings are written \
from the buyer's side — "we outsource manufacturing to TSMC" — so you will \
usually be turning the sentence around: TSMC is the supplier, the filer is the \
buyer, and the type is `supplies`. The roles come from who does what, never \
from the grammar of the sentence.
7. Returning an empty list is a correct answer. Most sections of most filings \
contain no named supply relationship at all.
"""

USER_PROMPT = """\
Filing: {company} ({ticker}), {form_type} filed {filing_date}
Section: {section_label}

Extract every supply relationship stated in the text below.

<filing_text>
{text}
</filing_text>
"""


@dataclass(frozen=True)
class ExtractionRequest:
    """One unit of extraction: a stretch of text plus the filing it came from."""

    filing: Filing
    section_key: str
    section_label: str
    text: str


# A filing writes about itself in the first person, and the model quite
# reasonably copies that: "we outsource manufacturing to TSMC" comes back with a
# buyer of "we". Seventeen per cent of the first full run had a pronoun on one
# end. Resolving it is not a guess — "we" in Broadcom's 10-K is Broadcom, and the
# filing's identity is a fact we already hold — so it is done here rather than
# thrown away at entity resolution.
_FIRST_PERSON = {
    "we", "us", "our", "ours", "ourselves", "the company", "the registrant",
    "the group", "company", "registrant", "the corporation", "our company",
    "the filer", "our platform", "the business",
}


def _resolve_self_reference(name: str, filing: Filing) -> tuple[str, bool]:
    """Turn a filing's word for itself into the company it means."""
    if (name or "").strip().lower() in _FIRST_PERSON:
        return filing.company_name, True
    return name, False


def stamp_provenance(record: dict, filing: Filing) -> dict:
    """Attach the filing's identity to a model-produced relationship."""
    buyer, buyer_was_self = _resolve_self_reference(record.get("buyer_name_raw", ""), filing)
    supplier, supplier_was_self = _resolve_self_reference(
        record.get("supplier_name_raw", ""), filing
    )

    stamped = {
        **record,
        "buyer_name_raw": buyer,
        "supplier_name_raw": supplier,
        "source_url": filing.document_url,
        "form_type": filing.form_type,
        "filing_date": filing.filing_date,
    }
    # Recorded, not silent: the site can say the filing wrote "we" and this is
    # whose filing it is, which is a different claim from the filing naming them.
    if buyer_was_self or supplier_was_self:
        stamped["self_reference_resolved"] = [
            side
            for side, changed in (("buyer", buyer_was_self), ("supplier", supplier_was_self))
            if changed
        ]
    return stamped


@runtime_checkable
class Extractor(Protocol):
    """Anything that turns filing text into candidate relationship records.

    Implementations return records only. They never verify, resolve, or store —
    verification is verify.py's job and it must run over whatever this produces,
    including records this extractor is confident about.
    """

    name: str

    def extract(self, request: ExtractionRequest) -> list[dict]:
        ...
