"""13D/13G activist feed from EDGAR full-text search (EFTS). READ-ONLY.

KEY FIND (2026-08-28): the SEC's beneficial-ownership modernization moved
13D/G to structured XML and EDGAR renamed the root form — 'SC 13D'/'SC 13G'
FROZE at Dec 2024 (their newest full-text hit is 2024-12-17); live filings
are root forms 'SCHEDULE 13D' / 'SCHEDULE 13G', amendments included under
the same root. Anything current must query the new names.

EFTS mechanics that make this one endpoint enough:
- forms= takes a comma list ("SCHEDULE 13D,SCHEDULE 13G").
- ciks= matches the entity in EITHER role, so the same query serves
  "filings ON our company" (subject) and "filings BY a fund" (filer) —
  the role is disambiguated at parse time.
- display_names lists the SUBJECT company first WITH its ticker(s);
  filers carry no ticker. That is the parse key.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

EFTS = "https://efts.sec.gov/LATEST/search-index"
UA = {"User-Agent": "ResearchDesk research " + os.getenv("EDGAR_CONTACT", "your-email@example.com")}
HERE = os.path.dirname(os.path.abspath(__file__))
TICKER_MAP_PATH = os.path.join(HERE, "cache", "ticker_cik.json")

_NAME_TICKER = re.compile(r"^(.*?)\s+\(([A-Z][A-Z0-9.\- ,]*)\)\s+\(CIK (\d+)\)\s*$")
_NAME_PLAIN = re.compile(r"^(.*?)\s+\(CIK (\d+)\)\s*$")


def _efts(**params):
    try:
        r = requests.get(EFTS, params=params, headers=UA, timeout=25)
        if r.status_code != 200:
            return []
        return r.json().get("hits", {}).get("hits", [])
    except Exception:  # noqa: BLE001
        return []


def ticker_cik_map():
    """SEC's ticker -> CIK master (10k+ names), disk-cached a week."""
    try:
        with open(TICKER_MAP_PATH) as fh:
            c = json.load(fh)
        if time.time() - c.get("at", 0) < 7 * 86400:
            return c["map"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        rows = requests.get("https://www.sec.gov/files/company_tickers.json",
                            headers=UA, timeout=25).json()
        mp = {v["ticker"].upper(): int(v["cik_str"]) for v in rows.values()}
    except Exception:  # noqa: BLE001
        return {}
    try:
        with open(TICKER_MAP_PATH, "w") as fh:
            json.dump({"at": time.time(), "map": mp}, fh)
    except OSError:
        pass
    return mp


def _parse(hit):
    s = hit.get("_source") or {}
    subject, filers = None, []
    for dn in s.get("display_names") or []:
        m = _NAME_TICKER.match(dn)
        if m and subject is None:
            subject = {"name": m.group(1).strip(),
                       "tickers": [t.strip() for t in m.group(2).split(",") if t.strip()],
                       "cik": int(m.group(3))}
            continue
        m = _NAME_PLAIN.match(dn)
        if m:
            filers.append({"name": m.group(1).strip(), "cik": int(m.group(2))})
    if subject is None and filers:          # subject with no live US ticker
        subject = {**filers[0], "tickers": []}
        filers = filers[1:]
    if subject is None:
        return None
    adsh = (s.get("adsh") or "")
    link = (f"https://www.sec.gov/Archives/edgar/data/{subject['cik']}/{adsh.replace('-', '')}/"
            if adsh else "")
    return {"date": s.get("file_date") or "", "form": s.get("form") or "",
            "root": (s.get("root_forms") or [""])[0],
            "subject": subject, "filers": filers, "link": link, "adsh": adsh}


def _rows(hits):
    out = []
    for h in hits:
        p = _parse(h)
        if p:
            out.append(p)
    return out


def build(our_tags, funds):
    """our_tags: {SYMBOL: 'held'|'watch'}; funds: [{cik, name}, ...].
    Returns {ours, funds, firehose, ts} — every row a parsed 13D/G filing."""
    mp = ticker_cik_map()
    cutoff = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")

    sym_by_cik = {}
    for sym in our_tags:
        cik = mp.get(sym.upper())
        if cik:
            sym_by_cik.setdefault(cik, sym)
    unmapped = sorted(s for s in our_tags if s.upper() not in mp)

    def _on_name(cik):
        hits = _efts(forms="SCHEDULE 13D,SCHEDULE 13G", ciks=f"{cik:010d}")
        out = []
        for p in _rows(hits):
            if p["subject"]["cik"] != cik or p["date"] < cutoff:
                continue          # our company acting as a filer = not "on us"
            sym = sym_by_cik[cik]
            out.append({**p, "symbol": sym, "tag": our_tags.get(sym, "watch")})
        return out

    def _by_fund(f):
        cik = int(f["cik"])
        hits = _efts(forms="SCHEDULE 13D,SCHEDULE 13G", ciks=f"{cik:010d}")
        rows = [p for p in _rows(hits)
                if any(fl["cik"] == cik for fl in p["filers"])]
        return {"name": f["name"], "cik": f["cik"], "rows": rows[:12]}

    def _firehose(page):
        return _rows(_efts(forms="SCHEDULE 13D", **{"from": page * 100}))

    # EDGAR allows 10 req/s; three workers with ~0.5s calls stays well under
    with ThreadPoolExecutor(max_workers=3) as ex:
        ours_f = [ex.submit(_on_name, cik) for cik in sym_by_cik]
        funds_f = [ex.submit(_by_fund, f) for f in funds]
        fire_f = [ex.submit(_firehose, p) for p in (0, 1)]
        ours = [r for f in ours_f for r in f.result()]
        fund_rows = [f.result() for f in funds_f]
        firehose = [r for f in fire_f for r in f.result()]

    seen = set()
    ours = [r for r in sorted(ours, key=lambda r: r["date"], reverse=True)
            if not (r["adsh"] in seen or seen.add(r["adsh"]))][:60]
    fund_rows = [fr for fr in fund_rows if fr["rows"]]
    fund_rows.sort(key=lambda fr: fr["rows"][0]["date"], reverse=True)
    firehose.sort(key=lambda r: r["date"], reverse=True)
    return {"ours": ours, "funds": fund_rows, "firehose": firehose[:100],
            "unmapped": unmapped,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
