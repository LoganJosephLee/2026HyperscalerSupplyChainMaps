"""Tests for the M3 hallucination check.

The strings here are hand-written test inputs, not dataset content: nothing in
this file describes a real supply relationship, and nothing here reaches the
graph.
"""

from __future__ import annotations

import pytest

from hscm.verify import (
    PreparedDocument,
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
        # Not "Northgate Components Inc." — the unnamed-party guard rejects "supplier",
        # and rightly so; no registered company calls itself one.
        "supplier_name_raw": "Northgate Components Inc.",
        "relationship_type": "supplies",
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


def test_a_percentage_of_something_other_than_money_survives():
    # A filing saying a foundry makes 90% of a company's wafers is real evidence.
    # Before "units" existed the only legal answer was null, which threw the record away.
    assert validate_record(record(quantified_pct=90, quantified_basis="units")) == []
    assert validate_record(record(quantified_pct=90, quantified_basis="other")) == []


@pytest.mark.parametrize(
    "description",
    [
        "third-party foundries located in Taiwan",
        "certain third-party manufacturers",
        "a limited number of suppliers",
        "various contract manufacturers",
        "a single supplier",
        "unnamed vendors",
    ],
)
def test_a_described_supplier_is_not_a_named_one(description):
    """Marvell's 10-K says its products are made by "third-party foundries located
    in Taiwan". True, a real dependency, and no edge — there is nobody at the
    other end. Verification cannot catch it: the sentence is really in the filing.
    """
    errors = validate_record(record(supplier_name_raw=description))
    assert any("does not name one" in e for e in errors)


@pytest.mark.parametrize(
    "name",
    [
        "Taiwan Semiconductor Manufacturing Company Limited",
        "Siliconware Precision Industries Co., Ltd.",
        "King Yuan Electronics Company",
        "Hon Hai Precision Industry Co., Ltd.",
        "Fabrinet",
        # Real names that a cruder rule keyed on opening words would have thrown out.
        "ONE Gas, Inc.",
        "Principal Financial Group",
        "Delta Air Lines",
    ],
)
def test_real_company_names_survive_the_guard(name):
    assert validate_record(record(supplier_name_raw=name)) == []


@pytest.mark.parametrize("label", ["Customer A", "Customer B", "Client 1", "Partner B"])
def test_an_anonymised_counterparty_is_not_a_name(label):
    """Marvell's 10-K discloses a concentration with "Customer A".

    The letter is there precisely because the name is being withheld. It is a
    real disclosure and no edge — and the role-noun guard misses it, because no
    company calls itself a supplier but plenty of companies are customers.
    """
    errors = validate_record(record(buyer_name_raw=label))
    assert any("does not name one" in e for e in errors)


def test_a_company_whose_name_starts_with_customer_survives():
    assert validate_record(record(supplier_name_raw="Customer Experience Solutions Inc.")) == []


def test_a_buyer_subject_verb_is_rejected():
    # The edge is supplier -> buyer, so every verb has to read in that direction.
    # "purchases_from" printed as "TSMC purchases_from Broadcom", which is backwards.
    errors = validate_record(record(relationship_type="purchases_from"))
    assert any("relationship_type" in e for e in errors)


def test_an_invented_basis_is_still_rejected():
    errors = validate_record(record(quantified_pct=19, quantified_basis="market share"))
    assert any("not recognised" in e for e in errors)


def test_unrecognised_relationship_type_is_invalid():
    assert validate_record(record(relationship_type="sells_to"))


def test_non_sec_source_url_is_invalid():
    assert validate_record(record(source_url="https://example.com/filing.htm"))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/www.sec.gov/Archives/edgar/data/1/x.htm",  # host is not SEC
        "https://sec.gov.evil.test/Archives/x.htm",  # suffix attack
        "https://notsec.gov/x.htm",
        "not a url at all",
    ],
)
def test_lookalike_urls_are_rejected(url):
    """The host has to be sec.gov — a substring check accepts all of these."""
    assert validate_record(record(source_url=url))


@pytest.mark.parametrize(
    "url",
    [
        "https://www.sec.gov/Archives/edgar/data/789019/000/x.htm",
        "https://sec.gov/Archives/edgar/data/789019/000/x.htm",
        "https://data.sec.gov/submissions/CIK0000789019.json",
    ],
)
def test_genuine_sec_urls_are_accepted(url):
    assert validate_record(record(source_url=url)) == []


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


# --- document preparation ---------------------------------------------------
def test_each_filing_is_prepared_once_however_many_records_cite_it():
    """Normalising a 2 MB 10-K per record is the difference between seconds and minutes."""
    calls = []

    def document_for(rec):
        calls.append(rec["source_url"])
        return FILING

    verify_records([record(), record(), record()], document_for)
    assert len(calls) == 1


def test_unresolvable_filing_is_not_retried_per_record():
    calls = []

    def document_for(rec):
        calls.append(rec["source_url"])
        return None

    report = verify_records([record(), record()], document_for)
    assert len(calls) == 1
    assert report.count("undocumented") == 2


def test_prepared_document_gives_the_same_verdict_as_raw_text():
    prepared = PreparedDocument(FILING)
    assert verify_sentence(VERBATIM, prepared).level == verify_sentence(VERBATIM, FILING).level
    assert not verify_sentence("We buy everything from Acme Corporation.", prepared).supported
