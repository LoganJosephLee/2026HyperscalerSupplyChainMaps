"""Tests for HTML-to-text and item splitting.

The HTML here is a synthetic stand-in shaped like a real 10-K — table of
contents up top, body sections far apart, a cross-reference planted where it
would break a naive splitter. It is a test input; it is not filing data and
never enters the dataset.
"""

from __future__ import annotations

import pytest

from hscm.sections import (
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
    assert set(sections) == {"item1", "item1a", "item1b", "item2", "item7", "item8"}


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
