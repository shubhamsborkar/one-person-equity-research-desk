# Technical notes

The README is written for a reader who hands the folder to a coding agent. This file is for the reader who wants to see what the agent does, or do it by hand.

## What this folder is, and what it is not

This folder is only the desk: the program that draws the twelve tabs, the pages, the broker adapter and the data templates. It is not the Obsidian vault. The vault (your notes, the rulebook file, the raw inbox, the wiki and output folders, the skills) is a separate folder that the newsletter edition walks you through building, and the desk works with or without it. The two connect in two places only: the `obsidian/Live Desk.md` note, which shows the desk inside Obsidian, and the optional `VAULT_OUTPUT_DIR` setting, which drops the desk's daily reports into your vault as notes. The desk folder can sit anywhere on your computer, inside the vault or next to it.

Day to day you do not need the coding agent to run the desk; it starts with your computer (or with the start file) and you look at it in a browser or in Obsidian. The agent (Claude Code, Codex, Kimi Code, Grok Build, in a terminal or in its desktop app) is for setting it up, adapting it to your broker, and changing it later by describing what you want.

## Two desks for two markets

The desk has two account pages, and they are built differently on purpose. If you invest in the US, Desk · US is your desk and it needs no broker at all; Desk · Home is the page that connects to a broker account in whatever market you trade, and it can stay dark.

- **Desk · Home** is your broker account in whatever market you trade: holdings, open futures, funds, margin used as a bar, an options tape on the index, a ticker strip, a results calendar and the alert strip. It talks to the broker through an adapter, and the adapter shipped here is for ICICI Direct's Breeze API (India), because that is the broker the desk was built against. With any other broker you swap the adapter (see *Adapting to your broker*); until then, leave the broker keys empty and the desk still boots with everything else live. Because the shipped adapter is Indian, the Home page's currency, results calendar and index labels are Indian until the adapter is swapped, and the agent changes them along with it.
- **Desk · US** is the US market read from the public record and one optional feed: a book of US positions priced live, the earnings countdown, the insider tape from Form 4 filings (with cluster buys), and a market pulse. It needs no broker at all, so it works from anywhere, and the US intelligence tabs (Funds, Flow, Short, Capitol) sit on the same free sources.

So a reader in Australia runs Desk · Home on an ASX broker adapter and Desk · US as it ships; a reader in the US can treat Desk · US as the home desk and leave Desk · Home dark; a reader in India runs both as they are. The labels are two strings at the top of `web/assets/desk.js`; rename them to your markets.

## What runs without any key

The desk is built to fetch whatever it can from the free record before it asks for a key. With no broker keys and no feed key it still boots, and this is what each tab does:

| Tab | With no key at all | What the feed key adds |
|---|---|---|
| Desk · US | Positions priced from Yahoo; earnings countdown from Yahoo; insider tape from SEC EDGAR (Form 4, your names); market pulse empty | Insider scan across the whole market; the movers and sector pulse |
| Watch · US, Global | Yahoo quotes, any Yahoo symbol from any exchange (`TALABAT.AE`, `0700.HK`, `ASML.AS`) | 50/200-day distance and market cap columns |
| Ticker page (`/t?symbol=X`) | Chart and quote from Yahoo, profile, ratios, targets and analyst counts from Yahoo when it is not rate-limiting, insider table from EDGAR | Statements, ratios history, segments, estimates, peers, dividends, news, the DCF seeds |
| Funds | 13F and 13D/G straight from EDGAR | Nothing, it never uses the feed |
| Flow | CBOE's free delayed chains | Nothing |
| Short | FINRA's free files | Nothing |
| Capitol | House disclosures read from the Clerk's public PDFs (the Senate site blocks scripts) | Both chambers, cleaner rows |
| Macro | FRED, free | Nothing |
| Risk | Yahoo price histories | Nothing |
| Chain | Your own map, priced from Yahoo | Nothing |

Two honest notes on the free paths. Yahoo's endpoints are unofficial and rate-limit bursts, so a fresh install can show "retry in a few minutes" on its first page loads; the desk backs off and retries. The first Capitol build downloads up to 150 recent House reports and reads them, which takes a minute or two once, then a few seconds a day.

## Setup, once (if you would rather do it yourself)

```
git clone https://github.com/shubhamsborkar/one-person-equity-research-desk.git
cd one-person-equity-research-desk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in your keys
```

Then put your names in the files under *The files you edit* (or add them in the pages once the desk is up).

Or hand all of this to your agent, as the README describes.

## Keep the desk running

Two ways to run it. Pick one.

**Start it when you want it.** Double-click `Start Desk.command` (Mac) or `Start Desk.bat` (Windows), then open `http://localhost:8765`. Close the window and the desk stops.

**Always on.** Double-click `Keep Desk Running.command` (Mac) or `Keep Desk Running.bat` (Windows) once. From then on the desk starts by itself when you log in, and if it ever stops, for any reason, it is back within a few seconds. Shut the laptop and it sleeps with it; open the lid and it carries on. `Stop Desk.command` / `Stop Desk.bat` switches it off (and, on the Mac, back on). On the Mac this uses the built-in launch agent; on Windows it registers a task in Task Scheduler that runs `desk-service.ps1` hidden at logon. The Windows files were written from Microsoft's documented commands and have not been run on a Windows machine by the author; if one of them complains, paste the window's text to your coding agent and ask it to fix it, which is the same method that built the desk. On Linux the same thing is a five-line systemd user unit (`ExecStart=<folder>/.venv/bin/python server.py`, `WorkingDirectory=<folder>`, `Restart=always`, enabled with `systemctl --user enable --now`), and your agent can write it.

Nothing on the desk needs a login of its own: Desk · US, the watch grids, Funds, Flow, Short, Capitol, Macro and Risk run from the public record and the optional feed key you set once.

Whether Desk · Home needs anything each day is up to your broker, not the desk. Most brokers keep an API session alive for weeks or months once the key is set. The shipped ICICI adapter is the exception: that broker's regulator requires a fresh login every trading day, so on a morning you want the Home page live you double-click `Paste Token.command` / `Paste Token.bat`, it opens the broker's login page, you paste the number after `apisession=` from the address bar, press Enter, and the desk reconnects (the token is cached for the day). Skip it and the desk keeps serving the last saved book re-priced live and shows a ribbon, and every other page is unaffected. Readers on other brokers can ignore the Paste Token files entirely.

To have the desk inside Obsidian: switch on the **Web Viewer** core plugin, copy `obsidian/Live Desk.md` into your vault, and (optional) copy `obsidian/desk.css` into `.obsidian/snippets/` and enable it, so the note uses the full width.

The dated markdown reports (`python daily.py`: holdings, movers, options tape, futures positions, a static dashboard) are the same data as files; point `VAULT_OUTPUT_DIR` at a folder in your vault to read them there.

## The files you edit

All of them sit in the `data/` folder, plain JSON you can open in any text editor.

| File | What it is |
|---|---|
| `data/us_book.json` | Your US positions and cash. The desk prices them. |
| `data/watchlist.json`, `data/watchlist_us.json`, `data/watchlist_global.json` | The three watch grids (also editable in the page). Home codes are your broker's stock codes. |
| `data/fno_watchlist.json` | Names for the home options tape (indices and large caps). |
| `data/funds.json` | The 13F filers you follow (name + CIK). |
| `data/members.json` | Congress members tracked by name. |
| `data/supply_chain.json` | Your value-chain maps (an example ships). |
| `data/alerts.json` | Alert rules: day moves, margin used, futures expiry, earnings, price levels, insider clusters, 13Ds. Checked every minute; fires a macOS notification and an on-desk chip once per rule per day. |
| `data/watch_levels.json` | Optional price levels per holding. |

## More than one account

The desk supports several accounts at the same broker. Add a line to `ACCOUNTS` in `breeze_session.py` and the matching key pair in `.env`. Only the first account's session is required; the others are optional and fall back to their last saved book, re-priced live. The same shape works across markets: a home account on the broker adapter, a US book in `data/us_book.json` (or a live pull if your US broker has an API), and any other market on its own adapter.

## Adapting to your broker

Everything that is specific to the shipped broker and its market sits in a short list of files, and everything else works from the shapes those return:

| File | What it does | What yours has to return |
|---|---|---|
| `breeze_session.py` | Login and the session (daily on the shipped adapter, longer-lived on most brokers) | A client object the reads below can call |
| `collect.py` | The four reads: holdings, open positions, funds, margin; plus a live quote | Lists of positions with code, quantity, average price and last price; funds and margin as numbers |
| `stream_in.py` | Live ticks for Watch · Home during market hours | Optional; without it the grid polls quotes |
| `secmaster.py` | The broker's symbol master (short code to name, exchange, 52-week range) | A lookup from your broker's codes to names |
| `fno.py`, `fno_positions.py`, `fno_tape.py` | Open futures and the index options tape | Optional; skip if your market has no index options you watch |
| `nse_fund.py` and the results calendar in `server.py` | Home-market fundamentals and upcoming results from the exchange's public filings | Optional; the ticker page for home names shows quotes without it |
| The two India rows in `MACRO_SERIES` (`server.py`) | Home-market macro cards | Swap for your market's FRED series |

The fastest route is to open this folder in your coding agent, give it your broker's API documentation, and ask it to rewrite `breeze_session.py` and the four reads in `collect.py` for your broker first; that alone lights up Desk · Home, Risk and Watch · Home. Interactive Brokers, Alpaca, Zerodha and most large brokers publish an API; some charge for it.

## Data sources

- Broker API: your account, live ticks during market hours (shipped adapter: ICICI Direct, India; any broker with an API can replace it).
- SEC EDGAR, keyless: Form 4 insider filings on your names (`sec_form4.py`), plus the 13F and 13D/G feeds.
- House Clerk, keyless: periodic transaction reports as PDFs, parsed with pypdf (`house_ptr.py`).
- Yahoo Finance, keyless: quotes, candles, and the ticker page basics when there is no feed (`freefeed.py`).
- Financial Modeling Prep (optional): US quotes, statements, estimates, peers, insider filings, Congress trades. The desk was built on the Starter plan; `scripts/audit_fmp.py` probes which endpoints your plan allows.
- SEC EDGAR: 13F and 13D/G filings, read directly. Set `EDGAR_CONTACT` in `.env`; the SEC asks for it.
- CBOE delayed option chains, FINRA short files, FRED, the home exchange's public filings and results calendar, Yahoo Finance quotes and history: all free, no key.

Yahoo's quote endpoints are unofficial and can change; the code degrades to the feed or to the last saved quotes when they do.

