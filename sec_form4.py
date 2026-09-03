"""Keyless Form 4 reader, straight from SEC EDGAR (the free record).

Ticker -> CIK from the SEC's company_tickers.json (cached a week), each
company's recent filings from the submissions API, and every Form 4's XML
parsed for its transactions. Used for the insider tape and the ticker page
when there is no feed key. The SEC asks for a contact address in the
User-Agent and no more than about ten requests a second; this stays well
under that. Scope: the names on the book and the watchlists, because a
whole-market scan from EDGAR would be thousands of filings a day.
"""
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
os.makedirs(CACHE, exist_ok=True)
UA = {"User-Agent": "ResearchDesk research " + os.getenv("EDGAR_CONTACT", "your-email@example.com"),
      "Accept-Encoding": "gzip, deflate"}
WINDOW_DAYS = 45
MIN_BUY_USD = 25_000
_map = {"ts": 0.0, "data": {}}
_tx_cache = {}          # symbol -> (ts, rows)
_lock = threading.Lock()


def _get(url, **kw):
    time.sleep(0.12)
    r = requests.get(url, headers=UA, timeout=25, **kw)
    if r.status_code != 200:
        return None
    return r


def cik_map():
    """{TICKER: (cik10, name)} from the SEC's own list, cached seven days on disk."""
    path = os.path.join(CACHE, "company_tickers.json")
    now = time.time()
    if _map["data"] and now - _map["ts"] < 7 * 86400:
        return _map["data"]
    data = None
    if os.path.exists(path) and now - os.path.getmtime(path) < 7 * 86400:
        with open(path) as fh:
            data = json.load(fh)
    if not data:
        r = _get("https://www.sec.gov/files/company_tickers.json")
        if not r:
            return _map["data"]
        data = r.json()
        with open(path, "w") as fh:
            json.dump(data, fh)
    out = {}
    for row in data.values():
        out[str(row.get("ticker", "")).upper()] = (str(row.get("cik_str", "")).zfill(10), row.get("title", ""))
    _map.update(ts=now, data=out)
    return out


def _text(node, path):
    el = node.find(path)
    return (el.text or "").strip() if el is not None and el.text else ""


def _val(node, path):
    """Form 4 wraps most figures as <tag><value>x</value></tag>."""
    return _text(node, path + "/value") or _text(node, path)


def _parse_form4(xml_text, url):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    sym = _text(root, "issuer/issuerTradingSymbol").upper()
    owners = []
    for o in root.findall("reportingOwner"):
        rel = o.find("reportingOwnerRelationship")
        role = "Insider"
        if rel is not None:
            title = _text(rel, "officerTitle")
            if title:
                role = title
            elif _text(rel, "isDirector") in ("1", "true"):
                role = "Director"
            elif _text(rel, "isTenPercentOwner") in ("1", "true"):
                role = "10% owner"
        owners.append((_text(o, "reportingOwnerId/rptOwnerName"),
                       _text(o, "reportingOwnerId/rptOwnerCik"), role))
    who, cik, role = owners[0] if owners else ("", "", "Insider")
    rows = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _val(t, "transactionCoding/transactionCode")
        try:
            qty = float(_val(t, "transactionAmounts/transactionShares") or 0)
            price = float(_val(t, "transactionAmounts/transactionPricePerShare") or 0)
        except ValueError:
            continue
        ad = _val(t, "transactionAmounts/transactionAcquiredDisposedCode")
        rows.append({"symbol": sym, "who": who.title(), "cik": cik, "role": role,
                     "code": code, "ad": ad, "date": _val(t, "transactionDate"),
                     "qty": qty, "price": round(price, 2), "value": round(qty * price),
                     "url": url})
    return rows


def transactions(symbol, days=90, max_filings=20):
    """Every non-derivative transaction on recent Form 4s for one ticker."""
    with _lock:
        hit = _tx_cache.get(symbol)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
    cik, _ = cik_map().get(symbol.upper(), (None, None))
    if not cik:
        return []
    r = _get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if not r:
        return []
    rec = (r.json().get("filings") or {}).get("recent") or {}
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = []
    n = 0
    for form, acc, fdate, doc in zip(rec.get("form", []), rec.get("accessionNumber", []),
                                     rec.get("filingDate", []), rec.get("primaryDocument", [])):
        if form != "4" or fdate < cutoff:
            continue
        n += 1
        if n > max_filings:
            break
        folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
        xml_name = re.sub(r"^xsl[^/]*/", "", doc or "")        # the styled view -> the raw XML
        page = _get(folder + xml_name) if xml_name.endswith(".xml") else None
        if not page:
            idx = _get(folder)
            m = re.search(r'href="([^"]+/[^"/]+\.xml)"', idx.text if idx else "")
            page = _get("https://www.sec.gov" + m.group(1)) if m else None
        if not page:
            continue
        for row in _parse_form4(page.text, folder):
            row["filed"] = fdate
            rows.append(row)
    rows.sort(key=lambda x: (x.get("filed") or "", x.get("date") or ""), reverse=True)
    with _lock:
        _tx_cache[symbol] = (time.time(), rows)
    return rows


def for_ticker(symbol, limit=12):
    """Rows in the shape the ticker page's insider table reads."""
    out = []
    for r in transactions(symbol):
        if r["code"] not in ("P", "S"):
            continue
        out.append({"transactionDate": r["date"], "reportingName": r["who"],
                    "acquisitionOrDisposition": r["ad"] or ("A" if r["code"] == "P" else "D"),
                    "securitiesTransacted": r["qty"], "price": r["price"],
                    "transactionType": "P-Purchase" if r["code"] == "P" else "S-Sale",
                    "typeOfOwner": r["role"], "link": r["url"]})
        if len(out) >= limit:
            break
    return out


def build(our_symbols, window_days=WINDOW_DAYS, min_buy=MIN_BUY_USD):
    """Open-market buys and cluster buys across the book and watchlist names,
    in the same shape the feed-based insider tape returns."""
    cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")
    buys = []
    for sym in sorted(set(our_symbols)):
        for r in transactions(sym, days=window_days + 10):
            if r["code"] != "P" or r["ad"] not in ("A", "") or r["price"] <= 0:
                continue
            if r["value"] < min_buy or (r.get("filed") or "") < cutoff:
                continue
            buys.append({"symbol": sym, "who": r["who"], "cik": r["cik"], "role": r["role"],
                         "date": r["date"] or r["filed"], "filed": r["filed"],
                         "qty": r["qty"], "price": r["price"], "value": r["value"], "url": r["url"]})
    by_symbol = {}
    for b in buys:
        by_symbol.setdefault(b["symbol"], []).append(b)
    clusters = []
    for sym, rows in by_symbol.items():
        people = {}
        for b in rows:
            p = people.setdefault(b["cik"] or b["who"], {"who": b["who"], "role": b["role"],
                                                          "value": 0, "qty": 0, "last": ""})
            p["value"] += b["value"]
            p["qty"] += b["qty"]
            p["last"] = max(p["last"], b["date"])
        if len(people) < 2:
            continue
        clusters.append({"symbol": sym, "buyers": sorted(people.values(), key=lambda p: -p["value"]),
                         "n_buyers": len(people), "total_value": sum(p["value"] for p in people.values()),
                         "first": min(b["date"] for b in rows), "last": max(b["date"] for b in rows),
                         "ours": True})
    clusters.sort(key=lambda c: (-c["n_buyers"], -c["total_value"]))
    return {"clusters": clusters[:60],
            "our_buys": sorted(buys, key=lambda b: b["filed"], reverse=True)[:40],
            "window_days": window_days, "min_buy_usd": min_buy,
            "fetched_rows": len(buys), "purchase_rows": len(buys),
            "source": "edgar",
            "note": "EDGAR direct: book and watchlist names only; a whole-market cluster scan needs the feed key",
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
