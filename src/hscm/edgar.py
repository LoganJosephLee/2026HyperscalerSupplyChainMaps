"""EDGAR client: ticker -> CIK -> filing metadata -> cached primary document.

Everything this module fetches is cached on disk under data/cache/filings and
re-read from there on subsequent runs. Nothing here parses filing content; see
sections.py for that.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from . import config


class RateLimiter:
    """Minimum-spacing limiter. SEC allows 10 req/s; we default to 5."""

    def __init__(self, requests_per_second: float) -> None:
        self._min_interval = 1.0 / requests_per_second
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last = time.monotonic()


@dataclass(frozen=True)
class Filing:
    """One filing, as described by the submissions API."""

    cik: int
    ticker: str
    company_name: str
    form_type: str
    filing_date: str  # YYYY-MM-DD
    report_date: str
    accession: str  # with dashes, e.g. 0000950170-24-087843
    primary_document: str

    @property
    def accession_nodash(self) -> str:
        return self.accession.replace("-", "")

    @property
    def document_url(self) -> str:
        """Direct link to the primary document — what we fetch and parse."""
        return (
            f"{config.SEC_WWW}/Archives/edgar/data/{self.cik}/"
            f"{self.accession_nodash}/{self.primary_document}"
        )

    @property
    def index_url(self) -> str:
        """Human-facing filing index page — the citation target for the site."""
        return (
            f"{config.SEC_WWW}/Archives/edgar/data/{self.cik}/"
            f"{self.accession_nodash}/{self.accession}-index.htm"
        )

    @property
    def cache_path(self) -> Path:
        return (
            config.FILINGS_DIR
            / f"CIK{self.cik:010d}"
            / self.accession_nodash
            / self.primary_document
        )


class EdgarClient:
    def __init__(self, rps: float = config.EDGAR_REQUESTS_PER_SECOND) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self._limiter = RateLimiter(rps)
        self._tickers: dict[str, tuple[int, str]] | None = None

    # --- low level ----------------------------------------------------------
    def _get(self, url: str, attempts: int = 4) -> requests.Response:
        """GET with backoff on throttling and transient server errors.

        SEC returns 403 with a rate-limit notice, or 429, when it decides you
        are asking too fast, and briefly 503s under load. Retrying those is the
        difference between a fetch that completes and one that dies partway
        through the seed set. Other 4xx are permanent and raise immediately.
        """
        delay = 2.0
        for attempt in range(1, attempts + 1):
            self._limiter.wait()
            response = self._session.get(url, timeout=30)
            retryable = response.status_code in (403, 429, 500, 502, 503, 504)
            if not retryable or attempt == attempts:
                response.raise_for_status()
                return response
            wait = float(response.headers.get("Retry-After") or delay)
            time.sleep(wait)
            delay *= 2
        raise AssertionError("unreachable")

    # --- spine --------------------------------------------------------------
    def ticker_map(self) -> dict[str, tuple[int, str]]:
        """TICKER -> (cik, registrant name) from SEC's company_tickers.json."""
        if self._tickers is None:
            cache = config.CACHE_DIR / "company_tickers.json"
            if cache.exists():
                payload = json.loads(cache.read_text(encoding="utf-8"))
            else:
                payload = self._get(config.COMPANY_TICKERS_URL).json()
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self._tickers = {
                row["ticker"].upper(): (int(row["cik_str"]), row["title"])
                for row in payload.values()
            }
        return self._tickers

    def resolve_ticker(self, ticker: str) -> tuple[int, str]:
        try:
            return self.ticker_map()[ticker.upper()]
        except KeyError:
            raise LookupError(
                f"{ticker} is not in SEC's company_tickers.json. Either the ticker "
                f"is wrong or the company does not file with the SEC — in which "
                f"case it is excluded from the dataset, not stubbed."
            ) from None

    # --- filings ------------------------------------------------------------
    def recent_filings(self, ticker: str, form_type: str, limit: int = 1) -> list[Filing]:
        """Most recent `limit` filings of `form_type` for `ticker`, newest first.

        Reads only `filings.recent` from the submissions API. That covers roughly
        the last year or 1000 filings, which is enough for "most recent 10-K" but
        not for history — paging into `filings.files` is deferred until a
        milestone needs it.
        """
        cik, name = self.resolve_ticker(ticker)
        payload = self._get(f"{config.SEC_DATA}/submissions/CIK{cik:010d}.json").json()
        recent = payload["filings"]["recent"]

        out: list[Filing] = []
        for i, form in enumerate(recent["form"]):
            if form != form_type:
                continue
            out.append(
                Filing(
                    cik=cik,
                    ticker=ticker.upper(),
                    company_name=payload.get("name", name),
                    form_type=form,
                    filing_date=recent["filingDate"][i],
                    report_date=recent["reportDate"][i],
                    accession=recent["accessionNumber"][i],
                    primary_document=recent["primaryDocument"][i],
                )
            )
            if len(out) == limit:
                break
        return out

    def fetch_document(self, filing: Filing, refresh: bool = False) -> Path:
        """Download the primary document to the cache, or reuse the cached copy."""
        path = filing.cache_path
        if path.exists() and not refresh:
            return path
        content = self._get(filing.document_url).content
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path


# --- manifest ---------------------------------------------------------------
def write_manifest(filings: list[Filing], merge: bool = True) -> Path:
    """Record what is in the cache so downstream steps never re-derive URLs.

    Merges with the existing manifest by default. Overwriting instead would mean
    `hscm fetch --form 8-K` silently unregisters every 10-K already on disk,
    leaving cached filings that nothing downstream can find.
    """
    config.MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict] = {}
    if merge:
        rows = {row["accession"]: row for row in read_manifest()}

    for f in filings:
        row = asdict(f)
        row["document_url"] = f.document_url
        row["index_url"] = f.index_url
        row["cache_path"] = str(f.cache_path.relative_to(config.REPO_ROOT))
        rows[f.accession] = row

    ordered = sorted(rows.values(), key=lambda r: (r["ticker"], r["filing_date"]))
    config.MANIFEST_PATH.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
    return config.MANIFEST_PATH


def read_manifest() -> list[dict]:
    if not config.MANIFEST_PATH.exists():
        return []
    return json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
