"""The watchlist is a list of filings to read, not relationships to assert."""

from __future__ import annotations

from hscm import config


def test_no_ticker_is_fetched_twice():
    """A duplicate costs an EDGAR round trip and doubles that filing's API bill."""
    assert len(set(config.WATCHLIST)) == len(config.WATCHLIST)


def test_every_seed_is_in_the_watchlist():
    assert set(config.SEED_TICKERS) <= set(config.WATCHLIST)


def test_foreign_private_issuers_are_asked_for_the_form_they_file():
    """EDGAR returns nothing for a 10-K request to a 20-F filer, which reads as a
    fetch failure rather than the filing-convention difference it is."""
    for ticker in config.DEFAULT_FORM_BY_TICKER:
        assert ticker in config.WATCHLIST
    assert config.DEFAULT_FORM_BY_TICKER["TSM"] == "20-F"
