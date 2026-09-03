"""Portfolio risk analytics for the desk. READ-ONLY math, no orders.

Method (stated on the page too):
- Window: last ~252 trading days of the book's home benchmark (^NSEI for the
  India accounts, ^GSPC for the US book). Daily simple returns.
- Each position's closes are forward-filled onto the benchmark's date grid, so
  an illiquid BSE name that doesn't print simply contributes a 0% day (which is
  what actually happens to the NAV mark) and a catch-up move when it trades.
- Exposure = equity market value + futures notional at the underlying's price
  (short futures = negative exposure). Weights are exposure / NAV where
  NAV = equity value + cash, so a leveraged book shows gross exposure > 100%.
- Book return series = sum of weight_i * return_i at today's weights (the
  standard current-weights approximation, not a P&L reconstruction).
- Beta = cov(book, bench) / var(bench). Vol = daily stdev * sqrt(252).
  Max drawdown = worst peak-to-trough of the (re-based) series over the window.
"""

import math
from datetime import datetime, timezone

import requests

WINDOW = 252          # ~1 trading year
MIN_OVERLAP = 40      # fewer aligned days than this -> stat reported as null


def yahoo_history(symbol):
    """Two years of daily closes from Yahoo's public chart API, ascending
    [(YYYY-MM-DD, close)]. Keyless; the caller caches."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "2y", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        res = (r.json().get("chart", {}).get("result") or [None])[0]
        if not res:
            return []
        ts = res.get("timestamp") or []
        closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        if d not in seen:
            seen.add(d)
            out.append((d, float(c)))
    return out


# ---- small pure-math helpers (books are small; no numpy needed) --------------
def _mean(xs):
    return sum(xs) / len(xs)


def _std(xs):
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _corr(a, b):
    if len(a) < 2:
        return None
    ma, mb = _mean(a), _mean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if not sa or not sb:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def _beta(rets, bench):
    if len(rets) < 2:
        return None
    mb = _mean(bench)
    var = sum((x - mb) ** 2 for x in bench)
    if not var:
        return None
    mr = _mean(rets)
    cov = sum((x - mr) * (y - mb) for x, y in zip(rets, bench))
    return cov / var


def max_drawdown(prices):
    """Worst peak-to-trough %, over the given (ascending) price path."""
    peak, worst = None, 0.0
    for p in prices:
        if p is None:
            continue
        peak = p if peak is None else max(peak, p)
        if peak:
            worst = min(worst, p / peak - 1)
    return worst * 100 if peak is not None else None


def _ffill(series, grid):
    """Align an ascending [(date, price)] series to the grid dates by carrying
    the last known price forward. None before the series starts."""
    out, i, last = [], 0, None
    for d in grid:
        while i < len(series) and series[i][0] <= d:
            last = series[i][1]
            i += 1
        out.append(last)
    return out


def _rets(prices):
    """Daily simple returns from a forward-filled price row (None-safe)."""
    out = []
    for prev, cur in zip(prices, prices[1:]):
        out.append((cur / prev - 1) if (prev and cur is not None) else None)
    return out


def _pair(a, b):
    """The overlapping (both non-None) points of two aligned return rows."""
    xs, ys = [], []
    for x, y in zip(a, b):
        if x is not None and y is not None:
            xs.append(x)
            ys.append(y)
    return xs, ys


def _stats_vs(rets, bench_rets):
    xs, ys = _pair(rets, bench_rets)
    if len(xs) < MIN_OVERLAP:
        return {"beta": None, "vol": None, "corr": None, "n": len(xs)}
    sd = _std(xs)
    return {"beta": _beta(xs, ys), "vol": sd * math.sqrt(252) * 100 if sd else None,
            "corr": _corr(xs, ys), "n": len(xs)}


def _book_series(positions, nav, grid_len):
    """Current-weights book return row: sum of w_i * r_i per day. A position
    contributes 0 on days it has no return yet (flat/not printing)."""
    row = [0.0] * grid_len
    for p in positions:
        w = p["exposure"] / nav if nav else 0
        for i, r in enumerate(p["_rets"]):
            if r is not None:
                row[i] += w * r
    return row


def _index_path(rets):
    """Compound a return row into a re-based index path (for drawdown)."""
    path, level = [100.0], 100.0
    for r in rets:
        level *= (1 + (r or 0))
        path.append(level)
    return path


def _round(v, nd=2):
    return round(v, nd) if v is not None else None


def build(books, benches):
    """books: [{key,label,currency,bench,nav,cash,positions:[{code,name,kinds,
    exposure,series}],margin,note}]; benches: {symbol: [(date, close)] asc}.
    Returns the full JSON blob the /risk page renders."""
    bench_rets_by_date = {}
    for sym, series in benches.items():
        dates = [d for d, _ in series]
        rets = _rets([p for _, p in series])
        bench_rets_by_date[sym] = dict(zip(dates[1:], rets))

    out_books, cross = [], []
    for bk in books:
        bench = benches.get(bk["bench"]) or []
        grid = [d for d, _ in bench][-(WINDOW + 1):]
        gset = set(grid)
        bench_rets = _rets([p for d, p in bench if d in gset])
        nav, gross = bk["nav"], sum(abs(p["exposure"]) for p in bk["positions"])

        pos_out = []
        for p in bk["positions"]:
            filled = _ffill(p["series"], grid)
            p["_rets"] = _rets(filled)
            st = _stats_vs(p["_rets"], bench_rets)
            pos_out.append({
                "code": p["code"], "name": p.get("name") or "",
                "kinds": p.get("kinds") or [],
                "exposure": _round(p["exposure"], 0),
                "weight": _round(p["exposure"] / nav * 100, 1) if nav else None,
                "beta": _round(st["beta"]), "vol": _round(st["vol"], 1),
                "corr": _round(st["corr"]),
                "mdd": _round(max_drawdown(filled), 1),
                "n": st["n"], "low_sample": st["n"] < 120,
            })

        book_rets = _book_series(bk["positions"], nav, len(grid) - 1)
        st = _stats_vs(book_rets, bench_rets)
        mdd = max_drawdown(_index_path(book_rets))
        # invested sleeve: same series scaled off gross instead of NAV — the
        # character of what's actually deployed, cash drag removed
        inv_rets = ([r * (nav / gross) for r in book_rets] if gross else book_rets)
        sti = _stats_vs(inv_rets, bench_rets)
        inv_mdd = max_drawdown(_index_path(inv_rets))

        # pairwise correlation matrix between positions
        codes = [p["code"] for p in bk["positions"]]
        matrix = []
        for a in bk["positions"]:
            rowv = []
            for b in bk["positions"]:
                xs, ys = _pair(a["_rets"], b["_rets"])
                rowv.append(_round(_corr(xs, ys)) if len(xs) >= MIN_OVERLAP else None)
            matrix.append(rowv)

        beta = st["beta"]
        stress = (beta * -0.05 * nav) if (beta is not None and nav) else None
        cross.append({"label": bk["label"],
                      "rets": dict(zip(grid[1:], book_rets))})

        # sector concentration: NET exposure per sector as a share of NAV
        # (a short future subtracts from its sector, same as it does from NAV)
        agg = {}
        for p in bk["positions"]:
            sec = p.get("sector") or "Unclassified"
            agg[sec] = agg.get(sec, 0.0) + p["exposure"]
        sectors = [{"name": k, "exposure": _round(v, 0),
                    "pct": _round(v / nav * 100, 1) if nav else None}
                   for k, v in sorted(agg.items(), key=lambda kv: -abs(kv[1]))]
        out_books.append({
            "key": bk["key"], "label": bk["label"], "currency": bk["currency"],
            "bench": bk["bench"], "nav": _round(nav, 0), "cash": _round(bk.get("cash"), 0),
            "gross": _round(gross, 0),
            "leverage": _round(gross / nav, 2) if nav else None,
            "beta": _round(st["beta"]), "vol": _round(st["vol"], 1),
            "corr": _round(st["corr"]), "mdd": _round(mdd, 1),
            "inv_beta": _round(sti["beta"]), "inv_vol": _round(sti["vol"], 1),
            "inv_mdd": _round(inv_mdd, 1),
            "stress_5pct": _round(stress, 0),
            "sectors": sectors,
            "cash_pct": _round(bk.get("cash", 0) / nav * 100, 1) if nav else None,
            "margin": bk.get("margin"), "note": bk.get("note"),
            "positions": sorted(pos_out, key=lambda p: -(abs(p["weight"] or 0))),
            "matrix": {"codes": codes, "vals": matrix},
        })

    # cross-book + benchmark correlation matrix, on overlapping calendar dates
    entries = ([{"label": b["label"], "rets": b["rets"]} for b in cross]
               + [{"label": s, "rets": bench_rets_by_date[s]} for s in benches])
    labels = [e["label"] for e in entries]
    xvals = []
    for a in entries:
        rowv = []
        for b in entries:
            common = sorted(set(a["rets"]) & set(b["rets"]))[-WINDOW:]
            xs = [a["rets"][d] for d in common if a["rets"][d] is not None and b["rets"][d] is not None]
            ys = [b["rets"][d] for d in common if a["rets"][d] is not None and b["rets"][d] is not None]
            rowv.append(_round(_corr(xs, ys)) if len(xs) >= MIN_OVERLAP else None)
        xvals.append(rowv)

    return {"books": out_books,
            "cross": {"labels": labels, "vals": xvals},
            "window": f"last ~{WINDOW} trading days, daily closes",
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
