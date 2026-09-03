"""US options flow from CBOE's free delayed chains. READ-ONLY.

The desk port of the options-tape method: take a daily chain snapshot to disk,
then read positioning from it — put/call ratios, OI walls, day-over-day OI
builds (fresh positioning), volume-over-OI unusual strikes (positions that
did not exist yesterday), ATM-straddle expected move, 5% put/call IV skew.

Source: cdn.cboe.com/api/global/delayed_quotes/options/<SYM>.json — keyless,
15-min delayed, whole chain with OI/volume/IV/greeks per contract plus spot
and iv30. Option symbol encodes everything: ROOT + YYMMDD + C|P + strike*1000.
OTC names (CNSWF) 404 — skipped honestly. Describe-only: no thesis call.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "cache", "optsnap")
os.makedirs(SNAP_DIR, exist_ok=True)

CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_OPT = re.compile(r"^([A-Z.]+)(\d{6})([CP])(\d{8})$")

WINDOW_DAYS = 90        # expiries considered for positioning metrics
SNAP_KEEP_DAYS = 14     # pruned beyond this


def fetch_chain(sym):
    """Chain trimmed to the metric window. None = no listed options / fetch fail."""
    try:
        r = requests.get(CBOE.format(sym=sym.upper()), headers=UA, timeout=20)
        if r.status_code != 200:
            return None
        d = r.json().get("data") or {}
    except Exception:  # noqa: BLE001
        return None
    spot = d.get("current_price") or d.get("close")
    if not spot:
        return None
    today = datetime.now().date()
    horizon = today + timedelta(days=WINDOW_DAYS)
    contracts = []
    for o in d.get("options") or []:
        m = _OPT.match(o.get("option") or "")
        if not m:
            continue
        try:
            exp = datetime.strptime(m.group(2), "%y%m%d").date()
        except ValueError:
            continue
        if exp < today or exp > horizon:
            continue
        contracts.append({
            "exp": exp.strftime("%Y-%m-%d"), "cp": m.group(3),
            "strike": int(m.group(4)) / 1000,
            "bid": o.get("bid") or 0, "ask": o.get("ask") or 0,
            "iv": o.get("iv") or 0,
            "oi": int(o.get("open_interest") or 0),
            "vol": int(o.get("volume") or 0),
        })
    if not contracts:
        return None
    return {"spot": spot, "day_pct": d.get("price_change_percent"),
            "iv30": d.get("iv30"), "contracts": contracts}


def _key(c):
    return f"{c['exp']}{c['cp']}{c['strike']:g}"


def save_snapshot(sym, chain, day):
    """Persist today's OI per contract — tomorrow's build baseline."""
    try:
        with open(os.path.join(SNAP_DIR, f"{sym}_{day}.json"), "w") as fh:
            json.dump({_key(c): c["oi"] for c in chain["contracts"]}, fh)
    except OSError:
        pass


def load_prev_snapshot(sym, day):
    """Most recent snapshot BEFORE `day` -> (date, {key: oi}), or (None, {})."""
    try:
        mine = sorted(f for f in os.listdir(SNAP_DIR)
                      if f.startswith(f"{sym}_") and f.endswith(".json")
                      and f[len(sym) + 1:-5] < day)
    except OSError:
        return None, {}
    if not mine:
        return None, {}
    f = mine[-1]
    try:
        with open(os.path.join(SNAP_DIR, f)) as fh:
            return f[len(sym) + 1:-5], json.load(fh)
    except (OSError, ValueError):
        return None, {}


def prune_snapshots():
    cutoff = (datetime.now() - timedelta(days=SNAP_KEEP_DAYS)).strftime("%Y-%m-%d")
    try:
        for f in os.listdir(SNAP_DIR):
            if f.endswith(".json") and f[:-5].rsplit("_", 1)[-1] < cutoff:
                os.remove(os.path.join(SNAP_DIR, f))
    except OSError:
        pass


def _mid(c):
    if c["bid"] and c["ask"]:
        return (c["bid"] + c["ask"]) / 2
    return c["bid"] or c["ask"] or 0


def analyze(sym, chain, prev_date, prev_oi):
    spot = chain["spot"]
    cs = chain["contracts"]
    calls = [c for c in cs if c["cp"] == "C"]
    puts = [c for c in cs if c["cp"] == "P"]
    call_oi, put_oi = sum(c["oi"] for c in calls), sum(c["oi"] for c in puts)
    call_vol, put_vol = sum(c["vol"] for c in calls), sum(c["vol"] for c in puts)

    # OI walls: the strikes where positioning is stacked (near-term window)
    def _wall(rows):
        agg = {}
        for c in rows:
            agg[c["strike"]] = agg.get(c["strike"], 0) + c["oi"]
        if not agg:
            return None
        k = max(agg, key=agg.get)
        return {"strike": k, "oi": agg[k]}

    # ATM straddle on the nearest expiry at least 2 days out = the priced move
    expiries = sorted({c["exp"] for c in cs})
    exp_move = None
    today = datetime.now().date()
    for e in expiries:
        dte = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
        if dte < 2:
            continue
        ec = [c for c in calls if c["exp"] == e]
        ep = [c for c in puts if c["exp"] == e]
        if not ec or not ep:
            continue
        k = min((c["strike"] for c in ec), key=lambda s: abs(s - spot))
        cm = next((_mid(c) for c in ec if c["strike"] == k), 0)
        pm = next((_mid(c) for c in ep if c["strike"] == k), 0)
        if cm and pm:
            exp_move = {"exp": e, "dte": dte, "pct": (cm + pm) / spot * 100,
                        "strike": k}
            break

    # 5% skew on the first monthly-ish expiry 20-75 days out
    skew = None
    for e in expiries:
        dte = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
        if dte < 20:
            continue
        pv = [c for c in puts if c["exp"] == e and c["iv"]]
        cv = [c for c in calls if c["exp"] == e and c["iv"]]
        if len(pv) < 3 or len(cv) < 3:
            continue
        p_iv = min(pv, key=lambda c: abs(c["strike"] - spot * 0.95))["iv"]
        c_iv = min(cv, key=lambda c: abs(c["strike"] - spot * 1.05))["iv"]
        skew = {"exp": e, "put_iv": p_iv * 100, "call_iv": c_iv * 100,
                "pts": (p_iv - c_iv) * 100}
        break

    # unusual strikes: today's volume dwarfing existing OI = positions that
    # did not exist yesterday (the tape method's new-position signal).
    # Contracts expiring TODAY are excluded — 0DTE volume is lottery churn
    # that never becomes positioning (NVDA's same-day calls buried everything).
    today_s = today.strftime("%Y-%m-%d")
    unusual = []
    for c in cs:
        if c["exp"] > today_s and c["vol"] >= 200 and c["vol"] >= 2 * max(c["oi"], 1):
            prem = _mid(c) * c["vol"] * 100
            if prem >= 25_000:
                unusual.append({"exp": c["exp"], "cp": c["cp"], "strike": c["strike"],
                                "vol": c["vol"], "oi": c["oi"], "prem": round(prem)})
    unusual.sort(key=lambda u: -u["prem"])

    # day-over-day OI builds vs the previous snapshot
    builds = []
    if prev_oi:
        for c in cs:
            d = c["oi"] - prev_oi.get(_key(c), 0)
            if d >= 250:
                builds.append({"exp": c["exp"], "cp": c["cp"], "strike": c["strike"],
                               "oi": c["oi"], "d_oi": d})
        builds.sort(key=lambda b: -b["d_oi"])

    return {
        "symbol": sym, "spot": spot, "day_pct": chain.get("day_pct"),
        "iv30": chain.get("iv30"),
        "pcr_oi": round(put_oi / call_oi, 2) if call_oi else None,
        "pcr_vol": round(put_vol / call_vol, 2) if call_vol else None,
        "call_oi": call_oi, "put_oi": put_oi,
        "call_vol": call_vol, "put_vol": put_vol,
        "call_wall": _wall(calls), "put_wall": _wall(puts),
        "exp_move": exp_move, "skew": skew,
        "unusual": unusual[:8],
        "builds": builds[:8], "builds_vs": prev_date,
    }


def build(symbols, held):
    """Snapshot + analyze every name; ~1s pacing keeps the CDN happy."""
    day = datetime.now().strftime("%Y-%m-%d")
    prune_snapshots()
    names, skipped = [], []
    for sym in symbols:
        chain = fetch_chain(sym)
        if chain is None:
            skipped.append(sym)
            time.sleep(0.4)
            continue
        prev_date, prev_oi = load_prev_snapshot(sym, day)
        save_snapshot(sym, chain, day)
        m = analyze(sym, chain, prev_date, prev_oi)
        m["held"] = sym in held
        names.append(m)
        time.sleep(1.0)
    names.sort(key=lambda n: (not n["held"], n["symbol"]))
    return {"names": names, "skipped": skipped, "window_days": WINDOW_DAYS,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
