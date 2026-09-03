"""Insider cluster-buy screener (FMP Form 4 feed, whole market).

The signal: two or more DISTINCT insiders making open-market purchases
(transactionType P-Purchase, real price) in the same name inside a trailing
window. One insider buying can be noise; a cluster is the classic strong
signal. The feed is FMP /stable/insider-trading/latest, ~1,200 Form 4 rows a
day at 1,000 rows a page, so ~30 pages covers a 30-day window.

Also returns every open-market buy on the desk's own names (book + watchlist),
solo buys included — on our names even one real purchase is worth seeing.
"""

import re
import os
import time
from datetime import datetime, timedelta

import requests

WINDOW_DAYS = 30
MIN_BUY_USD = 10_000          # drop token-sized DRIP-style purchases
MAX_PAGES = 32
PAGE_LIMIT = 1000


def _role(type_of_owner):
    """'officer: President and CEO' -> 'CEO' — compact role chip."""
    t = (type_of_owner or "").lower()
    out = []
    if "director" in t:
        out.append("DIR")
    if "10 percent" in t or "10%" in t:
        out.append("10%")
    m = re.search(r"officer:\s*(.+)", t)
    if m:
        title = m.group(1)
        if "chief executive" in title or re.search(r"\bceo\b", title):
            out.append("CEO")
        elif "chief financial" in title or re.search(r"\bcfo\b", title):
            out.append("CFO")
        elif "chief operating" in title or re.search(r"\bcoo\b", title):
            out.append("COO")
        elif "president" in title:
            out.append("PRES")
        else:
            out.append("OFF")
    elif "officer" in t and not m:
        out.append("OFF")
    return "/".join(dict.fromkeys(out)) or "OTHER"


def _fetch_pages(key, cutoff):
    rows = []
    for page in range(MAX_PAGES):
        try:
            r = requests.get(
                "https://financialmodelingprep.com/stable/insider-trading/latest",
                params={"page": page, "limit": PAGE_LIMIT, "apikey": key},
                timeout=25)
            if r.status_code != 200:
                break
            batch = r.json()
        except Exception:  # noqa: BLE001
            break
        if not batch:
            break
        rows.extend(batch)
        oldest = batch[-1].get("filingDate") or ""
        if oldest and oldest < cutoff:
            break
        time.sleep(0.25)
    return rows


def build(our_symbols):
    """-> {clusters: [...], our_buys: [...], window_days, fetched, ts} or None
    when the feed is unreachable (caller must not cache that)."""
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key:
        return None
    cutoff = (datetime.now() - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    raw = _fetch_pages(key, cutoff)
    if not raw:
        return None

    ours = set(our_symbols)
    buys = []
    for r in raw:
        if (r.get("transactionType") != "P-Purchase"
                or r.get("acquisitionOrDisposition") != "A"):
            continue
        price = r.get("price") or 0
        qty = r.get("securitiesTransacted") or 0
        value = price * qty
        if price <= 0 or value < MIN_BUY_USD:
            continue
        fd = r.get("filingDate") or ""
        if fd < cutoff:
            continue
        buys.append({
            "symbol": r.get("symbol"),
            "who": (r.get("reportingName") or "").title(),
            "cik": r.get("reportingCik"),
            "role": _role(r.get("typeOfOwner")),
            "date": r.get("transactionDate") or fd,
            "filed": fd,
            "qty": qty, "price": round(price, 2),
            "value": round(value),
            "url": r.get("url"),
        })

    by_symbol = {}
    for b in buys:
        by_symbol.setdefault(b["symbol"], []).append(b)

    clusters = []
    for sym, rows in by_symbol.items():
        people = {}
        for b in rows:
            p = people.setdefault(b["cik"], {
                "who": b["who"], "role": b["role"], "value": 0,
                "qty": 0, "last": ""})
            p["value"] += b["value"]
            p["qty"] += b["qty"]
            p["last"] = max(p["last"], b["date"])
        if len(people) < 2:
            continue
        total = sum(p["value"] for p in people.values())
        clusters.append({
            "symbol": sym,
            "buyers": sorted(people.values(), key=lambda p: -p["value"]),
            "n_buyers": len(people),
            "total_value": total,
            "first": min(b["date"] for b in rows),
            "last": max(b["date"] for b in rows),
            "ours": sym in ours,
        })
    clusters.sort(key=lambda c: (-c["n_buyers"], -c["total_value"]))

    our_buys = sorted((b for b in buys if b["symbol"] in ours),
                      key=lambda b: b["filed"], reverse=True)
    return {
        "clusters": clusters[:60],
        "our_buys": our_buys[:40],
        "window_days": WINDOW_DAYS,
        "min_buy_usd": MIN_BUY_USD,
        "fetched_rows": len(raw),
        "purchase_rows": len(buys),
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
