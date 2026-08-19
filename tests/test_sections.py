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
    extraction_sections,
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


# --- what extraction actually reads -----------------------------------------
def test_extraction_reads_the_located_sections(sections):
    from hscm.sections import extraction_sections

    keys = [key for key, _, _ in extraction_sections(sections, "10-K")]
    assert keys == ["item1", "item1a", "item8"]


def test_stub_item8_falls_back_to_item15():
    """Oracle answers Item 8 with a cross-reference and files the statements under 15."""
    from hscm.sections import Section, extraction_sections

    sections = {
        "item1": Section("item1", "Item 1. Business", 0, 10, "x" * 10_000),
        "item1a": Section("item1a", "Item 1A. Risk Factors", 0, 10, "y" * 10_000),
        "item8": Section("item8", "Item 8. Financial Statements", 0, 10,
                         "The response to this item is submitted as a separate section."),
        "item15": Section("item15", "Item 15. Exhibits", 0, 10, "z" * 160_000),
    }
    chosen = extraction_sections(sections, "10-K")
    keys = [key for key, _, _ in chosen]
    assert "item15" in keys and "item8" not in keys
    label = next(label for key, label, _ in chosen if key == "item15")
    assert "cross-reference" in label


def test_healthy_item8_is_not_replaced():
    from hscm.sections import Section, extraction_sections

    sections = {
        "item8": Section("item8", "Item 8. Financial Statements", 0, 10, "z" * 100_000),
        "item15": Section("item15", "Item 15. Exhibits", 0, 10, "w" * 5_000),
    }
    assert [key for key, _, _ in extraction_sections(sections, "10-K")] == ["item8"]


# --- candidate diagnostics --------------------------------------------------
def test_candidate_report_explains_each_decision(text):
    from hscm.sections import candidate_report

    rows = candidate_report(text, "10-K")
    assert rows, "the report must list candidates"

    item1_rows = [r for r in rows if r["key"] == "item1"]
    assert len(item1_rows) >= 2  # the contents row and the body header

    chosen = [r for r in item1_rows if r["chosen"]]
    assert len(chosen) == 1
    assert not chosen[0]["dropped_as_contents_row"]

    contents_row = next(r for r in item1_rows if r["dropped_as_contents_row"])
    assert contents_row["in_dense_span"]
    assert contents_row["tail_looks_like_page_number"]
    assert contents_row["position"] < chosen[0]["position"]


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


# --- regressions from the first twenty real filings --------------------------
# Each of these reproduces something the synthetic fixture did not catch and a
# real 10-K or 20-F did.

FILLER_2 = "<p>{}</p>".format(" ".join(["Body prose that separates the items."] * 400))

DASH_HEADER_HTML = f"""
<html><body>
<table>
  <tr><td>Item 1.</td><td>Business</td><td>5</td></tr>
  <tr><td>Item 1A.</td><td>Risk Factors</td><td>16</td></tr>
  <tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>31</td></tr>
  <tr><td>Item 2.</td><td>Properties</td><td>33</td></tr>
  <tr><td>Item 7.</td><td>Management&rsquo;s Discussion and Analysis</td><td>40</td></tr>
  <tr><td>Item 8.</td><td>Financial Statements and Supplementary Data</td><td>66</td></tr>
</table>
<p>ITEM 1 &mdash; BUSINESS</p>
<p>We design and sell servers and storage.</p>
{FILLER_2}
<p>ITEM 1A &mdash; RISK FACTORS</p>
<p>We rely on a limited number of suppliers for critical components.</p>
{FILLER_2}
<p>ITEM 8 &mdash; FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA</p>
<p>Concentration of credit risk information appears here.</p>
{FILLER_2}
</body></html>
"""


def test_headers_with_spaced_dashes_are_found():
    """Dell writes "ITEM 1 — BUSINESS". Requiring flush punctuation found nothing,
    and the entire document collapsed into Item 15."""
    sections = split_items(document_text(DASH_HEADER_HTML), "10-K")
    assert "item1" in sections and "item1a" in sections
    assert "We design and sell servers" in sections["item1"].text
    assert "limited number of suppliers" in sections["item1a"].text


def test_first_item_does_not_start_on_the_contents_page():
    """The bug every real filing showed: item1 began at the contents page.

    The optimiser scores chains by capped gaps, and the first item has no
    preceding gap — so the contents row and the body header tied, and the
    tie-break took the earlier one.
    """
    text = document_text(DASH_HEADER_HTML)
    sections = split_items(text, "10-K")
    assert "Risk Factors 16" not in sections["item1"].text
    assert sections["item1"].text.lower().startswith("item 1 - business")


def test_contents_row_is_recognised_by_the_crowd_that_follows_it(text):
    from hscm.sections import candidate_report

    rows = candidate_report(text, "10-K")
    dropped = [r for r in rows if r["dropped_as_contents_row"]]
    assert dropped, "the contents page must be identified"
    assert all(r["in_dense_span"] for r in dropped)


TWENTY_F_HTML = f"""
<html><body>
<table>
  <tr><td>ITEM 3.</td><td>KEY INFORMATION</td><td>4</td></tr>
  <tr><td>ITEM 4.</td><td>INFORMATION ON THE COMPANY</td><td>14</td></tr>
  <tr><td>ITEM 4A.</td><td>UNRESOLVED STAFF COMMENTS</td><td>26</td></tr>
  <tr><td>ITEM 5.</td><td>OPERATING AND FINANCIAL REVIEWS AND PROSPECTS</td><td>27</td></tr>
  <tr><td>ITEM 8.</td><td>FINANCIAL INFORMATION</td><td>40</td></tr>
  <tr><td>ITEM 18.</td><td>FINANCIAL STATEMENTS</td><td>60</td></tr>
</table>
<p>ITEM 3.KEY INFORMATION</p>
<p>Selected financial data follows.</p>
{FILLER_2}
<p>D. Risk Factors</p>
<p>We depend on a limited number of equipment suppliers for advanced nodes.</p>
{FILLER_2}
<p>ITEM 4.INFORMATION ON THE COMPANY</p>
<p>We are a dedicated semiconductor foundry.</p>
{FILLER_2}
<p>ITEM 4A.UNRESOLVED STAFF COMMENTS</p>
<p>None.</p>
<p>ITEM 5.OPERATING AND FINANCIAL REVIEWS AND PROSPECTS</p>
<p>Revenue grew on advanced node demand.</p>
{FILLER_2}
<p>ITEM 8.FINANCIAL INFORMATION</p>
<p>One customer accounted for 23% of net revenue.</p>
{FILLER_2}
<p>ITEM 18.FINANCIAL STATEMENTS</p>
<p>Refer to the consolidated financial statements starting on page F-1.</p>
</body></html>
"""


def test_20f_risk_factors_are_found_as_a_sub_item():
    """TSMC writes "D. Risk Factors", not "Item 3.D Risk Factors"."""
    sections = split_items(document_text(TWENTY_F_HTML), "20-F")
    assert "item3d" in sections
    assert "limited number of equipment suppliers" in sections["item3d"].text


def test_20f_operating_review_matches_the_plural_heading():
    """TSMC's heading is "OPERATING AND FINANCIAL REVIEWS"; \\b after the
    singular never matched it, so Item 4A swallowed 100,000 characters."""
    sections = split_items(document_text(TWENTY_F_HTML), "20-F")
    assert "item5" in sections
    assert sections["item4a"].char_count < 5_000


def test_20f_stub_item18_falls_back_to_item8():
    from hscm.sections import extraction_sections

    sections = split_items(document_text(TWENTY_F_HTML), "20-F")
    chosen = [key for key, _, _ in extraction_sections(sections, "20-F")]
    assert "item8" in chosen and "item18" not in chosen


def test_stub_fallback_does_not_duplicate_an_already_chosen_section():
    from hscm.sections import Section, extraction_sections

    sections = {
        "item4": Section("item4", "Item 4", 0, 1, "a" * 10_000),
        "item3d": Section("item3d", "Item 3.D", 0, 1, "b" * 10_000),
        "item8": Section("item8", "Item 8", 0, 1, "c" * 50_000),
        "item18": Section("item18", "Item 18", 0, 1, "stub"),
    }
    keys = [key for key, _, _ in extraction_sections(sections, "20-F")]
    assert keys.count("item8") == 1


# --- page furniture ----------------------------------------------------------
PAGINATED_HTML = """
<html><body>
<p>We currently outsource all of our IC manufacturing to</p>
<p>23</p>
<p>Table of Contents</p>
<p>TSMC, with the assembly and testing processes outsourced to other
subcontractors primarily in Asia.</p>
</body></html>
"""


def test_page_numbers_do_not_interrupt_a_sentence():
    """A real extraction was rejected because the filing text read
    "...manufacturing to 23 TSMC, with...". The 23 is a page number."""
    text = document_text(PAGINATED_HTML)
    assert "23" not in text
    assert "Table of Contents" not in text


def test_a_sentence_spanning_a_page_break_verifies():
    from hscm.verify import verify_sentence

    quoted = (
        "We currently outsource all of our IC manufacturing to TSMC, with the "
        "assembly and testing processes outsourced to other subcontractors "
        "primarily in Asia."
    )
    assert verify_sentence(quoted, document_text(PAGINATED_HTML)).supported


def test_numbers_inside_a_line_are_kept():
    """Only a line that is nothing but a number is furniture."""
    html = "<p>One customer accounted for 23% of total revenue in 2025.</p>"
    assert "23% of total revenue" in document_text(html)


def test_table_rows_of_figures_survive():
    html = "<table><tr><td>Revenue</td><td>1,234</td><td>5,678</td></tr></table>"
    text = document_text(html)
    assert "1,234" in text and "5,678" in text


def test_a_split_that_misses_the_document_is_measurable():
    """ASML's 20-F located one 38,000-char section in a 1,400,000-char filing.

    Every per-section check passed: the section it found was a reasonable size
    and looked like risk factors. What it had actually found was the 20-F
    cross-reference table at the very end of the annual report. The only signal
    that anything was wrong is how little of the filing was being read.
    """
    # The marker sits near the very end, as ASML's does: everything before it is
    # a document the splitter has no name for, so what it finds is genuine and
    # tiny at the same time.
    filler = "Annual report prose that no item marker introduces. " * 4000
    tail = "Item 1A. Risk Factors\nRisk prose. " + ("More risk prose. " * 40)
    text = normalize_text(filler + "\n" + tail)

    sections = split_items(text, "10-K")
    covered = sum(len(t) for _, _, t in extraction_sections(sections, "10-K"))
    assert 0 < covered, "the section really is found — that is what makes this hard to spot"
    assert covered / len(text) < 0.10
