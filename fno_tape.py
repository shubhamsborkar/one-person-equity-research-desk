"""Phase 3 entry: the India options-tape read.

For each F&O name in fno_watchlist.json, pull the nearest-expiry chain and write
a dated markdown report describing how the options market is positioned (OI
walls, today's fresh positioning, expected move, downside skew). Read-only,
describe-only. No thesis judgement, no trade.

    python fno_tape.py
"""

import json
import os
from datetime import datetime

from breeze_session import get_client
from dashboard import output_path
from fno import analyze, find_chain, posture_note


def load_watchlist():
    path = os.path.join(os.path.dirname(__file__), "fno_watchlist.json")
    with open(path) as fh:
        data = json.load(fh)
    return data.get("names", [])


def _n(v, dp=0):
    return "n/a" if v is None else f"{v:,.{dp}f}"


def section(name, m, expiry):
    return (
        f"## {name}  (expiry {expiry})\n\n"
        f"Spot {_n(m['spot'], 2)}  |  PCR {_n(m['pcr_oi'], 2)}  |  "
        f"Call OI {_n(m['call_oi'])}  |  Put OI {_n(m['put_oi'])}  |  "
        f"Today dOI: calls {_n(m['call_doi'])}, puts {_n(m['put_doi'])}\n\n"
        f"{posture_note(m)}\n"
    )


def main():
    breeze = get_client()
    names = load_watchlist()
    stamp = datetime.now().strftime("%Y-%m-%d")

    sections = []
    for entry in names:
        code = entry.get("stock_code")
        exch = entry.get("exchange_code", "NFO")
        calls, puts, expiry = find_chain(breeze, code, exch)
        if not calls:
            print(f"  {code}: no chain found")
            sections.append(f"## {code}\n\nNo option chain returned. Check the ICICI code.\n")
            continue
        m = analyze(calls, puts)
        print(f"  {code}: spot={_n(m['spot'],2)} PCR={_n(m['pcr_oi'],2)} exp_move={_n(m['exp_move_pct'],1)}%")
        sections.append(section(code, m, expiry))

    report = (
        f"# India options tape, {stamp}\n\n"
        f"Where the options market is positioned on F&O names, from the Breeze "
        f"chain (single snapshot; change-in-OI shows today's fresh positioning). "
        f"PCR = put OI / call OI. Read-only, describe-only: this reports "
        f"positioning, it does not judge a thesis or suggest a trade. Verify "
        f"anything surprising against the ICICI Direct chain.\n\n"
        + "\n---\n\n".join(sections)
        + "\n"
    )

    path = output_path(stamp).replace("-india-holdings.md", "-india-options-tape.md")
    with open(path, "w") as fh:
        fh.write(report)
    print(f"\nWrote options tape for {len(names)} names to:\n  {path}")


if __name__ == "__main__":
    main()
