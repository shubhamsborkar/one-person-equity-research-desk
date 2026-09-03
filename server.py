"""Live research-desk dashboard server. READ-ONLY — there is no order code path.

    python server.py
    open http://localhost:8765

Serves web/index.html and two JSON endpoints the page polls:
  /api/snapshot  positions + funds + limits for both accounts (cached ~30s)
  /api/tape      options-tape read for the F&O watchlist (cached ~10min)

Breeze clients are created once at startup (terminal token prompt per account,
same daily flow as everything else) and reused.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import requests

import activist
import insiders
import nse_fund
import options_us
import risk
import shortint
import secmaster
import stream_in
from breeze_session import ACCOUNTS, get_client, get_client_if_cached
import freefeed          # keyless Yahoo fallbacks for the US pages
import sec_form4         # keyless Form 4 from EDGAR
import house_ptr         # keyless House trading disclosures
from collect import (_equity, _funds, _futures, load_last_snapshot_block,
                     refresh_marks)
from fno import analyze, find_chain
from pricing import _pull_candles

PORT = int(os.getenv("DESK_PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")   # the files you edit: watchlists, book, funds, alerts

SNAP_TTL = 30      # seconds between fresh Breeze pulls for positions/funds
TAPE_TTL = 600     # option chains are heavy; refresh every 10 min

# Home-desk extras, edit to your market. SPARK_NAMES: two names whose 5-min
# cash candles draw the sparklines on the ticker strip. TAPE_NAMES: the index
# option chains for the options tape (the shipped adapter reads NSE/NFO codes).
SPARK_NAMES = [("RELIND", "NSE"), ("INFTEC", "NSE")]
TAPE_NAMES = [("NIFTY", "NFO"), ("CNXBAN", "NFO")]

clients = {}
ACCOUNT_LABELS = {}   # account key -> "A/C ··1234" (last four digits, never a name)
# Tokens die at midnight (SEBI). When every Breeze pull comes back empty the UI
# shows a "paste tokens" banner instead of misleading zeros.
breeze_health = {"dead": not clients}
_cache = {"snap": (0.0, None), "tape": (0.0, None),
          "earn": (0.0, None), "earn_in": (0.0, None), "macro": (0.0, None),
          "funds": (0.0, None), "capitol": (0.0, None), "econcal": (0.0, None),
          "burry": (0.0, None), "pulse": (0.0, None), "insiders": (0.0, None),
          "risk": (0.0, None), "act13d": (0.0, None), "flow": (0.0, None),
          "short": (0.0, None)}
_locks = {k: threading.Lock() for k in _cache}
EARN_TTL, MACRO_TTL, FUNDS_TTL, CAPITOL_TTL = 12 * 3600, 6 * 3600, 24 * 3600, 6 * 3600


def _account_label(name, breeze):
    """Display label from the last four digits of the account number."""
    try:
        f = breeze.get_funds().get("Success") or {}
        acct = str(f.get("bank_account") or "")
        if len(acct) >= 4:
            return f"A/C ··{acct[-4:]}"
    except Exception:  # noqa: BLE001
        pass
    return name

# ---- watchlist ---------------------------------------------------------------
WATCHLIST_PATH = os.path.join(DATA_DIR, "watchlist.json")
WATCH = {}                     # code -> latest quote dict
_watch_lock = threading.Lock()


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _first(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, "", "NA"):
            return d[k]
    return None


def load_watchlist():
    with open(WATCHLIST_PATH) as fh:
        return json.load(fh).get("names", [])


def save_watchlist(names):
    with open(WATCHLIST_PATH, "w") as fh:
        json.dump({"_comment": "Edit in the Watchlist page.", "names": names}, fh, indent=2)


def fetch_watch_quote(breeze, code, exch):
    """One cash quote, normalized for the watch grid. None if nothing came back."""
    try:
        r = breeze.get_quotes(stock_code=code, exchange_code=exch, product_type="cash")
    except Exception:  # noqa: BLE001
        return None
    rows = (r.get("Success") if isinstance(r, dict) else None) or []
    if not rows:
        return None
    q = rows[0]
    ltp = _num(_first(q, ["ltp", "last_traded_price"]))
    prev = _num(_first(q, ["previous_close", "prev_close"]))
    if ltp is None or ltp <= 0:
        return None
    # Compute day % ourselves: Breeze's ltp_percent_change loses the sign.
    day = (ltp - prev) / prev * 100 if prev else _num(_first(q, ["ltp_percent_change"]))
    return {
        "code": code, "exch": exch, "ltp": ltp, "prev": prev, "day_pct": day,
        "bid": _num(_first(q, ["best_bid_price"])),
        "bid_qty": _num(_first(q, ["best_bid_quantity"])),
        "offer": _num(_first(q, ["best_offer_price"])),
        "offer_qty": _num(_first(q, ["best_offer_quantity"])),
        "open": _num(_first(q, ["open"])), "high": _num(_first(q, ["high"])),
        "low": _num(_first(q, ["low"])),
        "ttq": _num(_first(q, ["total_quantity_traded", "volume"])),
        "ts": datetime.now().strftime("%H:%M:%S"),
    }


# ---- US watchlist (FMP) ------------------------------------------------------
WATCHLIST_US_PATH = os.path.join(DATA_DIR, "watchlist_us.json")
WATCH_US = {}
_fmp_backoff = {"until": 0.0}


def us_market_open(now=None):
    """US regular session in UTC (13:30-20:00 covers EDT; close enough for a
    status dot — the data itself is whatever FMP serves)."""
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (13 * 60 + 30) <= hm < (20 * 60)


def load_watchlist_us():
    with open(WATCHLIST_US_PATH) as fh:
        return json.load(fh).get("names", [])


def save_watchlist_us(names):
    with open(WATCHLIST_US_PATH, "w") as fh:
        json.dump({"_comment": "Edit in the Watch US page.", "names": names}, fh, indent=2)


def fetch_us_quote(symbol):
    """One FMP quote, normalized to the watch-grid schema. None if nothing."""
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key or time.time() < _fmp_backoff["until"]:
        return None
    try:
        r = requests.get(
            "https://financialmodelingprep.com/stable/quote",
            params={"symbol": symbol, "apikey": key}, timeout=10)
        if r.status_code == 429:          # rate-limited: back off 5 min
            _fmp_backoff["until"] = time.time() + 300
            return None
        rows = r.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(rows, list) or not rows:
        return None
    q = rows[0]
    price = _num(q.get("price"))
    if price is None or price <= 0:
        return None
    d50, d200 = _num(q.get("priceAvg50")), _num(q.get("priceAvg200"))
    return {
        "code": symbol, "exch": q.get("exchange") or "US", "name": q.get("name"),
        "ltp": price, "prev": _num(q.get("previousClose")),
        "day_pct": _num(q.get("changePercentage")), "chg": _num(q.get("change")),
        "open": _num(q.get("open")), "high": _num(q.get("dayHigh")),
        "low": _num(q.get("dayLow")), "ttq": _num(q.get("volume")),
        "yhigh": _num(q.get("yearHigh")), "ylow": _num(q.get("yearLow")),
        "vs50": ((price - d50) / d50 * 100) if d50 else None,
        "vs200": ((price - d200) / d200 * 100) if d200 else None,
        "mcap": _num(q.get("marketCap")),
        "ts": datetime.now().strftime("%H:%M:%S"),
    }


# ---- Yahoo batch quotes (the "live" Watch·US path) ---------------------------
# One request quotes the whole list, so a 2.5s cadence is polite. Needs a
# session cookie + crumb; when Yahoo throttles the crumb, we fall back to a
# parallel FMP sweep (10s full-grid refresh) until the next crumb attempt.
_yahoo = {"session": None, "crumb": None, "next_try": 0.0}
_YUA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"}


def _yahoo_auth():
    if time.time() < _yahoo["next_try"]:
        return False
    try:
        s = requests.Session()
        s.headers.update(_YUA)
        s.get("https://fc.yahoo.com", timeout=10)
        crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                      timeout=10).text.strip()
        if crumb and "Too Many" not in crumb and len(crumb) <= 16:
            _yahoo.update(session=s, crumb=crumb)
            print(f"  Yahoo batch quotes: ON (crumb ok) — Watch·US refreshes every ~2.5s in-session")
            return True
    except Exception:  # noqa: BLE001
        pass
    _yahoo.update(session=None, crumb=None, next_try=time.time() + 600)
    return False


def fetch_us_batch(symbols):
    """All US names in ONE Yahoo v7 call, normalized to the watch schema.
    None = batch path unavailable (caller falls back to FMP)."""
    if not _yahoo["session"] and not _yahoo_auth():
        return None
    try:
        r = _yahoo["session"].get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": ",".join(symbols), "crumb": _yahoo["crumb"]},
            timeout=10)
        if r.status_code != 200:
            _yahoo.update(session=None, crumb=None, next_try=time.time() + 600)
            return None
        rows = r.json().get("quoteResponse", {}).get("result", [])
    except Exception:  # noqa: BLE001
        _yahoo.update(session=None, crumb=None, next_try=time.time() + 300)
        return None
    out = {}
    for q in rows:
        price = _num(q.get("regularMarketPrice"))
        if price is None:
            continue
        d50, d200 = _num(q.get("fiftyDayAverage")), _num(q.get("twoHundredDayAverage"))
        out[q["symbol"]] = {
            "code": q["symbol"], "exch": q.get("fullExchangeName") or "US",
            "name": q.get("shortName") or q.get("longName"),
            "ltp": price, "prev": _num(q.get("regularMarketPreviousClose")),
            "day_pct": _num(q.get("regularMarketChangePercent")),
            "chg": _num(q.get("regularMarketChange")),
            "open": _num(q.get("regularMarketOpen")),
            "high": _num(q.get("regularMarketDayHigh")),
            "low": _num(q.get("regularMarketDayLow")),
            "ttq": _num(q.get("regularMarketVolume")),
            "yhigh": _num(q.get("fiftyTwoWeekHigh")), "ylow": _num(q.get("fiftyTwoWeekLow")),
            "vs50": ((price - d50) / d50 * 100) if d50 else None,
            "vs200": ((price - d200) / d200 * 100) if d200 else None,
            "mcap": _num(q.get("marketCap")),
            "ts": datetime.now().strftime("%H:%M:%S"),
        }
    return out


def fmp_get(path, **params):
    """One FMP stable-API call; None on any failure."""
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key:
        return None
    params["apikey"] = key
    try:
        r = requests.get(f"https://financialmodelingprep.com/stable/{path}",
                         params=params, timeout=12)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


# ---- symbol search (the add-box dropdown: confirm before adding) -------------
def search_symbols(q, region):
    q = (q or "").strip().upper()
    if not q:
        return []
    out = []
    if region == "us":
        rows = fmp_get("search-symbol", query=q, limit=10) or []
        if not rows:
            rows = fmp_get("search-name", query=q, limit=10) or []
        rows.sort(key=lambda r: ("." in (r.get("symbol") or ""), len(r.get("symbol") or "")))
        for r in rows[:8]:
            if r.get("symbol"):
                out.append({"code": r["symbol"], "name": r.get("name") or "",
                            "exch": r.get("exchange") or ""})
        if not out:                         # no feed key (or nothing back): Yahoo search
            out = freefeed.search(q)
    elif region == "global":
        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "quotesCount": 8, "newsCount": 0},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
            for r in resp.get("quotes", [])[:8]:
                if r.get("symbol"):
                    out.append({"code": r["symbol"],
                                "name": r.get("shortname") or r.get("longname") or "",
                                "exch": f"{r.get('quoteType') or ''} · {r.get('exchange') or ''}"})
        except Exception:  # noqa: BLE001
            pass
    else:                               # india: the ICICI security master
        db = secmaster.load()
        matches = []
        for code, meta in db.items():
            co = (meta.get("company") or "").upper()
            nse = (meta.get("nse_symbol") or "").upper()
            if code.startswith(q) or nse.startswith(q):
                matches.append((0, code, meta))
            elif q in co:
                matches.append((1, code, meta))
        matches.sort(key=lambda m: (m[0], m[1]))
        for _, code, meta in matches[:8]:
            nse = meta.get("nse_symbol")
            out.append({"code": code, "name": (meta.get("company") or "").title(),
                        "exch": (meta.get("exch") or "") + (f" · NSE:{nse}" if nse else "")})
    return out


# ---- ticker research page ----------------------------------------------------
TICKER_TTL = 600
_ticker_cache = {}


def _held_context(symbol):
    """Where this name sits across the books: US book position and/or watchlists."""
    ctx = {}
    try:
        with open(os.path.join(DATA_DIR, "us_book.json")) as fh:
            book = json.load(fh)
        for p in book.get("positions", []):
            if p["symbol"] == symbol:
                ctx["us_book"] = p
    except OSError:
        pass
    return ctx


HIST_CACHE_DIR = os.path.join(HERE, "cache")
os.makedirs(HIST_CACHE_DIR, exist_ok=True)


def _daily_history_in(breeze, code, exch, years=8):
    """Chunked Breeze daily OHLCV candles (the API caps ~1000 rows per call),
    disk-cached per day — history changes once a session, so only the first
    click of the day pays the multi-call cost (his 'too slow' feedback).
    When the session is dead (weekend, lapsed token) the stale cache is served
    rather than an empty chart: Friday's history beats a blank screen."""
    today = datetime.now().strftime("%Y-%m-%d")
    cpath = os.path.join(HIST_CACHE_DIR, f"hist_{code}.json")
    stale = None
    try:
        with open(cpath) as fh:
            c = json.load(fh)
        if c.get("data"):
            if c.get("date") == today:
                return c["data"]
            stale = c["data"]
    except (OSError, ValueError):
        pass
    if breeze is None:
        return stale or []

    out, now = [], datetime.now()
    start_year = now.year - years
    for y0 in range(start_year, now.year + 1, 3):
        frm = f"{y0}-01-01T09:00:00.000Z"
        to = min(datetime(y0 + 3, 1, 1), now).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        try:
            r = breeze.get_historical_data_v2(
                interval="1day", from_date=frm, to_date=to,
                stock_code=code, exchange_code=exch, product_type="cash")
            rows = r.get("Success") or []
        except Exception:  # noqa: BLE001
            rows = []
        for c in rows:
            p = _num(c.get("close"))
            if p:
                out.append({"date": str(c.get("datetime"))[:10], "price": p,
                            "o": _num(c.get("open")), "h": _num(c.get("high")),
                            "l": _num(c.get("low")), "v": _num(c.get("volume"))})
        time.sleep(0.15)
    seen, dedup = set(), []
    for row in out:
        if row["date"] not in seen:
            seen.add(row["date"])
            dedup.append(row)
    data = sorted(dedup, key=lambda r: r["date"], reverse=True)
    if data:
        try:
            with open(cpath, "w") as fh:
                json.dump({"date": today, "data": data}, fh)
        except OSError:
            pass
        return data
    return stale or []


def _intraday_in(breeze, code, exch):
    """Intraday series — the Breeze edge no US feed matches on Starter.
    1D = 1-minute candles of the latest session; 5D = 5-minute over five."""
    now = datetime.now()
    out = {}
    for key, interval, back in (("1D", "1minute", 6), ("5D", "5minute", 12)):
        try:
            r = breeze.get_historical_data_v2(
                interval=interval,
                from_date=(now - timedelta(days=back)).strftime("%Y-%m-%dT09:00:00.000Z"),
                to_date=now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                stock_code=code, exchange_code=exch, product_type="cash")
            rows = r.get("Success") or []
        except Exception:  # noqa: BLE001
            rows = []
        pts = [{"date": str(c.get("datetime"))[:16], "price": _num(c.get("close")),
                "o": _num(c.get("open")), "h": _num(c.get("high")),
                "l": _num(c.get("low")), "v": _num(c.get("volume"))}
               for c in rows if _num(c.get("close"))]
        dates = sorted({p["date"][:10] for p in pts})
        keep = dates[-1:] if key == "1D" else dates[-5:]
        out[key] = [p for p in pts if p["date"][:10] in keep]
        time.sleep(0.2)
    return out


def _futures_quote_in(breeze, code, expiry_human):
    """Bid/ask/OI on the actual futures contract (click the contract,
    see its book). Breeze wants an ISO expiry; the position carries 27-Oct-2026."""
    try:
        iso = datetime.strptime(expiry_human, "%d-%b-%Y").strftime("%Y-%m-%dT06:00:00.000Z")
    except (ValueError, TypeError):
        return None
    for exp in (iso, expiry_human):
        try:
            r = breeze.get_quotes(stock_code=code, exchange_code="NFO",
                                  product_type="futures", expiry_date=exp,
                                  right="others", strike_price="0")
            rows = (r.get("Success") if isinstance(r, dict) else None) or []
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            q = rows[0]
            return {
                "expiry": expiry_human,
                "ltp": _num(q.get("ltp")),
                "bid": _num(q.get("best_bid_price")), "bid_qty": _num(q.get("best_bid_quantity")),
                "offer": _num(q.get("best_offer_price")), "offer_qty": _num(q.get("best_offer_quantity")),
                "oi": _num(q.get("open_interest")),
                "ttq": _num(q.get("total_quantity_traded")),
                "prev": _num(q.get("previous_close")),
            }
        time.sleep(0.3)
    return None


def build_ticker_in(code):
    """India research view: Breeze quote + chunked candles + intraday + security
    master meta + account positions (labelled by account number) + futures book.
    Fundamentals/news need a non-FMP source (Starter is US-only) — sections the
    page simply hides."""
    breeze = next(iter(clients.values()), None)
    meta = secmaster.lookup(code) or {}
    exch = meta.get("exch") or "NSE"
    q = None
    for attempt in range(3):          # Breeze drops the odd response; retry
        q = fetch_watch_quote(breeze, code, exch)
        if q:
            break
        if exch == "NSE":
            q = fetch_watch_quote(breeze, code, "BSE")
            if q:
                exch = "BSE"
                break
        time.sleep(0.8)
    if not q:
        return {"symbol": code, "region": "in", "error": f"no Breeze quote for {code} right now — try again"}

    held, fut_expiries = {}, []
    for account, cli in clients.items():
        label = ACCOUNT_LABELS.get(account, account)
        try:
            for e in _equity(cli):
                if e["code"] == code:
                    held[label] = e
            for f in _futures(cli):
                if (f.get("underlying") or "") == code:
                    held[f"{label} · futures"] = f
                    if f.get("expiry") and f["expiry"] not in fut_expiries:
                        fut_expiries.append(f["expiry"])
        except Exception:  # noqa: BLE001
            pass

    futures_quotes = []
    for exp in fut_expiries:
        fq = _futures_quote_in(breeze, code, exp)
        if fq:
            futures_quotes.append(fq)

    return {
        "symbol": code, "region": "in",
        "meta": meta, "quote": q,
        "history": _daily_history_in(breeze, code, exch),
        "intraday": _intraday_in(breeze, code, exch),
        "futures_quotes": futures_quotes,
        "held_in": held,
        "ts": datetime.now().strftime("%H:%M:%S"),
    }


def _trim_ohlcv(rows):
    """Full-EOD rows to the compact OHLCV shape the chart eats. Newest-first."""
    out = []
    for r in rows or []:
        c = _num(r.get("close") if "close" in r else r.get("price"))
        if c is None:
            continue
        out.append({"date": r.get("date"), "price": c,
                    "o": _num(r.get("open")), "h": _num(r.get("high")),
                    "l": _num(r.get("low")), "v": _num(r.get("volume"))})
    return out


def _us_intraday(rows):
    """FMP 5-min bars -> the same {1D, 5D} shape the India pages use; the chart
    frontend then shows the 1D/5D tabs on US names too."""
    pts = []
    for r in rows or []:
        c = _num(r.get("close"))
        if c is None:
            continue
        pts.append({"date": (r.get("date") or "")[:16], "price": c,
                    "o": _num(r.get("open")), "h": _num(r.get("high")),
                    "l": _num(r.get("low")), "v": _num(r.get("volume"))})
    pts.sort(key=lambda p: p["date"])
    days = sorted({p["date"][:10] for p in pts})
    return {"1D": [p for p in pts if p["date"][:10] in days[-1:]],
            "5D": [p for p in pts if p["date"][:10] in days[-5:]]}


def build_ticker(symbol):
    """Aggregate the full research view for one US symbol. The ~12 FMP calls
    run IN PARALLEL (his 'too slow' feedback: was 4-6s sequential, now ~1s),
    cached TICKER_TTL seconds."""
    frm = "2005-01-01"  # MAX range; the chart slices shorter windows client-side
    first = lambda x: (x[0] if isinstance(x, list) and x else {})  # noqa: E731
    jobs = {
        "quote": ("quote", {"symbol": symbol}),
        "profile": ("profile", {"symbol": symbol}),
        "ratios": ("ratios-ttm", {"symbol": symbol}),
        "metrics": ("key-metrics-ttm", {"symbol": symbol}),
        "history": ("historical-price-eod/full", {"symbol": symbol, "from": frm}),
        "intra": ("historical-chart/5min", {"symbol": symbol}),
        "news": ("news/stock", {"symbols": symbol, "limit": 14}),
        "pt": ("price-target-summary", {"symbol": symbol}),
        "grades": ("grades-consensus", {"symbol": symbol}),
        "earnings": ("earnings", {"symbol": symbol, "limit": 6}),
        "insiders": ("insider-trading/search", {"symbol": symbol, "limit": 12}),
        "dividends": ("dividends", {"symbol": symbol, "limit": 4}),
    }
    res = {}
    if os.getenv("FMP_API_KEY", "").strip():
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {k: ex.submit(fmp_get, path, **params) for k, (path, params) in jobs.items()}
            for k, f in futs.items():
                res[k] = f.result()
    quote = first(res.get("quote"))
    if not quote.get("price"):
        # No feed key, or the feed had nothing: the keyless path. Quote and
        # candles from Yahoo, the insider table straight from EDGAR.
        page = freefeed.ticker(symbol)
        if not page.get("error"):
            try:
                page["insiders"] = sec_form4.for_ticker(symbol)
            except Exception:  # noqa: BLE001
                page["insiders"] = []
            page["held"] = _held_context(symbol)
        return page
    return {
        "symbol": symbol,
        "quote": quote,
        "profile": first(res["profile"]),
        "ratios": first(res["ratios"]),
        "metrics": first(res["metrics"]),
        "history": _trim_ohlcv(res["history"]),
        "intraday": _us_intraday(res["intra"]),
        "news": res["news"] or [],
        "pt": first(res["pt"]),
        "grades": first(res["grades"]),
        "earnings": res["earnings"] or [],
        "insiders": res["insiders"] or [],
        "dividends": res["dividends"] or [],
        "held": _held_context(symbol),
        "ts": datetime.now().strftime("%H:%M:%S"),
    }


INFUND_TTL = 12 * 3600
_infund_cache = {}
_infund_lock = threading.Lock()


def cached_infund(code):
    """India fundamentals for the ticker page: NSE integrated-filing results +
    shareholding + Reg 30 stream. Per-symbol, disk-backed, 12h; never caches
    an unreachable-NSE failure."""
    meta = secmaster.lookup(code) or {}
    sym = meta.get("nse_symbol")
    if not sym:
        return {"error": "no NSE listing mapped for this code (BSE-only names have no filing feed here)"}
    now = time.time()
    with _infund_lock:
        hit = _infund_cache.get(code)
    if hit and now - hit[0] < INFUND_TTL:
        return hit[1]
    dpath = os.path.join(HIST_CACHE_DIR, f"api_infund_{code}.json")
    if not hit:
        try:
            with open(dpath) as fh:
                c = json.load(fh)
            if now - c["at"] < INFUND_TTL:
                with _infund_lock:
                    _infund_cache[code] = (c["at"], c["data"])
                return c["data"]
        except (OSError, ValueError, KeyError):
            pass
    data = nse_fund.build(sym)
    if data is None:
        return {"error": "NSE not reachable right now — reload to retry"}
    with _infund_lock:
        _infund_cache[code] = (now, data)
    try:
        with open(dpath, "w") as fh:
            json.dump({"at": now, "data": data}, fh)
    except OSError:
        pass
    return data


def cached_ticker(symbol, region="us"):
    now = time.time()
    key = f"{region}:{symbol}"
    hit = _ticker_cache.get(key)
    if hit and now - hit[0] < TICKER_TTL:
        return hit[1]
    data = build_ticker_in(symbol) if region == "in" else build_ticker(symbol)
    if not data.get("error"):        # never cache a failure; retry next click
        _ticker_cache[key] = (now, data)
    return data


# ---- financials layer (statements / ratios / segments / estimates / peers) --
FIN_TTL = 12 * 3600
_fin_cache = {}
_fin_lock = threading.Lock()


def build_fin(symbol):
    """The fundamentals bundle for one US symbol: all three statements
    (annual + quarterly), annual ratio/metric history, segment mix, forward
    estimates, dividend history, a peer comparison, and the pieces the
    client-side DCF workspace seeds from. All single-symbol Starter calls
    (batch is gated), run in parallel, cached 12h per symbol."""
    jobs = {
        "inc_a": ("income-statement", {"symbol": symbol, "limit": 6}),
        "inc_q": ("income-statement", {"symbol": symbol, "period": "quarter", "limit": 8}),
        "bs_a": ("balance-sheet-statement", {"symbol": symbol, "limit": 6}),
        "bs_q": ("balance-sheet-statement", {"symbol": symbol, "period": "quarter", "limit": 8}),
        "cf_a": ("cash-flow-statement", {"symbol": symbol, "limit": 6}),
        "cf_q": ("cash-flow-statement", {"symbol": symbol, "period": "quarter", "limit": 8}),
        "ratios": ("ratios", {"symbol": symbol, "limit": 6}),
        "metrics": ("key-metrics", {"symbol": symbol, "limit": 6}),
        "est_a": ("analyst-estimates", {"symbol": symbol, "period": "annual", "limit": 10}),
        "seg_p": ("revenue-product-segmentation", {"symbol": symbol}),
        "seg_g": ("revenue-geographic-segmentation", {"symbol": symbol}),
        "peers": ("stock-peers", {"symbol": symbol}),
        "divs": ("dividends", {"symbol": symbol, "limit": 40}),
        "dcf": ("discounted-cash-flow", {"symbol": symbol}),
        "scores": ("financial-scores", {"symbol": symbol}),
    }
    res = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {k: ex.submit(fmp_get, path, **params) for k, (path, params) in jobs.items()}
        for k, f in futs.items():
            try:
                res[k] = f.result()
            except Exception:  # noqa: BLE001
                res[k] = None
    if not (res.get("inc_a") or []):
        return {"symbol": symbol, "error": f"no FMP financials for {symbol}"}

    # peer comparison rows: the name itself first, then up to 8 FMP peers,
    # each priced from quote + TTM ratios (single-symbol calls)
    peer_syms = [symbol] + [p.get("symbol") for p in (res.get("peers") or [])[:8]
                            if p.get("symbol") and p.get("symbol") != symbol]

    def _peer_row(sym2):
        try:
            q = (fmp_get("quote", symbol=sym2) or [{}])[0]
            r = (fmp_get("ratios-ttm", symbol=sym2) or [{}])[0]
        except Exception:  # noqa: BLE001
            return None
        if not q.get("price"):
            return None
        return {"symbol": sym2, "name": q.get("name") or "",
                "mktCap": q.get("marketCap"), "price": q.get("price"),
                "pe": r.get("priceToEarningsRatioTTM"),
                "ps": r.get("priceToSalesRatioTTM"),
                "pb": r.get("priceToBookRatioTTM"),
                "evx": r.get("enterpriseValueMultipleTTM"),
                "gm": r.get("grossProfitMarginTTM"),
                "om": r.get("operatingProfitMarginTTM"),
                "nm": r.get("netProfitMarginTTM"),
                "de": r.get("debtToEquityRatioTTM"),
                "divy": r.get("dividendYieldPercentageTTM")}
    with ThreadPoolExecutor(max_workers=8) as ex:
        peer_rows = [r for r in ex.map(_peer_row, peer_syms) if r]

    first = lambda x: (x[0] if isinstance(x, list) and x else {})  # noqa: E731
    return {"symbol": symbol,
            "inc_a": res.get("inc_a") or [], "inc_q": res.get("inc_q") or [],
            "bs_a": res.get("bs_a") or [], "bs_q": res.get("bs_q") or [],
            "cf_a": res.get("cf_a") or [], "cf_q": res.get("cf_q") or [],
            "ratios": res.get("ratios") or [], "metrics": res.get("metrics") or [],
            "est_a": res.get("est_a") or [],
            "seg_p": res.get("seg_p") or [], "seg_g": res.get("seg_g") or [],
            "divs": res.get("divs") or [], "peers": peer_rows,
            "dcf": first(res.get("dcf")), "scores": first(res.get("scores")),
            "ts": datetime.now().strftime("%H:%M:%S")}


def cached_fin(symbol):
    """Per-symbol, disk-backed 12h cache; never caches a failure."""
    now = time.time()
    with _fin_lock:
        hit = _fin_cache.get(symbol)
    if hit and now - hit[0] < FIN_TTL:
        return hit[1]
    dpath = os.path.join(HIST_CACHE_DIR, f"api_fin_{symbol}.json")
    if not hit:
        try:
            with open(dpath) as fh:
                c = json.load(fh)
            if now - c["at"] < FIN_TTL:
                with _fin_lock:
                    _fin_cache[symbol] = (c["at"], c["data"])
                return c["data"]
        except (OSError, ValueError, KeyError):
            pass
    data = build_fin(symbol)
    if data.get("error"):
        return data
    with _fin_lock:
        _fin_cache[symbol] = (now, data)
    try:
        with open(dpath, "w") as fh:
            json.dump({"at": now, "data": data}, fh)
    except OSError:
        pass
    return data


# ---- global watch (Yahoo, keyless) ------------------------------------------
WATCHLIST_GLOBAL_PATH = os.path.join(DATA_DIR, "watchlist_global.json")
WATCH_GLOBAL = {}


def load_watchlist_global():
    try:
        with open(WATCHLIST_GLOBAL_PATH) as fh:
            return json.load(fh).get("names", [])
    except OSError:
        return []


def save_watchlist_global(names):
    with open(WATCHLIST_GLOBAL_PATH, "w") as fh:
        json.dump({"_comment": "Edit in the Watch Global page. Yahoo symbols "
                   "(TALABAT.AE, 0700.HK, MC.PA ...).", "names": names}, fh, indent=2)


def fetch_yahoo_quote(symbol):
    """Quote from Yahoo's public chart API. Keyless; be a polite guest."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res = r.json().get("chart", {}).get("result")
        if not res:
            return None
        m = res[0].get("meta", {})
        price = _num(m.get("regularMarketPrice"))
        prev = _num(m.get("chartPreviousClose")) or _num(m.get("previousClose"))
        if price is None:
            return None
        return {
            "code": symbol, "exch": m.get("exchangeName") or "",
            "name": m.get("longName") or m.get("shortName"),
            "currency": m.get("currency") or "",
            "ltp": price, "prev": prev,
            "day_pct": ((price - prev) / prev * 100) if prev else None,
            "high": _num(m.get("regularMarketDayHigh")),
            "low": _num(m.get("regularMarketDayLow")),
            "ttq": _num(m.get("regularMarketVolume")),
            "yhigh": _num(m.get("fiftyTwoWeekHigh")),
            "ylow": _num(m.get("fiftyTwoWeekLow")),
            "ts": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception:  # noqa: BLE001
        return None


def global_watch_loop():
    first_cycle = True
    while True:
        for entry in load_watchlist_global():
            q = fetch_yahoo_quote(entry["code"])
            if q:
                with _watch_lock:
                    WATCH_GLOBAL[entry["code"]] = q
            time.sleep(1.5 if first_cycle else 5)   # keyless API: gentle after the fill
        first_cycle = False
        time.sleep(30)


def build_usbook():
    """The US book: positions from data/us_book.json, priced live via FMP."""
    with open(os.path.join(DATA_DIR, "us_book.json")) as fh:
        book = json.load(fh)
    rows = []
    for p in book.get("positions", []):
        q = None
        with _watch_lock:
            q = WATCH_US.get(p["symbol"])
        if not q:
            q = fetch_us_quote(p["symbol"]) or freefeed.quote(p["symbol"])
        ltp = q["ltp"] if q else None
        value = ltp * p["shares"] if ltp is not None else None
        cost = p["avg_cost"] * p["shares"]
        rows.append({
            **p, "ltp": ltp, "value": value,
            "day_pct": q.get("day_pct") if q else None,
            "pnl": (value - cost) if value is not None else None,
            "pnl_pct": ((value - cost) / cost * 100) if (value is not None and cost) else None,
        })
    deployed = sum(r["value"] for r in rows if r["value"] is not None)
    cash = book.get("cash_usd") or 0
    total = deployed + cash
    return {
        "as_of": book.get("as_of"), "cash": cash, "cash_note": book.get("cash_note"),
        "mandate": book.get("mandate_target_usd"), "positions": rows,
        "deployed": deployed, "total": total,
        "total_pnl": sum(r["pnl"] for r in rows if r["pnl"] is not None),
        "market_open": us_market_open(),
        "ts": datetime.now().strftime("%H:%M:%S"),
    }


def us_watch_loop():
    """Live-first US quotes. Preferred path: ONE Yahoo batch call for the whole
    list every 2.5s while the US session is open (the live-terminal feel) and
    every 60s closed. Fallback when Yahoo throttles the crumb: a parallel FMP
    sweep — full grid every ~10s open / 120s closed, inside Starter's rate
    budget. First pass after a start is always brisk."""
    first_cycle = True
    while True:
        names = [n["code"] for n in load_watchlist_us()]
        if not names:
            time.sleep(30)
            continue
        open_ = us_market_open()
        batch = fetch_us_batch(names)
        if batch is not None:
            if batch:
                with _watch_lock:
                    WATCH_US.update(batch)
            first_cycle = False
            time.sleep(2.5 if open_ else 60)
            continue
        # FMP fallback: sweep in parallel, then rest
        with ThreadPoolExecutor(max_workers=8) as ex:
            for code, q in zip(names, ex.map(lambda c: fetch_us_quote(c) or freefeed.quote(c), names)):
                if q:
                    with _watch_lock:
                        WATCH_US[code] = q
        nap = 10 if (open_ or first_cycle) else 120
        first_cycle = False
        time.sleep(nap)


def watch_loop():
    """Cycle the watchlist forever, one quote at a time, latest kept in WATCH.
    Gentle pacing keeps us well inside Breeze's rate limit; slower off-hours."""
    first_cycle = True
    while True:
        breeze = next(iter(clients.values()), None)
        if breeze is None:
            time.sleep(60)     # no Breeze session yet; India watch idles
            continue
        names = load_watchlist()
        any_ok = False
        for entry in names:
            if entry.get("source", "breeze") != "breeze":
                continue  # fmp names arrive in Phase B
            q = fetch_watch_quote(breeze, entry["code"], entry.get("exch", "NSE"))
            if q:
                any_ok = True
                with _watch_lock:
                    WATCH[entry["code"]] = q
            # While the websocket is delivering, this loop is only the fallback
            # + dead-session probe — no need to hammer the REST quote API.
            if stream_in.healthy():
                time.sleep(5.0)
            else:
                time.sleep(0.7 if (market_open() or first_cycle) else 3.0)
        if names:                      # a full silent pass = the session is dead
            breeze_health["dead"] = not any_ok
        # BSE micro-caps often return no usable cash quote (ltp 0 / empty body).
        # For held names the holdings feed carries the broker's own mark — use it
        # rather than leaving a dead row on the grid.
        with _watch_lock:
            missing = [e for e in names
                       if e.get("source", "breeze") == "breeze"
                       and e["code"] not in WATCH]
        if missing and not breeze_health["dead"]:
            marks = {}
            for cl in clients.values():
                try:
                    for row in _equity(cl):
                        if row.get("ltp"):
                            marks[row["code"]] = row
                except Exception:  # noqa: BLE001
                    pass
            for e in missing:
                row = marks.get(e["code"])
                if row:
                    with _watch_lock:
                        WATCH[e["code"]] = {
                            "code": e["code"], "exch": e.get("exch", "NSE"),
                            "ltp": row["ltp"], "prev": None,
                            "day_pct": row.get("day_pct"),
                            "bid": None, "bid_qty": None,
                            "offer": None, "offer_qty": None,
                            "open": None, "high": None, "low": None, "ttq": None,
                            "ts": datetime.now().strftime("%H:%M:%S"),
                        }
        first_cycle = False
        time.sleep(2)


def market_open(now=None):
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= hm <= (15 * 60 + 30)


def build_snapshot():
    accounts = {}
    alive = False
    live_client = None
    for name, breeze in clients.items():
        equity = _equity(breeze)
        futures = _futures(breeze)
        funds = _funds(breeze)
        if equity or futures or funds.get("cash") is not None:
            alive = True
            live_client = live_client or breeze
        eq_value = sum(e["value"] for e in equity if e["value"] is not None)
        eq_pnl = sum(e["pnl"] for e in equity if e["pnl"] is not None)
        fno_mtm = sum(f["mtm"] for f in futures if f["mtm"] is not None)
        limit_total = funds.get("fno_limit_total")
        blocked = funds.get("fno_blocked") or 0
        accounts[name] = {
            "label": ACCOUNT_LABELS.get(name, name),
            "equity": equity,
            "futures": futures,
            "funds": funds,
            "totals": {
                "equity_value": eq_value,
                "equity_pnl": eq_pnl,
                "fno_mtm": fno_mtm,
                "cash": funds.get("cash"),
                "free_limit": funds.get("fno_free"),
                "limit_total": limit_total,
                "blocked": blocked,
                "util_pct": (blocked / limit_total * 100) if limit_total else None,
            },
            "broker_at": time.time(),
        }

    # Accounts with NO live session today (token not pasted): serve the last
    # saved broker book (qty / avg / funds / margin frozen at broker time) with
    # every MARKET mark re-priced through the live session — quotes are market
    # data, so one daily token prices every account's names. Funds and margin
    # stay frozen because those really are account-scoped at the broker.
    if alive:
        for name in ACCOUNTS:
            if name in accounts and (accounts[name].get("equity")
                                     or accounts[name].get("futures")
                                     or (accounts[name].get("funds") or {}).get("cash") is not None):
                continue  # live this pass
            block, as_of = load_last_snapshot_block(name)
            if not block:
                continue
            try:
                refresh_marks(block, live_client)
            except Exception:  # noqa: BLE001
                pass
            funds = block.get("funds") or {}
            equity = block.get("equity", [])
            futures = block.get("futures", [])
            limit_total = funds.get("fno_limit_total")
            blocked = funds.get("fno_blocked") or 0
            block["totals"] = {
                "equity_value": sum(e["value"] for e in equity if e.get("value") is not None),
                "equity_pnl": sum(e["pnl"] for e in equity if e.get("pnl") is not None),
                "fno_mtm": sum(f["mtm"] for f in futures if f.get("mtm") is not None),
                "cash": funds.get("cash"),
                "free_limit": funds.get("fno_free"),
                "limit_total": limit_total,
                "blocked": blocked,
                "util_pct": (blocked / limit_total * 100) if limit_total else None,
            }
            block["label"] = ACCOUNT_LABELS.get(name) or block.get("label") or name
            block["stale_funds"] = True
            block["broker_as_of"] = as_of
            accounts[name] = block

    # Sparkline series off the first client (any client works for market data).
    sparks = {}
    spark_client = next(iter(clients.values()), None)
    if spark_client:
        for code, exch in SPARK_NAMES:
            candles = _pull_candles(spark_client, code, exch, days=3)
            closes = [c.get("close") for c in candles if c.get("close") is not None]
            sparks[code] = closes[-120:]

    breeze_health["dead"] = not alive
    data = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_open": market_open(),
        "session_dead": not alive,
        "accounts": accounts,
        "sparks": sparks,
    }
    if alive:
        try:
            with open(LAST_SNAP_PATH, "w") as fh:
                json.dump({"at": time.time(), "data": data}, fh)
        except OSError:
            pass
        return data
    # Session dead (lapsed token / weekend): never a blank desk. Serve the last
    # good broker book with equity marks refreshed from Yahoo's delayed NSE
    # quotes; futures marks and every margin number stay frozen at broker time.
    return _stale_snapshot() or data


LAST_SNAP_PATH = os.path.join(HIST_CACHE_DIR, "last_snapshot.json")
_ystale = {}          # yahoo symbol -> (fetched_ts, quote)


def _yahoo_in_quote(code):
    """Delayed Yahoo quote for an ICICI code via its NSE symbol; 10-min cached.
    BSE-only names have no Yahoo mapping here — they stay on the frozen mark."""
    meta = secmaster.lookup(code) or {}
    sym = meta.get("nse_symbol")
    if not sym:
        return None
    ysym = f"{sym}.NS"
    hit = _ystale.get(ysym)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    q = fetch_yahoo_quote(ysym)
    if q:
        _ystale[ysym] = (time.time(), q)
    return q


def load_last_snapshot():
    try:
        with open(LAST_SNAP_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _stale_snapshot():
    c = load_last_snapshot()
    if not c or not c.get("data", {}).get("accounts"):
        return None
    data = c["data"]
    broker_as_of = datetime.fromtimestamp(c["at"]).strftime("%a %d %b, %H:%M")
    codes = sorted({e["code"] for a in data["accounts"].values()
                    for e in a.get("equity", []) if e.get("code")})
    with ThreadPoolExecutor(max_workers=6) as ex:
        quotes = dict(zip(codes, ex.map(_yahoo_in_quote, codes)))
    fresh = 0
    for a in data["accounts"].values():
        for e in a.get("equity", []):
            q = quotes.get(e.get("code"))
            if not q or not q.get("ltp"):
                continue
            fresh += 1
            e["ltp"], e["day_pct"] = q["ltp"], q.get("day_pct")
            if e.get("qty") is not None:
                e["value"] = q["ltp"] * e["qty"]
                cost = (e["avg"] * e["qty"]) if e.get("avg") is not None else None
                if cost is not None:
                    e["pnl"] = e["value"] - cost
                    e["pnl_pct"] = (e["pnl"] / cost * 100) if cost else None
        t = a.get("totals") or {}
        eq = [e for e in a.get("equity", [])]
        t["equity_value"] = sum(e["value"] for e in eq if e.get("value") is not None)
        t["equity_pnl"] = sum(e["pnl"] for e in eq if e.get("pnl") is not None)
    data["session_dead"] = True
    data["market_open"] = market_open()
    data["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["stale"] = {"broker_as_of": broker_as_of,
                     "yahoo_marks": fresh, "eq_rows": len(codes)}
    return data


def build_tape():
    breeze = next(iter(clients.values()), None)
    if breeze is None:
        return {"ts": datetime.now().strftime("%H:%M:%S"), "names": []}
    out = []
    for code, exch in TAPE_NAMES:
        calls, puts, expiry = find_chain(breeze, code, exch)
        if not calls:
            out.append({"code": code, "error": "no chain"})
            continue
        m = analyze(calls, puts)
        out.append({
            "code": code,
            "expiry": expiry,
            "spot": m["spot"],
            "pcr": m["pcr_oi"],
            "flow_pcr": m["flow_pcr"],
            "support": m["support"][0] if m["support"] else None,
            "resistance": m["resistance"][0] if m["resistance"] else None,
            "exp_move_pct": m["exp_move_pct"],
            "skew": m["skew"],
        })
    return {"ts": datetime.now().strftime("%H:%M:%S"), "names": out}


# ---- earnings calendar (US, FMP bulk) ---------------------------------------
def build_earnings():
    """Upcoming earnings for every US name we track (book + watchlist), from
    one bulk FMP calendar call. India has no comparable feed yet - said so in UI."""
    ours = set()
    try:
        with open(os.path.join(DATA_DIR, "us_book.json")) as fh:
            for p in json.load(fh).get("positions", []):
                ours.add((p["symbol"], "held"))
    except OSError:
        pass
    for n in load_watchlist_us():
        ours.add((n["code"], "watch"))
    tag = {}
    for sym, t in ours:
        tag[sym] = "held" if (tag.get(sym) == "held" or t == "held") else t
    # per-symbol (the bulk calendar truncates its universe); parallel = fast
    today = datetime.now().strftime("%Y-%m-%d")
    if not os.getenv("FMP_API_KEY", "").strip():
        rows = freefeed.earnings(tag)
        return {"rows": rows, "ts": datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "yahoo",
                "note": "" if rows else "no feed key: earnings dates come from Yahoo, which is rate-limiting right now; retry later"}

    def _next(sym):
        rows = fmp_get("earnings", symbol=sym, limit=6) or []
        fut = [e for e in rows if (e.get("date") or "") >= today]
        if not fut:
            return None
        e = min(fut, key=lambda x: x["date"])
        return {"symbol": sym, "date": e["date"], "tag": tag[sym],
                "eps_est": e.get("epsEstimated"), "rev_est": e.get("revenueEstimated")}

    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(_next, list(tag)):
            if r:
                out.append(r)
    out.sort(key=lambda r: r["date"])
    return {"rows": out, "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ---- India results calendar (NSE event-calendar; needs a cookie warmup) ------
NSE_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36",
          "Accept": "application/json"}
_nse = {"session": None, "warmed": 0.0}


def _nse_get(path):
    """NSE blocks bare API hits; a homepage visit first sets the cookies it
    checks. Session reused ~10 min, rebuilt on any failure."""
    now = time.time()
    s = _nse["session"]
    if s is None or now - _nse["warmed"] > 600:
        s = requests.Session()
        s.headers.update(NSE_UA)
        try:
            s.get("https://www.nseindia.com", timeout=15)
        except Exception:  # noqa: BLE001
            return None
        _nse.update(session=s, warmed=now)
    try:
        r = s.get(f"https://www.nseindia.com/api/{path}", timeout=15)
        if r.status_code != 200:
            _nse["session"] = None
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        _nse["session"] = None
        return None


def build_earnings_in():
    """Upcoming board meetings with a Financial Results purpose for every India
    watchlist name that has an NSE listing. Per-symbol calls, politely paced;
    cached half a day. BSE-only names have no feed here — counted, not hidden."""
    today = datetime.now().date()
    rows, skipped = [], 0
    for entry in load_watchlist():
        code = entry["code"]
        meta = secmaster.lookup(code) or {}
        sym = meta.get("nse_symbol")
        if not sym:
            skipped += 1
            continue
        events = _nse_get(f"event-calendar?index=equities&symbol={sym}") or []
        best = None
        for e in events:
            if "result" not in (e.get("purpose") or "").lower():
                continue
            try:
                d = datetime.strptime(e.get("date") or "", "%d-%b-%Y").date()
            except ValueError:
                continue
            if d >= today and (best is None or d < best[0]):
                best = (d, e)
        if best:
            d, e = best
            rows.append({"code": code, "symbol": sym,
                         "company": (meta.get("company") or e.get("company") or "").title(),
                         "date": d.strftime("%Y-%m-%d"),
                         "purpose": e.get("purpose")})
        time.sleep(0.4)
    rows.sort(key=lambda r: r["date"])
    return {"rows": rows, "skipped_bse": skipped,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ---- macro (FRED, keyless CSV) ----------------------------------------------
MACRO_SERIES = [
    # (fred id, label, unit, group) — unit "%yoy" = YoY % computed from levels,
    # "k" = thousands. Groups drive the page sections.
    ("DGS2", "2Y Treasury", "%", "Rates"),
    ("DGS10", "10Y Treasury", "%", "Rates"),
    ("DGS30", "30Y Treasury", "%", "Rates"),
    ("T10Y2Y", "2s10s curve", "pp", "Rates"),
    ("FEDFUNDS", "Fed funds", "%", "Rates"),
    ("MORTGAGE30US", "30Y mortgage", "%", "Rates"),
    ("CPIAUCSL", "CPI YoY", "%yoy", "Inflation"),
    ("T10YIE", "10Y breakeven", "%", "Inflation"),
    ("T5YIFR", "5y5y fwd inflation", "%", "Inflation"),
    ("DFII10", "10Y real yield", "%", "Inflation"),
    ("UNRATE", "Unemployment", "%", "Growth & labor"),
    ("ICSA", "Initial claims", "k", "Growth & labor"),
    ("UMCSENT", "Consumer sentiment", "", "Growth & labor"),
    ("VIXCLS", "VIX", "", "Credit & vol"),
    ("BAMLH0A0HYM2", "HY spread (OAS)", "%", "Credit & vol"),
    ("BAMLC0A0CM", "IG spread (OAS)", "%", "Credit & vol"),
    ("DCOILWTICO", "WTI crude", "$", "Commodities & dollar"),
    ("DCOILBRENTEU", "Brent crude", "$", "Commodities & dollar"),
    ("DHHNGSP", "Nat gas (Henry Hub)", "$", "Commodities & dollar"),
    ("DTWEXBGS", "Dollar index", "", "Commodities & dollar"),
    ("SP500", "S&P 500", "", "Equities"),
    ("NASDAQCOM", "Nasdaq Composite", "", "Equities"),
    # India on FRED is thin: the 10Y (monthly, ~2-month lag) is the one series
    # still current. RBI repo + India CPI need an RBI/MOSPI source — pending.
    ("INDIRLTLT01STM", "India 10Y yield", "%", "India"),
]


def _fred_csv(series):
    """FRED's CDN stalls python-requests' TLS fingerprint but serves curl fine —
    so shell out to curl for this one host."""
    import subprocess
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "25",
             f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"],
            capture_output=True, text=True, timeout=30)
        lines = r.stdout.strip().splitlines()[1:]
        out = []
        for ln in lines:
            d, _, v = ln.partition(",")
            v = v.strip()
            if v and v != ".":
                try:
                    out.append((d, float(v)))
                except ValueError:
                    pass
        return out
    except Exception:  # noqa: BLE001
        return []


# ---- economic calendar (FMP; the Trading-Economics-style dated prints) -------
def build_econcal():
    """Upcoming macro prints, next ~10 days, US + India, High/Medium impact
    (Low-impact noise like rig counts stays out). Dates from FMP are UTC."""
    frm = datetime.now().strftime("%Y-%m-%d")
    to = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
    rows = fmp_get("economic-calendar", **{"from": frm, "to": to}) or []
    keep = []
    for r in rows:
        if r.get("country") not in ("US", "IN"):
            continue
        if (r.get("impact") or "") not in ("High", "Medium"):
            continue
        keep.append({"date": r.get("date"), "country": r.get("country"),
                     "event": r.get("event"), "impact": r.get("impact"),
                     "estimate": r.get("estimate"), "previous": r.get("previous"),
                     "actual": r.get("actual"), "unit": r.get("unit")})
    keep.sort(key=lambda r: r["date"] or "")
    return {"rows": keep[:80], "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ---- RBI repo rate (scraped; it moves only a few times a year) ---------------
REPO_PATH = os.path.join(HIST_CACHE_DIR, "india_repo.json")


def _india_repo():
    """Repo rate WITH its change history, scraped from BankBazaar's history
    table (Effective Date / Repo Rate / %Change rows); current-rate fallback is
    Trading Economics' stable sentence. Last good data persists on disk, so a
    failed scrape degrades to stale-but-dated, never to nothing."""
    import re
    import subprocess
    try:
        with open(REPO_PATH) as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        prev = None
    if prev and time.time() - prev.get("at", 0) < 24 * 3600:
        return prev

    hist = []
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "25", "-A", _YUA["User-Agent"],
             "https://www.bankbazaar.com/home-loan/repo-rate.html"],
            capture_output=True, text=True, timeout=30)
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", r.stdout, re.S)]
        for i, c in enumerate(cells):
            m = re.match(r"^(\d{1,2}\s+\w+\s+\d{4})$", c)
            if m and i + 1 < len(cells):
                rate = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*%$", cells[i + 1])
                if rate:
                    try:
                        d = datetime.strptime(m.group(1), "%d %B %Y")
                        hist.append((d.strftime("%Y-%m-%d"), float(rate.group(1))))
                    except ValueError:
                        pass
    except Exception:  # noqa: BLE001
        pass
    hist = sorted(set(hist))
    if hist:
        data = {"rate": hist[-1][1], "date": hist[-1][0], "at": time.time(),
                "history": hist}
        with open(REPO_PATH, "w") as fh:
            json.dump(data, fh)
        return data

    # fallback: current value only, from TE's sentence
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "25", "-A", _YUA["User-Agent"],
             "https://tradingeconomics.com/india/interest-rate"],
            capture_output=True, text=True, timeout=30)
        m = re.search(r"benchmark interest rate in india was last recorded at\s*"
                      r"([0-9]+(?:\.[0-9]+)?)\s*percent", r.stdout, re.I)
        if m:
            data = {"rate": float(m.group(1)), "at": time.time(),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "history": (prev or {}).get("history", [])}
            with open(REPO_PATH, "w") as fh:
                json.dump(data, fh)
            return data
    except Exception:  # noqa: BLE001
        pass
    return prev


_MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}


def _india_cpi():
    """All-India headline CPI YoY from MOSPI's public API (keyless). The API
    still serves the 2012-base series, which ended December 2025 when India
    rebased to 2024 — the new series is not in this API yet, so the card is
    history-complete but its latest print is Dec 2025 (date shown on card)."""
    # MOSPI needs legacy TLS renegotiation, which python's OpenSSL refuses —
    # curl handles it fine (same workaround as FRED's CDN).
    import subprocess
    series = []
    for year in range(2014, 2026):
        url = ("https://api.mospi.gov.in/api/cpi/getCPIIndex?base_year=2012"
               f"&series=Current&year={year}&sector_code=3&group_code=0"
               "&state_code=99&limit=20")
        try:
            r = subprocess.run(["curl", "-s", "-m", "25", "-A", "Mozilla/5.0", url],
                               capture_output=True, text=True, timeout=30)
            rows = json.loads(r.stdout).get("data", [])
        except Exception:  # noqa: BLE001
            rows = []
        for row in rows:
            infl = _num(row.get("inflation"))
            mn = _MONTH_NUM.get(row.get("month"))
            if infl is not None and mn:
                series.append((f"{year}-{mn:02d}-01", infl))
        time.sleep(0.3)
    return sorted(set(series))


def build_macro():
    cards = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        fetched = dict(zip((s[0] for s in MACRO_SERIES),
                           ex.map(_fred_csv, (s[0] for s in MACRO_SERIES))))
    all_series = list(MACRO_SERIES) + [("INCPI_MOSPI", "India CPI YoY", "%", "India")]
    fetched["INCPI_MOSPI"] = _india_cpi()
    for sid, label, unit, group in all_series:
        data = fetched.get(sid) or []
        if not data:
            continue
        if unit == "%yoy":     # CPI level -> YoY %
            data = [(d, (v / data[i - 12][1] - 1) * 100)
                    for i, (d, v) in enumerate(data) if i >= 12]
            unit = "%"
        if unit == "k":        # claims come as raw counts
            data = [(d, v / 1000) for d, v in data]
        latest_d, latest = data[-1]
        # delta vs ~1 month back; frequency from the actual date spacing (a
        # length heuristic misread long monthly series like UNRATE as daily)
        try:
            gap = (datetime.strptime(data[-1][0], "%Y-%m-%d")
                   - datetime.strptime(data[-2][0], "%Y-%m-%d")).days
        except (ValueError, IndexError):
            gap = 30
        back = 1 if gap >= 25 else (4 if gap >= 6 else 22)
        prev = data[-1 - back][1] if len(data) > back else None
        two_yr = data[-504:] if len(data) > 504 else data
        step = max(1, len(two_yr) // 110)
        spark = [round(v, 3) for _, v in two_yr[::step]]
        # full history, downsampled — feeds the click-to-expand chart
        fstep = max(1, len(data) // 480)
        full = [[d, round(v, 3)] for d, v in data[::fstep]]
        if full and full[-1][0] != latest_d:
            full.append([latest_d, round(latest, 3)])
        cards.append({"id": sid, "label": label, "unit": unit, "group": group,
                      "value": latest, "date": latest_d,
                      "delta": (latest - prev) if prev is not None else None,
                      "spark": spark, "full": full})
    repo = _india_repo()
    if repo:
        hist = repo.get("history") or []
        delta = (hist[-1][1] - hist[-2][1]) if len(hist) >= 2 else None
        cards.append({"id": "REPO_RBI", "label": "RBI repo rate", "unit": "%",
                      "group": "India", "value": repo["rate"],
                      "date": repo.get("date", ""), "delta": delta,
                      "spark": [v for _, v in hist][-40:],
                      "full": [[d, v] for d, v in hist]})
    return {"cards": cards, "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ---- 13F tracker (SEC EDGAR direct — FMP gates this; EDGAR is free/primary) --
EDGAR_UA = {"User-Agent": "ResearchDesk research " + os.getenv("EDGAR_CONTACT", "your-email@example.com")}

# CUSIP -> US ticker via FMP search-cusip (verified working on Starter). The
# mapping never changes, so it lives on disk forever. "" means FMP answered
# "no US listing" (a real answer, cached); a failed lookup is never cached.
CUSIP_MAP_PATH = os.path.join(HIST_CACHE_DIR, "cusip_map.json")
_cusip_lock = threading.Lock()
try:
    with open(CUSIP_MAP_PATH) as _fh:
        CUSIP_MAP = json.load(_fh)
except (OSError, ValueError):
    CUSIP_MAP = {}


def _cusip_ticker(cusip):
    if not cusip or len(cusip) < 9:
        return None
    with _cusip_lock:
        if cusip in CUSIP_MAP:
            return CUSIP_MAP[cusip] or None
    rows = fmp_get("search-cusip", cusip=cusip)
    if rows is None:
        return None
    us = [r["symbol"] for r in rows
          if r.get("symbol") and "." not in r["symbol"] and "-" not in r["symbol"]]
    sym = sorted(us, key=len)[0] if us else ""
    with _cusip_lock:
        CUSIP_MAP[cusip] = sym
        try:
            with open(CUSIP_MAP_PATH, "w") as fh:
                json.dump(CUSIP_MAP, fh)
        except OSError:
            pass
    return sym or None


_shares_out = {}   # symbol -> (fetched_ts, shares outstanding)


def _shares_outstanding(symbol):
    """Shares outstanding = live marketCap / price (one FMP quote). Slow-moving,
    so cached half a day in memory; only successes are cached."""
    hit = _shares_out.get(symbol)
    if hit and time.time() - hit[0] < 12 * 3600:
        return hit[1]
    rows = fmp_get("quote", symbol=symbol)
    q = rows[0] if isinstance(rows, list) and rows else {}
    mcap, price = _num(q.get("marketCap")), _num(q.get("price"))
    so = (mcap / price) if (mcap and price) else None
    if so:
        _shares_out[symbol] = (time.time(), so)
    return so


def _edgar_json(url):
    try:
        r = requests.get(url, headers=EDGAR_UA, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def _parse_13f(cik, accession):
    """Parse one 13F filing's infotable into {key: {issuer, value, shares}},
    keyed by CUSIP-6 (issuer id) so quarter-over-quarter diffs don't break on
    name spelling. Values scale-fixed for filers still reporting thousands."""
    import re
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}"
    index = _edgar_json(f"{base}/index.json") or {}
    xml_names = [it["name"] for it in index.get("directory", {}).get("item", [])
                 if it["name"].lower().endswith(".xml")
                 and "primary_doc" not in it["name"].lower()]
    holdings = []
    for xn in xml_names:
        try:
            raw = requests.get(f"{base}/{xn}", headers=EDGAR_UA, timeout=25).text
        except Exception:  # noqa: BLE001
            continue
        if "infoTable" not in raw:
            continue
        for m in re.finditer(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>", raw, re.S):
            blk = m.group(1)

            def _tag(t):
                mm = re.search(rf"<(?:\w+:)?{t}>\s*([^<]+?)\s*<", blk)
                return mm.group(1) if mm else None
            issuer, cusip = _tag("nameOfIssuer"), _tag("cusip")
            val, sh = _num(_tag("value")), _num(_tag("sshPrnamt"))
            if issuer and val:
                import html as _html
                issuer = _html.unescape(issuer)
                holdings.append({"issuer": issuer.title(), "cusip": (cusip or "").strip(),
                                 "value": val, "shares": sh or 0})
        if holdings:
            break
    if not holdings:
        return None
    agg = {}
    for h in holdings:
        key = h["cusip"][:6] if len(h["cusip"]) >= 6 else h["issuer"].upper()
        a = agg.setdefault(key, {"issuer": h["issuer"], "value": 0, "shares": 0,
                                 "cusip9": h["cusip"]})
        a["value"] += h["value"]
        a["shares"] += h["shares"]
    priced = [r["value"] / r["shares"] for r in agg.values() if r["shares"]]
    if priced and sorted(priced)[len(priced) // 2] < 2:
        for r in agg.values():
            r["value"] *= 1000
    return agg


def _fund_13f(cik, name, note):
    """Latest 13F top holdings PLUS what changed vs the prior quarter (new buys,
    exits, adds, trims) — diffed on CUSIP from two EDGAR filings. Disk-cached."""
    cpath = os.path.join(HIST_CACHE_DIR, f"13fv3_{cik}.json")
    try:
        with open(cpath) as fh:
            c = json.load(fh)
        if time.time() - c.get("fetched", 0) < 20 * 3600:
            return c["data"]
    except (OSError, ValueError):
        pass

    sub = _edgar_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if not sub:
        return None
    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    # newest filing per report period, latest two periods (amendments win: list
    # is newest-filed-first, and we keep the first seen per period)
    per_period = {}
    for i, f in enumerate(forms):
        if f in ("13F-HR", "13F-HR/A"):
            rp = recent.get("reportDate", [""] * len(forms))[i]
            if rp and rp not in per_period:
                per_period[rp] = i
    periods = sorted(per_period, reverse=True)[:2]
    if not periods:
        return None
    idx = per_period[periods[0]]
    filed = recent["filingDate"][idx]
    cur = _parse_13f(cik, recent["accessionNumber"][idx].replace("-", ""))
    if not cur:
        return None
    prev, prev_period = None, None
    if len(periods) > 1:
        prev_period = periods[1]
        prev = _parse_13f(cik, recent["accessionNumber"][per_period[prev_period]].replace("-", ""))

    total = sum(r["value"] for r in cur.values())
    changes = {"new": [], "exits": [], "adds": [], "trims": []}
    chg_by_key = {}
    if prev:
        for k, r in cur.items():
            p = prev.get(k)
            if p is None:
                chg_by_key[k] = "NEW"
                changes["new"].append({"issuer": r["issuer"], "value": r["value"]})
            elif p["shares"] and r["shares"]:
                d = (r["shares"] / p["shares"] - 1) * 100
                if d >= 10:
                    chg_by_key[k] = f"+{d:.0f}%"
                    changes["adds"].append({"issuer": r["issuer"], "pct": d})
                elif d <= -10:
                    chg_by_key[k] = f"{d:.0f}%"
                    changes["trims"].append({"issuer": r["issuer"], "pct": d})
        for k, p in prev.items():
            if k not in cur:
                changes["exits"].append({"issuer": p["issuer"], "value": p["value"]})
        changes["new"].sort(key=lambda x: -x["value"])
        changes["exits"].sort(key=lambda x: -x["value"])
        changes["adds"].sort(key=lambda x: -x["pct"])
        changes["trims"].sort(key=lambda x: x["pct"])
        for key in ("new", "exits"):
            changes[key] = changes[key][:6]
        for key in ("adds", "trims"):
            changes[key] = changes[key][:5]

    rows = sorted(cur.items(), key=lambda kv: -kv[1]["value"])
    top = [{**r, "weight": (r["value"] / total * 100) if total else None,
            "chg": chg_by_key.get(k)} for k, r in rows[:12]]

    # % of the company owned (share counts are irrelevant; ownership
    # of the business is the number that means something).
    def _ownership(r):
        ticker = _cusip_ticker(r.get("cusip9"))
        r["ticker"] = ticker
        so = _shares_outstanding(ticker) if (ticker and r.get("shares")) else None
        r["own_pct"] = (r["shares"] / so * 100) if so else None
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(_ownership, top))
    data = {"cik": cik, "name": name, "note": note, "filed": filed,
            "period": periods[0], "prev_period": prev_period,
            "positions": len(cur), "total_value": total, "top": top,
            "changes": changes if prev else None}
    try:
        with open(cpath, "w") as fh:
            json.dump({"fetched": time.time(), "data": data}, fh)
    except OSError:
        pass
    return data


def build_funds():
    with open(os.path.join(DATA_DIR, "funds.json")) as fh:
        funds = json.load(fh).get("funds", [])
    out = []
    with ThreadPoolExecutor(max_workers=4) as ex:   # EDGAR allows 10 req/s; be modest
        futs = [ex.submit(_fund_13f, f["cik"], f["name"], f.get("note", "")) for f in funds]
        for f in futs:
            d = f.result()
            if d:
                out.append(d)
    return {"funds": out, "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ---- quote persistence -------------------------------------------------------
QUOTES_SNAPSHOT = os.path.join(HIST_CACHE_DIR, "quotes_snapshot.json")


def restore_quotes():
    """Reload the last saved quote grids so a restart never blanks the watch
    pages (they used to show dashes for the ~12 min a full off-hours cycle
    takes). Quotes older than 3 days stay dropped."""
    try:
        with open(QUOTES_SNAPSHOT) as fh:
            c = json.load(fh)
        if time.time() - c.get("at", 0) > 3 * 86400:
            return
        with _watch_lock:
            WATCH.update(c.get("in", {}))
            WATCH_US.update(c.get("us", {}))
            WATCH_GLOBAL.update(c.get("global", {}))
        print(f"  quotes restored: {len(WATCH)} IN / {len(WATCH_US)} US / "
              f"{len(WATCH_GLOBAL)} global (from last run)")
    except (OSError, ValueError):
        pass


def quote_saver_loop():
    while True:
        time.sleep(60)
        try:
            with _watch_lock:
                blob = {"at": time.time(), "in": dict(WATCH),
                        "us": dict(WATCH_US), "global": dict(WATCH_GLOBAL)}
            with open(QUOTES_SNAPSHOT, "w") as fh:
                json.dump(blob, fh)
        except Exception:  # noqa: BLE001
            pass


# ---- alerts engine (read-only: notifies, never acts) -------------------------
ALERTS_PATH = os.path.join(DATA_DIR, "alerts.json")
ALERTS = {"active": [], "fired": set()}   # fired keys: "YYYY-MM-DD|rule|symbol"
_alerts_lock = threading.Lock()


def _load_alert_rules():
    try:
        with open(ALERTS_PATH) as fh:
            return json.load(fh).get("rules", [])
    except (OSError, ValueError):
        return []


def _notify_mac(title, body):
    """Desktop ping via osascript. Best effort; the desk UI shows it anyway."""
    import subprocess
    try:
        safe_t = title.replace('"', "'")
        safe_b = body.replace('"', "'")
        subprocess.run(["osascript", "-e",
                        f'display notification "{safe_b}" with title "{safe_t}"'],
                       capture_output=True, timeout=5)
    except Exception:  # noqa: BLE001
        pass


ALERTS_STATE_PATH = os.path.join(HIST_CACHE_DIR, "alerts_state.json")


def _restore_alerts():
    """Reload alert state at boot so a restart neither re-pings the desktop nor
    stacks duplicate chips (the old behavior he flagged). Fired keys older than
    two days age out; the visible list stays capped."""
    try:
        with open(ALERTS_STATE_PATH) as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return
    cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    with _alerts_lock:
        ALERTS["fired"] = {k for k in c.get("fired", []) if k[:10] >= cutoff}
        ALERTS["active"] = c.get("active", [])[:40]


def _save_alerts():
    try:
        with _alerts_lock:
            blob = {"fired": sorted(ALERTS["fired"]), "active": ALERTS["active"]}
        with open(ALERTS_STATE_PATH, "w") as fh:
            json.dump(blob, fh)
    except Exception:  # noqa: BLE001
        pass


def _fire(key, level, text):
    """Register one alert (once per key per day) and ping the desktop. The
    visible list is deduped on text — a re-fire (new day, restart) refreshes
    the timestamp of the one chip instead of adding another."""
    now = datetime.now()
    full = f"{now.strftime('%Y-%m-%d')}|{key}"
    with _alerts_lock:
        if full in ALERTS["fired"]:
            return
        ALERTS["fired"].add(full)
        ALERTS["active"] = [a for a in ALERTS["active"] if a.get("text") != text]
        ALERTS["active"].insert(0, {
            "level": level, "text": text,
            "ts": now.strftime("%H:%M"), "date": now.strftime("%Y-%m-%d"),
        })
        ALERTS["active"] = ALERTS["active"][:40]
    _save_alerts()
    _notify_mac("Research Desk", text)


def _eval_alerts():
    rules = _load_alert_rules()
    if not rules:
        return
    snap = _cache["snap"][1]            # reuse whatever the pages already pulled;
    earn = _cache["earn"][1]            # the engine adds no API traffic of its own
    today = datetime.now().strftime("%Y-%m-%d")

    for rule in rules:
        rtype = rule.get("type")
        try:
            if rtype == "day_move":
                thr = float(rule.get("threshold_pct") or 5)
                scope = rule.get("scope", "held")
                if scope == "held" and snap and not snap.get("session_dead"):
                    for name, a in snap.get("accounts", {}).items():
                        label = a.get("label", name)
                        for e in a.get("equity", []):
                            d = e.get("day_pct")
                            if d is not None and abs(d) >= thr:
                                _fire(f"day_move|{label}|{e['code']}", "warn" if abs(d) < thr * 1.6 else "hot",
                                      f"{e['code']} {d:+.1f}% today ({label})")
                elif scope == "watch_in":
                    with _watch_lock:
                        quotes = dict(WATCH)
                    for code, q in quotes.items():
                        d = q.get("day_pct")
                        if d is not None and abs(d) >= thr:
                            _fire(f"day_move|watch|{code}", "warn", f"{code} {d:+.1f}% today (watch)")
                elif scope == "watch_us":
                    with _watch_lock:
                        quotes = dict(WATCH_US)
                    for code, q in quotes.items():
                        d = q.get("day_pct")
                        if d is not None and abs(d) >= thr:
                            _fire(f"day_move|us|{code}", "warn", f"{code} {d:+.1f}% today (US watch)")

            elif rtype == "margin_util" and snap and not snap.get("session_dead"):
                thr = float(rule.get("threshold_pct") or 70)
                for name, a in snap.get("accounts", {}).items():
                    u = a.get("totals", {}).get("util_pct")
                    if u is not None and u >= thr:
                        _fire(f"margin|{name}", "hot",
                              f"Margin used {u:.0f}% on {a.get('label', name)} — cushion thinning")

            elif rtype == "fno_dte" and snap and not snap.get("session_dead"):
                lim = int(rule.get("days") or 7)
                for name, a in snap.get("accounts", {}).items():
                    for f in a.get("futures", []):
                        if not f.get("expiry"):
                            continue
                        try:
                            dte = (datetime.strptime(f["expiry"], "%d-%b-%Y") - datetime.now()).days
                        except ValueError:
                            continue
                        if 0 <= dte <= lim:
                            _fire(f"dte|{name}|{f['underlying']}|{f['expiry']}", "warn",
                                  f"{f['underlying']} futures expire in {dte}d ({a.get('label', name)})")

            elif rtype == "earnings_within" and earn:
                lim = int(rule.get("days") or 3)
                for e in earn.get("rows", []):
                    dd = (datetime.strptime(e["date"], "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
                    if 0 <= dd <= lim and e.get("tag") == "held":
                        _fire(f"earn|{e['symbol']}|{e['date']}", "warn",
                              f"{e['symbol']} reports in {dd}d ({e['date']})")

            elif rtype == "congress_held":
                cap = _cache["capitol"][1]
                lookback = int(rule.get("days") or 4)
                if cap:
                    cutoff = (datetime.now() - timedelta(days=lookback)).strftime("%Y-%m-%d")
                    for r in cap.get("ours", []):
                        if (r["symbol"] in cap.get("held", [])
                                and (r.get("disclosed") or "") >= cutoff):
                            _fire(f"congress|{r['symbol']}|{r['name']}|{r['tx']}", "warn",
                                  f"Congress filing on held {r['symbol']}: {r['name']} "
                                  f"{r['type']} {r['amount']} (traded {r['tx']})")

            elif rtype == "insider_cluster":
                ins = _cache["insiders"][1]
                for c in (ins or {}).get("clusters", []):
                    if c.get("ours"):
                        _fire(f"inscluster|{c['symbol']}|{c['n_buyers']}", "hot",
                              f"Insider cluster on {c['symbol']}: {c['n_buyers']} buyers, "
                              f"${c['total_value']:,} ({c['first']} → {c['last']})")

            elif rtype == "activist_13d":
                act = _cache["act13d"][1]
                lookback = int(rule.get("days") or 7)
                if act:
                    cutoff = (datetime.now() - timedelta(days=lookback)).strftime("%Y-%m-%d")
                    for r in act.get("ours", []):
                        if r.get("root") != "SCHEDULE 13D" or (r.get("date") or "") < cutoff:
                            continue
                        who = ", ".join(f["name"] for f in r.get("filers", [])[:2]) or "?"
                        _fire(f"act13d|{r['symbol']}|{r['adsh']}",
                              "hot" if r.get("tag") == "held" else "warn",
                              f"{r['form']} on {'held ' if r.get('tag') == 'held' else ''}"
                              f"{r['symbol']} by {who} (filed {r['date']})")

            elif rtype == "price_level":
                sym = (rule.get("symbol") or "").upper()
                region = rule.get("region", "in")
                with _watch_lock:
                    q = (WATCH if region == "in" else WATCH_US if region == "us" else WATCH_GLOBAL).get(sym)
                ltp = q and q.get("ltp")
                if ltp is None:
                    continue
                above, below = rule.get("above"), rule.get("below")
                if above is not None and ltp >= float(above):
                    _fire(f"lvl|{sym}|>{above}", "warn", f"{sym} {ltp:,.2f} crossed above {above}")
                if below is not None and ltp <= float(below):
                    _fire(f"lvl|{sym}|<{below}", "hot", f"{sym} {ltp:,.2f} broke below {below}")
        except Exception:  # noqa: BLE001 - one bad rule never kills the loop
            pass


def alerts_loop():
    time.sleep(90)          # let the first snapshot/watch cycles land
    while True:
        _eval_alerts()
        time.sleep(60)


# ---- supply chain (vault research rendered live) -----------------------------
CHAIN_TTL = 180
_chain_cache = {"at": 0.0, "data": None}


def build_chain():
    """data/supply_chain.json (one or more chains) + a live quote per name — Breeze
    for India chains, FMP/watch cache for US ones. Research content lives in
    the JSON; this only prices it."""
    now = time.time()
    if _chain_cache["data"] and now - _chain_cache["at"] < CHAIN_TTL:
        return _chain_cache["data"]
    with open(os.path.join(DATA_DIR, "supply_chain.json")) as fh:
        blob = json.load(fh)
    chains = blob.get("chains", [])
    breeze = next(iter(clients.values()), None)
    quoted = {}
    for chain in chains:
        region = chain.get("region", "in")
        for layer in chain.get("layers", []):
            for nm in layer.get("names", []):
                code = nm.get("code")
                key = f"{region}:{code}"
                if not code or key in quoted:
                    q = quoted.get(key)
                elif region == "us":
                    with _watch_lock:
                        q = WATCH_US.get(code)
                    if not q:
                        q = fetch_us_quote(code)
                    quoted[key] = q
                else:
                    with _watch_lock:
                        q = WATCH.get(code)
                    if not q and breeze and not breeze_health["dead"]:
                        q = fetch_watch_quote(breeze, code, "NSE") or fetch_watch_quote(breeze, code, "BSE")
                    quoted[key] = q
                if q:
                    nm["ltp"], nm["day_pct"] = q.get("ltp"), q.get("day_pct")
    data = {"chains": chains, "ts": datetime.now().strftime("%H:%M"),
            "session_dead": breeze_health["dead"]}
    _chain_cache.update(at=now, data=data)
    return data


# ---- Burry watch (Cassandra Unchained on Substack; RSS is public) ------------
def build_burry():
    """Latest posts from michaeljburry.substack.com/feed. He shut Scion (last
    13F Q3 2025, on the Funds tab) and publishes here now — titles + dates are
    the trackable surface; content is paywalled and stays his."""
    import re
    try:
        r = requests.get("https://michaeljburry.substack.com/feed",
                         headers=_YUA, timeout=15)
        xml = r.text if r.status_code == 200 else ""
    except Exception:  # noqa: BLE001
        xml = ""
    posts = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:10]:
        def _tag(t, blk=item):
            m = re.search(rf"<{t}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>", blk, re.S)
            return (m.group(1).strip() if m else "")
        title, link, pub = _tag("title"), _tag("link"), _tag("pubDate")
        try:
            d = datetime.strptime(pub[:16].strip(), "%a, %d %b %Y").strftime("%Y-%m-%d")
        except ValueError:
            d = pub[:16]
        if title:
            posts.append({"title": title, "link": link, "date": d})
    return {"posts": posts, "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ---- insider cluster screener (whole-market Form 4 feed) ---------------------
INSIDERS_TTL = 6 * 3600


def build_insiders():
    """Cluster buys across the whole US tape + every open-market buy on our
    names. ~30 FMP pages per refresh; None (uncached) when the feed fails."""
    ours = set()
    try:
        with open(os.path.join(DATA_DIR, "us_book.json")) as fh:
            for p in json.load(fh).get("positions", []):
                ours.add(p["symbol"])
    except OSError:
        pass
    for n in load_watchlist_us():
        ours.add(n["code"])
    if not os.getenv("FMP_API_KEY", "").strip():
        return sec_form4.build(sorted(ours))
    return insiders.build(sorted(ours))


# ---- sector classification (for the Risk tab's concentration bars) ----------
# Disk-cached forever once known (cache/sectors.json — edit it to correct a
# name); a failed lookup is NOT cached, so it retries next build.
SECTOR_CACHE_PATH = os.path.join(HERE, "cache", "sectors.json")
_sector_lock = threading.Lock()
_sectors = None


def _fetch_sector_us(sym):
    rows = fmp_get("profile", symbol=sym) or []
    row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
    return (row or {}).get("sector") or None


def _fetch_sector_in(nse_symbol):
    """Yahoo assetProfile for an NSE name, FMP profile on the .NS symbol as
    the fallback. Either can be empty for small BSE-ish names."""
    if not nse_symbol:
        return None
    if _yahoo["session"] or _yahoo_auth():
        try:
            r = _yahoo["session"].get(
                f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{nse_symbol}.NS",
                params={"modules": "assetProfile", "crumb": _yahoo["crumb"]}, timeout=15)
            res = (r.json().get("quoteSummary", {}).get("result") or [None])[0] or {}
            sec = (res.get("assetProfile") or {}).get("sector")
            if sec:
                return sec
        except Exception:  # noqa: BLE001
            pass
    return _fetch_sector_us(f"{nse_symbol}.NS")


def sector_of(key, fetch):
    """Memoised sector lookup: cache/sectors.json first, else fetch() once."""
    global _sectors
    with _sector_lock:
        if _sectors is None:
            try:
                with open(SECTOR_CACHE_PATH) as fh:
                    _sectors = json.load(fh)
            except (OSError, ValueError):
                _sectors = {}
        if key in _sectors:
            return _sectors[key]
    try:
        val = fetch()
    except Exception:  # noqa: BLE001
        val = None
    if not val:
        return None
    with _sector_lock:
        _sectors[key] = val
        try:
            with open(SECTOR_CACHE_PATH, "w") as fh:
                json.dump(_sectors, fh, indent=1, sort_keys=True)
        except OSError:
            pass
    return val


# ---- portfolio risk panel (beta / vol / drawdown / correlation, all books) ---
RISK_TTL = 1800


def build_risk():
    """Risk analytics across the three books: both India accounts (Breeze,
    equity + futures notional, margin cushion) and the US book (vault-synced,
    FMP histories). Benchmarks ^NSEI / ^GSPC keyless from Yahoo. India daily
    histories reuse the same per-day disk cache the ticker pages fill."""
    benches = {}
    for sym in ("^NSEI", "^GSPC"):
        h = risk.yahoo_history(sym)
        if h:
            benches[sym] = h
    books = []
    stale_used = False
    prev = load_last_snapshot()
    prev_accounts = (prev or {}).get("data", {}).get("accounts", {})
    accounts_iter = list(clients.items()) or [(n, None) for n in prev_accounts]
    for account, cli in accounts_iter:
        label = ACCOUNT_LABELS.get(account, account)
        equity, futures, funds = [], [], {}
        if cli is not None:
            try:
                equity, futures, funds = _equity(cli), _futures(cli), _funds(cli)
            except Exception:  # noqa: BLE001
                pass
        if not equity and not futures:
            # dead session: the last saved broker book still prices the risk
            # view (histories come from the disk cache, benchmarks from Yahoo)
            pa = prev_accounts.get(account)
            if not pa:
                continue
            equity = pa.get("equity") or []
            futures = pa.get("futures") or []
            funds = pa.get("funds") or {}
            label = pa.get("label") or label
            stale_used = True
        if not equity and not futures:
            continue
        pos = {}
        for e in equity:
            if not e.get("value") or not e.get("code"):
                continue
            p = pos.setdefault(e["code"], {"code": e["code"], "exposure": 0.0,
                                           "kinds": [], "exch": e.get("exch") or "NSE"})
            p["exposure"] += e["value"]
            if "EQ" not in p["kinds"]:
                p["kinds"].append("EQ")
        for f in futures:
            code = f.get("underlying")
            if not code or not f.get("notional"):
                continue
            sign = 1 if (f.get("side") or "Buy").lower() == "buy" else -1
            meta = secmaster.lookup(code) or {}
            p = pos.setdefault(code, {"code": code, "exposure": 0.0, "kinds": [],
                                      "exch": meta.get("exch") or "NSE"})
            p["exposure"] += sign * f["notional"]
            if "FUT" not in p["kinds"]:
                p["kinds"].append("FUT")
        positions = []
        for p in pos.values():
            hist = _daily_history_in(cli, p["code"], p["exch"])   # newest-first
            meta = secmaster.lookup(p["code"]) or {}
            nse_sym = meta.get("nse_symbol")
            positions.append({**p, "name": (meta.get("company") or "").title(),
                              "sector": sector_of("IN:" + p["code"],
                                                  lambda s=nse_sym: _fetch_sector_in(s)),
                              "series": [(r["date"], r["price"]) for r in reversed(hist)]})
        eq_value = sum(e["value"] for e in equity if e["value"] is not None)
        cash = funds.get("cash") or 0
        limit_total = funds.get("fno_limit_total")
        blocked = funds.get("fno_blocked") or 0
        books.append({
            "key": account, "label": label, "currency": "₹", "bench": "^NSEI",
            "nav": eq_value + cash, "cash": cash, "positions": positions,
            "margin": {**funds,
                       "util_pct": (blocked / limit_total * 100) if limit_total else None},
        })
    try:
        with open(os.path.join(DATA_DIR, "us_book.json")) as fh:
            usb = json.load(fh)
        frm = (datetime.now() - timedelta(days=500)).strftime("%Y-%m-%d")
        positions = []
        for pn in usb.get("positions", []):
            sym = pn["symbol"]
            with _watch_lock:
                q = WATCH_US.get(sym)
            q = q or fetch_us_quote(sym)
            rows = fmp_get("historical-price-eod/light", symbol=sym, **{"from": frm}) or []
            series = sorted(((r.get("date"),
                              _num(r.get("price") if r.get("price") is not None else r.get("close")))
                             for r in rows), key=lambda x: x[0] or "")
            series = [(d, v) for d, v in series if d and v]
            price = (q["ltp"] if q else None) or (series[-1][1] if series else None)
            if price is None:
                continue
            positions.append({"code": sym, "name": pn.get("name") or "",
                              "kinds": ["EQ"], "exposure": price * pn["shares"],
                              "sector": sector_of("US:" + sym,
                                                  lambda s=sym: _fetch_sector_us(s)),
                              "series": series})
        cash = usb.get("cash_usd") or 0
        if positions:
            books.append({"key": "us_book", "label": "Desk · US", "currency": "$",
                          "bench": "^GSPC",
                          "nav": cash + sum(p["exposure"] for p in positions),
                          "cash": cash, "positions": positions, "margin": None,
                          "note": f"positions as of {usb.get('as_of')} (from data/us_book.json); "
                                  "cash account, no margin"})
    except OSError:
        pass
    data = risk.build(books, benches)
    data["session_dead"] = breeze_health["dead"]
    data["stale"] = ({"broker_as_of": datetime.fromtimestamp(prev["at"]).strftime("%a %d %b, %H:%M")}
                     if (stale_used and prev) else None)
    return data


# ---- 13D/G activist feed (EDGAR full-text search; see activist.py) -----------
ACT_TTL = 12 * 3600


def build_activist():
    """13D/G filings on our names + by the tracked funds + a recent-13D
    firehose. All from EDGAR's full-text search (the live SCHEDULE 13D/G
    root forms — the old SC 13D root froze Dec 2024)."""
    our = {}
    for n in load_watchlist_us():
        our[n["code"]] = "watch"
    try:
        with open(os.path.join(DATA_DIR, "us_book.json")) as fh:
            for p in json.load(fh).get("positions", []):
                our[p["symbol"]] = "held"
    except OSError:
        pass
    try:
        with open(os.path.join(DATA_DIR, "funds.json")) as fh:
            funds = json.load(fh).get("funds", [])
    except (OSError, ValueError):
        funds = []
    return activist.build(our, funds)


# ---- US options flow (CBOE delayed chains; the options-tape method) ----------
FLOW_TTL = 3600


def build_flow():
    """Options positioning across the US book + watchlist. The daily snapshot
    lands on disk inside options_us.build, so day-over-day OI builds appear
    from the second day a name is covered."""
    held = set()
    try:
        with open(os.path.join(DATA_DIR, "us_book.json")) as fh:
            held = {p["symbol"] for p in json.load(fh).get("positions", [])}
    except OSError:
        pass
    syms = sorted(held | {n["code"] for n in load_watchlist_us()})
    return options_us.build(syms, held)


# ---- short interest + daily short-volume ratio (FINRA, free) -----------------
SHORT_TTL = 12 * 3600
_float_cache = {}


def _float_shares(sym):
    """Float via FMP shares-float (audited working on Starter); day-cached."""
    hit = _float_cache.get(sym)
    if hit and time.time() - hit[0] < 24 * 3600:
        return hit[1]
    rows = fmp_get("shares-float", symbol=sym)
    f = _num((rows[0] or {}).get("floatShares")) if isinstance(rows, list) and rows else None
    if f:
        _float_cache[sym] = (time.time(), f)
    return f


def build_short():
    held = set()
    try:
        with open(os.path.join(DATA_DIR, "us_book.json")) as fh:
            held = {p["symbol"] for p in json.load(fh).get("positions", [])}
    except OSError:
        pass
    syms = sorted(held | {n["code"] for n in load_watchlist_us()})
    return shortint.build(syms, held, float_lookup=_float_shares)


# ---- US market pulse (movers + sector heat; audited working on Starter) ------
def build_pulse():
    if not os.getenv("FMP_API_KEY", "").strip():
        return {"gainers": [], "losers": [], "actives": [], "sectors": [], "sector_date": None,
                "note": "the market pulse (movers and sector snapshot) needs the feed key",
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
    with ThreadPoolExecutor(max_workers=4) as ex:
        g = ex.submit(fmp_get, "biggest-gainers")
        l = ex.submit(fmp_get, "biggest-losers")
        a = ex.submit(fmp_get, "most-actives")
        gainers, losers, actives = g.result() or [], l.result() or [], a.result() or []

    def _trim(rows):
        out = []
        for r in rows[:10]:
            out.append({"symbol": r.get("symbol"), "name": r.get("name"),
                        "price": _num(r.get("price")),
                        "chg_pct": _num(r.get("changesPercentage"))})
        return out

    # sector snapshot: walk back to the last day the market actually printed
    sectors, sec_date = [], None
    for back in range(1, 6):
        d = (datetime.now(timezone.utc) - timedelta(days=back)).strftime("%Y-%m-%d")
        rows = fmp_get("sector-performance-snapshot", date=d) or []
        nyse = [r for r in rows if r.get("exchange") == "NYSE"] or rows
        if nyse:
            seen = {}
            for r in nyse:
                seen.setdefault(r.get("sector"), _num(r.get("averageChange")))
            sectors = sorted(({"sector": k, "chg": v} for k, v in seen.items()
                              if k and v is not None), key=lambda x: -x["chg"])
            sec_date = d
            break
    return {"gainers": _trim(gainers), "losers": _trim(losers),
            "actives": _trim(actives), "sectors": sectors, "sector_date": sec_date,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ---- Capitol trades (FMP senate/house disclosures — audited working) ---------
def _norm_congress(rows, chamber):
    out = []
    for r in rows or []:
        sym = (r.get("symbol") or "").strip().upper()
        out.append({
            "chamber": chamber,
            "symbol": sym,
            "name": f"{r.get('firstName') or ''} {r.get('lastName') or ''}".strip()
                    or (r.get("office") or ""),
            "district": r.get("district") or "",
            "owner": r.get("owner") or "",
            "asset": r.get("assetDescription") or "",
            "asset_type": r.get("assetType") or "",
            "type": r.get("type") or "",
            "amount": r.get("amount") or "",
            "tx": r.get("transactionDate") or "",
            "disclosed": r.get("disclosureDate") or "",
            "link": r.get("link") or "",
        })
    return out


def build_capitol():
    """Congress trading: the disclosure firehose plus every filing that touches
    a name on the book or watchlist. Read-only public PTR data via FMP."""
    ours = set()
    try:
        with open(os.path.join(DATA_DIR, "us_book.json")) as fh:
            held = {p["symbol"] for p in json.load(fh).get("positions", [])}
    except OSError:
        held = set()
    ours |= held
    ours |= {n["code"] for n in load_watchlist_us()}

    try:
        with open(os.path.join(DATA_DIR, "members.json")) as fh:
            tracked_members = json.load(fh).get("members", [])
    except (OSError, ValueError):
        tracked_members = []
    if not os.getenv("FMP_API_KEY", "").strip():
        return house_ptr.build(sorted(ours), tracked_members, held)

    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        latest_f = {
            "senate": [ex.submit(fmp_get, "senate-latest", page=p, limit=100) for p in (0, 1)],
            "house": [ex.submit(fmp_get, "house-latest", page=p, limit=100) for p in (0, 1)],
        }
        for sym in sorted(ours):
            jobs[sym] = (ex.submit(fmp_get, "senate-trades", symbol=sym),
                         ex.submit(fmp_get, "house-trades", symbol=sym))
        member_f = [(m, ex.submit(fmp_get, f"{m.get('chamber', 'house')}-trades-by-name",
                                  name=m["name"], page=0, limit=100))
                    for m in tracked_members]
        djt_f = ex.submit(fmp_get, "insider-trading/search", symbol="DJT", limit=15)
        flow = []
        for chamber, futs in latest_f.items():
            for f in futs:
                flow.extend(_norm_congress(f.result(), chamber))
        mine = []
        for sym, (sf, hf) in jobs.items():
            mine.extend(_norm_congress(sf.result(), "senate"))
            mine.extend(_norm_congress(hf.result(), "house"))
        members = []
        for m, fut in member_f:
            rows = _norm_congress(fut.result(), m.get("chamber", "house"))
            rows.sort(key=lambda r: r["tx"] or "", reverse=True)
            members.append({"label": m.get("label") or m["name"],
                            "chamber": m.get("chamber", "house"),
                            "count": len(rows), "rows": rows[:100]})

    flow.sort(key=lambda r: r["disclosed"] or "", reverse=True)
    # de-dup our-names rows and keep the recent year of activity
    seen, ours_rows = set(), []
    for r in sorted(mine, key=lambda r: r["tx"] or "", reverse=True):
        k = (r["chamber"], r["symbol"], r["name"], r["tx"], r["amount"], r["type"])
        if k in seen:
            continue
        seen.add(k)
        ours_rows.append(r)
    djt = [{"filed": r.get("filingDate"), "name": r.get("reportingName"),
            "type": r.get("transactionType"),
            "shares": _num(r.get("securitiesTransacted")),
            "price": _num(r.get("price"))} for r in (djt_f.result() or [])]
    return {"ours": ours_rows[:80], "flow": flow[:200], "members": members,
            "djt": djt, "held": sorted(held), "tracked": len(ours),
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}


DISKLESS = {"snap", "tape"}   # position data: never serve a restart a stale book


def _cached(kind, ttl, builder):
    with _locks[kind]:
        stamp, data = _cache[kind]
        if data is not None and (time.time() - stamp) < ttl:
            return data
        dpath = os.path.join(HIST_CACHE_DIR, f"api_{kind}.json")
        if data is None and kind not in DISKLESS:
            # a fresh process serves yesterday's cache instantly instead of a
            # spinner (his "time it takes to load" complaint)
            try:
                with open(dpath) as fh:
                    c = json.load(fh)
                if time.time() - c["at"] < ttl:
                    _cache[kind] = (c["at"], c["data"])
                    return c["data"]
            except (OSError, ValueError, KeyError):
                pass
        data = builder()
        _cache[kind] = (time.time(), data)
        if kind not in DISKLESS:
            try:
                with open(dpath, "w") as fh:
                    json.dump({"at": time.time(), "data": data}, fh)
            except OSError:
                pass
        return data


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - stdlib naming
        try:
            path = urlparse(self.path).path
            qs = parse_qs(urlparse(self.path).query)
            region = (qs.get("list", ["in"])[0] or "in").lower()
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "web", "index.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path.startswith("/assets/"):
                # shared css/js only; the basename check keeps it inside the dir
                name = os.path.basename(path)
                full = os.path.join(HERE, "web", "assets", name)
                if not os.path.isfile(full):
                    return self.send_error(404)
                ctype = "text/css" if name.endswith(".css") else "application/javascript"
                with open(full, "rb") as fh:
                    self._send(fh.read(), f"{ctype}; charset=utf-8")
            elif path == "/watch":
                with open(os.path.join(HERE, "web", "watch.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/usdesk":
                with open(os.path.join(HERE, "web", "usdesk.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/t":
                with open(os.path.join(HERE, "web", "ticker.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/api/usbook":
                self._send(json.dumps(build_usbook()).encode(), "application/json")
            elif path == "/macro":
                with open(os.path.join(HERE, "web", "macro.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/funds":
                with open(os.path.join(HERE, "web", "funds.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/capitol":
                with open(os.path.join(HERE, "web", "capitol.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/api/capitol":
                self._send(json.dumps(_cached("capitol", CAPITOL_TTL, build_capitol)).encode(), "application/json")
            elif path == "/chain":
                with open(os.path.join(HERE, "web", "chain.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/risk":
                with open(os.path.join(HERE, "web", "risk.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/api/risk":
                self._send(json.dumps(_cached("risk", RISK_TTL, build_risk)).encode(), "application/json")
            elif path == "/api/activist":
                self._send(json.dumps(_cached("act13d", ACT_TTL, build_activist)).encode(), "application/json")
            elif path == "/flow":
                with open(os.path.join(HERE, "web", "flow.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/api/flow":
                self._send(json.dumps(_cached("flow", FLOW_TTL, build_flow)).encode(), "application/json")
            elif path == "/short":
                with open(os.path.join(HERE, "web", "short.html"), "rb") as fh:
                    self._send(fh.read(), "text/html; charset=utf-8")
            elif path == "/api/short":
                self._send(json.dumps(_cached("short", SHORT_TTL, build_short)).encode(), "application/json")
            elif path == "/api/chain":
                self._send(json.dumps(build_chain()).encode(), "application/json")
            elif path == "/api/insiders":
                data = _cached("insiders", INSIDERS_TTL, build_insiders)
                self._send(json.dumps(data or {"error": "insider feed unreachable"}).encode(),
                           "application/json")
            elif path == "/api/infund":
                sym = (qs.get("symbol", [""])[0] or "").upper()
                if not sym:
                    return self.send_error(400)
                self._send(json.dumps(cached_infund(sym)).encode(), "application/json")
            elif path == "/api/alerts":
                with _alerts_lock:
                    body = json.dumps({"active": ALERTS["active"],
                                       "rules": _load_alert_rules()})
                self._send(body.encode(), "application/json")
            elif path == "/api/search":
                q = (qs.get("q", [""])[0] or "")
                self._send(json.dumps({"results": search_symbols(q, region)}).encode(),
                           "application/json")
            elif path == "/api/earnings":
                self._send(json.dumps(_cached("earn", EARN_TTL, build_earnings)).encode(), "application/json")
            elif path == "/api/earnings_in":
                self._send(json.dumps(_cached("earn_in", EARN_TTL, build_earnings_in)).encode(), "application/json")
            elif path == "/api/macro":
                self._send(json.dumps(_cached("macro", MACRO_TTL, build_macro)).encode(), "application/json")
            elif path == "/api/econcal":
                self._send(json.dumps(_cached("econcal", 6 * 3600, build_econcal)).encode(), "application/json")
            elif path == "/api/burry":
                self._send(json.dumps(_cached("burry", 6 * 3600, build_burry)).encode(), "application/json")
            elif path == "/api/pulse":
                self._send(json.dumps(_cached("pulse", 900, build_pulse)).encode(), "application/json")
            elif path == "/api/funds":
                self._send(json.dumps(_cached("funds", FUNDS_TTL, build_funds)).encode(), "application/json")
            elif path == "/api/ticker":
                sym = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not sym:
                    return self.send_error(400)
                reg = (qs.get("region", ["us"])[0] or "us").lower()
                self._send(json.dumps(cached_ticker(sym, reg)).encode(), "application/json")
            elif path == "/api/fin":
                sym = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not sym:
                    return self.send_error(400)
                self._send(json.dumps(cached_fin(sym)).encode(), "application/json")
            elif path == "/api/watch":
                with _watch_lock:
                    if region == "us":
                        data = {"region": "us", "quotes": WATCH_US,
                                "names": load_watchlist_us(),
                                "market_open": us_market_open()}
                    elif region == "global":
                        data = {"region": "global", "quotes": WATCH_GLOBAL,
                                "names": load_watchlist_global(),
                                "market_open": True}
                    else:
                        data = {"region": "in", "quotes": WATCH,
                                "names": load_watchlist(),
                                "market_open": market_open(),
                                "stream": stream_in.healthy(),
                                "session_dead": breeze_health["dead"]}
                self._send(json.dumps(data).encode(), "application/json")
            elif path == "/api/snapshot":
                data = _cached("snap", SNAP_TTL, build_snapshot)
                self._send(json.dumps(data).encode(), "application/json")
            elif self.path == "/api/tape":
                data = _cached("tape", TAPE_TTL, build_tape)
                self._send(json.dumps(data).encode(), "application/json")
            else:
                self.send_error(404)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 - report, keep serving
            try:
                self.send_error(500, str(exc))
            except Exception:  # noqa: BLE001
                pass

    def do_POST(self):  # noqa: N802 - stdlib naming
        """Watchlist add/remove. Still zero order capability — these endpoints
        only edit which names the READ-ONLY watch grid quotes."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            region = str(body.get("list", "in")).lower()
            code = str(body.get("code", "")).strip().upper()
            if self.path == "/api/watch/add":
                if not code:
                    return self._send(b'{"ok":false,"error":"empty code"}', "application/json")
                if region == "us":
                    names = load_watchlist_us()
                    if any(n["code"] == code for n in names):
                        return self._send(b'{"ok":false,"error":"already on the list"}', "application/json")
                    q = fetch_us_quote(code) or freefeed.quote(code)
                    if not q:
                        return self._send(
                            json.dumps({"ok": False, "error": f"no quote for {code} - check the ticker"}).encode(),
                            "application/json")
                    names.append({"code": code, "source": "fmp"})
                    save_watchlist_us(names)
                    with _watch_lock:
                        WATCH_US[code] = q
                    return self._send(b'{"ok":true}', "application/json")
                if region == "global":
                    names = load_watchlist_global()
                    if any(n["code"] == code for n in names):
                        return self._send(b'{"ok":false,"error":"already on the list"}', "application/json")
                    q = fetch_yahoo_quote(code)
                    if not q:
                        return self._send(
                            json.dumps({"ok": False, "error": f"no Yahoo quote for {code} — use Yahoo symbols like TALABAT.AE"}).encode(),
                            "application/json")
                    names.append({"code": code, "source": "yahoo"})
                    save_watchlist_global(names)
                    with _watch_lock:
                        WATCH_GLOBAL[code] = q
                    return self._send(b'{"ok":true}', "application/json")
                names = load_watchlist()
                exch = str(body.get("exch", "NSE")).strip().upper()
                if any(n["code"] == code for n in names):
                    return self._send(b'{"ok":false,"error":"already on the list"}', "application/json")
                breeze = next(iter(clients.values()), None)
                q = fetch_watch_quote(breeze, code, exch)
                if not q and exch == "NSE":       # try the other exchange
                    exch, q = "BSE", fetch_watch_quote(breeze, code, "BSE")
                if not q:
                    return self._send(
                        json.dumps({"ok": False, "error": f"no quote for {code} on NSE or BSE — check the ICICI code"}).encode(),
                        "application/json")
                names.append({"code": code, "exch": exch, "source": "breeze"})
                save_watchlist(names)
                with _watch_lock:
                    WATCH[code] = q
                return self._send(b'{"ok":true}', "application/json")
            if self.path == "/api/watch/remove":
                if region == "us":
                    save_watchlist_us([n for n in load_watchlist_us() if n["code"] != code])
                    with _watch_lock:
                        WATCH_US.pop(code, None)
                elif region == "global":
                    save_watchlist_global([n for n in load_watchlist_global() if n["code"] != code])
                    with _watch_lock:
                        WATCH_GLOBAL.pop(code, None)
                else:
                    save_watchlist([n for n in load_watchlist() if n["code"] != code])
                    with _watch_lock:
                        WATCH.pop(code, None)
                return self._send(b'{"ok":true}', "application/json")
            self.send_error(404)
        except Exception as exc:  # noqa: BLE001
            self._send(json.dumps({"ok": False, "error": str(exc)}).encode(), "application/json")

    def log_message(self, *args):  # quiet
        pass


def main():
    print("Authenticating accounts (ONE daily token is enough, the first account's; "
          "quotes are market data and price every account's names)...")
    for account in ACCOUNTS:
        try:
            if not clients:
                clients[account] = get_client(account)
            else:
                cli = get_client_if_cached(account)
                if cli is None:
                    print(f"  {account}: no token pasted today — book served from the "
                          f"last saved broker state, marks re-priced LIVE via the "
                          f"first session; funds/margin frozen. Paste {account}'s "
                          f"token any day you want those live too.")
                    continue
                clients[account] = cli
            ACCOUNT_LABELS[account] = _account_label(account, clients[account])
            print(f"  {account}: OK ({ACCOUNT_LABELS[account]})")
        except BaseException as exc:  # noqa: BLE001 - incl. SystemExit/EOFError from the prompt
            print(f"  {account}: SKIPPED ({type(exc).__name__}) - home-desk data offline "
                  f"until a fresh token; US/Global still live")
    breeze_health["dead"] = not clients
    if not clients:
        print("  NOTE: no broker session. The home desk and its watch grid show the last "
              "saved book (or nothing on a fresh install); US and Global run fine.")
    # Dead tokens must not cost the account-number labels 
    # or re-fire yesterday's alert chips — both restore from disk.
    prev_snap = load_last_snapshot()
    if prev_snap:
        for account, a in prev_snap.get("data", {}).get("accounts", {}).items():
            if ACCOUNT_LABELS.get(account, account) == account and a.get("label"):
                ACCOUNT_LABELS[account] = a["label"]
    _restore_alerts()
    restore_quotes()
    stream_client = next(iter(clients.values()), None)
    if stream_client:
        def _stream_sink(code, q):
            with _watch_lock:
                WATCH[code] = q
        threading.Thread(
            target=stream_in.manager,
            args=(stream_client, load_watchlist, _stream_sink, market_open),
            daemon=True).start()
    threading.Thread(target=watch_loop, daemon=True).start()
    threading.Thread(target=us_watch_loop, daemon=True).start()
    threading.Thread(target=global_watch_loop, daemon=True).start()
    threading.Thread(target=alerts_loop, daemon=True).start()
    threading.Thread(target=quote_saver_loop, daemon=True).start()

    def warmup():
        # pre-build the slow caches so the first click on any tab is instant;
        # each also refreshes on its TTL while the server runs
        for kind, ttl, builder in (("earn", EARN_TTL, build_earnings),
                                   ("macro", MACRO_TTL, build_macro),
                                   ("capitol", CAPITOL_TTL, build_capitol),
                                   ("funds", FUNDS_TTL, build_funds),
                                   ("earn_in", EARN_TTL, build_earnings_in),
                                   ("insiders", INSIDERS_TTL, build_insiders),
                                   ("risk", RISK_TTL, build_risk),
                                   ("act13d", ACT_TTL, build_activist),
                                   ("flow", FLOW_TTL, build_flow),
                                   ("short", SHORT_TTL, build_short)):
            try:
                _cached(kind, ttl, builder)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
    threading.Thread(target=warmup, daemon=True).start()
    print(f"\nDesk is live:  http://localhost:{PORT}")
    print(f"Watch Home:    http://localhost:{PORT}/watch")
    print(f"Watch US:      http://localhost:{PORT}/watch?list=us\nCtrl+C to stop.")
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except OSError:
        print(f"\nThe desk is already running at http://localhost:{PORT} — nothing to do.")


if __name__ == "__main__":
    main()
