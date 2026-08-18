"""Tests for the extractor interface and the fixture implementation.

AnthropicExtractor is not exercised here: it has never made a call, and a test
against a mocked client would only assert that the mock matches my guess about
the API. Its window-splitting is pure and is tested; the request itself is not.
"""

from __future__ import annotations

import json

import pytest

from hscm.edgar import Filing
from hscm.extract import RELATIONSHIP_SCHEMA, ExtractionRequest, get_extractor, stamp_provenance
from hscm.extract.fixture import FixtureExtractor, FixtureNotFoundError

FILING = Filing(
    cik=789019,
    ticker="MSFT",
    company_name="MICROSOFT CORP",
    form_type="10-K",
    filing_date="2025-07-31",
    report_date="2025-06-30",
    accession="0000950170-25-000001",
    primary_document="filing.htm",
)


def request(section_key="item1a") -> ExtractionRequest:
    return ExtractionRequest(FILING, section_key, "Item 1A. Risk Factors", "some filing text")


# --- the schema -------------------------------------------------------------
def test_schema_requires_every_property():
    """Structured outputs need every property listed in required, or the model may omit one."""
    item = RELATIONSHIP_SCHEMA["properties"]["relationships"]["items"]
    assert set(item["required"]) == set(item["properties"])
    assert item["additionalProperties"] is False
    assert RELATIONSHIP_SCHEMA["additionalProperties"] is False


def test_schema_does_not_ask_the_model_for_provenance():
    """source_url, form_type and filing_date are facts about the fetch, not model output."""
    item = RELATIONSHIP_SCHEMA["properties"]["relationships"]["items"]
    for field in ("source_url", "form_type", "filing_date"):
        assert field not in item["properties"]
    assert "source_sentence" in item["properties"]


def test_schema_keeps_unclear_as_a_relationship_type():
    types = RELATIONSHIP_SCHEMA["properties"]["relationships"]["items"]["properties"][
        "relationship_type"
    ]["enum"]
    assert "unclear" in types


def test_nullable_fields_use_anyof_not_a_type_list():
    """Structured outputs reject `"type": ["number", "null"]`."""
    props = RELATIONSHIP_SCHEMA["properties"]["relationships"]["items"]["properties"]
    assert "anyOf" in props["quantified_pct"]
    assert "anyOf" in props["product_or_service"]


# --- provenance stamping ----------------------------------------------------
def test_provenance_comes_from_the_filing():
    stamped = stamp_provenance({"source_sentence": "..."}, FILING)
    assert stamped["source_url"] == FILING.document_url
    assert stamped["form_type"] == "10-K"
    assert stamped["filing_date"] == "2025-07-31"


def test_stamping_overrides_a_wrong_url():
    """A citation pointing at the wrong filing is worse than no citation."""
    stamped = stamp_provenance({"source_url": "https://example.com/made-up.htm"}, FILING)
    assert stamped["source_url"] == FILING.document_url


# --- selection --------------------------------------------------------------
def test_default_extractor_is_the_fixture(monkeypatch):
    from hscm import config

    monkeypatch.setattr(config, "EXTRACTOR", "fixture")
    assert get_extractor().name == "fixture"


def test_explicit_choice_overrides_config(monkeypatch):
    from hscm import config

    monkeypatch.setattr(config, "EXTRACTOR", "anthropic")
    assert get_extractor("fixture").name == "fixture"


def test_unknown_extractor_names_the_valid_options():
    with pytest.raises(ValueError, match="fixture"):
        get_extractor("gpt")


# --- FixtureExtractor -------------------------------------------------------
def test_missing_fixture_explains_itself(tmp_path):
    extractor = FixtureExtractor(tmp_path / "nope.json")
    with pytest.raises(FixtureNotFoundError, match="HSCM_EXTRACTOR=anthropic"):
        extractor.extract(request())


def test_fixture_replays_records_and_stamps_provenance(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps([
        {"supplier_name_raw": "A Vendor", "buyer_name_raw": "MICROSOFT CORP",
         "source_sentence": "A sentence from the filing.", "relationship_type": "supplies"}
    ]), encoding="utf-8")
    records = FixtureExtractor(path).extract(request())
    assert len(records) == 1
    assert records[0]["source_url"] == FILING.document_url
    assert records[0]["form_type"] == "10-K"


def test_fixture_filters_by_accession_and_section(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps([
        {"_accession": "0000950170-25-000001", "_section_key": "item1a", "source_sentence": "kept"},
        {"_accession": "0000950170-25-000001", "_section_key": "item1", "source_sentence": "wrong section"},
        {"_accession": "9999999999-99-999999", "_section_key": "item1a", "source_sentence": "wrong filing"},
    ]), encoding="utf-8")
    records = FixtureExtractor(path).extract(request("item1a"))
    assert [r["source_sentence"] for r in records] == ["kept"]
    assert all(not key.startswith("_") for record in records for key in record)


def test_fixture_accepts_the_wrapped_form(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"relationships": [{"source_sentence": "kept"}]}), encoding="utf-8")
    assert len(FixtureExtractor(path).extract(request())) == 1


# --- window splitting (pure, so testable without the API) -------------------
def test_short_section_is_one_window():
    from hscm.extract.anthropic_api import AnthropicExtractor

    assert AnthropicExtractor.windows("short text") == ["short text"]


def test_long_section_is_split_with_overlap():
    from hscm.extract.anthropic_api import WINDOW_CHARS, AnthropicExtractor

    text = "\n".join(f"line {i} of the filing text" for i in range(6000))
    windows = AnthropicExtractor.windows(text)

    assert len(windows) > 1
    assert all(len(w) <= WINDOW_CHARS for w in windows)
    # No content may fall between two windows.
    assert "".join(windows).replace("\n", "") != ""
    for earlier, later in zip(windows, windows[1:]):
        assert earlier[-200:] and later[:200]
        assert text.index(later[:100]) < text.index(earlier[-100:]) + len(earlier)
