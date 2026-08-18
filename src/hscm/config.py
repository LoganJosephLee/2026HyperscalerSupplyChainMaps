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

# --- Supply side ------------------------------------------------------------
# Filings we read, NOT relationships we assert. Nothing here becomes an edge
# unless a sentence in some filing states it and survives verification.
#
# Why these companies: naming obligations fall on suppliers, not buyers. A
# registrant must disclose customers accounting for more than 10% of revenue,
# so a chipmaker names its large cloud customers while the cloud provider's own
# 10-K says only "a limited number of suppliers". Microsoft's entire risk
# factors section yielded one relationship; the disclosure we want is on the
# other side of the trade.
SUPPLIER_TICKERS: tuple[str, ...] = (
    "NVDA",   # NVIDIA
    "AVGO",   # Broadcom
    "AMD",    # Advanced Micro Devices
    "MU",     # Micron
    "MRVL",   # Marvell
    "SMCI",   # Super Micro Computer
    "DELL",   # Dell Technologies
    "ANET",   # Arista Networks
    "VRT",    # Vertiv
    "ETN",    # Eaton
    "CLS",    # Celestica
    "CRDO",   # Credo
    "COHR",   # Coherent
    "TSM",    # TSMC — files a 20-F, not a 10-K
)

# Everything we fetch by default.
WATCHLIST: tuple[str, ...] = SEED_TICKERS + SUPPLIER_TICKERS

# Foreign private issuers file a 20-F rather than a 10-K. Asking EDGAR for a
# 10-K from one of these returns nothing at all, which looks like a fetch
# failure rather than the filing-convention difference it actually is.
DEFAULT_FORM_BY_TICKER: dict[str, str] = {
    "TSM": "20-F",
}

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

# --- extraction (Decision 3) ------------------------------------------------
# Which Extractor implementation runs. Nothing outside hscm.extract is aware of
# which one is active. Flip to "anthropic" once ANTHROPIC_API_KEY is set.
EXTRACTOR = os.environ.get("HSCM_EXTRACTOR", "fixture")
_fixture_override = os.environ.get("HSCM_FIXTURE")
FIXTURE_PATH = (
    Path(_fixture_override) if _fixture_override else DATA_DIR / "fixtures" / "extractions.json"
)

# --- entity resolution (Decision 4) -----------------------------------------
ALIASES_PATH = REPO_ROOT / "aliases.yaml"
REVIEW_QUEUE_PATH = DATA_DIR / "review" / "entity_review_queue.csv"
# Below this score a fuzzy match is not accepted automatically; it goes to the
# review queue for a human. Deliberately high — a wrong merge is invisible once
# it is in the graph, while a queue row is merely tedious.
MATCH_ACCEPT_THRESHOLD = 0.93

# --- outputs ----------------------------------------------------------------
SITE_DIR = REPO_ROOT / "site"
EXPORT_DIR = SITE_DIR / "data"

SEC_WWW = "https://www.sec.gov"
SEC_DATA = "https://data.sec.gov"
COMPANY_TICKERS_URL = f"{SEC_WWW}/files/company_tickers.json"
