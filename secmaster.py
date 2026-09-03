"""ICICI security master: maps ICICI short codes to real names/symbols.

Downloads ICICI's published SecurityMaster.zip (cached 7 days), parses the NSE
and BSE scrip masters, and exposes lookup(code) -> metadata. This is what makes
the India ticker page work for EVERY stock at once: one mapping, not per-name
wiring. The NSE master also carries 52-week and lifetime high/low with dates —
free range context, no API call.
"""

import csv
import io
import os
import time
import zipfile

import requests

URL = "https://directlink.icicidirect.com/NewSecurityMaster/SecurityMaster.zip"
CACHE = os.path.join(os.path.dirname(__file__), "security_master.zip")
MAX_AGE = 7 * 86400

_map = None


def _num(v):
    try:
        f = float(str(v).replace(",", ""))
        return f if f != 0 else None
    except (ValueError, TypeError):
        return None


def _ensure_zip():
    if os.path.exists(CACHE) and (time.time() - os.path.getmtime(CACHE)) < MAX_AGE:
        return
    r = requests.get(URL, timeout=60)
    r.raise_for_status()
    with open(CACHE, "wb") as fh:
        fh.write(r.content)


def _parse(zf, member, exch):
    out = {}
    with zf.open(member) as fh:
        rdr = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
        hdr = [h.strip().strip('"') for h in next(rdr)]
        idx = {h: i for i, h in enumerate(hdr)}

        def g(row, col):
            i = idx.get(col)
            return row[i].strip().strip('"') if (i is not None and i < len(row)) else ""

        for row in rdr:
            code = g(row, "ShortName")
            series = g(row, "Series")
            # keep tradeable equity series only; skip rights/partly-paid/debt
            if not code or series in ("DR", "E1", "RE", "BL"):
                continue
            entry = {
                "exch": exch,
                "company": g(row, "CompanyName"),
                "nse_symbol": g(row, "ExchangeCode"),
                "isin": g(row, "ISINCode"),
                "w52h": _num(g(row, "52WeeksHigh")),
                "w52l": _num(g(row, "52WeeksLow")),
                "lth": _num(g(row, "LifeTimeHigh")),
                "ltl": _num(g(row, "LifeTimeLow")),
                "high_date": g(row, "HighDate"),
                "low_date": g(row, "LowDate"),
            }
            # NSE wins over BSE when a code lists on both (richer row there)
            if code not in out or exch == "NSE":
                out[code] = entry
    return out


def load():
    """Return {ICICI code -> metadata}, building the cache on first use."""
    global _map  # noqa: PLW0603
    if _map is not None:
        return _map
    _ensure_zip()
    with zipfile.ZipFile(CACHE) as zf:
        m = _parse(zf, "BSEScripMaster.txt", "BSE")
        m.update(_parse(zf, "NSEScripMaster.txt", "NSE"))
    _map = m
    return _map


def lookup(code):
    return load().get((code or "").upper())
