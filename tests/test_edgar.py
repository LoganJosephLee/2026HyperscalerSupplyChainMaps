"""Tests for the EDGAR client's caching and throttling behaviour.

No network access: the HTTP session is replaced with a scripted stand-in.
"""

from __future__ import annotations

import json

import pytest
import requests

from hscm import config, edgar
from hscm.edgar import EdgarClient, Filing, read_manifest, write_manifest


@pytest.fixture(autouse=True)
def temp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "data/cache")
    monkeypatch.setattr(config, "FILINGS_DIR", tmp_path / "data/cache/filings")
    monkeypatch.setattr(config, "MANIFEST_PATH", tmp_path / "data/cache/manifest.json")
    return tmp_path


def filing(ticker="MSFT", accession="0000950170-25-000001", form="10-K", date="2025-07-31") -> Filing:
    return Filing(
        cik=789019,
        ticker=ticker,
        company_name="Example Filer Corp",
        form_type=form,
        filing_date=date,
        report_date="2025-06-30",
        accession=accession,
        primary_document="filing.htm",
    )


# --- manifest ---------------------------------------------------------------
def test_manifest_round_trips_urls_and_cache_path():
    write_manifest([filing()])
    rows = read_manifest()
    assert len(rows) == 1
    assert rows[0]["document_url"].endswith("/000095017025000001/filing.htm")
    assert rows[0]["cache_path"] == "data/cache/filings/CIK0000789019/000095017025000001/filing.htm"


def test_second_fetch_of_another_form_keeps_the_first():
    """`fetch --form 8-K` must not unregister the 10-Ks already on disk."""
    write_manifest([filing(form="10-K", accession="0000950170-25-000001")])
    write_manifest([filing(form="8-K", accession="0000950170-25-000002", date="2025-08-01")])

    rows = read_manifest()
    assert {row["form_type"] for row in rows} == {"10-K", "8-K"}
    assert len(rows) == 2


def test_refetching_the_same_filing_does_not_duplicate_it():
    write_manifest([filing()])
    write_manifest([filing()])
    assert len(read_manifest()) == 1


def test_merge_false_replaces_the_manifest():
    write_manifest([filing(accession="0000950170-25-000001")])
    write_manifest([filing(accession="0000950170-25-000002")], merge=False)
    assert [row["accession"] for row in read_manifest()] == ["0000950170-25-000002"]


def test_missing_manifest_reads_as_empty():
    assert read_manifest() == []


# --- retry / throttling -----------------------------------------------------
class ScriptedSession:
    """Returns the queued status codes in order, recording every call."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0
        self.headers = {}

    def get(self, url, timeout=None):
        self.calls += 1
        response = requests.Response()
        response.status_code = self.statuses.pop(0)
        response.url = url
        response._content = b"{}"
        return response


def client_with(statuses, monkeypatch) -> tuple[EdgarClient, ScriptedSession]:
    monkeypatch.setattr(edgar.time, "sleep", lambda _: None)
    client = EdgarClient(rps=1000)
    session = ScriptedSession(statuses)
    client._session = session
    return client, session


def test_throttled_request_is_retried_then_succeeds(monkeypatch):
    """SEC answers 403 or 429 when you ask too fast; that is not permanent."""
    client, session = client_with([429, 403, 200], monkeypatch)
    assert client._get("https://www.sec.gov/x").status_code == 200
    assert session.calls == 3


def test_transient_server_error_is_retried(monkeypatch):
    client, session = client_with([503, 200], monkeypatch)
    assert client._get("https://www.sec.gov/x").status_code == 200
    assert session.calls == 2


def test_retries_are_bounded(monkeypatch):
    client, session = client_with([429, 429, 429, 429], monkeypatch)
    with pytest.raises(requests.HTTPError):
        client._get("https://www.sec.gov/x")
    assert session.calls == 4


def test_permanent_error_is_not_retried(monkeypatch):
    """A 404 means the document is not there; asking four times will not help."""
    client, session = client_with([404], monkeypatch)
    with pytest.raises(requests.HTTPError):
        client._get("https://www.sec.gov/x")
    assert session.calls == 1


# --- document cache ---------------------------------------------------------
def test_cached_document_is_not_refetched(monkeypatch):
    client, session = client_with([200], monkeypatch)
    target = filing()
    target.cache_path.parent.mkdir(parents=True, exist_ok=True)
    target.cache_path.write_bytes(b"<html>already here</html>")

    assert client.fetch_document(target) == target.cache_path
    assert session.calls == 0


def test_unknown_ticker_names_the_exclusion_rule(monkeypatch):
    client, _ = client_with([], monkeypatch)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (config.CACHE_DIR / "company_tickers.json").write_text(
        json.dumps({"0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"}})
    )
    with pytest.raises(LookupError, match="does not file with the SEC"):
        client.resolve_ticker("005930.KS")
