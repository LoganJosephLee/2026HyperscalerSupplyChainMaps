"""Tests for the M3 hallucination check.

The strings here are hand-written test inputs, not dataset content: nothing in
this file describes a real supply relationship, and nothing here reaches the
graph.
"""

from __future__ import annotations

import pytest

from hscm.verify import (
    VerificationReport,
    validate_record,
    verify_records,
    verify_sentence,
)

FILING = """
Item 1A. Risk Factors

We depend on a limited number of suppliers for the components used in our
datacenter servers. In particular, we purchase graphics processing units from
a single vendor, and a disruption in that relationship would harm our business.

One customer accounted for 19% of total revenue in fiscal year 2025.
"""

VERBATIM = (
    "In particular, we purchase graphics processing units from a single vendor, "
    "and a disruption in that relationship would harm our business."
)


def record(**overrides) -> dict:
    base = {
        "buyer_name_raw": "Example Buyer Corporation",
        "supplier_name_raw": "Example Supplier Inc.",
        "relationship_type": "purchases_from",
        "product_or_service": "graphics processing units",
        "quantified_pct": None,
        "quantified_basis": None,
        "source_sentence": VERBATIM,
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/000000000000000000/x.htm",
        "form_type": "10-K",
        "filing_date": "2025-07-30",
        "extraction_confidence": "high",
    }
    base.update(overrides)
    return base


# --- sentence matching ------------------------------------------------------
def test_sentence_spanning_line_breaks_is_supported():
    """The filing wraps this sentence across three lines; that is formatting."""
    check = verify_sentence(VERBATIM, FILING)
    assert check.supported
    assert check.level == "normalized"


def test_identical_single_line_sentence_matches_exactly():
    check = verify_sentence("One customer accounted for 19% of total revenue in fiscal year 2025.", FILING)
    assert check.level == "exact"
    assert check.supported


def test_paraphrase_is_not_supported():
    """Close-but-not-equal is how a fabricated claim actually looks."""
    check = verify_sentence(
        "In particular, we purchase graphics processing units from NVIDIA, "
        "and a disruption in that relationship would harm our business.",
        FILING,
    )
    assert not check.supported
    assert check.level == "fuzzy"
    assert check.closest_text  # kept for prompt debugging, not for acceptance


def test_invented_sentence_is_not_found():
    check = verify_sentence(
        "We entered into a multi-year agreement with Broadcom to co-design custom accelerators.",
        FILING,
    )
    assert not check.supported
    assert check.level == "not_found"


def test_quantity_swap_is_caught():
    """Changing 19% to 90% must fail even though every other word matches."""
    check = verify_sentence(
        "One customer accounted for 90% of total revenue in fiscal year 2025.", FILING
    )
    assert not check.supported


# --- structural validation --------------------------------------------------
@pytest.mark.parametrize("missing", ["source_sentence", "source_url", "buyer_name_raw"])
def test_missing_required_field_is_invalid(missing):
    assert validate_record(record(**{missing: None}))


def test_valid_record_has_no_errors():
    assert validate_record(record()) == []


def test_percentage_without_basis_is_invalid():
    errors = validate_record(record(quantified_pct=19, quantified_basis=None))
    assert any("basis" in e for e in errors)


def test_percentage_with_basis_is_valid():
    assert validate_record(record(quantified_pct=19, quantified_basis="revenue")) == []


def test_unrecognised_relationship_type_is_invalid():
    assert validate_record(record(relationship_type="sells_to"))


def test_non_sec_source_url_is_invalid():
    assert validate_record(record(source_url="https://example.com/filing.htm"))


def test_unclear_is_a_permitted_relationship_type():
    assert validate_record(record(relationship_type="unclear")) == []


# --- batch reporting --------------------------------------------------------
def test_report_counts_and_failure_rate():
    records = [
        record(),                                    # supported
        record(source_sentence="We buy chips from Acme Corporation every year."),  # unsupported
        record(source_sentence=None),                # invalid, never reaches the check
    ]
    report = verify_records(records, lambda _: FILING)

    assert report.total == 3
    assert report.count("supported") == 1
    assert report.count("unsupported") == 1
    assert report.count("invalid") == 1
    assert report.checked == 2
    assert report.failure_rate == 0.5
    assert report.supported_records(records) == [records[0]]


def test_missing_filing_is_undocumented_not_supported():
    report = verify_records([record()], lambda _: None)
    assert report.count("undocumented") == 1
    assert report.count("supported") == 0
    assert report.checked == 0


def test_empty_report_has_zero_failure_rate_but_nothing_passed():
    report = VerificationReport([])
    assert report.failure_rate == 0.0
    assert report.checked == 0
