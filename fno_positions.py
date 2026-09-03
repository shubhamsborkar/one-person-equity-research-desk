"""Track open F&O positions across the configured accounts. Places no orders.
The primary account's positions come live from the API; positions in accounts
you have no key for can be recorded in fno_positions.json and are marked to
market off the live price of the same contract.

    python fno_positions.py
"""

import json
import os
from datetime import datetime

from breeze_session import ACCOUNTS, get_client, get_client_if_cached
from collect import load_last_snapshot_block, nfo_quote
from dashboard import _fmt, _num, output_path


def _load_config():
    path = os.path.join(os.path.dirname(__file__), "fno_positions.json")
    with open(path) as fh:
        return json.load(fh)


def _live_futures(breeze):
    """The account's open futures positions, straight from the API."""
    resp = breeze.get_portfolio_positions()
    rows = (resp.get("Success") if isinstance(resp, dict) else None) or []
    return [r for r in rows if str(r.get("segment")).lower() == "fno"
            and str(r.get("product_type")).lower() in ("futures", "options")]


def _lots(qty, underlying, lot_sizes):
    size = lot_sizes.get(underlying)
    if not size or qty is None:
        return None
    return qty / size


def _row_from_live(r, lot_sizes, account):
    underlying = r.get("underlying") or r.get("stock_code")
    right = str(r.get("right") or "").lower()
    strike = _num(r.get("strike_price"))
    if str(r.get("product_type")).lower() == "options":
        kind = "CE" if right.startswith("c") else ("PE" if right.startswith("p") else "OPT")
        underlying = f"{underlying} {int(strike) if strike and strike == int(strike) else strike} {kind}"
    qty = _num(r.get("quantity"))
    avg = _num(r.get("average_price"))
    ltp = _num(r.get("ltp"))
    side = r.get("action") or ""
    sign = 1 if side.lower() == "buy" else -1
    pnl = (ltp - avg) * qty * sign if (ltp is not None and avg is not None and qty is not None) else None
    pnl_pct = ((ltp - avg) / avg * 100 * sign) if (ltp is not None and avg) else None
    stop = _num(r.get("stoploss_trigger"))
    stop_dist = ((ltp - stop) / ltp * 100) if (ltp is not None and stop) else None
    return {
        "account": f"{account} (live)",
        "underlying": underlying,
        "expiry": r.get("expiry_date"),
        "side": side,
        "lots": _lots(qty, underlying, lot_sizes),
        "qty": qty,
        "avg": avg,
        "ltp": ltp,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "stop": stop,
        "stop_dist": stop_dist,
    }


def _row_from_manual(m, lot_sizes, price_map):
    underlying = m.get("underlying")
    expiry = m.get("expiry")
    size = lot_sizes.get(underlying)
    qty = (m.get("lots") * size) if (m.get("lots") and size) else None
    avg = _num(m.get("avg_price"))
    ltp = price_map.get((underlying, expiry))  # live price of the same contract
    side = m.get("side") or "Buy"
    sign = 1 if side.lower() == "buy" else -1
    pnl = (ltp - avg) * qty * sign if (ltp is not None and avg is not None and qty is not None) else None
    pnl_pct = ((ltp - avg) / avg * 100 * sign) if (ltp is not None and avg) else None
    return {
        "account": m.get("account", "manual"),
        "underlying": underlying,
        "expiry": expiry,
        "side": side,
        "lots": m.get("lots"),
        "qty": qty,
        "avg": avg,
        "ltp": ltp,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "stop": None,
        "stop_dist": None,
    }


def render(rows, stamp, flags):
    total_pnl = sum(r["pnl"] for r in rows if r["pnl"] is not None)
    header = (
        "| Account | Name | Expiry | Side | Lots | Qty | Avg | Mark | Notional | MTM | MTM % |\n"
        "| --- | --- | :---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    lines = [header]
    for r in rows:
        notional = (r["ltp"] * r["qty"]) if (r["ltp"] is not None and r["qty"] is not None) else None
        lines.append(
            f"| {r['account']} | {r['underlying']} | {r['expiry']} | {r['side']} | "
            f"{_fmt(r['lots'], 0)} | {_fmt(r['qty'], 0)} | {_fmt(r['avg'])} | {_fmt(r['ltp'])} | "
            f"{_fmt(notional)} | {_fmt(r['pnl'])} | {_fmt(r['pnl_pct'], pct=True)} |"
        )
    flag_note = ("\n".join(f"- {f}" for f in flags) + "\n\n") if flags else ""
    return (
        f"# F&O positions, {stamp}\n\n"
        f"Read-only. The tool places no orders in any account. Accounts with a "
        f"token pasted today are pulled live from their own Breeze key; any other "
        f"account is its last saved broker book marked to the live contract price. "
        f"Verify against the broker's own page.\n\n"
        f"Combined marked-to-market P&L: {_fmt(total_pnl)}\n\n"
        f"{flag_note}"
        f"{chr(10).join(lines)}\n\n"
        f"Mark = the broker's live price. Blank cells mean a value was not "
        f"available; nothing is estimated.\n"
    )


def main():
    cfg = _load_config()
    lot_sizes = cfg.get("lot_sizes", {})
    stamp = datetime.now().strftime("%Y-%m-%d")

    # Pull every account we hold a TODAY-token for (read-only). ONE daily token
    # is enough: the first live session prices every contract; an account whose
    # token was not pasted gets its positions from the desk's last saved
    # snapshot, marked to the live price of the same contract.
    live_rows = []
    snapshot_rows = []
    live_client = None
    flags = []
    for account in ACCOUNTS:
        try:
            breeze = get_client(account) if live_client is None else get_client_if_cached(account)
        except SystemExit:
            print(f"  {account}: no usable key, skipping live pull")
            continue
        if breeze is not None:
            live_client = live_client or breeze
            for r in _live_futures(breeze):
                live_rows.append(_row_from_live(r, lot_sizes, account))
            continue
        block, as_of = load_last_snapshot_block(account)
        if block is None:
            print(f"  {account}: no token today and no saved snapshot — skipped")
            continue
        for f in block.get("futures", []):
            q = nfo_quote(live_client, f.get("underlying"), f.get("expiry")) if live_client else None
            ltp = q["ltp"] if q else None
            side = f.get("side") or "Buy"
            sign = 1 if side.lower() == "buy" else -1
            qty, avg = f.get("qty"), f.get("avg")
            pnl = (ltp - avg) * qty * sign if None not in (ltp, avg, qty) else None
            snapshot_rows.append({
                "account": f"{account} (saved book, live mark)" if ltp else f"{account} (saved book, STALE mark)",
                "underlying": f.get("underlying"), "expiry": f.get("expiry"),
                "side": side, "lots": _lots(qty, f.get("underlying"), lot_sizes),
                "qty": qty, "avg": avg, "ltp": ltp if ltp else f.get("ltp"),
                "pnl": pnl if pnl is not None else f.get("mtm"),
                "pnl_pct": ((ltp - avg) / avg * 100 * sign) if None not in (ltp, avg) and avg else f.get("mtm_pct"),
                "stop": None, "stop_dist": None,
            })
        flags.append(f"{account}: no token pasted today — positions are the broker book "
                     f"as of {as_of}, marked to the live contract price via the other "
                     f"session. Cash/margin not shown live.")
    live_rows += snapshot_rows

    # Live prices of each contract, so manual positions in other accounts can be
    # marked to the same market price.
    price_map = {(r["underlying"], r["expiry"]): r["ltp"] for r in live_rows if r["ltp"] is not None}

    manual_rows = []
    for m in cfg.get("manual_positions", []):
        row = _row_from_manual(m, lot_sizes, price_map)
        manual_rows.append(row)
        if row["avg"] is None:
            flags.append(f"{row['account']} {row['underlying']} {row['expiry']}: entry price not provided, P&L blank until you give the average.")
        if row["ltp"] is None:
            flags.append(f"{row['account']} {row['underlying']} {row['expiry']}: no live price for this contract from the live session, so not marked to market.")

    rows = live_rows + manual_rows
    for r in rows:
        print(f"  {r['account']}: {r['underlying']} {r['expiry']} {r['side']} "
              f"{_fmt(r['lots'],0)} lot(s) pnl={_fmt(r['pnl'])}")

    path = output_path(stamp).replace("-india-holdings.md", "-family-fno-positions.md")
    with open(path, "w") as fh:
        fh.write(render(rows, stamp, flags))
    print(f"\nWrote {len(rows)} F&O positions to:\n  {path}")


if __name__ == "__main__":
    main()
