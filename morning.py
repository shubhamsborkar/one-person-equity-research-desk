"""Phase 2: morning movers view for the India book.

For every holding, on the closing-window VWAP basis (same house rule as the
dashboard): the 1-day move, the 5-session move, and a volume-spike flag (last
session's volume vs the trailing average). Read-only, describe-only. Sorted by
the size of the 1-day move so the names that actually did something sit at top.

    python morning.py
"""

from datetime import datetime

from breeze_session import get_client
from dashboard import _fmt, load_watch_levels, output_path
from pricing import session_metrics


def _pct(a, b):
    return ((a - b) / b * 100) if (a is not None and b) else None


def build_mover(breeze, holding, watch_levels):
    code = holding.get("stock_code") or "?"
    exch = holding.get("exchange_code") or "NSE"
    series = session_metrics(breeze, code, exch)

    last = series[-1] if series else None
    prev = series[-2] if len(series) >= 2 else None
    five = series[-6] if len(series) >= 6 else (series[0] if series else None)

    ref = last["vwap"] if last else None
    move_1d = _pct(ref, prev["vwap"]) if (last and prev) else None
    move_5d = _pct(ref, five["vwap"]) if (last and five) else None

    # Volume spike: last session vs the average of the sessions before it.
    vol_spike = None
    if len(series) >= 3:
        prior_vols = [s["volume"] for s in series[:-1] if s["volume"]]
        avg_vol = (sum(prior_vols) / len(prior_vols)) if prior_vols else 0
        if avg_vol:
            vol_spike = last["volume"] / avg_vol

    level = watch_levels.get(code)
    dist = _pct(ref, level) if (ref and level not in (None, 0)) else None

    return {
        "code": code,
        "exch": exch,
        "date": last["date"] if last else None,
        "price": ref,
        "move_1d": move_1d,
        "move_5d": move_5d,
        "vol_spike": vol_spike,
        "dist": dist,
    }


def render(rows, stamp):
    session = max((r["date"] for r in rows if r["date"]), default="n/a")
    header = (
        "| Name | Exch | Price | 1-day % | 5-day % | Vol vs avg | Dist from level % |\n"
        "| --- | :---: | ---: | ---: | ---: | ---: | ---: |"
    )
    lines = [header]
    for r in sorted(rows, key=lambda x: abs(x["move_1d"] or 0), reverse=True):
        spike = f"{r['vol_spike']:.1f}x" if r["vol_spike"] is not None else ""
        lines.append(
            f"| {r['code']} | {r['exch']} | {_fmt(r['price'])} | "
            f"{_fmt(r['move_1d'], pct=True)} | {_fmt(r['move_5d'], pct=True)} | "
            f"{spike} | {_fmt(r['dist'], pct=True)} |"
        )
    body = "\n".join(lines)
    return (
        f"# India book movers, {stamp}\n\n"
        f"Morning read on the book. All moves on the last 30-min VWAP basis, "
        f"newest completed session {session}. 1-day = that session vs the one "
        f"before; 5-day = vs five sessions back; Vol vs avg = last session's "
        f"volume over the trailing average (a spike above ~2x is worth a look). "
        f"Read-only, describe-only. Verify anything surprising against ICICI "
        f"Direct.\n\n"
        f"{body}\n\n"
        f"Blank cells mean not enough session history was available; nothing is "
        f"estimated.\n"
    )


def main():
    breeze = get_client()
    resp = breeze.get_portfolio_holdings(
        exchange_code="NSE", from_date="", to_date="", stock_code="", portfolio_type=""
    )
    holdings = (resp.get("Success") if isinstance(resp, dict) else None) or []
    if not holdings:
        print("No holdings returned. Exiting.")
        return

    watch_levels = load_watch_levels()
    stamp = datetime.now().strftime("%Y-%m-%d")

    rows = []
    for h in holdings:
        r = build_mover(breeze, h, watch_levels)
        rows.append(r)
        print(f"  {r['code']}: 1d={_fmt(r['move_1d'], pct=True)} 5d={_fmt(r['move_5d'], pct=True)}")

    path = output_path(stamp).replace("-india-holdings.md", "-india-movers.md")
    with open(path, "w") as fh:
        fh.write(render(rows, stamp))
    print(f"\nWrote movers for {len(rows)} names to:\n  {path}")


if __name__ == "__main__":
    main()
