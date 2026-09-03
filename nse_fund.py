"""India fundamentals per name, from NSE's public corporate-filing APIs.

Three feeds, all keyless behind the same cookie warmup the results calendar
uses (homepage visit first, browser UA):
- integrated-filing-results: quarterly results since the SEBI Integrated
  Filing regime (Dec 2024). The old corporates-financial-results feed froze
  exactly there, so this is the only current source. Each filing links a
  rendered "iXBRL_WEB" HTML table; the numbers are parsed from that table
  and normalized to ₹ Cr using the filing's own stated rounding level.
- corporate-share-holdings-master: promoter vs public per quarter.
- corporate-announcements: Reg 30 stream with PDF links (the hunt surface).

Filed results are immutable, so parsed filings cache on disk forever
(keyed by the ixbrl URL). Errors are never cached.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta
from html import unescape

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36",
      "Accept": "application/json"}
_nse = {"session": None, "warmed": 0.0}

# stated rounding level -> multiply parsed value by this to get ₹ Cr
_ROUND_TO_CR = {"lakhs": 0.01, "lacs": 0.01, "crores": 1.0, "crore": 1.0,
                "millions": 0.1, "thousands": 0.0001, "actual": 1e-7,
                "absolute": 1e-7, "billions": 100.0}


def _session():
    now = time.time()
    s = _nse["session"]
    if s is None or now - _nse["warmed"] > 600:
        s = requests.Session()
        s.headers.update(UA)
        try:
            s.get("https://www.nseindia.com", timeout=15)
        except Exception:  # noqa: BLE001
            return None
        _nse.update(session=s, warmed=now)
    return s


def _get(path, raw=False):
    s = _session()
    if s is None:
        return None
    try:
        r = s.get(("https://www.nseindia.com/api/" + path) if not raw else path,
                  timeout=20)
        if r.status_code != 200:
            _nse["session"] = None
            return None
        return r.text if raw else r.json()
    except Exception:  # noqa: BLE001
        _nse["session"] = None
        return None


def _num(txt):
    t = txt.replace(",", "").replace("\xa0", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
        return -v if neg else v
    except ValueError:
        return None


def _table_rows(html):
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", c))).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        cells = [c for c in cells if c]
        if cells:
            out.append(cells)
    return out


def _row_value(rows, label_prefix):
    """First numeric cell of the first row whose label starts with the prefix.
    The label is the first non-index cell (index cells are 1-3 chars)."""
    lp = label_prefix.lower()
    for cells in rows:
        for i, c in enumerate(cells):
            if len(c) > 3 and c.lower().startswith(lp):
                for v in cells[i + 1:]:
                    n = _num(v)
                    if n is not None:
                        return n
                return None
    return None


def _parse_filing(url):
    """One iXBRL_WEB table -> dict of the headline lines, in ₹ Cr."""
    fname = os.path.join(CACHE_DIR, "nsefund_" +
                         re.sub(r"\W", "", url.rsplit("/", 1)[-1])[-60:] + ".json")
    if os.path.exists(fname):
        with open(fname) as fh:
            return json.load(fh)
    html = _get(url, raw=True)
    if not html:
        return None
    rows = _table_rows(html)
    rounding = None
    for cells in rows:
        if cells[0].lower().startswith("level of rounding") and len(cells) > 1:
            rounding = cells[1].strip().lower()
            break
    factor = _ROUND_TO_CR.get(rounding or "lakhs", 0.01)
    raw_rev = _row_value(rows, "Revenue from operations")
    raw_pat = _row_value(rows, "Total profit (loss) for period")
    eps_probe = _row_value(rows, "Basic earnings")
    # Some filings state one rounding level but carry absolute-rupee values
    # (seen live: Suzlon FY25 quarters say "Crores", numbers are rupees).
    # Cross-check the stated factor and fall back to the one that yields a
    # plausible EPS-implied share count / revenue magnitude.
    def _plausible(f):
        rev = raw_rev * f if raw_rev is not None else None
        if rev is not None and not (0.0001 <= abs(rev) <= 3e6):
            return False
        if eps_probe and raw_pat:
            shares = abs(raw_pat * f * 1e7 / eps_probe)
            if not (1e5 <= shares <= 3e11):
                return False
        return rev is not None or raw_pat is not None
    if not _plausible(factor):
        for f in (1e-7, 0.0001, 0.01, 0.1, 1.0):
            if _plausible(f):
                factor, rounding = f, f"{rounding} (corrected)"
                break
    to_cr = lambda v: round(v * factor, 2) if v is not None else None  # noqa: E731
    d = {
        "revenue": to_cr(_row_value(rows, "Revenue from operations")),
        "total_income": to_cr(_row_value(rows, "Total income")),
        "expenses": to_cr(_row_value(rows, "Total expenses")),
        "pbt": to_cr(_row_value(rows, "Total profit before tax")),
        "pat": to_cr(_row_value(rows, "Total profit (loss) for period")),
        "eps": _row_value(rows, "Basic earnings (loss) per share from continuing"),
        "rounding": rounding,
    }
    if d["eps"] is None:
        d["eps"] = _row_value(rows, "Basic earnings")
    if d["pat"] is None and d["revenue"] is None:
        return None                      # unparsed layout — never cache a blank
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(fname, "w") as fh:
        json.dump(d, fh)
    return d


def quarterly_results(nse_symbol, max_q=6):
    """Last quarters from integrated filings, consolidated preferred (falls
    back to standalone when that is all the company files)."""
    blob = _get(f"integrated-filing-results?index=equities&symbol={nse_symbol}")
    filings = (blob or {}).get("data", [])
    flavor = "Consolidated" if any(
        f.get("consolidated") == "Consolidated" for f in filings) else "Standalone"
    by_q = {}
    for f in filings:
        if f.get("consolidated") != flavor or not f.get("ixbrl"):
            continue
        qe = f.get("qe_Date")
        if qe and qe not in by_q:        # list is newest-first per quarter
            by_q[qe] = f
    out = []
    for qe, f in sorted(by_q.items(),
                        key=lambda kv: datetime.strptime(kv[0], "%d-%b-%Y")
                        if "-" in kv[0] else datetime.min, reverse=True)[:max_q]:
        d = _parse_filing(f["ixbrl"])
        if d:
            d.update(quarter_ended=qe, flavor=flavor,
                     filed=(f.get("broadcast_Date") or "")[:11],
                     audited=f.get("audited"), pdf=f.get("pdf_attach"))
            out.append(d)
        time.sleep(0.4)
    return out


def shareholding(nse_symbol, max_n=5):
    rows = _get(f"corporate-share-holdings-master?index=equities&symbol={nse_symbol}") or []
    out = []
    for r in rows[:max_n]:
        pr, pub = r.get("pr_and_prgrp"), r.get("public_val")
        if pr is None and pub is None:
            continue
        out.append({"date": r.get("date"),
                    "promoter": float(pr) if pr not in (None, "") else None,
                    "public": float(pub) if pub not in (None, "") else None})
    return out


def announcements(nse_symbol, days=90, max_n=25):
    to_d = datetime.now()
    fr_d = to_d - timedelta(days=days)
    rows = _get("corporate-announcements?index=equities"
                f"&symbol={nse_symbol}"
                f"&from_date={fr_d.strftime('%d-%m-%Y')}"
                f"&to_date={to_d.strftime('%d-%m-%Y')}") or []
    out = []
    for r in rows[:max_n]:
        out.append({"date": (r.get("an_dt") or "")[:11],
                    "desc": r.get("desc"),
                    "text": (r.get("attchmntText") or "")[:300],
                    "pdf": r.get("attchmntFile")})
    return out


def build(nse_symbol):
    """Everything the India ticker page needs. None only if NSE is unreachable."""
    q = quarterly_results(nse_symbol)
    sh = shareholding(nse_symbol)
    ann = announcements(nse_symbol)
    if not q and not sh and not ann:
        return None
    return {"symbol": nse_symbol, "quarters": q, "shareholding": sh,
            "announcements": ann,
            "built": datetime.now().strftime("%Y-%m-%d %H:%M")}
