"""Gather a full read-only snapshot of every configured account for the dashboard.

Uses broker marks (get_portfolio_holdings' current_market_price and the
position ltp), because square-off risk is computed by ICICI against ITS marks,
so that is the operative number for a risk view. Places no orders.
"""

import json
import os
import time
from datetime import datetime

from breeze_session import ACCOUNTS, get_client, get_client_if_cached

# The live-desk server persists the last alive broker snapshot here. It is the
# qty/avg/funds source for an account whose daily token was not pasted; the
# MARKET marks on those rows are then refreshed through any live session,
# because quotes are market data, not account data.
LAST_SNAP_PATH = os.path.join(os.path.dirname(__file__), "cache", "last_snapshot.json")


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _equity(breeze):
    resp = breeze.get_portfolio_holdings(
        exchange_code="NSE", from_date="", to_date="", stock_code="", portfolio_type=""
    )
    rows = (resp.get("Success") if isinstance(resp, dict) else None) or []
    out = []
    for r in rows:
        qty = _num(r.get("quantity"))
        avg = _num(r.get("average_price"))
        ltp = _num(r.get("current_market_price"))
        val = ltp * qty if (ltp is not None and qty is not None) else None
        cost = avg * qty if (avg is not None and qty is not None) else None
        pnl = (val - cost) if (val is not None and cost is not None) else None
        out.append({
            "code": r.get("stock_code"),
            "exch": r.get("exchange_code") or "NSE",
            "qty": qty, "avg": avg, "ltp": ltp,
            "value": val, "pnl": pnl,
            "pnl_pct": (pnl / cost * 100) if (pnl is not None and cost) else None,
            "day_pct": _num(r.get("change_percentage")),
        })
    return out


def _futures(breeze):
    resp = breeze.get_portfolio_positions()
    rows = (resp.get("Success") if isinstance(resp, dict) else None) or []
    out = []
    for r in rows:
        ptype = str(r.get("product_type")).lower()
        if str(r.get("segment")).lower() != "fno" or ptype not in ("futures", "options"):
            continue
        qty = _num(r.get("quantity"))
        avg = _num(r.get("average_price"))
        ltp = _num(r.get("ltp"))
        side = r.get("action") or "Buy"
        sign = 1 if side.lower() == "buy" else -1
        mtm = (ltp - avg) * qty * sign if (ltp is not None and avg is not None and qty is not None) else None
        underlying = r.get("underlying") or r.get("stock_code")
        right = str(r.get("right") or "").lower()
        strike = _num(r.get("strike_price"))
        if ptype == "options":
            kind = "CE" if right.startswith("c") else ("PE" if right.startswith("p") else "OPT")
            contract = f"{underlying} {int(strike) if strike and strike == int(strike) else strike} {kind}"
        else:
            kind, contract, right, strike = "FUT", f"{underlying} FUT", "others", 0
        out.append({
            "underlying": underlying, "contract": contract, "kind": kind,
            "right": right, "strike": strike,
            "expiry": r.get("expiry_date"),
            "side": side, "qty": qty, "avg": avg, "ltp": ltp,
            "mtm": mtm,
            "mtm_pct": ((ltp - avg) / avg * 100 * sign) if (ltp is not None and avg) else None,
            # Futures: contract value at the mark (what the Risk tab counts as
            # exposure). Options: the premium value of the position, which is
            # what the account can lose on a long leg; delta-adjusted exposure
            # is not computed here.
            "notional": (ltp * qty) if (ltp is not None and qty is not None) else None,
        })
    return out


def _funds(breeze):
    resp = breeze.get_funds()
    f = resp.get("Success") if isinstance(resp, dict) else resp
    f = f or {}
    cash = _num(f.get("total_bank_balance"))

    # get_margin(NFO).cash_limit is the TOTAL F&O trading limit: cash PLUS the
    # pledged-shares collateral limit. This is the true headroom that governs
    # square-off, so it is the number the cushion is built from.
    fno_limit_total = fno_blocked = None
    try:
        m = breeze.get_margin(exchange_code="NFO")
        s = (m.get("Success") if isinstance(m, dict) else None) or {}
        fno_limit_total = _num(s.get("cash_limit"))
        # The real position margin sits in limit_list as a NEGATIVE amount
        # (utilized from the limit); block_by_trade alone reads 0 on an account
        # carrying open futures (seen live on an account with open
        # futures: a large negative limit_list amount, block_by_trade 0). Whether block_by_trade overlaps the
        # limit_list amount is undocumented; adding both can only overstate
        # utilization slightly, which is the safe side for a square-off cushion.
        utilized = sum(abs(a) for a in
                       (_num(x.get("amount")) for x in s.get("limit_list") or [])
                       if a is not None and a < 0)
        fno_blocked = utilized + (_num(s.get("block_by_trade")) or 0)
    except Exception:  # noqa: BLE001
        pass

    fno_free = (fno_limit_total - (fno_blocked or 0)) if fno_limit_total is not None else None
    # Collateral limit = total F&O limit minus the cash that fed it (implied).
    implied_collateral = (fno_limit_total - cash) if (fno_limit_total is not None and cash is not None) else None
    if implied_collateral is not None and implied_collateral < 0:
        implied_collateral = None

    return {
        "cash": cash,
        "fno_limit_total": fno_limit_total,
        "fno_blocked": fno_blocked,
        "fno_free": fno_free,
        "implied_collateral": implied_collateral,
    }


def cash_quote(breeze, code, exch="NSE"):
    """Lean live cash quote (ltp + day%) through ANY session; NSE then BSE."""
    for ex in (exch or "NSE", "BSE"):
        try:
            r = breeze.get_quotes(stock_code=code, exchange_code=ex, product_type="cash")
            rows = (r.get("Success") if isinstance(r, dict) else None) or []
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            ltp = _num(rows[0].get("ltp"))
            prev = _num(rows[0].get("previous_close"))
            if ltp:
                return {"ltp": ltp,
                        "day_pct": ((ltp - prev) / prev * 100) if prev else None}
    return None


def nfo_quote(breeze, code, expiry_human, right="others", strike=0):
    """Live ltp for a derivatives contract through ANY session. Futures: Breeze
    wants an ISO expiry plus right='others' and strike_price='0' (an empty
    right returns 0 rows). Options: right='call'/'put' and the strike."""
    try:
        iso = datetime.strptime(expiry_human, "%d-%b-%Y").strftime("%Y-%m-%dT06:00:00.000Z")
    except (ValueError, TypeError):
        return None
    is_opt = str(right).lower().startswith(("c", "p"))
    ptype = "options" if is_opt else "futures"
    rt = ("call" if str(right).lower().startswith("c") else "put") if is_opt else "others"
    sp = str(int(strike) if strike and float(strike) == int(strike) else (strike or 0))
    for exp in (iso, expiry_human):
        try:
            r = breeze.get_quotes(stock_code=code, exchange_code="NFO",
                                  product_type=ptype, expiry_date=exp,
                                  right=rt, strike_price=sp)
            rows = (r.get("Success") if isinstance(r, dict) else None) or []
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            ltp = _num(rows[0].get("ltp"))
            if ltp:
                return {"ltp": ltp}
        time.sleep(0.3)
    return None


def refresh_marks(block, breeze):
    """Refresh the MARKET marks of a snapshot account block in place, via a
    live session that need not belong to that account. Funds/margin are left
    untouched — those are account-scoped and need the account's own token.
    Returns how many rows got a fresh price."""
    fresh = 0
    for e in block.get("equity", []):
        q = cash_quote(breeze, e.get("code"), e.get("exch")) if e.get("code") else None
        if not q:
            continue
        fresh += 1
        e["ltp"], e["day_pct"] = q["ltp"], q.get("day_pct")
        if e.get("qty") is not None:
            e["value"] = q["ltp"] * e["qty"]
            cost = (e["avg"] * e["qty"]) if e.get("avg") is not None else None
            if cost is not None:
                e["pnl"] = e["value"] - cost
                e["pnl_pct"] = (e["pnl"] / cost * 100) if cost else None
    for f in block.get("futures", []):
        code = f.get("underlying")
        q = nfo_quote(breeze, code, f.get("expiry"), f.get("right", "others"), f.get("strike", 0)) if code else None
        if not q:
            continue
        fresh += 1
        f["ltp"] = q["ltp"]
        sign = 1 if str(f.get("side", "Buy")).lower() == "buy" else -1
        if f.get("avg") is not None and f.get("qty") is not None:
            f["mtm"] = (f["ltp"] - f["avg"]) * f["qty"] * sign
            f["mtm_pct"] = (f["ltp"] - f["avg"]) / f["avg"] * 100 * sign if f["avg"] else None
            f["notional"] = f["ltp"] * f["qty"]
    return fresh


def load_last_snapshot_block(account):
    """(block, broker_as_of_string) for one account from the desk server's last
    alive snapshot, or (None, None)."""
    try:
        with open(LAST_SNAP_PATH) as fh:
            snap = json.load(fh)
    except (OSError, ValueError):
        return None, None
    block = (snap.get("data", {}).get("accounts", {}) or {}).get(account)
    if not block:
        return None, None
    at = block.get("broker_at") or snap.get("at")
    if at:
        # Stamp the block so re-saved snapshots keep the TRUE broker time of
        # this account's funds/qty, not the time of the merge.
        block["broker_at"] = at
    as_of = datetime.fromtimestamp(at).strftime("%a %d %b, %H:%M") if at else "unknown"
    return block, as_of


def _totals(equity, futures, funds):
    return {
        "equity_value": sum(e["value"] for e in equity if e.get("value") is not None),
        "equity_pnl": sum(e["pnl"] for e in equity if e.get("pnl") is not None),
        "fno_mtm": sum(f["mtm"] for f in futures if f.get("mtm") is not None),
        "cash": funds.get("cash"),
        # Free F&O limit (cash + collateral, minus blocked) is the real
        # cushion before square-off.
        "cushion": funds.get("fno_free"),
    }


def collect():
    """Return {account: {equity, futures, funds, totals}} for all accounts.

    ONE daily token is enough: the first account authenticates
    normally; any other account whose token was not pasted today falls back to
    its last saved broker book (qty/avg/funds) with every market mark
    refreshed live through the first account's session. Such a block carries
    stale_funds=True + broker_as_of.
    """
    data = {}
    live_client = None
    for account in ACCOUNTS:
        breeze = get_client(account) if live_client is None else get_client_if_cached(account)
        if breeze is not None:
            live_client = live_client or breeze
            equity = _equity(breeze)
            futures = _futures(breeze)
            funds = _funds(breeze)
            data[account] = {
                "equity": equity,
                "futures": futures,
                "funds": funds,
                "totals": _totals(equity, futures, funds),
            }
            continue
        block, as_of = load_last_snapshot_block(account)
        if block is None:
            print(f"  {account}: no token today and no saved snapshot — skipped")
            continue
        if live_client is not None:
            n = refresh_marks(block, live_client)
            print(f"  {account}: no token today — using saved book "
                  f"(broker state {as_of}), {n} marks re-priced live via the first account's session")
        funds = block.get("funds") or {}
        equity = block.get("equity", [])
        futures = block.get("futures", [])
        data[account] = {
            "equity": equity,
            "futures": futures,
            "funds": funds,
            "totals": _totals(equity, futures, funds),
            "stale_funds": True,
            "broker_as_of": as_of,
        }
    return data
