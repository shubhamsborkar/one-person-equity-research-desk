"""Keyless fallbacks for the US pages, from Yahoo Finance's public endpoints.

Used whenever FMP_API_KEY is empty, and as the backstop when the feed fails.
Two endpoints: the chart API (quote + candles, no login of any kind) and the
quoteSummary API (profile, estimates, earnings dates), which needs a session
cookie and a "crumb" that Yahoo hands out freely but rate-limits. Everything
here returns an empty shape instead of raising, so a page degrades instead of
dying. Yahoo's endpoints are unofficial and can change without notice.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"}
_Y = {"session": None, "crumb": None, "next_try": 0.0}
_lock = threading.Lock()
_throttle = {"until": 0.0}      # chart/search endpoints: five minutes off after a 429


def _get(path, params):
    """GET against Yahoo with a 429 backoff and the second host as a retry."""
    if time.time() < _throttle["until"]:
        return None
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com{path}", params=params, headers=UA, timeout=15)
        except Exception:  # noqa: BLE001
            continue
        if r.status_code == 200:
            return r
        if r.status_code == 429:
            continue
    _throttle["until"] = time.time() + 300
    return None


def _num(x):
    if isinstance(x, dict):            # quoteSummary wraps values as {raw, fmt}
        x = x.get("raw")
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None       # drop NaN


def _auth():
    """Session cookie + crumb for quoteSummary. Backs off ten minutes when
    Yahoo throttles, and the callers then return empty shapes."""
    with _lock:
        if _Y["session"] and _Y["crumb"]:
            return True
        if time.time() < _Y["next_try"]:
            return False
        try:
            s = requests.Session()
            s.headers.update(UA)
            s.get("https://fc.yahoo.com", timeout=10)
            crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text
            if crumb and "<" not in crumb and len(crumb) < 40:
                _Y.update(session=s, crumb=crumb)
                return True
        except Exception:  # noqa: BLE001
            pass
        _Y.update(session=None, crumb=None, next_try=time.time() + 600)
        return False


def chart(symbol, rng="1y", interval="1d"):
    """-> (meta, rows). rows ascending, {date, price, o, h, l, v}. Keyless."""
    r = _get(f"/v8/finance/chart/{symbol}", {"range": rng, "interval": interval})
    try:
        res = (r.json().get("chart", {}).get("result") or [None])[0] if r else None
    except Exception:  # noqa: BLE001
        return {}, []
    if not res:
        return {}, []
    meta = res.get("meta") or {}
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    off = meta.get("gmtoffset") or 0
    rows = []
    for i, t in enumerate(ts):
        c = _num((q.get("close") or [None] * len(ts))[i])
        if c is None:
            continue
        d = datetime.fromtimestamp(t + off, tz=timezone.utc)
        rows.append({"date": d.strftime("%Y-%m-%d" if interval.endswith("d") or interval.endswith("wk") or interval.endswith("mo") else "%Y-%m-%d %H:%M"),
                     "price": c,
                     "o": _num((q.get("open") or [None] * len(ts))[i]),
                     "h": _num((q.get("high") or [None] * len(ts))[i]),
                     "l": _num((q.get("low") or [None] * len(ts))[i]),
                     "v": _num((q.get("volume") or [None] * len(ts))[i])})
    return meta, rows


def summary(symbol, modules):
    """quoteSummary modules for one symbol -> {module: {...}} or {}."""
    if not _auth():
        return {}
    try:
        r = _Y["session"].get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
            params={"modules": ",".join(modules), "crumb": _Y["crumb"]}, timeout=15)
        if r.status_code in (401, 403, 429):
            with _lock:
                _Y.update(session=None, crumb=None, next_try=time.time() + 600)
            return {}
        res = (r.json().get("quoteSummary", {}).get("result") or [None])[0]
        return res or {}
    except Exception:  # noqa: BLE001
        return {}


def quote(symbol):
    """One quote in the watch-grid schema (the shape the desk prices books with)."""
    meta, rows = chart(symbol, "5d", "1d")
    price = _num(meta.get("regularMarketPrice"))
    if price is None or price <= 0:
        return None
    prev = _num(meta.get("chartPreviousClose")) or _num(meta.get("previousClose"))
    last = rows[-1] if rows else {}
    return {
        "code": symbol, "exch": meta.get("exchangeName") or "US",
        "name": meta.get("longName") or meta.get("shortName"),
        "ltp": price, "prev": prev,
        "day_pct": ((price - prev) / prev * 100) if prev else None,
        "chg": (price - prev) if prev else None,
        "open": last.get("o"), "high": _num(meta.get("regularMarketDayHigh")) or last.get("h"),
        "low": _num(meta.get("regularMarketDayLow")) or last.get("l"),
        "ttq": _num(meta.get("regularMarketVolume")) or last.get("v"),
        "yhigh": _num(meta.get("fiftyTwoWeekHigh")), "ylow": _num(meta.get("fiftyTwoWeekLow")),
        "vs50": None, "vs200": None, "mcap": None,
        "ts": datetime.now().strftime("%H:%M:%S"),
    }


def ticker(symbol):
    """The ticker page's research view, keyless. Quote and candles from the
    chart API (always); profile, ratios, targets, analyst counts and the
    earnings record from quoteSummary when the crumb is available."""
    meta, hist = chart(symbol, "max", "1d")
    price = _num(meta.get("regularMarketPrice"))
    if price is None or price <= 0:
        return {"symbol": symbol, "error": f"no quote for {symbol} right now (Yahoo symbol needed, e.g. BRK-B; Yahoo also rate-limits bursts, so retry in a few minutes)"}
    _, intra = chart(symbol, "5d", "5m")
    prev = _num(meta.get("chartPreviousClose")) or _num(meta.get("previousClose"))
    s = summary(symbol, ["summaryProfile", "summaryDetail", "defaultKeyStatistics",
                         "financialData", "recommendationTrend", "earningsHistory",
                         "calendarEvents"])
    prof, det, ks, fin = (s.get("summaryProfile") or {}, s.get("summaryDetail") or {},
                          s.get("defaultKeyStatistics") or {}, s.get("financialData") or {})
    trend = ((s.get("recommendationTrend") or {}).get("trend") or [{}])[0]
    quote_ = {
        "symbol": symbol, "price": price, "previousClose": prev,
        "change": (price - prev) if prev else None,
        "changePercentage": ((price - prev) / prev * 100) if prev else None,
        "open": (hist[-1].get("o") if hist else None),
        "dayHigh": _num(meta.get("regularMarketDayHigh")), "dayLow": _num(meta.get("regularMarketDayLow")),
        "yearHigh": _num(meta.get("fiftyTwoWeekHigh")), "yearLow": _num(meta.get("fiftyTwoWeekLow")),
        "volume": _num(meta.get("regularMarketVolume")), "avgVolume": _num(det.get("averageVolume")),
        "marketCap": _num(det.get("marketCap")), "exchange": meta.get("exchangeName"),
        "name": meta.get("longName") or meta.get("shortName"),
    }
    profile = {
        "companyName": meta.get("longName") or meta.get("shortName"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "sector": prof.get("sector"), "industry": prof.get("industry"),
        "description": prof.get("longBusinessSummary"), "ceo": None,
        "fullTimeEmployees": prof.get("fullTimeEmployees"),
        "beta": _num(det.get("beta")), "lastDividend": _num(det.get("dividendRate")),
        "averageVolume": _num(det.get("averageVolume")),
    }
    mcap = _num(det.get("marketCap"))
    fcf = _num(fin.get("freeCashflow"))
    ratios = {
        "priceToEarningsRatioTTM": _num(det.get("trailingPE")),
        "priceToSalesRatioTTM": _num(det.get("priceToSalesTrailing12Months")),
        "priceToBookRatioTTM": _num(ks.get("priceToBook")),
        "priceToFreeCashFlowRatioTTM": (mcap / fcf) if (mcap and fcf and fcf > 0) else None,
        "priceToEarningsGrowthRatioTTM": _num(ks.get("pegRatio")),
        "dividendYieldTTM": _num(det.get("dividendYield")),
        "netProfitMarginTTM": _num(fin.get("profitMargins")), "grossProfitMarginTTM": _num(fin.get("grossMargins")),
        "operatingProfitMarginTTM": _num(fin.get("operatingMargins")),
        "debtToEquityRatioTTM": (_num(fin.get("debtToEquity")) / 100.0) if _num(fin.get("debtToEquity")) is not None else None,
        "currentRatioTTM": _num(fin.get("currentRatio")),
    }
    metrics = {
        "returnOnEquityTTM": _num(fin.get("returnOnEquity")), "returnOnAssetsTTM": _num(fin.get("returnOnAssets")),
        "returnOnInvestedCapitalTTM": None, "netDebtToEBITDATTM": None,
        "freeCashFlowYieldTTM": (fcf / mcap) if (mcap and fcf is not None) else None,
        "stockBasedCompensationToRevenueTTM": None, "researchAndDevelopementToRevenueTTM": None,
        "evToEBITDATTM": _num(ks.get("enterpriseToEbitda")), "marketCap": mcap,
        "enterpriseValueTTM": _num(ks.get("enterpriseValue")),
    }
    pt = {"lastQuarterAvgPriceTarget": _num(fin.get("targetMeanPrice")),
          "lastQuarterAvgPriceTargetHigh": _num(fin.get("targetHighPrice")),
          "lastQuarterAvgPriceTargetLow": _num(fin.get("targetLowPrice"))}
    grades = {"strongBuy": trend.get("strongBuy") or 0, "buy": trend.get("buy") or 0,
              "hold": trend.get("hold") or 0, "sell": trend.get("sell") or 0,
              "strongSell": trend.get("strongSell") or 0,
              "consensus": (fin.get("recommendationKey") or "").replace("_", " ").title()}
    earnings = []
    for h in ((s.get("earningsHistory") or {}).get("history") or []):
        d = h.get("quarter", {}).get("fmt") if isinstance(h.get("quarter"), dict) else h.get("quarter")
        earnings.append({"date": d, "epsActual": _num(h.get("epsActual")),
                         "epsEstimated": _num(h.get("epsEstimate")), "revenueActual": None})
    nxt = next_earnings_date(s)
    if nxt:
        earnings.append({"date": nxt, "epsActual": None, "epsEstimated": None, "revenueActual": None})
    earnings.sort(key=lambda e: e.get("date") or "", reverse=True)
    hist_desc = list(reversed(hist))
    days = sorted({p["date"][:10] for p in intra})
    return {
        "symbol": symbol, "quote": quote_, "profile": profile, "ratios": ratios,
        "metrics": metrics, "history": hist_desc,
        "intraday": {"1D": [p for p in intra if p["date"][:10] in days[-1:]],
                     "5D": [p for p in intra if p["date"][:10] in days[-5:]]},
        "news": [], "pt": pt, "grades": grades, "earnings": earnings,
        "insiders": [], "dividends": [],
        "source": "yahoo", "ts": datetime.now().strftime("%H:%M:%S"),
    }


def next_earnings_date(s):
    """'YYYY-MM-DD' of the next earnings date from a quoteSummary result, or None."""
    ev = (s.get("calendarEvents") or {}).get("earnings") or {}
    dates = ev.get("earningsDate") or []
    out = []
    for d in dates:
        raw = d.get("raw") if isinstance(d, dict) else d
        try:
            out.append(datetime.fromtimestamp(float(raw), tz=timezone.utc).strftime("%Y-%m-%d"))
        except (TypeError, ValueError):
            continue
    today = datetime.now().strftime("%Y-%m-%d")
    fut = [d for d in out if d >= today]
    return min(fut) if fut else None


def earnings(tag):
    """{symbol: 'held'|'watch'} -> the countdown rows the US desk renders."""
    def _one(sym):
        s = summary(sym, ["calendarEvents"])
        d = next_earnings_date(s) if s else None
        if not d:
            return None
        ev = (s.get("calendarEvents") or {}).get("earnings") or {}
        return {"symbol": sym, "date": d, "tag": tag[sym],
                "eps_est": _num(ev.get("earningsAverage")),
                "rev_est": _num(ev.get("revenueAverage"))}
    out = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(_one, list(tag)):
            if r:
                out.append(r)
    out.sort(key=lambda r: r["date"])
    return out


def search(q, limit=8):
    """Symbol search, keyless: [{code, name, exch}]."""
    r = _get("/v1/finance/search", {"q": q, "quotesCount": limit, "newsCount": 0})
    try:
        rows = (r.json().get("quotes") or []) if r else []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for x in rows:
        if x.get("symbol") and x.get("quoteType") in (None, "EQUITY", "ETF", "INDEX", "MUTUALFUND"):
            out.append({"code": x["symbol"], "name": x.get("shortname") or x.get("longname") or "",
                        "exch": x.get("exchDisp") or x.get("exchange") or ""})
    return out[:limit]
