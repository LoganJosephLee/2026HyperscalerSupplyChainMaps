"""Tests for HTML-to-text and item splitting.

The HTML here is a synthetic stand-in shaped like a real 10-K — table of
contents up top, body sections far apart, a cross-reference planted where it
would break a naive splitter. It is a test input; it is not filing data and
never enters the dataset.
"""

from __future__ import annotations

import pytest

from hscm.sections import (
    EXTRACTION_KEYS,
    document_text,
    find_concentration_passages,
    normalize_text,
    split_items,
)

FILLER = "<p>{}</p>".format(" ".join(["This paragraph exists only to create distance."] * 300))

TEN_K_HTML = f"""
<html><body>
<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p>
<p>FORM 10-K</p>

<!-- table of contents: every item header appears here first -->
<table>
  <tr><td>Item 1.</td><td>Business</td><td>3</td></tr>
  <tr><td>Item 1A.</td><td>Risk Factors</td><td>12</td></tr>
  <tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>30</td></tr>
  <tr><td>Item 2.</td><td>Properties</td><td>31</td></tr>
  <tr><td>Item 7.</td><td>Management&rsquo;s Discussion and Analysis</td><td>40</td></tr>
  <tr><td>Item 8.</td><td>Financial Statements and Supplementary Data</td><td>55</td></tr>
</table>

<p>Item 1. Business</p>
<p>We operate a cloud computing business and build datacenters worldwide.</p>
{FILLER}

<p>Item 1A. Risk Factors</p>
<p>We depend on a limited number of suppliers for critical datacenter components,
including graphics processing units and high&nbsp;bandwidth memory.</p>
<p>For further detail see Item 1. Business above and Item 8. Financial Statements below.</p>
{FILLER}

<p>Item 1B. Unresolved Staff Comments</p>
<p>None.</p>
{FILLER}

<p>Item 2. Properties</p>
<p>We lease and own datacenter facilities in North America, Europe and Asia.</p>
{FILLER}

<p>Item 7. Management&rsquo;s Discussion and Analysis of Financial Condition</p>
<p>Revenue increased primarily due to growth in cloud services.</p>
{FILLER}

<p>Item 8. Financial Statements and Supplementary Data</p>
<p>Concentration of Credit Risk. One customer accounted for 21% of total revenue
during the year ended June 30, 2025, and no other customer exceeded 10%.</p>
{FILLER}

<p>Item 15. Exhibits, Financial Statement Schedules</p>
<p>The following exhibits are filed as part of this report.</p>
<p>SIGNATURES. Pursuant to the requirements of the Securities Exchange Act of 1934,
the registrant has duly caused this report to be signed on its behalf.</p>
</body></html>
"""

TEN_Q_HTML = f"""
<html><body>
<p>FORM 10-Q</p>
<table>
  <tr><td>Item 1.</td><td>Financial Statements</td><td>3</td></tr>
  <tr><td>Item 2.</td><td>Management&rsquo;s Discussion and Analysis</td><td>20</td></tr>
  <tr><td>Item 3.</td><td>Quantitative and Qualitative Disclosures</td><td>30</td></tr>
  <tr><td>Item 4.</td><td>Controls and Procedures</td><td>32</td></tr>
  <tr><td>Item 1.</td><td>Legal Proceedings</td><td>33</td></tr>
  <tr><td>Item 1A.</td><td>Risk Factors</td><td>34</td></tr>
</table>

<p>PART I &mdash; FINANCIAL INFORMATION</p>
<p>Item 1. Financial Statements</p>
<p>One customer accounted for 18% of total revenue for the quarter.</p>
{FILLER}

<p>Item 2. Management&rsquo;s Discussion and Analysis of Financial Condition</p>
<p>Capital expenditures rose on datacenter construction.</p>
{FILLER}

<p>Item 4. Controls and Procedures</p>
<p>Disclosure controls were effective.</p>
{FILLER}

<p>PART II &mdash; OTHER INFORMATION</p>
<p>Item 1. Legal Proceedings</p>
<p>We are subject to various claims arising in the ordinary course of business.</p>
{FILLER}

<p>Item 1A. Risk Factors</p>
<p>We rely on a limited number of suppliers for accelerators used in our datacenters.</p>
{FILLER}
</body></html>
"""

EIGHT_K_HTML = """
<html><body>
<p>FORM 8-K &mdash; CURRENT REPORT</p>
<p>Item 1.01 Entry into a Material Definitive Agreement.</p>
<p>On August 1, 2025, the registrant entered into a supply agreement for
datacenter power equipment.</p>
</body></html>
"""


@pytest.fixture(scope="module")
def text() -> str:
    return document_text(TEN_K_HTML)


@pytest.fixture(scope="module")
def sections(text):
    return split_items(text, "10-K")


# --- normalisation ----------------------------------------------------------
def test_non_breaking_space_becomes_a_plain_space(text):
    assert "\xa0" not in text
    assert "high bandwidth memory" in text


def test_curly_punctuation_is_normalised():
    assert normalize_text("Management’s “discussion” — here") == (
        "Management's \"discussion\" - here"
    )


def test_normalisation_is_idempotent(text):
    assert normalize_text(text) == text


# --- item splitting ---------------------------------------------------------
def test_all_items_located(sections):
    assert set(sections) == {"item1", "item1a", "item1b", "item2", "item7", "item8", "item15"}


def test_last_content_item_does_not_swallow_the_exhibit_index(sections):
    """Without an Item 15 terminator, Item 8 runs to the end of the document."""
    assert "SIGNATURES" not in sections["item8"].text
    assert "exhibits are filed as part of this report" not in sections["item8"].text
    assert "SIGNATURES" in sections["item15"].text


def test_table_of_contents_is_not_mistaken_for_the_body(text, sections):
    """No item may start on a contents row, and no section may contain one."""
    contents_row = "Item 1A. Risk Factors 12"
    assert contents_row in text  # the trap is present in the document
    assert all(contents_row not in section.text for section in sections.values())
    assert sections["item1"].text.startswith("Item 1. Business\n\nWe operate")


def test_first_item_starts_after_the_contents_page(text, sections):
    last_contents_row = text.index("Item 8. Financial Statements and Supplementary Data 55")
    assert min(s.start for s in sections.values()) > last_contents_row


def test_contents_page_is_reported_in_diagnostics(text):
    diagnostics: dict = {}
    split_items(text, "10-K", diagnostics)
    assert diagnostics["toc_spans"]
    assert diagnostics["candidates"]["item1"] >= 2  # contents row plus body header


def test_items_are_in_document_order(sections):
    starts = [sections[key].start for key in ("item1", "item1a", "item1b", "item2", "item7", "item8")]
    assert starts == sorted(starts)


def test_section_content_lands_in_the_right_item(sections):
    assert "cloud computing business" in sections["item1"].text
    assert "limited number of suppliers" in sections["item1a"].text
    assert "21% of total revenue" in sections["item8"].text


def test_cross_reference_does_not_split_the_section(sections):
    """'see Item 1. Business above' sits inside 1A and must not restart Item 1."""
    assert "see Item 1. Business above" in sections["item1a"].text


def test_sections_are_contiguous_and_non_overlapping(sections):
    ordered = sorted(sections.values(), key=lambda s: s.start)
    for earlier, later in zip(ordered, ordered[1:]):
        assert earlier.end == later.start


def test_unknown_form_type_raises():
    with pytest.raises(ValueError):
        split_items("some text", "S-1")


# --- other forms ------------------------------------------------------------
def test_10q_uses_its_own_numbering():
    """10-K markers applied to a 10-Q find Properties and MD&A that are not there."""
    sections = split_items(document_text(TEN_Q_HTML), "10-Q")
    assert "part1_item1" in sections
    assert "part2_item1a" in sections
    assert "18% of total revenue" in sections["part1_item1"].text
    assert "limited number of suppliers" in sections["part2_item1a"].text


def test_10q_distinguishes_item_1_in_part_i_from_part_ii():
    sections = split_items(document_text(TEN_Q_HTML), "10-Q")
    assert "Financial Statements" in sections["part1_item1"].text[:60]
    assert "Legal Proceedings" in sections["part2_item1"].text[:60]
    assert sections["part1_item1"].start < sections["part2_item1"].start


def test_8k_is_returned_whole_rather_than_raising():
    """8-K numbering shares nothing with the annual forms; do not split it."""
    sections = split_items(document_text(EIGHT_K_HTML), "8-K")
    assert set(sections) == {"body"}
    assert "supply agreement for datacenter power equipment" in sections["body"].text


def test_every_configured_form_type_can_be_split():
    """config.FORM_TYPES and MARKERS_BY_FORM must not drift apart."""
    from hscm.config import FORM_TYPES

    for form in FORM_TYPES:
        split_items("Item 1. Business\nWe do things.", form)  # must not raise


def test_extraction_keys_exist_for_every_form():
    from hscm.config import FORM_TYPES

    assert set(FORM_TYPES) <= set(EXTRACTION_KEYS)


# --- encoding ---------------------------------------------------------------
def test_windows_1252_filing_decodes_without_mangling_quotes():
    """Filings are not all UTF-8; a mangled quote makes its sentence unverifiable."""
    html = (
        '<html><head><meta charset="windows-1252"></head><body>'
        "<p>Management’s discussion of the Company’s suppliers.</p>"
        "</body></html>"
    ).encode("cp1252")
    text = document_text(html)
    assert "�" not in text
    assert "Management's discussion of the Company's suppliers." in text


def test_utf8_filing_still_decodes():
    html = "<html><body><p>Management’s discussion.</p></body></html>".encode("utf-8")
    assert "Management's discussion." in document_text(html)


def test_missing_items_are_absent_not_empty():
    sections = split_items(document_text("<p>Item 1. Business</p><p>We do things.</p>"), "10-K")
    assert "item1a" not in sections


# --- concentration passages -------------------------------------------------
def test_concentration_passage_found(text):
    passages = find_concentration_passages(text)
    joined = " ".join(p.text for p in passages)
    assert "21% of total revenue" in joined


def test_supplier_side_language_found(text):
    passages = find_concentration_passages(text)
    joined = " ".join(p.text for p in passages)
    assert "limited number of suppliers" in joined


def test_short_table_cells_are_ignored():
    assert find_concentration_passages("Customer A\nMajor customers\n21%") == []
