"""SEC EDGAR filings + Form 4 insider-transaction data for a ticker.

Uses SEC's free public APIs (no key required, but SEC requires a descriptive
User-Agent on every request -- see secrets.load_sec_user_agent).
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from typing import Any

import requests

from .. import cache
from ..secrets import load_sec_user_agent

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_TICKER_MAP_TTL = 24 * 3600
_SUBMISSIONS_TTL = 6 * 3600

NOTABLE_FORMS = {"8-K", "10-K", "10-Q", "SC 13D", "SC 13G", "13D", "13G", "4/A"}


def _headers() -> dict[str, str]:
    return {"User-Agent": load_sec_user_agent()}


def _ticker_to_cik(ticker: str) -> str | None:
    cached = cache.get("sec::ticker_map", _TICKER_MAP_TTL)
    if cached is None:
        resp = requests.get(TICKER_MAP_URL, headers=_headers(), timeout=30)
        resp.raise_for_status()
        cached = resp.json()
        cache.set("sec::ticker_map", cached)

    ticker = ticker.upper()
    for entry in cached.values():
        if entry.get("ticker") == ticker:
            return str(entry["cik_str"]).zfill(10)
    return None


def _fetch_submissions(cik: str) -> dict[str, Any]:
    cache_key = f"sec::submissions::{cik}"
    cached = cache.get(cache_key, _SUBMISSIONS_TTL)
    if cached is not None:
        return cached
    resp = requests.get(
        f"https://data.sec.gov/submissions/CIK{cik}.json", headers=_headers(), timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    cache.set(cache_key, data)
    return data


def _recent_filings(submissions: dict[str, Any], days: int = 120) -> list[dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    cutoff = dt.date.today() - dt.timedelta(days=days)

    out = []
    for form, date_str, accession, doc in zip(forms, dates, accessions, docs):
        try:
            filed = dt.date.fromisoformat(date_str)
        except ValueError:
            continue
        if filed < cutoff:
            continue
        out.append({"form": form, "date": date_str, "accession": accession, "doc": doc})
    return out


def _form4_net_direction(cik: str, filings: list[dict[str, Any]], max_filings: int = 5) -> str:
    form4 = [f for f in filings if f["form"] in ("4", "4/A")][:max_filings]
    buys = 0.0
    sells = 0.0
    cik_int = str(int(cik))

    for f in form4:
        accession_nodash = f["accession"].replace("-", "")
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
            f"{accession_nodash}/{f['doc']}"
        )
        try:
            resp = requests.get(url, headers=_headers(), timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError):
            continue

        for tx in root.iter("nonDerivativeTransaction"):
            code_el = tx.find("transactionAmounts/transactionAcquiredDisposedCode/value")
            shares_el = tx.find("transactionAmounts/transactionShares/value")
            if code_el is None or shares_el is None:
                continue
            try:
                shares = float(shares_el.text)
            except (TypeError, ValueError):
                continue
            if code_el.text == "A":
                buys += shares
            elif code_el.text == "D":
                sells += shares

    if buys == 0 and sells == 0:
        return "neutral"
    if buys > sells * 1.2:
        return "buying"
    if sells > buys * 1.2:
        return "selling"
    return "neutral"


def fetch_filing_summary_inputs(ticker: str) -> dict[str, Any]:
    """Raw filing data + a programmatically-computed insider trend -- this is
    what gets handed to Haiku to compress into the final summary.
    """
    cik = _ticker_to_cik(ticker)
    if cik is None:
        return {"error": f"No CIK found for ticker {ticker.upper()}"}

    submissions = _fetch_submissions(cik)
    filings = _recent_filings(submissions)
    insider_trend = _form4_net_direction(cik, filings)
    notable = [f for f in filings if f["form"] in NOTABLE_FORMS]

    return {
        "ticker": ticker.upper(),
        "insider_trend_computed": insider_trend,
        "recent_notable_filings": notable[:10],
        "recent_form4_count": len([f for f in filings if f["form"] in ("4", "4/A")]),
    }
