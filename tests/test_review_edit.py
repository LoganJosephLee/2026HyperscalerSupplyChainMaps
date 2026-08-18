"""The interactive review prompter.

The CSV form of the queue was unusable — eighteen rows of thirteen columns wraps
into a solid block in any editor. This asks one question at a time. It writes the
same aliases.yaml the CSV path writes, so the two must agree.
"""

from __future__ import annotations

import argparse
import json

import pytest

from hscm import config
from hscm.cli import cmd_review_edit
from hscm.resolve import Aliases

SPINE = {
    "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "1": {"cik_str": 1835632, "ticker": "MRVL", "title": "Marvell Technology, Inc."},
}


def record(supplier: str) -> dict:
    return {
        "supplier_name_raw": supplier,
        "buyer_name_raw": "NVIDIA CORP",
        "relationship_type": "supplies",
        "product_or_service": None,
        "quantified_pct": None,
        "quantified_basis": None,
        "extraction_confidence": "high",
        "form_type": "10-K",
        "filing_date": "2026-02-25",
        "source_sentence": "A sentence from a filing that is long enough to be a citation.",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/000/x.htm",
    }


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "ALIASES_PATH", tmp_path / "aliases.yaml")
    monkeypatch.setattr(config, "REVIEW_QUEUE_PATH", tmp_path / "queue.csv")
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (config.CACHE_DIR / "company_tickers.json").write_text(json.dumps(SPINE), encoding="utf-8")
    return tmp_path


def run(workspace, monkeypatch, records: list[dict], answers: list[str]) -> Aliases:
    path = workspace / "records.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(replies))
    cmd_review_edit(argparse.Namespace(extractions=str(path), threshold=None))
    return Aliases.load(config.ALIASES_PATH)


def test_choosing_a_candidate_writes_an_alias(workspace, monkeypatch):
    aliases = run(workspace, monkeypatch, [record("Marvell")], ["1"])
    assert aliases.aliases["Marvell"]["cik"] == 1835632


def test_marking_a_non_filer_records_the_fold_and_the_reason(workspace, monkeypatch):
    aliases = run(
        workspace,
        monkeypatch,
        [record("KYEC")],
        ["n", "King Yuan Electronics Company", "Taiwan-listed; files nothing with the SEC"],
    )
    assert aliases.non_filers["KYEC"]["canonical"] == "King Yuan Electronics Company"
    assert "Taiwan-listed" in aliases.non_filers["KYEC"]["note"]


def test_a_name_containing_a_role_noun_never_reaches_the_reviewer(workspace, monkeypatch):
    """The unnamed-party guard is aggressive: any name containing "vendor",
    "foundry" or "supplier" is rejected before resolution. That is right for the
    descriptions it is aimed at, and it would also reject a real company that
    happened to be called one — the tradeoff is deliberate, and the rejection is
    printed by `hscm verify` rather than made quietly.
    """
    aliases = run(workspace, monkeypatch, [record("Some Vendor Holdings")], [])
    assert aliases.aliases == {}


def test_a_typed_cik_is_checked_against_the_registry(workspace, monkeypatch):
    aliases = run(workspace, monkeypatch, [record("Nantong Precision Holdings")], ["1045810"])
    assert aliases.aliases["Nantong Precision Holdings"]["cik"] == 1045810


def test_a_cik_that_is_not_in_the_registry_is_refused(workspace, monkeypatch):
    """A mistyped CIK merges two companies silently. Nothing downstream can tell."""
    aliases = run(workspace, monkeypatch, [record("Nantong Precision Holdings")], ["9999999999"])
    assert aliases.aliases == {}


def test_skipping_leaves_the_name_undecided(workspace, monkeypatch):
    aliases = run(workspace, monkeypatch, [record("Nantong Precision Holdings")], ["s"])
    assert aliases.aliases == {} and aliases.non_filers == {}


def test_an_exclusion_without_a_reason_is_refused(workspace, monkeypatch):
    aliases = run(workspace, monkeypatch, [record("Nantong Precision Holdings")], ["x", ""])
    assert aliases.excluded == {}


def test_quitting_keeps_what_was_already_decided(workspace, monkeypatch):
    aliases = run(
        workspace,
        monkeypatch,
        [record("Marvell"), record("Another Unknown Vendor")],
        ["1", "q"],
    )
    assert aliases.aliases["Marvell"]["cik"] == 1835632
    assert "Another Unknown Vendor" not in aliases.aliases


def test_a_record_the_validator_rejects_never_reaches_the_reviewer(workspace, monkeypatch):
    """"third-party foundries located in Taiwan" is not a name awaiting a CIK."""
    aliases = run(
        workspace, monkeypatch, [record("third-party foundries located in Taiwan")], []
    )
    assert aliases.aliases == {} and aliases.non_filers == {}
