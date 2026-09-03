"""Entry point. Pull the India book, write a dated markdown dashboard.

    python run.py

Read-only. Places no orders. Needs a fresh session token in .env each day.
"""

from datetime import datetime

from breeze_session import get_client
from dashboard import (
    build_row,
    fetch_holdings,
    load_watch_levels,
    output_path,
    render_markdown,
)
from pricing import reference_price


def main():
    breeze = get_client()
    holdings = fetch_holdings(breeze)
    if not holdings:
        print("Nothing to render. Exiting.")
        return

    watch_levels = load_watch_levels()
    stamp = datetime.now().strftime("%Y-%m-%d")

    rows = []
    for h in holdings:
        code = h.get("stock_code")
        exch = h.get("exchange_code") or "NSE"
        pricing = reference_price(breeze, code, exch)
        row = build_row(h, pricing, watch_levels)
        rows.append(row)
        print(
            f"  {row['code']} ({row['exch']}): qty={row['qty']} avg={row['avg']} "
            f"price={row['ltp']} [{row['basis']} @ {row.get('ref_date')}]"
        )

    markdown = render_markdown(rows, stamp)
    path = output_path(stamp)
    with open(path, "w") as fh:
        fh.write(markdown)

    print(f"\nWrote {len(rows)} holdings to:\n  {path}")


if __name__ == "__main__":
    main()
