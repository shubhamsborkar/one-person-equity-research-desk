"""Phase 3 core: read an options chain and describe how the market is
positioned. India analogue of the options-tape disagreement detector.

Single-snapshot, because Breeze returns change-in-OI per strike (chnge_oi), so
fresh positioning is visible without storing history. Describe-only: it reports
what the chain shows, it does not judge the thesis or suggest a trade.
"""

import time
from datetime import datetime, timedelta


def _num(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _candidate_expiries(days=45):
    base = datetime.now()
    out = []
    for i in range(days):
        d = base + timedelta(days=i)
        if d.weekday() in (1, 2, 3):  # Tue/Wed/Thu cover NSE's expiry schedule
            out.append(d.strftime("%Y-%m-%dT06:00:00.000Z"))
    return out


def _one_side(breeze, code, exch, exp, right, pause=0.3, retries=2):
    """Fetch one side of a chain, tolerating Breeze's transient empty bodies."""
    for _ in range(retries):
        try:
            resp = breeze.get_option_chain_quotes(
                stock_code=code, exchange_code=exch, product_type="options",
                expiry_date=exp, right=right, strike_price="",
            )
            rows = (resp.get("Success") if isinstance(resp, dict) else None) or []
            time.sleep(pause)
            if rows:
                return rows
        except Exception:  # noqa: BLE001 - empty/non-JSON body; retry then move on
            time.sleep(pause)
    return []


def find_chain(breeze, code, exch):
    """Return (calls, puts, expiry_human) for the nearest expiry that has both
    sides, or (None, None, None)."""
    for exp in _candidate_expiries():
        calls = _one_side(breeze, code, exch, exp, "call")
        if not calls:
            continue
        puts = _one_side(breeze, code, exch, exp, "put")
        if not puts:
            continue
        human = str(calls[0].get("expiry_date")) or exp
        return calls, puts, human
    return None, None, None


def _sum(rows, key):
    return sum(_num(r.get(key)) or 0 for r in rows)


def _max_strike_by(rows, key):
    best = None
    for r in rows:
        v = _num(r.get(key))
        s = _num(r.get("strike_price"))
        if v is None or s is None:
            continue
        if best is None or v > best[1]:
            best = (s, v)
    return best  # (strike, value) or None


def _nearest(rows, spot):
    """Row whose strike is closest to spot."""
    best = None
    for r in rows:
        s = _num(r.get("strike_price"))
        if s is None:
            continue
        d = abs(s - spot)
        if best is None or d < best[0]:
            best = (d, r)
    return best[1] if best else None


def _strike_row(rows, target):
    """Row with strike nearest to a target price."""
    return _nearest(rows, target)


def analyze(calls, puts):
    spot = _num(calls[0].get("spot_price")) if calls else None
    if spot is None and puts:
        spot = _num(puts[0].get("spot_price"))

    call_oi, put_oi = _sum(calls, "open_interest"), _sum(puts, "open_interest")
    call_doi, put_doi = _sum(calls, "chnge_oi"), _sum(puts, "chnge_oi")

    pcr_oi = (put_oi / call_oi) if call_oi else None
    # Flow PCR: today's fresh OI. Only meaningful when both sides added.
    flow_pcr = (put_doi / call_doi) if call_doi and call_doi > 0 else None

    resistance = _max_strike_by(calls, "open_interest")   # biggest call wall
    support = _max_strike_by(puts, "open_interest")        # biggest put wall
    call_build = _max_strike_by(calls, "chnge_oi")         # where calls were added today
    put_build = _max_strike_by(puts, "chnge_oi")           # where puts were added today

    # Expected move from the ATM straddle.
    exp_move_pct = atm_strike = None
    if spot:
        atm_c, atm_p = _nearest(calls, spot), _nearest(puts, spot)
        if atm_c and atm_p:
            straddle = (_num(atm_c.get("ltp")) or 0) + (_num(atm_p.get("ltp")) or 0)
            atm_strike = _num(atm_c.get("strike_price"))
            exp_move_pct = (straddle / spot * 100) if spot else None

    # Price skew at ~5% OTM: is the downside bid up relative to the upside?
    skew = None
    if spot:
        otm_put = _strike_row(puts, spot * 0.95)
        otm_call = _strike_row(calls, spot * 1.05)
        pp, cp = _num(otm_put.get("ltp")) if otm_put else None, _num(otm_call.get("ltp")) if otm_call else None
        if pp and cp:
            skew = pp / cp

    return {
        "spot": spot,
        "call_oi": call_oi, "put_oi": put_oi, "pcr_oi": pcr_oi,
        "call_doi": call_doi, "put_doi": put_doi, "flow_pcr": flow_pcr,
        "resistance": resistance, "support": support,
        "call_build": call_build, "put_build": put_build,
        "atm_strike": atm_strike, "exp_move_pct": exp_move_pct,
        "skew": skew,
    }


def posture_note(m):
    """Plain-language, describe-only summary. No thesis call, no trade."""
    bits = []
    pcr = m["pcr_oi"]
    if pcr is not None:
        if pcr >= 1.2:
            bits.append(f"Put-heavy chain (PCR {pcr:.2f}): more open puts than calls, typical of support-building or hedged longs.")
        elif pcr <= 0.8:
            bits.append(f"Call-heavy chain (PCR {pcr:.2f}): more open calls than puts, upside bets or capped resistance overhead.")
        else:
            bits.append(f"Balanced chain (PCR {pcr:.2f}).")
    if m["flow_pcr"] is not None:
        fp = m["flow_pcr"]
        side = "puts" if fp > 1.1 else ("calls" if fp < 0.9 else "both sides evenly")
        bits.append(f"Today's fresh OI leaned to {side} (flow PCR {fp:.2f}).")
    if m["support"] and m["resistance"]:
        bits.append(f"Biggest walls: put OI at {m['support'][0]:.0f} (support), call OI at {m['resistance'][0]:.0f} (resistance).")
    if m["exp_move_pct"] is not None:
        bits.append(f"ATM straddle implies about +/-{m['exp_move_pct']:.1f}% into expiry.")
    if m["skew"] is not None:
        if m["skew"] >= 1.3:
            bits.append(f"Downside is bid up (5% put costs {m['skew']:.1f}x the 5% call): the market is paying for protection.")
        elif m["skew"] <= 0.77:
            bits.append(f"Upside is bid up (5% call richer than the 5% put, skew {m['skew']:.2f}).")
    return " ".join(bits)
