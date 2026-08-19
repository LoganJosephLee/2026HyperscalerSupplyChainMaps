"""Running extraction over many filings: resume, failure, and wasted spend.

At four API calls none of this mattered. At five hundred it is the difference
between a dropped connection costing a retry and costing the whole run.
"""

from __future__ import annotations

import argparse
import json

import pytest

from hscm import config
from hscm.cli import cmd_extract


class Recorder:
    """Stands in for the real extractor; counts what it was asked to do."""

    name = "recorder"

    def __init__(self, fail_on: set[str] | None = None, stop_after: int | None = None):
        self.seen: list[str] = []
        self.fail_on = fail_on or set()
        self.stop_after = stop_after

    def extract(self, request) -> list[dict]:
        if self.stop_after is not None and len(self.seen) >= self.stop_after:
            raise KeyboardInterrupt
        self.seen.append(request.section_key)
        if request.section_key in self.fail_on:
            raise RuntimeError("upstream said no")
        return [{
            "supplier_name_raw": "Northgate Components Inc.",
            "buyer_name_raw": request.filing.company_name,
            "source_sentence": f"A sentence from {request.section_key}.",
            "source_url": request.filing.document_url,
        }]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "data" / "cache")
    monkeypatch.setattr(config, "MANIFEST_PATH", tmp_path / "data" / "cache" / "manifest.json")
    config.CACHE_DIR.mkdir(parents=True)

    body = (
        "<html><body>"
        "<p>Item 1. Business</p>" + "<p>Business prose about suppliers.</p>" * 400 +
        "<p>Item 1A. Risk Factors</p>" + "<p>Risk prose about suppliers.</p>" * 400 +
        "</body></html>"
    )
    filing_path = config.CACHE_DIR / "acme-10k.htm"
    filing_path.write_text(body, encoding="utf-8")
    config.MANIFEST_PATH.write_text(json.dumps([{
        "cik": 1, "ticker": "ACME", "company_name": "Acme Corp", "form_type": "10-K",
        "filing_date": "2026-01-01", "report_date": "2025-12-31",
        "accession": "0000000000-26-000001", "primary_document": "acme-10k.htm",
        "cache_path": str(filing_path.relative_to(tmp_path)),
        "document_url": "https://www.sec.gov/Archives/edgar/data/1/000/acme-10k.htm",
    }]), encoding="utf-8")
    return tmp_path


def run(monkeypatch, out, extractor, **overrides):
    monkeypatch.setattr("hscm.extract.get_extractor", lambda _name=None: extractor)
    args = argparse.Namespace(
        tickers=[], extractor=None, out=str(out), sections=None, estimate=False,
        restart=False, include_poor_splits=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return cmd_extract(args)


def test_records_survive_an_interruption(workspace, monkeypatch, capsys):
    out = workspace / "records.json"
    run(monkeypatch, out, Recorder(stop_after=1))
    assert json.loads(out.read_text(encoding="utf-8")), "the finished section was kept"


def test_a_second_run_does_not_pay_for_finished_sections_again(workspace, monkeypatch):
    out = workspace / "records.json"
    run(monkeypatch, out, Recorder(stop_after=1))

    second = Recorder()
    run(monkeypatch, out, second)
    assert "item1" not in second.seen, "item1 was already extracted and paid for"
    assert "item1a" in second.seen


def test_restart_ignores_saved_progress(workspace, monkeypatch):
    out = workspace / "records.json"
    run(monkeypatch, out, Recorder(stop_after=1))
    second = Recorder()
    run(monkeypatch, out, second, restart=True)
    assert "item1" in second.seen


def test_one_failing_section_does_not_end_the_run(workspace, monkeypatch, capsys):
    out = workspace / "records.json"
    extractor = Recorder(fail_on={"item1"})
    run(monkeypatch, out, extractor)
    assert "item1a" in extractor.seen, "the run continued past the failure"
    assert "failed and were skipped" in capsys.readouterr().err


def test_a_failed_section_is_retried_next_run(workspace, monkeypatch):
    out = workspace / "records.json"
    run(monkeypatch, out, Recorder(fail_on={"item1"}))
    second = Recorder()
    run(monkeypatch, out, second)
    assert "item1" in second.seen, "a failure is not progress"
