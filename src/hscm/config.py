"""Project-wide configuration.

Deliberately holds tickers, not CIKs. CIKs are resolved at runtime from SEC's
company_tickers.json, which is the canonical spine described in Decision 4.
Hardcoding CIKs here would mean carrying numbers nobody verified.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Seed set (Decision 1): the demand side of the AI buildout. -------------
SEED_TICKERS: tuple[str, ...] = (
    "MSFT",   # Microsoft
    "GOOGL",  # Alphabet
    "AMZN",   # Amazon
    "META",   # Meta Platforms
    "ORCL",   # Oracle
    "CRWV",   # CoreWeave
)

# Forms we ingest (Decision 2).
FORM_TYPES: tuple[str, ...] = ("10-K", "20-F", "10-Q", "8-K")

# --- Paths ------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
FILINGS_DIR = CACHE_DIR / "filings"
MANIFEST_PATH = CACHE_DIR / "manifest.json"

# --- EDGAR access -----------------------------------------------------------
# SEC's fair-access policy requires a descriptive User-Agent that identifies
# the requester and a real contact address, and caps traffic at 10 req/s.
# We run at half that. Override the contact via HSCM_EDGAR_CONTACT.
EDGAR_CONTACT = os.environ.get("HSCM_EDGAR_CONTACT", "logan.j.lee2007@gmail.com")
USER_AGENT = f"2026HyperscalerSupplyChainMaps/0.1 ({EDGAR_CONTACT})"
EDGAR_REQUESTS_PER_SECOND = 5.0

SEC_WWW = "https://www.sec.gov"
SEC_DATA = "https://data.sec.gov"
COMPANY_TICKERS_URL = f"{SEC_WWW}/files/company_tickers.json"
