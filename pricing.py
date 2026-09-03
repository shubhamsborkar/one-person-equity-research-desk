"""Closing-window reference price (last 30-min VWAP of the most recent
completed session).

House rule: never anchor on a single last-traded/close print. One stale tick on
a thin name poisons every downstream number. Instead, volume-weight the final 30
minutes of the session. Fall back to a simple mean of candle closes when the
window has no traded volume (common on illiquid micro-caps).
"""

import time
from datetime import datetime, timedelta

# India cash market closes 15:30 IST. Last 30 min = the 15:00 -> 15:30 candles.
WINDOW_START = "15:00"
WINDOW_END = "15:30"


def _num(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _pull_candles(breeze, code, exch, days=7):
    now = datetime.now()
    frm = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    try:
        resp = breeze.get_historical_data_v2(
            interval="5minute",
            from_date=frm,
            to_date=to,
            stock_code=code,
            exchange_code=exch,
            product_type="cash",
        )
    except Exception:  # noqa: BLE001
        return []
    return (resp.get("Success") if isinstance(resp, dict) else None) or []


def _window_vwap(candles, session_date):
    """VWAP over the closing window of one session, or None if no candles."""
    win = [
        c
        for c in candles
        if str(c.get("datetime"))[:10] == session_date
        and WINDOW_START <= str(c.get("datetime"))[11:16] < WINDOW_END
    ]
    if not win:
        return None
    total_vol = sum(_num(c.get("volume")) or 0 for c in win)
    if total_vol > 0:
        num = sum((_num(c.get("close")) or 0) * (_num(c.get("volume")) or 0) for c in win)
        return num / total_vol
    closes = [_num(c.get("close")) for c in win if _num(c.get("close")) is not None]
    return (sum(closes) / len(closes)) if closes else None


def reference_price(breeze, code, exch, pause=0.3):
    """Return a dict:
        ref      last completed session's closing-window VWAP (float or None)
        prior    the session before that, same basis (for day move)
        ref_date / prior_date  which sessions those were
    Only sessions that actually have a closing window are considered, so an
    intraday run correctly falls back to yesterday.
    """
    candles = _pull_candles(breeze, code, exch)
    time.sleep(pause)
    if not candles:
        return {"ref": None, "prior": None, "ref_date": None, "prior_date": None}

    dates = sorted({str(c.get("datetime"))[:10] for c in candles})
    priced = []
    for d in dates:
        v = _window_vwap(candles, d)
        if v is not None:
            priced.append((d, v))

    ref_date = ref = prior_date = prior = None
    if priced:
        ref_date, ref = priced[-1]
    if len(priced) >= 2:
        prior_date, prior = priced[-2]
    return {"ref": ref, "prior": prior, "ref_date": ref_date, "prior_date": prior_date}


def _day_volume(candles, session_date):
    return sum(_num(c.get("volume")) or 0 for c in candles
               if str(c.get("datetime"))[:10] == session_date)


def session_metrics(breeze, code, exch, pause=0.3):
    """Per completed session, the closing-window VWAP and that day's volume.

    Returns a list ordered oldest -> newest of dicts {date, vwap, volume}.
    Used by the morning movers view for 1-day / multi-day moves and volume
    spikes, all on the same averaged basis as the holdings dashboard.
    """
    candles = _pull_candles(breeze, code, exch)
    time.sleep(pause)
    if not candles:
        return []
    dates = sorted({str(c.get("datetime"))[:10] for c in candles})
    series = []
    for d in dates:
        vwap = _window_vwap(candles, d)
        if vwap is None:
            continue
        series.append({"date": d, "vwap": vwap, "volume": _day_volume(candles, d)})
    return series
