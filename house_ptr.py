"""Keyless House of Representatives trading disclosures (periodic transaction
reports, PTRs) from the Clerk's public site.

The Clerk publishes a zip with an index of every financial disclosure filed
this year; each PTR is a PDF. This module downloads the index (cached half a
day), fetches the recent PTR PDFs once each (cached on disk), reads their
text with pypdf and parses the transaction rows. Scanned PDFs with no text
layer are counted, not hidden. The Senate's site sits behind a form that a
script cannot click, so without a feed key Capitol is House-only, and the
page says so.
"""
import io
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "ptrs")
os.makedirs(CACHE, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"}
LOOKBACK_DAYS = 45
MAX_PDFS = 150
ROW = re.compile(
    r"(?:(?P<owner>SP|JT|DC)\s+)?(?P<asset>[A-Z][^\n$]{2,110}?)\s*\((?P<sym>[A-Z][A-Z0-9.\-]{0,6})\)"
    r"\s*\[(?P<atype>[A-Za-z]{1,3})\]\s*(?P<type>P|S \(partial\)|S|E)\s*"
    r"(?P<tx>\d{2}/\d{2}/\d{4})\s*(?P<disc>\d{2}/\d{2}/\d{4})\s*"
    r"(?P<amt>\$[\d,]+\s*-\s*\$[\d,]+|Over \$[\d,]+)", re.S)


def _index(year):
    """[{docid, name, last, state, date}] for this year's PTR filings."""
    path = os.path.join(CACHE, f"{year}FD.zip")
    if not os.path.exists(path) or time.time() - os.path.getmtime(path) > 12 * 3600:
        r = requests.get(f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip",
                         headers=UA, timeout=60)
        if r.status_code != 200:
            if not os.path.exists(path):
                return []
        else:
            with open(path, "wb") as fh:
                fh.write(r.content)
    out = []
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read(f"{year}FD.xml"))
    for m in root.findall("Member"):
        if (m.findtext("FilingType") or "") != "P":
            continue
        fd = m.findtext("FilingDate") or ""
        try:
            mm, dd, yy = map(int, fd.split("/"))
            fdate = datetime(yy, mm, dd)
        except ValueError:
            continue
        out.append({"docid": m.findtext("DocID"), "first": m.findtext("First") or "",
                    "last": m.findtext("Last") or "", "state": m.findtext("StateDst") or "",
                    "date": fdate})
    return out


def _pdf_text(year, docid):
    path = os.path.join(CACHE, f"{docid}.pdf")
    if not os.path.exists(path):
        r = requests.get(f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{docid}.pdf",
                         headers=UA, timeout=60)
        if r.status_code != 200:
            return ""
        with open(path, "wb") as fh:
            fh.write(r.content)
        time.sleep(0.2)
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    except Exception:  # noqa: BLE001
        return ""


def _us(d):
    try:
        return datetime.strptime(d, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return d


def build(ours, tracked_members, held):
    """Same shape as the feed-based Capitol build, House only."""
    today = datetime.now()
    year = today.year
    cutoff = today - timedelta(days=LOOKBACK_DAYS)
    ptrs = [p for p in _index(year) if p["date"] >= cutoff]
    if today.month == 1:
        ptrs += [p for p in _index(year - 1) if p["date"] >= cutoff]
    ptrs.sort(key=lambda p: p["date"], reverse=True)
    ptrs = ptrs[:MAX_PDFS]
    flow, no_text = [], 0
    for p in ptrs:
        text = _pdf_text(p["date"].year, p["docid"])
        if len(text.strip()) < 50:
            no_text += 1
            continue
        link = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{p['date'].year}/{p['docid']}.pdf"
        for m in ROW.finditer(text):
            t = m.group("type")
            flow.append({"chamber": "house", "symbol": m.group("sym").upper(),
                         "name": f"{p['first']} {p['last']}".strip(), "district": p["state"],
                         "owner": m.group("owner") or "", "asset": re.sub(r"\s+", " ", m.group("asset")).strip(),
                         "asset_type": m.group("atype"),
                         "type": {"P": "Purchase", "S": "Sale", "S (partial)": "Sale (Partial)",
                                  "E": "Exchange"}.get(t, t),
                         "amount": re.sub(r"\s+", " ", m.group("amt")), "tx": _us(m.group("tx")),
                         "disclosed": p["date"].strftime("%Y-%m-%d"), "link": link})
    flow.sort(key=lambda r: (r["disclosed"], r["tx"]), reverse=True)
    ours_set = set(ours)
    seen, ours_rows = set(), []
    for r in sorted(flow, key=lambda r: r["tx"] or "", reverse=True):
        if r["symbol"] not in ours_set:
            continue
        k = (r["symbol"], r["name"], r["tx"], r["amount"], r["type"])
        if k in seen:
            continue
        seen.add(k)
        ours_rows.append(r)
    members = []
    for mbr in tracked_members:
        last = (mbr.get("name") or "").split(",")[0].split()[-1].lower() if mbr.get("name") else ""
        rows = [r for r in flow if last and r["name"].lower().endswith(last)]
        rows.sort(key=lambda r: r["tx"] or "", reverse=True)
        members.append({"label": mbr.get("label") or mbr.get("name"), "chamber": "house",
                        "count": len(rows), "rows": rows[:100]})
    return {"ours": ours_rows[:80], "flow": flow[:200], "members": members, "djt": [],
            "held": sorted(held), "tracked": len(ours), "source": "house-clerk",
            "note": (f"House PTRs only (the Senate site blocks scripts; a feed key covers both), "
                     f"last {LOOKBACK_DAYS} days, {len(ptrs)} reports read, {no_text} scanned with no text layer"),
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
