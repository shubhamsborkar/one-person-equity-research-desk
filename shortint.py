"""Short positioning for the US names, from FINRA's free public data. READ-ONLY.

TWO DIFFERENT THINGS, labeled as such everywhere:
- Short INTEREST: the bi-monthly consolidated settlement report (actual open
  short positions, days-to-cover, change vs prior period). The FINRA Query API
  serves this WITHOUT auth — the trick is that sorting/filtering requires the
  partition key pinned: first GET /partitions for the settlement dates, then
  POST with settlementDate in an EQUAL compareFilter + symbolCode in a
  domainFilter (whole universe in ONE call per settlement).
- Short VOLUME ratio: FINRA's daily Reg SHO short-sale volume files
  (cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt) — the share of the
  DAY'S off-exchange volume marked short. It is tape flow, NOT open interest,
  and the page says so.
"""

import json
import os
import time
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SV_DIR = os.path.join(HERE, "cache", "shortvol")
os.makedirs(SV_DIR, exist_ok=True)

API = "https://api.finra.org"
GROUP = "group/otcMarket/name/consolidatedShortInterest"
UA = {"User-Agent": "Mozilla/5.0 (ResearchDesk research)",
      "Accept": "application/json"}

SI_PERIODS = 6          # settlements of history (~3 months)
SV_DAYS = 20            # trading days of daily short-volume files
SV_KEEP_DAYS = 30


def si_partitions():
    """Available settlement dates, newest first."""
    try:
        r = requests.get(f"{API}/partitions/{GROUP}", headers=UA, timeout=20)
        parts = [p["partitions"][0] for p in
                 r.json().get("availablePartitions", []) if p.get("partitions")]
        return sorted(parts, reverse=True)
    except Exception:  # noqa: BLE001
        return []


def si_for(settlement, symbols):
    """One settlement's rows for the whole universe, one POST."""
    body = {"limit": max(len(symbols) * 2, 200),
            "compareFilters": [{"fieldName": "settlementDate",
                                "compareType": "EQUAL",
                                "fieldValue": settlement}],
            "domainFilters": [{"fieldName": "symbolCode",
                               "values": sorted(symbols)}]}
    try:
        r = requests.post(f"{API}/data/{GROUP}", json=body,
                          headers={**UA, "Content-Type": "application/json"},
                          timeout=30)
        if r.status_code != 200:
            return []
        return r.json()
    except Exception:  # noqa: BLE001
        return []


def _sv_path(day):
    return os.path.join(SV_DIR, f"sv_{day}.json")


def daily_short_volume(day, symbols):
    """{SYM: (short, total)} for one day. The raw file is immutable, so the
    parsed slice caches forever; missing file (holiday / not posted yet) is
    cached as empty for the day so we don't re-hit FINRA."""
    try:
        with open(_sv_path(day)) as fh:
            c = json.load(fh)
        if set(symbols) <= set(c.get("syms", [])):
            return {k: tuple(v) for k, v in c["data"].items()}
    except (OSError, ValueError, KeyError):
        pass
    url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day.replace('-', '')}.txt"
    data = {}
    try:
        r = requests.get(url, headers=UA, timeout=25)
        if r.status_code == 200:
            want = set(symbols)
            for ln in r.text.splitlines()[1:]:
                parts = ln.split("|")
                if len(parts) >= 5 and parts[1] in want:
                    try:
                        data[parts[1]] = (float(parts[2]), float(parts[4]))
                    except ValueError:
                        pass
        elif r.status_code not in (403, 404):
            return {}                     # transient failure: don't cache
    except Exception:  # noqa: BLE001
        return {}
    try:
        with open(_sv_path(day), "w") as fh:
            json.dump({"syms": sorted(symbols),
                       "data": {k: list(v) for k, v in data.items()}}, fh)
    except OSError:
        pass
    return data


def _prune_sv():
    cutoff = (datetime.now() - timedelta(days=SV_KEEP_DAYS)).strftime("%Y-%m-%d")
    try:
        for f in os.listdir(SV_DIR):
            if f.startswith("sv_") and f[3:13] < cutoff:
                os.remove(os.path.join(SV_DIR, f))
    except OSError:
        pass


def build(symbols, held, float_lookup=None):
    """Full short view. float_lookup(sym) -> float shares or None (FMP side)."""
    _prune_sv()
    parts = si_partitions()[:SI_PERIODS]
    si_rows = {}                          # settlement -> {sym: row}
    for p in parts:
        rows = si_for(p, symbols)
        si_rows[p] = {r["symbolCode"]: r for r in rows}
        time.sleep(0.3)

    # daily short-volume ratio, last ~SV_DAYS weekdays (skips holidays/unposted)
    days = []
    d = datetime.now().date()
    while len(days) < SV_DAYS + 4:
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    sv_by_day = {}
    for day in days:
        got = daily_short_volume(day, symbols)
        if got:
            sv_by_day[day] = got
        time.sleep(0.2)

    latest = parts[0] if parts else None
    out = []
    for sym in sorted(symbols):
        cur = (si_rows.get(latest) or {}).get(sym) if latest else None
        trend = []
        for p in reversed(parts):
            r = si_rows.get(p, {}).get(sym)
            if r:
                trend.append([p, r.get("currentShortPositionQuantity")])
        flt = float_lookup(sym) if float_lookup else None
        si = cur.get("currentShortPositionQuantity") if cur else None
        svs = []
        for day in sorted(sv_by_day):
            row = sv_by_day[day].get(sym)
            if row and row[1]:
                svs.append([day, round(row[0] / row[1] * 100, 1), row[1]])
        srv_latest = svs[-1][1] if svs else None
        srv_avg = round(sum(s[1] for s in svs) / len(svs), 1) if svs else None
        if cur is None and not svs:
            continue                      # no FINRA presence at all (OTC etc.)
        out.append({
            "symbol": sym, "held": sym in held,
            "si": si,
            "si_prev": cur.get("previousShortPositionQuantity") if cur else None,
            "si_chg_pct": cur.get("changePercent") if cur else None,
            "dtc": cur.get("daysToCoverQuantity") if cur else None,
            "adv": cur.get("averageDailyVolumeQuantity") if cur else None,
            "float": flt,
            "si_pct_float": (si / flt * 100) if (si and flt) else None,
            "si_trend": trend,
            "srv_latest": srv_latest, "srv_avg": srv_avg, "srv_series": svs,
        })
    out.sort(key=lambda r: (not r["held"], -(r["si_pct_float"] or 0)))
    return {"names": out, "settlement": latest,
            "prev_settlement": parts[1] if len(parts) > 1 else None,
            "sv_days": len(sv_by_day),
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
