# One-person equity research desk

A research desk that runs on your own computer: twelve screens with your positions, the filings, the 13F and insider trades, the options tape, short interest, macro, and a page for any ticker, priced live. It reads your broker, the public record (SEC EDGAR, CBOE, FINRA, FRED) and one optional data feed, and it opens as a tab inside Obsidian or in any browser.

You do not write any of it. An AI coding agent (Claude Code, Codex, Kimi Code or Grok Build) installs it, connects it to your broker and changes it when you ask. This is the whole desk from the newsletter edition [How to Build a One-Person Equity Research Desk (a Mini Bloomberg) with Claude Fable](https://ai.shikshannivesh.com/p/how-to-build-a-one-person-equity), without the author's positions. The screenshots there show the author's own copy.

## What you need

- A computer you leave on while you work. Mac or Windows.
- One AI coding agent installed: Claude Code, Codex, Kimi Code or Grok Build. If you have none, search "how do I install Claude Code" and follow the two or three steps.
- Optional: an account with a broker that lets a program read it, which brokers call an API (Interactive Brokers, Alpaca, Robinhood, Zerodha, ICICI and most large brokers do). Without one, the US desk and every intelligence screen still work.
- Optional: a Financial Modeling Prep key for the parsed financial statements on the ticker page. Everything else runs on free public sources.

## Install it (about twenty minutes, the agent does the work)

1. **Get the folder.** Click the green **Code** button at the top of this page, choose **Download ZIP**, and unzip it. Documents is fine. That folder is the desk.
2. **Open the folder in your agent.** Start your agent the way you normally do and point it at that folder.
3. **Paste this and press Enter:**

```
Read README.md in this folder and set the desk up for me on this computer. Install what it needs, copy .env.example to .env, and ask me for each key one at a time, telling me where to get it. My broker is <your broker>. If it is not the shipped one, read its API documentation and rewrite the broker adapter the way TECHNICAL.md describes. If I say I have no broker to connect yet, leave the broker keys empty and skip the adapter. Then start the desk, set it to start by itself whenever I log in using the Keep Desk Running file for my operating system (TECHNICAL.md explains it), and tell me the address to open.
```

4. **Answer its questions.** When it says the desk is up, open the address it gives you, normally `http://localhost:8765`, in your browser.

If anything goes wrong at any step, copy the error, paste it to the agent and ask it to fix it. That is the whole method, and it is the same one that built the desk.

## Every day

Open `http://localhost:8765` in your browser, or the Live Desk note in Obsidian (below). The desk starts with your computer and comes back by itself if it ever stops. If the page is blank, tell the agent: *start my desk*.

Nothing on the desk needs a daily login. Whether your broker account does is up to your broker, and most keep the connection alive for months. The one exception is the shipped ICICI Direct adapter, whose regulator requires a fresh login every trading day: on a morning you want that account live, open the desk folder and double-click the file called **Paste Token**, log in on the page it opens, and paste the number it asks for. Readers on any other broker never see this step.

## Inside Obsidian

Tell the agent: *put the desk inside my Obsidian vault*. It switches on Obsidian's Web Viewer, copies the Live Desk note into your vault, and the desk opens as a tab next to your notes.

## The twelve screens

- **Desk · Home**: your broker account, holdings, open futures, funds, margin used, an options tape on the index, a results calendar and the alert strip.
- **Desk · US**: a US book priced live, the earnings countdown, the insider tape from Form 4 filings with cluster buys, and a market pulse. Needs no broker.
- **Risk**: beta, volatility, worst drawdown and correlation for every book against its index, leverage at underlying notional, margin cushion, a 5 percent stress line, sector concentration.
- **Watch · Home, Watch · US, Global**: three watch grids; add a name by typing it.
- **Macro**: 23 FRED series in groups and an economic calendar.
- **Funds**: 13F tracker straight from SEC EDGAR, top holdings, quarter-over-quarter changes, share of each company owned, plus the 13D/G activist feed.
- **Flow**: the options tape on every US name from CBOE's free delayed chains: put/call, open-interest walls, expected move, unusual strikes, day-over-day builds.
- **Short**: FINRA short interest and the daily short-volume ratio, kept apart.
- **Capitol**: Senate and House trading disclosures on your names, plus members you track.
- **Chain**: a value-chain map, receipt-graded and priced live.
- **Any ticker**: Cmd+K, type a symbol: chart, valuation, quality, estimates, insiders, dividends, news, and with a feed key six years of statements, ratios, segments, peers and a DCF sandbox.

Two of those screens, Desk · Home and Watch · Home, need a broker. Everything else runs with no key at all; the feed key adds the parsed statements and a few columns. The desk was built against ICICI Direct, so until you connect your own broker the Home screen's currency and calendar are Indian; the agent changes them along with the adapter.

## Changing it

Everything is a plain sentence to the agent. *Add Nvidia to my US watchlist. Follow Pershing Square on the Funds tab. Alert me when any holding moves 5 percent in a day. Add a tab that shows my dividend calendar.* Your lists also sit as plain text files in the `data/` folder if you prefer to edit them yourself.

## For the technical reader

Setup by hand, the file map, how to adapt the broker adapter, the data sources, and how the always-on service works on Mac, Windows and Linux: [TECHNICAL.md](TECHNICAL.md).

## Built with an agent

Every line here was written by Claude Code from plain-English descriptions and screenshots. The edition that walks through every screen, the build and the setup around it (the vault, the rulebook, the skills) is [How to Build a One-Person Equity Research Desk (a Mini Bloomberg) with Claude Fable](https://ai.shikshannivesh.com/p/how-to-build-a-one-person-equity) in the *Alpha with AI* newsletter.

## Disclaimer

This is an investment research tool, published for educational purposes by Alpha with AI and Shubham Borkar. Nothing in this repository, and nothing the desk shows, is investment, legal, or tax advice, a recommendation to buy, sell, or hold any security, or tailored to anyone's situation. The author is not a registered investment adviser or research analyst. The desk reads broker accounts, public filings, market data feeds and news sources; every number on it can be wrong, late, or misread, because feeds change, filings get restated, and code has bugs, so verify against the primary source before acting on anything. This copy places no orders. If you extend it to trade, you do so entirely at your own risk and under your own broker's and regulator's rules. Data from third-party providers is subject to their terms; a paid feed's data may not be redistributed. AI coding tools, including Claude Code, wrote this software. Investing carries risk, including the loss of capital. Do your own research and consult a qualified professional in your jurisdiction before making investment decisions.

## Licence

MIT, copyright Alpha with AI and Shubham Borkar. See `LICENSE`.
