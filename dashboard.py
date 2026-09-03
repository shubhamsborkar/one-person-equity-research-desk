"""Phase 1: pull the portfolio, compute, write a markdown dashboard.

Read-only. Places no orders. Uses get_portfolio_holdings, which returns
quantity, average cost, current market price, exchange, and day change in one
call. Fields are read defensively and left blank (never guessed) when absent.
"""

import json
import os


# ---- defensive field access -------------------------------------------------

def _first(d, candidates):
    """Return the first present, non-empty value among candidate keys."""
    for key in candidates:
        if key in d and d[key] not in (None, "", "NA"):
            return d[key]
    return None


def _num(value):
    """Coerce to float, or None if it will not parse. Never guesses a value."""
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None


# ---- data pull --------------------------------------------------------------

def fetch_holdings(breeze):
    """Return the list of portfolio holdings, or [] with a printed reason.

    get_portfolio_holdings returns the whole book with a correct exchange_code
    per row regardless of the exchange_code argument, so one call is enough.
    """
    resp = breeze.get_portfolio_holdings(
        exchange_code="NSE",
        from_date="",
        to_date="",
        stock_code="",
        portfolio_type="",
    )
    rows = resp.get("Success") if isinstance(resp, dict) else None
    if not rows:
        err = resp.get("Error") if isinstance(resp, dict) else resp
        print(f"No portfolio holdings returned. Broker said: {err}")
        return []
    return rows


# ---- compute ----------------------------------------------------------------

def build_row(holding, pricing, watch_levels):
    code = _first(holding, ["stock_code", "symbol"]) or "?"
    exch = _first(holding, ["exchange_code", "exchange"]) or ""
    qty = _num(_first(holding, ["quantity", "qty"]))
    avg = _num(_first(holding, ["average_price", "average_cost_price", "avg_cost"]))
    live = _num(_first(holding, ["current_market_price", "ltp", "last_traded_price"]))

    # Anchor on the closing-window VWAP (house rule). Fall back to the live
    # print only when no VWAP is available, and flag it so it is never mistaken
    # for the averaged basis.
    ref = pricing.get("ref")
    prior = pricing.get("prior")
    if ref is not None:
        ltp = ref
        basis = "vwap"
        day_pct = ((ref - prior) / prior * 100) if prior else None
    else:
        ltp = live
        basis = "last"
        day_pct = _num(_first(holding, ["change_percentage", "change_pct"]))

    mkt_value = ltp * qty if (ltp is not None and qty is not None) else None
    cost_value = avg * qty if (avg is not None and qty is not None) else None
    pnl_abs = (mkt_value - cost_value) if (mkt_value is not None and cost_value is not None) else None
    pnl_pct = (pnl_abs / cost_value * 100) if (pnl_abs is not None and cost_value) else None

    level = watch_levels.get(code)
    level = _num(level) if level not in (None, 0) else None
    dist_pct = ((ltp - level) / level * 100) if (ltp is not None and level) else None

    return {
        "code": code,
        "exch": exch,
        "qty": qty,
        "avg": avg,
        "ltp": ltp,
        "basis": basis,
        "ref_date": pricing.get("ref_date"),
        "live": live,
        "mkt_value": mkt_value,
        "pnl_abs": pnl_abs,
        "pnl_pct": pnl_pct,
        "day_pct": day_pct,
        "level": level,
        "dist_pct": dist_pct,
    }


# ---- render -----------------------------------------------------------------

def _fmt(value, dp=2, pct=False):
    if value is None:
        return ""
    s = f"{value:,.{dp}f}"
    return f"{s}%" if pct else s


def render_markdown(rows, stamp):
    total_mv = sum(r["mkt_value"] for r in rows if r["mkt_value"] is not None)
    total_pnl = sum(r["pnl_abs"] for r in rows if r["pnl_abs"] is not None)
    total_cost = sum(
        r["mkt_value"] - r["pnl_abs"]
        for r in rows
        if r["mkt_value"] is not None and r["pnl_abs"] is not None
    )
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else None

    for r in rows:
        r["weight"] = (r["mkt_value"] / total_mv * 100) if (r["mkt_value"] and total_mv) else None

    # Reference session used for pricing (the most common ref_date across rows).
    ref_dates = [r.get("ref_date") for r in rows if r.get("ref_date")]
    ref_session = max(set(ref_dates), key=ref_dates.count) if ref_dates else "n/a"
    fallbacks = [r["code"] for r in rows if r.get("basis") == "last"]

    header = (
        "| Name | Exch | Qty | Avg cost | Price | Basis | Mkt value | P&L | P&L % | "
        "Day % | Weight % | Watch level | Dist % |\n"
        "| --- | :---: | ---: | ---: | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    lines = [header]
    for r in sorted(rows, key=lambda x: (x["mkt_value"] or 0), reverse=True):
        lines.append(
            "| {code} | {exch} | {qty} | {avg} | {ltp} | {basis} | {mv} | {pnl} | {pnlp} | "
            "{day} | {wt} | {lvl} | {dist} |".format(
                code=r["code"],
                exch=r.get("exch") or "",
                qty=_fmt(r["qty"], 0),
                avg=_fmt(r["avg"]),
                ltp=_fmt(r["ltp"]),
                basis=r.get("basis") or "",
                mv=_fmt(r["mkt_value"]),
                pnl=_fmt(r["pnl_abs"]),
                pnlp=_fmt(r["pnl_pct"], pct=True),
                day=_fmt(r["day_pct"], pct=True),
                wt=_fmt(r["weight"], 1, pct=True),
                lvl=_fmt(r["level"]),
                dist=_fmt(r["dist_pct"], pct=True),
            )
        )

    body = "\n".join(lines)
    fb_note = (
        f"Fallback to last print (no closing window available): "
        f"{', '.join(fallbacks)}.\n"
        if fallbacks
        else ""
    )
    return (
        f"# India book holdings, {stamp}\n\n"
        f"Read-only snapshot from ICICI Breeze. Holdings via get_portfolio_holdings; "
        f"price is the last 30-min VWAP of the {ref_session} session (house rule: "
        f"never a single last print). Day % is that VWAP vs the prior session's "
        f"VWAP. Describe-only, no trade instructions. Verify any surprising figure "
        f"against the ICICI Direct web view before acting.\n\n"
        f"Total market value: {_fmt(total_mv)}  |  "
        f"Total unrealised P&L: {_fmt(total_pnl)} "
        f"({_fmt(total_pnl_pct, pct=True)})\n\n"
        f"{body}\n\n"
        f"Basis column: vwap = 30-min closing-window average; last = fell back to "
        f"the live/last print. {fb_note}"
        f"Blank cells mean the broker response did not carry that field; "
        f"nothing is estimated.\n"
    )


# ---- output path ------------------------------------------------------------

def output_path(stamp):
    vault_dir = os.getenv("VAULT_OUTPUT_DIR", "").strip()
    base = vault_dir if vault_dir else os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{stamp}-india-holdings.md")


def load_watch_levels():
    path = os.path.join(os.path.dirname(__file__), "watch_levels.json")
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        data = json.load(fh)
    return {k: v for k, v in data.items() if not k.startswith("_")}
