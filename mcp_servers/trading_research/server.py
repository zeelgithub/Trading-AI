"""Trading-research MCP server.

Every tool fetches raw data, then passes it to Haiku internally
(see haiku.summarize_to_json) to compress it into a small structured JSON
summary -- the calling agent never sees raw HTML, dataset dumps, or article
text.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from . import cache, haiku
from .sources import capitol_trades, news, sec_filings

mcp = FastMCP("trading-research")

_SUMMARY_TTL = 900  # 15 minutes


@mcp.tool()
def get_capitol_trades(ticker: str, days: int = 30) -> dict:
    """Most significant recent congressional trades in a ticker.

    Returns {"trades": [...]}, at most 5 entries, each:
    {politician, party, action, amount_range, date, significance_score}.
    """
    cache_key = f"summary::capitol_trades::{ticker.upper()}::{days}"
    cached = cache.get(cache_key, _SUMMARY_TTL)
    if cached is not None:
        return cached

    records = capitol_trades.fetch_recent_trades(ticker, days)
    if not records:
        result = {"trades": []}
    else:
        system = (
            "You compress congressional stock-trading disclosures into JSON. "
            "Given a JSON list of disclosure records for one ticker, return ONLY "
            'a JSON object {"trades": [...]} with at most 5 entries, most '
            "significant first. Each entry: "
            '{"politician": str, "party": "D"|"R"|"I"|"unknown", '
            '"action": "buy"|"sell"|"exchange", "amount_range": str, '
            '"date": "YYYY-MM-DD" (transaction date), '
            '"significance_score": number 0-1 (weigh amount size and recency)}. '
            "If party is not derivable from the data, use \"unknown\". "
            "Output raw JSON only, no markdown, no commentary."
        )
        result = haiku.summarize_to_json(system, json.dumps(records), max_tokens=200)

    cache.set(cache_key, result)
    return result


@mcp.tool()
def get_news_sentiment(ticker: str) -> dict:
    """Sentiment snapshot for a ticker from recent news.

    Returns {sentiment, confidence, top_3_headlines, key_risk, key_catalyst}.
    """
    cache_key = f"summary::news_sentiment::{ticker.upper()}"
    cached = cache.get(cache_key, _SUMMARY_TTL)
    if cached is not None:
        return cached

    articles = news.fetch_recent_news(ticker)
    if not articles:
        result = {
            "sentiment": "neutral",
            "confidence": 0.0,
            "top_3_headlines": [],
            "key_risk": "no recent news found",
            "key_catalyst": "",
        }
    else:
        system = (
            "You compress recent news into a trading sentiment snapshot. Given a "
            "JSON list of recent news articles (headline, summary, date, source) "
            'for one ticker, return ONLY a JSON object: {"sentiment": '
            '"bullish"|"bearish"|"neutral", "confidence": number 0-1, '
            '"top_3_headlines": [str, str, str], "key_risk": str (one '
            'sentence), "key_catalyst": str (one sentence)}. Output raw JSON '
            "only, no markdown, no commentary."
        )
        result = haiku.summarize_to_json(system, json.dumps(articles), max_tokens=200)

    cache.set(cache_key, result)
    return result


@mcp.tool()
def get_sec_filings(ticker: str) -> dict:
    """Insider-trading trend and most notable recent SEC filing for a ticker.

    Returns {insider_trend, notable_filing, date}.
    """
    cache_key = f"summary::sec_filings::{ticker.upper()}"
    cached = cache.get(cache_key, _SUMMARY_TTL)
    if cached is not None:
        return cached

    raw = sec_filings.fetch_filing_summary_inputs(ticker)
    if "error" in raw:
        result = {"insider_trend": "unknown", "notable_filing": raw["error"], "date": ""}
    else:
        system = (
            "You compress SEC EDGAR filing data into a short summary. Given JSON "
            "with a computed insider-trading trend and a list of recent notable "
            'filings (form, date), return ONLY a JSON object: {"insider_trend": '
            '"buying"|"selling"|"neutral", "notable_filing": str (form type and '
            'one-line description), "date": "YYYY-MM-DD"}. Use the given '
            "insider_trend_computed value unless clearly contradicted. Output raw "
            "JSON only, no markdown, no commentary."
        )
        result = haiku.summarize_to_json(system, json.dumps(raw), max_tokens=200)

    cache.set(cache_key, result)
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
