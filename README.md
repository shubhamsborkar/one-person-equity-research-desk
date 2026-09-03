# One-person equity research desk

A twelve-tab investment research desk that runs on your own computer, reads your broker, the public record and one optional data feed, and opens as a tab inside Obsidian. Built by describing it to an AI coding agent (Claude Code) in plain English. This is the whole desk, minus the author's positions, published by Alpha with AI (Shubham Borkar) alongside the newsletter edition [How to Build a One-Person Equity Research Desk (a Mini Bloomberg) with Claude Fable](https://ai.shikshannivesh.com/p/how-to-build-a-one-person-equity), which explains every tab and the build. The screenshots in the newsletter edition show the author's own copy, whose sidebar reads "Family Desk"; this is the same code with a neutral name.

This copy reads accounts and shows them; it contains no order code. Your broker's API can take orders, and wiring that in is your own build, under your broker's and regulator's rules.

## Start here if GitHub is new to you

You do not need to know git, and you do not need to type the setup commands lower down yourself. The agent that built this desk can install it for you.

1. **Get the folder.** Click the green **Code** button at the top of this page, choose **Download ZIP**, and unzip it anywhere on your computer. Documents is fine.
2. **Open it in your coding agent.** Claude Code, Codex, Kimi Code or Grok Build, whichever you use. Start the agent the way you normally do and tell it the folder you just unzipped, or open a terminal in that folder and type the agent's name.
3. **Paste this and press Enter.**

```
Read README.md in this folder and set the desk up for me on this computer. Create the Python environment, install the requirements, copy .env.example to .env, and ask me for each key one at a time, telling me where to get it. My broker is <your broker>. If it is not the shipped one, read its API documentation and rewrite the broker adapter the way the README describes. If I say I have no broker to connect yet, leave the broker keys empty and skip the adapter; the US desk and the intelligence tabs run without one. Then start the desk and tell me the address to open.
```

4. **Answer its questions.** When it says the desk is up, open the address it gives you (normally `http://localhost:8765`) in your browser, or inside Obsidian as described further down.

If anything goes wrong at any step, copy the error, paste it to the agent and ask it to fix it. That is the whole method, and it is the same one that built the desk.

## What this folder is, and what it is not

This folder is only the desk: the program that draws the twelve tabs, the pages, the broker adapter and the data templates. It is not the Obsidian vault. The vault (your notes, the rulebook file, the raw inbox, the wiki and output folders, the skills) is a separate folder that the newsletter edition walks you through building, and the desk works with or without it. The two connect in two places only: the `obsidian/Live Desk.md` note, which shows the desk inside Obsidian, and the optional `VAULT_OUTPUT_DIR` setting, which drops the desk's daily reports into your vault as notes. The desk folder can sit anywhere on your computer, inside the vault or next to it.

Day to day you do not need the coding agent to run the desk; you start it with the start file and look at it in a browser or in Obsidian. The agent (Claude Code, Codex, Kimi Code, Grok Build, in a terminal or in its desktop app) is for setting it up, adapting it to your broker, and changing it later by describing what you want.

## Two desks for two markets

The desk has two account pages, and they are built differently on purpose. If you invest in the US, Desk · US is your desk and it needs no broker at all; Desk · Home is the page that connects to a broker account in whatever market you trade, and it can stay dark.

- **Desk · Home** is your broker account in whatever market you trade: holdings, open futures, funds, margin used as a bar, an options tape on the index, a ticker strip, a results calendar and the alert strip. It talks to the broker through an adapter, and the adapter shipped here is for ICICI Direct's Breeze API (India), because that is the broker the desk was built against. With any other broker you swap the adapter (see *Adapting to your broker*); until then, leave the broker keys empty and the desk still boots with everything else live. Because the shipped adapter is Indian, the Home page's currency, results calendar and index labels are Indian until the adapter is swapped, and the agent changes them along with it.
- **Desk · US** is the US market read from the public record and one optional feed: a book of US positions priced live, the earnings countdown, the insider tape from Form 4 filings (with cluster buys), and a market pulse. It needs no broker at all, so it works from anywhere, and the US intelligence tabs (Funds, Flow, Short, Capitol) sit on the same free sources.

So a reader in Australia runs Desk · Home on an ASX broker adapter and Desk · US as it ships; a reader in the US can treat Desk · US as the home desk and leave Desk · Home dark; a reader in India runs both as they are. The labels are two strings at the top of `web/assets/desk.js`; rename them to your markets.

## The twelve tabs

- **Desk · Home** and **Desk · US**: above.
- **Risk**: beta, volatility, worst drawdown and correlation for every book against its index, cross-book correlation, leverage at underlying notional, margin cushion and a -5% stress line, sector concentration.
- **Watch · Home / Watch · US / Global**: market-watch grids (broker ticks / feed quotes / Yahoo keyless). Add names in the page.
- **Macro**: 23 FRED series in groups, an economic calendar, click any card for full history.
- **Funds**: 13F tracker straight from SEC EDGAR (top holdings, quarter-over-quarter changes, % of each company owned) plus the 13D/G activist feed.
- **Flow**: the options-tape read on every US name from CBOE's free delayed chains: put/call ratios, OI walls, expected move, unusual strikes, day-over-day OI builds.
- **Short**: FINRA short interest (bi-monthly) and the daily short-volume ratio, kept apart.
- **Capitol**: Senate and House trading disclosures on your names, plus tracked members.
- **Chain**: a value-chain map from `data/supply_chain.json`, receipt-graded, live-priced.
- **/t?symbol=X**: one page per ticker: chart, valuation, quality, estimates, insiders, dividends, news, and with a feed key the full financials block (statements, ratios, segments, peers, DCF sandbox).

Cmd+K opens a command palette that jumps to any page or ticker. Two themes (graphite and a light one). A collapsible sidebar.

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

## What you need

- Python 3.10 or newer.
- For Desk · Home: a broker with an API. Shipped adapter: an ICICI Direct account and its free Breeze API key (`https://api.icicidirect.com/apiuser/home`). Any other broker: *Adapting to your broker* below.
- Optional: a Financial Modeling Prep key (any plan) for the US financials block on the ticker page and a few US pages. Without it the US watch grid runs on Yahoo's free quotes, and Funds, Flow, Short, Capitol and Macro still work, because they read free public sources.
- Nothing else. No database, no hosting, no framework.

## Setup, once (if you would rather do it yourself)

```
git clone https://github.com/shubhamsborkar/one-person-equity-research-desk.git
cd one-person-equity-research-desk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in your keys
```

Then put your names in the files under *The files you edit* (or add them in the pages once the desk is up).

Or hand all of this to your agent, as the *Start here* section at the top describes.

## Every day

Double-click `Start Desk.command` (or `python server.py`), then open `http://localhost:8765`.

That is the whole routine. Nothing on the desk needs a login of its own: Desk · US, the watch grids, Funds, Flow, Short, Capitol, Macro and Risk run from the public record and the optional feed key you set once.

Whether Desk · Home needs anything each day is up to your broker, not the desk. Most brokers keep an API session alive for weeks or months once the key is set. The shipped ICICI adapter is the exception: that broker's regulator requires a fresh login every trading day, so on days you want the Home page live it asks for a session token in the terminal (open the login URL it prints, log in, and when the page jumps to a `localhost` address copy the value after `apisession=` and paste it; it is cached for the day). If you skip it, the desk keeps serving the last saved book re-priced live and shows a ribbon, and every other page is unaffected.

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

## Built with an agent

Every line here was written by Claude Code from plain-English descriptions and screenshots. The edition this repository ships with, which walks through every tab, how the desk was built and how to install the setup around it (the vault, the rulebook, the skills, the clock), is [How to Build a One-Person Equity Research Desk (a Mini Bloomberg) with Claude Fable](https://ai.shikshannivesh.com/p/how-to-build-a-one-person-equity) in the *Alpha with AI* newsletter. The earlier setup editions start at [How I Set Up Claude Code as My Investment Research Analyst](https://ai.shikshannivesh.com/p/how-i-set-up-claude-code-as-my-investment).

## Disclaimer

This is an investment research tool, published for educational purposes by Alpha with AI and Shubham Borkar. Nothing in this repository, and nothing the desk shows, is investment, legal, or tax advice, a recommendation to buy, sell, or hold any security, or tailored to anyone's situation. The author is not a registered investment adviser or research analyst. The desk reads broker accounts, public filings, market data feeds and news sources; every number on it can be wrong, late, or misread, because feeds change, filings get restated, and code has bugs, so verify against the primary source before acting on anything. This copy places no orders. If you extend it to trade, you do so entirely at your own risk and under your own broker's and regulator's rules. Data from third-party providers is subject to their terms; a paid feed's data may not be redistributed. AI coding tools, including Claude Code, wrote this software. Investing carries risk, including the loss of capital. Do your own research and consult a qualified professional in your jurisdiction before making investment decisions.

## Licence

MIT, copyright Alpha with AI and Shubham Borkar. See `LICENSE`.
