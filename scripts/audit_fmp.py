"""Audit which FMP /stable endpoints this API key (Starter tier) can actually
use. Read-only GETs against fixed test symbols; writes a report to
scripts/fmp_audit_results.json and prints a summary table.

    python scripts/audit_fmp.py
"""

import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

KEY = os.getenv("FMP_API_KEY", "").strip()
BASE = "https://financialmodelingprep.com/stable"

# (category, endpoint, params) — params WITHOUT apikey
TESTS = [
    ("search", "search-symbol", {"query": "AAPL"}),
    ("search", "search-name", {"query": "Apple"}),
    ("search", "search-cik", {"cik": "0000320193"}),
    ("search", "search-cusip", {"cusip": "037833100"}),
    ("search", "search-isin", {"isin": "US0378331005"}),
    ("directory", "stock-list", {}),
    ("directory", "etf-list", {}),
    ("directory", "available-exchanges", {}),
    ("directory", "available-sectors", {}),
    ("directory", "available-industries", {}),
    ("company", "profile", {"symbol": "AAPL"}),
    ("company", "company-notes", {"symbol": "AAPL"}),
    ("company", "stock-peers", {"symbol": "AAPL"}),
    ("company", "employee-count", {"symbol": "AAPL"}),
    ("company", "historical-employee-count", {"symbol": "AAPL"}),
    ("company", "market-cap", {"symbol": "AAPL"}),
    ("company", "historical-market-capitalization", {"symbol": "AAPL", "limit": 10}),
    ("company", "shares-float", {"symbol": "AAPL"}),
    ("company", "key-executives", {"symbol": "AAPL"}),
    ("company", "governance-executive-compensation", {"symbol": "AAPL"}),
    ("company", "mergers-acquisitions-latest", {"page": 0, "limit": 5}),
    ("company", "delisted-companies", {"page": 0, "limit": 5}),
    ("quote", "quote", {"symbol": "AAPL"}),
    ("quote", "quote-short", {"symbol": "AAPL"}),
    ("quote", "aftermarket-trade", {"symbol": "AAPL"}),
    ("quote", "aftermarket-quote", {"symbol": "AAPL"}),
    ("quote", "stock-price-change", {"symbol": "AAPL"}),
    ("quote", "batch-quote", {"symbols": "AAPL,MSFT"}),
    ("quote", "batch-quote-short", {"symbols": "AAPL,MSFT"}),
    ("quote", "exchange-market-hours", {"exchange": "NASDAQ"}),
    ("chart", "historical-price-eod/light", {"symbol": "AAPL", "from": "2026-08-01"}),
    ("chart", "historical-price-eod/full", {"symbol": "AAPL", "from": "2026-08-01"}),
    ("chart", "historical-price-eod/dividend-adjusted", {"symbol": "AAPL", "from": "2026-08-01"}),
    ("chart", "historical-chart/1min", {"symbol": "AAPL"}),
    ("chart", "historical-chart/5min", {"symbol": "AAPL"}),
    ("chart", "historical-chart/15min", {"symbol": "AAPL"}),
    ("chart", "historical-chart/1hour", {"symbol": "AAPL"}),
    ("statements", "income-statement", {"symbol": "AAPL", "limit": 2}),
    ("statements", "balance-sheet-statement", {"symbol": "AAPL", "limit": 2}),
    ("statements", "cash-flow-statement", {"symbol": "AAPL", "limit": 2}),
    ("statements", "latest-financial-statements", {"page": 0, "limit": 5}),
    ("statements", "income-statement-growth", {"symbol": "AAPL", "limit": 2}),
    ("statements", "financial-growth", {"symbol": "AAPL", "limit": 2}),
    ("statements", "ratios", {"symbol": "AAPL", "limit": 2}),
    ("statements", "ratios-ttm", {"symbol": "AAPL"}),
    ("statements", "key-metrics", {"symbol": "AAPL", "limit": 2}),
    ("statements", "key-metrics-ttm", {"symbol": "AAPL"}),
    ("statements", "financial-scores", {"symbol": "AAPL"}),
    ("statements", "owner-earnings", {"symbol": "AAPL"}),
    ("statements", "enterprise-values", {"symbol": "AAPL", "limit": 2}),
    ("statements", "revenue-product-segmentation", {"symbol": "AAPL"}),
    ("statements", "revenue-geographic-segmentation", {"symbol": "AAPL"}),
    ("valuation", "discounted-cash-flow", {"symbol": "AAPL"}),
    ("valuation", "levered-discounted-cash-flow", {"symbol": "AAPL"}),
    ("analyst", "analyst-estimates", {"symbol": "AAPL", "period": "annual", "page": 0, "limit": 4}),
    ("analyst", "ratings-snapshot", {"symbol": "AAPL"}),
    ("analyst", "ratings-historical", {"symbol": "AAPL", "limit": 4}),
    ("analyst", "price-target-summary", {"symbol": "AAPL"}),
    ("analyst", "price-target-consensus", {"symbol": "AAPL"}),
    ("analyst", "price-target-news", {"symbol": "AAPL", "limit": 3}),
    ("analyst", "price-target-latest-news", {"page": 0, "limit": 3}),
    ("analyst", "grades", {"symbol": "AAPL"}),
    ("analyst", "grades-historical", {"symbol": "AAPL", "limit": 4}),
    ("analyst", "grades-consensus", {"symbol": "AAPL"}),
    ("analyst", "grades-news", {"symbol": "AAPL", "limit": 3}),
    ("analyst", "grades-latest-news", {"page": 0, "limit": 3}),
    ("calendar", "dividends", {"symbol": "AAPL", "limit": 4}),
    ("calendar", "dividends-calendar", {"from": "2026-09-01", "to": "2026-09-10"}),
    ("calendar", "earnings", {"symbol": "AAPL", "limit": 4}),
    ("calendar", "earnings-calendar", {"from": "2026-09-01", "to": "2026-09-05"}),
    ("calendar", "ipos-calendar", {"from": "2026-08-01", "to": "2026-09-30"}),
    ("calendar", "splits", {"symbol": "AAPL", "limit": 4}),
    ("calendar", "splits-calendar", {"from": "2026-08-01", "to": "2026-09-30"}),
    ("news", "news/stock", {"symbols": "AAPL", "limit": 3}),
    ("news", "news/stock-latest", {"page": 0, "limit": 3}),
    ("news", "news/general-latest", {"page": 0, "limit": 3}),
    ("news", "news/press-releases", {"symbols": "AAPL", "limit": 3}),
    ("news", "news/crypto", {"symbols": "BTCUSD", "limit": 3}),
    ("news", "news/forex", {"symbols": "EURUSD", "limit": 3}),
    ("insider", "insider-trading/search", {"symbol": "AAPL", "limit": 4}),
    ("insider", "insider-trading/latest", {"page": 0, "limit": 4}),
    ("insider", "insider-trading-statistics", {"symbol": "AAPL"}),
    ("insider", "acquisition-of-beneficial-ownership", {"symbol": "AAPL", "limit": 4}),
    ("congress", "senate-latest", {"page": 0, "limit": 4}),
    ("congress", "house-latest", {"page": 0, "limit": 4}),
    ("congress", "senate-trades", {"symbol": "AAPL"}),
    ("congress", "house-trades", {"symbol": "AAPL"}),
    ("institutional", "institutional-ownership/latest", {"page": 0, "limit": 4}),
    ("institutional", "institutional-ownership/symbol-positions-summary", {"symbol": "AAPL", "year": 2026, "quarter": 1}),
    ("market", "biggest-gainers", {}),
    ("market", "biggest-losers", {}),
    ("market", "most-actives", {}),
    ("market", "sector-performance-snapshot", {"date": "2026-08-27"}),
    ("market", "industry-performance-snapshot", {"date": "2026-08-27"}),
    ("market", "historical-sector-performance", {"sector": "Technology", "from": "2026-08-01"}),
    ("market", "sector-pe-snapshot", {"date": "2026-08-27"}),
    ("market", "industry-pe-snapshot", {"date": "2026-08-27"}),
    ("index", "index-list", {}),
    ("index", "sp500-constituent", {}),
    ("index", "nasdaq-constituent", {}),
    ("index", "dowjones-constituent", {}),
    ("commodity", "commodities-list", {}),
    ("commodity", "quote?symbol=GCUSD", {"symbol": "GCUSD"}),
    ("forex", "forex-list", {}),
    ("forex", "quote?symbol=EURUSD", {"symbol": "EURUSD"}),
    ("crypto", "cryptocurrency-list", {}),
    ("crypto", "quote?symbol=BTCUSD", {"symbol": "BTCUSD"}),
    ("economics", "treasury-rates", {}),
    ("economics", "economic-indicators", {"name": "GDP"}),
    ("economics", "economic-calendar", {"from": "2026-08-25", "to": "2026-08-29"}),
    ("economics", "market-risk-premium", {}),
    ("etf", "etf/info", {"symbol": "SPY"}),
    ("etf", "etf/holdings", {"symbol": "SPY"}),
    ("etf", "etf/asset-exposure", {"symbol": "AAPL"}),
    ("etf", "etf/sector-weightings", {"symbol": "SPY"}),
    ("etf", "etf/country-weightings", {"symbol": "SPY"}),
    ("esg", "esg-disclosures", {"symbol": "AAPL"}),
    ("esg", "esg-ratings", {"symbol": "AAPL"}),
    ("sec", "sec-filings-search/symbol", {"symbol": "AAPL", "from": "2026-01-01", "to": "2026-08-28", "page": 0, "limit": 4}),
    ("sec", "sec-filings-8k", {"from": "2026-08-01", "to": "2026-08-28", "page": 0, "limit": 4}),
    ("sec", "sec-profile", {"symbol": "AAPL"}),
    ("transcripts", "earning-call-transcript-latest", {"page": 0, "limit": 3}),
    ("transcripts", "earning-call-transcript", {"symbol": "AAPL", "year": 2026, "quarter": 2}),
    ("cot", "commitment-of-traders-report", {"symbol": "GC"}),
    ("cot", "commitment-of-traders-analysis", {"symbol": "GC"}),
    ("technical", "technical-indicators/sma", {"symbol": "AAPL", "periodLength": 20, "timeframe": "1day"}),
    ("technical", "technical-indicators/rsi", {"symbol": "AAPL", "periodLength": 14, "timeframe": "1day"}),
]


def probe(path, params):
    p = dict(params)
    p["apikey"] = KEY
    url = f"{BASE}/{path.split('?')[0]}"
    try:
        r = requests.get(url, params=p, timeout=15)
    except Exception as exc:  # noqa: BLE001
        return "NETFAIL", str(exc)[:80]
    if r.status_code == 200:
        try:
            j = r.json()
        except ValueError:
            return "BADJSON", r.text[:80]
        if isinstance(j, dict) and "Error Message" in j:
            return "GATED", j["Error Message"][:80]
        if j in ([], {}):
            return "EMPTY", ""
        sample = j[0] if isinstance(j, list) else j
        keys = list(sample.keys())[:8] if isinstance(sample, dict) else []
        return "OK", ",".join(keys)
    if r.status_code in (402, 403):
        return "GATED", f"HTTP {r.status_code}"
    if r.status_code == 429:
        return "RATELIMIT", ""
    return f"HTTP{r.status_code}", r.text[:80]


def main():
    if not KEY:
        sys.exit("no FMP_API_KEY in .env")
    results = []
    for cat, path, params in TESTS:
        status, note = probe(path, params)
        if status == "RATELIMIT":
            time.sleep(20)
            status, note = probe(path, params)
        results.append({"category": cat, "endpoint": path, "status": status, "note": note})
        print(f"{status:8} {cat:13} {path}")
        time.sleep(0.25)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fmp_audit_results.json")
    with open(out, "w") as fh:
        json.dump(results, fh, indent=1)
    ok = sum(1 for r in results if r["status"] == "OK")
    gated = sum(1 for r in results if r["status"] == "GATED")
    print(f"\n{ok} OK · {gated} gated · {len(results)-ok-gated} other · saved {out}")


if __name__ == "__main__":
    main()
