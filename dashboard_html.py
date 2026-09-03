"""Build a good-looking, self-contained HTML dashboard for the configured accounts.

Open the file in any browser. Read-only monitor; the tool places no orders.

    python dashboard_html.py
"""

import os
from datetime import datetime

from collect import collect
from dashboard import output_path


# ---- formatting -------------------------------------------------------------

def inr(x, dp=0):
    """Indian-grouped rupee string, e.g. 1,11,381. Blank for None."""
    if x is None:
        return "&mdash;"
    neg = x < 0
    x = abs(x)
    whole = f"{x:.{dp}f}"
    if "." in whole:
        intp, frac = whole.split(".")
    else:
        intp, frac = whole, ""
    if len(intp) > 3:
        head, tail = intp[:-3], intp[-3:]
        # group the head in 2s
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        parts.insert(0, head)
        intp = ",".join(parts) + "," + tail
    s = intp + (("." + frac) if frac else "")
    return ("-" if neg else "") + s


def pct(x, dp=2):
    if x is None:
        return "&mdash;"
    return f"{x:+.{dp}f}%"


def pnl_cell(x, as_pct=False):
    """Colored, signed P&L cell. Sign is the non-color encoding."""
    if x is None:
        return '<span class="muted">&mdash;</span>'
    cls = "pos" if x >= 0 else "neg"
    val = pct(x) if as_pct else ("+" if x >= 0 else "-") + inr(abs(x))
    return f'<span class="{cls}">{val}</span>'


# ---- html pieces ------------------------------------------------------------

def tile(label, value, sub="", tone=""):
    return (
        f'<div class="tile {tone}">'
        f'<div class="tile-label">{label}</div>'
        f'<div class="tile-value">{value}</div>'
        f'<div class="tile-sub">{sub}</div>'
        f'</div>'
    )


def futures_table(futs):
    if not futs:
        return '<p class="muted">No open futures.</p>'
    rows = ""
    for f in sorted(futs, key=lambda r: (r["mtm"] or 0)):
        rows += (
            "<tr>"
            f'<td class="name">{f["underlying"]}</td>'
            f'<td class="mono">{f["expiry"]}</td>'
            f'<td>{f["side"]}</td>'
            f'<td class="mono num">{inr(f["qty"])}</td>'
            f'<td class="mono num">{inr(f["avg"], 2)}</td>'
            f'<td class="mono num">{inr(f["ltp"], 2)}</td>'
            f'<td class="mono num">{inr(f["notional"])}</td>'
            f'<td class="mono num">{pnl_cell(f["mtm"])}</td>'
            f'<td class="mono num">{pnl_cell(f["mtm_pct"], as_pct=True)}</td>'
            "</tr>"
        )
    return (
        '<table><thead><tr>'
        '<th>Contract</th><th>Expiry</th><th>Side</th><th class="num">Qty</th>'
        '<th class="num">Avg</th><th class="num">Mark</th><th class="num">Notional</th>'
        '<th class="num">MTM</th><th class="num">MTM %</th>'
        '</tr></thead><tbody>' + rows + '</tbody></table>'
    )


def equity_table(eq):
    if not eq:
        return '<p class="muted">No equity holdings.</p>'
    total = sum(e["value"] for e in eq if e["value"]) or 0
    rows = ""
    for e in sorted(eq, key=lambda r: (r["value"] or 0), reverse=True):
        wt = (e["value"] / total * 100) if (e["value"] and total) else None
        rows += (
            "<tr>"
            f'<td class="name">{e["code"]}</td>'
            f'<td class="mono num">{inr(e["qty"])}</td>'
            f'<td class="mono num">{inr(e["avg"], 2)}</td>'
            f'<td class="mono num">{inr(e["ltp"], 2)}</td>'
            f'<td class="mono num">{inr(e["value"])}</td>'
            f'<td class="mono num">{pnl_cell(e["pnl"])}</td>'
            f'<td class="mono num">{pnl_cell(e["pnl_pct"], as_pct=True)}</td>'
            f'<td class="mono num">{pnl_cell(e["day_pct"], as_pct=True)}</td>'
            f'<td class="mono num muted">{("%.1f%%" % wt) if wt is not None else "&mdash;"}</td>'
            "</tr>"
        )
    return (
        '<table><thead><tr>'
        '<th>Name</th><th class="num">Qty</th><th class="num">Avg</th>'
        '<th class="num">Mark</th><th class="num">Value</th><th class="num">P&amp;L</th>'
        '<th class="num">P&amp;L %</th><th class="num">Day %</th><th class="num">Wt</th>'
        '</tr></thead><tbody>' + rows + '</tbody></table>'
    )


def account_section(name, acc):
    t = acc["totals"]
    f = acc["funds"]
    tiles = "".join([
        tile("Equity value", "&#8377; " + inr(t["equity_value"])),
        tile("Equity P&amp;L", pnl_cell(t["equity_pnl"]),
             pct((t["equity_pnl"] / (t["equity_value"] - t["equity_pnl"]) * 100)
                 if (t["equity_value"] - t["equity_pnl"]) else None)),
        tile("F&amp;O mark-to-market", pnl_cell(t["fno_mtm"])),
        tile("Cash", "&#8377; " + inr(t["cash"])),
        tile("Free F&amp;O limit", "&#8377; " + inr(t["cushion"]),
             "cash + collateral, before square-off"),
        tile("Collateral limit", "&#8377; " + inr(f["implied_collateral"]),
             "implied (F&amp;O limit &minus; cash)"),
    ])
    return (
        f'<section class="account"><h2>{name.capitalize()}</h2>'
        f'<div class="tiles">{tiles}</div>'
        f'<h3>Open futures</h3>{futures_table(acc["futures"])}'
        f'<h3>Equity holdings</h3>{equity_table(acc["equity"])}'
        f'</section>'
    )


CSS = """
:root{
  --bg:#0e1116; --card:#171b22; --card2:#1d222b; --line:#272e39;
  --ink:#e7ebf0; --ink2:#9aa4b0; --muted:#69727e;
  --pos:#36c98a; --neg:#f0616d; --accent:#ED5A24; --warn:#e8a13c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  line-height:1.45;padding:32px 28px 64px;max-width:1180px;margin:0 auto}
header{display:flex;align-items:baseline;justify-content:space-between;
  border-bottom:2px solid var(--accent);padding-bottom:14px;margin-bottom:22px;flex-wrap:wrap;gap:8px}
header h1{font-size:20px;margin:0;letter-spacing:.2px}
header .ts{color:var(--ink2);font-size:13px}
h2{font-size:17px;margin:34px 0 12px;color:var(--ink)}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink2);
  margin:22px 0 8px;font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.tile.warn{border-color:#4a3a22}
.tile-label{font-size:12px;color:var(--ink2);margin-bottom:6px}
.tile-value{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}
.tile-sub{font-size:11px;color:var(--muted);margin-top:4px;min-height:13px}
.family .tile{background:var(--card2)}
table{width:100%;border-collapse:collapse;font-size:13px;
  background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--ink2);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
  background:#141922}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:#1b212b}
.num{text-align:right}
td.num{text-align:right}
th.num{text-align:right}
.mono{font-variant-numeric:tabular-nums;
  font-family:"SF Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.name{font-weight:600}
.pos{color:var(--pos)} .neg{color:var(--neg)} .muted{color:var(--muted)}
footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12px}
footer b{color:var(--ink2)}
"""


def build_html(data, stamp_full):
    fam_equity = sum(a["totals"]["equity_value"] for a in data.values())
    fam_eq_pnl = sum(a["totals"]["equity_pnl"] for a in data.values())
    fam_fno = sum(a["totals"]["fno_mtm"] for a in data.values())
    fam_cash = sum(a["totals"]["cash"] or 0 for a in data.values())
    fam_free = sum(a["totals"]["cushion"] or 0 for a in data.values())

    fam_tiles = "".join([
        tile("Total equity value", "&#8377; " + inr(fam_equity)),
        tile("Total equity P&amp;L", pnl_cell(fam_eq_pnl)),
        tile("Total F&amp;O MTM", pnl_cell(fam_fno)),
        tile("Total cash", "&#8377; " + inr(fam_cash)),
        tile("Total free F&amp;O limit", "&#8377; " + inr(fam_free)),
    ])

    sections = "".join(account_section(name, acc) for name, acc in data.items())

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portfolio Monitor</title>
<style>{CSS}</style></head>
<body>
<header>
  <h1>Portfolio Monitor</h1>
  <div class="ts">{stamp_full} &middot; read-only, no orders</div>
</header>
<section class="family"><div class="tiles">{fam_tiles}</div></section>
{sections}
<footer>
  <p><b>Marks:</b> equity and futures use ICICI's live marks (the prices the
  broker computes margin and square-off against), not a VWAP.</p>
  <p><b>Square-off risk:</b> positions are NOT held to a stop-loss. ICICI squares
  off only when the full limit is exhausted. "Free F&amp;O limit" is that headroom
  (from get_margin: total F&amp;O limit = cash + pledged-shares collateral, minus
  what open trades block). "Collateral limit" is implied as that total minus cash.
  Cross-check the exact free limit in ICICI Direct's Limits / Allocate Funds tab.</p>
  <p><b>Read-only.</b> This monitor places no orders in any account.</p>
</footer>
</body></html>"""


def main():
    data = collect()
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d")
    stamp_full = now.strftime("%Y-%m-%d %H:%M")
    html = build_html(data, stamp_full)

    path = output_path(stamp).replace("-india-holdings.md", "-dashboard.html")
    with open(path, "w") as fh:
        fh.write(html)
    print(f"Wrote dashboard to:\n  {path}\n\nOpen it in a browser:\n  file://{path}")


if __name__ == "__main__":
    main()
