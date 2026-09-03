# One-person equity research desk

A research desk that runs on your own computer: twelve screens with your positions, the filings, the 13F and insider trades, the options tape, short interest, macro, and a page for any ticker, priced live. It reads your broker, the public record (SEC EDGAR, CBOE, FINRA, FRED) and one optional data feed, and it opens as a tab inside Obsidian or in any browser.

You do not write any of it. An AI coding agent (Claude Code, Codex, Kimi Code or Grok Build) installs it, connects it to your broker and changes it when you ask. This is the whole desk from the newsletter edition [How to Build a One-Person Equity Research Desk (a Mini Bloomberg) with Claude Fable](https://ai.shikshannivesh.com/p/how-to-build-a-one-person-equity), without the author's positions. The screenshots there show the author's own copy.

## How it works, in plain words

Three things are involved, and it helps to know which is which.

**The desk** is a small program that lives in the folder you download. When it is running, it shows its screens at an address in your browser. It runs on your computer, it stores everything on your computer, and it keeps running whether or not the agent is open.

**The agent** is the AI tool you type to. It installs the desk, connects your broker, and changes the desk when you ask for something new. You do not need it open to use the desk day to day.

**The data** comes from three places. Your broker knows what you own. The public record (SEC EDGAR for filings, CBOE for options, FINRA for short interest, FRED for macro, Yahoo for quotes) is free and needs no account. An optional paid feed adds the parsed financial statements on the ticker page and a few columns. If you skip the feed, nothing breaks.

## What you need

- A computer you leave on while you work. Mac or Windows.
- One AI coding agent installed: Claude Code, Codex, Kimi Code or Grok Build. If you have none, search "how do I install Claude Code" and follow the two or three steps. You need it for the install and for changes, not for daily use.
- Optional: an account with a broker that lets a program read it, which brokers call an API (Interactive Brokers, Alpaca, Robinhood, Zerodha, ICICI and most large brokers do). Without one, the US desk and every intelligence screen still work.
- Optional: a Financial Modeling Prep key for the parsed financial statements on the ticker page.

## Install it (about twenty minutes, the agent does the work)

1. **Get the folder.** Click the green **Code** button at the top of this page and choose **Download ZIP**. The file lands in your Downloads folder. Double-click it and a folder with the same name appears next to it; drag that folder into Documents. That folder is the desk, and everything below happens inside it.

2. **Get an agent, if you do not have one.** You do not need to know what a terminal is. Install the Claude desktop app from claude.ai/download (Codex, Kimi Code and Grok Build have their own apps and work the same way), sign in, and open its **Code** section. It asks which folder to work in: choose the desk folder you just moved to Documents. That is what "open the folder in your agent" means everywhere in this page. If you already use Claude Code in a terminal, open a terminal, type `cd ` (with the space), drag the desk folder onto the terminal window, press Enter, then type `claude` and press Enter.

3. **Paste this and press Enter:**

```
Read README.md in this folder and set the desk up for me on this computer. Install what it needs, copy .env.example to .env, and ask me for each key one at a time, telling me where to get it. My broker is <your broker>. If it is not the shipped one, read its API documentation and rewrite the broker adapter the way TECHNICAL.md describes. If I say I have no broker to connect yet, leave the broker keys empty and skip the adapter. Then start the desk, set it to start by itself whenever I log in using the Keep Desk Running file for my operating system (TECHNICAL.md explains it), and tell me the address to open.
```

4. **Answer its questions.** It will ask for your broker's key and, if you want one, the feed key, and it tells you where each comes from. If you have neither, say so and it skips them. It then installs everything, starts the desk, and gives you an address. Open that address in your browser. The whole thing takes about twenty minutes, most of it the agent working while you watch.

If anything goes wrong at any step, or at any point later, give your agent this file. Whether you use Claude Code, Codex, Kimi Code, Grok Build or any other agent, point it at the desk folder, tell it to read README.md, and describe the problem in your own words: it cannot install, the page is blank, the broker will not connect, you want a screen changed. Copy any error you see and paste it in. That is the whole method, and it is the same one that built the desk.

## Where the desk lives: the address

The desk opens at `http://localhost:8765`. "localhost" means *this computer*: the address is not a website, it is your own machine talking to itself, so nobody else on the internet can open it, and it works with no internet connection at all for the parts already loaded. `8765` is just the door number the desk answers on. Bookmark that address. If it does not open, the desk is not running, and the next section is what to do.

## Keeping it running

The folder you downloaded contains a few files whose names are plain English, and you use them by opening the folder (Finder on a Mac, File Explorer on Windows) and double-clicking the file. On a Mac the names end in `.command`, on Windows in `.bat`; you can ignore the ending.

- **Start Desk** starts the desk and keeps a small window open while it runs. Close that window and the desk stops. Use this if you only want the desk while you are at the screen.
- **Keep Desk Running** is the one to double-click once, if you want the desk to be there every time you sit down. From then on the desk starts by itself when you log in to your computer, and if it ever stops, for any reason, it is back within a few seconds without you doing anything. The agent's install instruction above already does this for you; the file is there for when you want to do it yourself or on a second computer.
- **Stop Desk** switches the always-on desk off. On a Mac, double-clicking it again switches it back on; on Windows, double-click Keep Desk Running again.
- **Paste Token** is only for the shipped ICICI Direct adapter, explained under *Every morning*.

What happens in daily life once Keep Desk Running has been used: you shut the computer down and switch it on again, the desk is back once you log in. You close the laptop lid, the desk sleeps with it and carries on when you open the lid. Something crashes, the desk restarts itself. You never start it by hand again.

How to tell it is running: the address opens. If the page is blank, do these in order. First close that tab and open the address in a fresh tab, because a tab that once found the desk down keeps showing it down even after it is back, and a reload does not clear that. If the fresh tab is also blank, wait thirty seconds and open it again; the always-on service restarts the desk on its own. Then double-click Start Desk. Then, if it is still blank, give your agent this file and what the window says, and ask it to fix it.

Two honest notes. The Windows files were written from Microsoft's documented commands and have not been run on a Windows machine by the author; if one of them complains, the agent fixes it. And on the first ever start the Capitol screen downloads recent House disclosures and reads them, which takes a minute or two once; Yahoo's free quotes occasionally rate-limit a brand new install and show "retry in a few minutes", and the desk retries on its own.

## Every morning

Nothing, for most readers. The desk does not have a login of its own, and most brokers keep the connection to your account alive for months once the key is set.

The one exception is the shipped ICICI Direct adapter, whose regulator requires a fresh login every trading day. On a morning you want that account live, open the desk folder, double-click **Paste Token**, log in on the page it opens, copy the number it asks for from the address bar, and paste it. Skip it and the desk keeps showing the last saved book, re-priced live, with a ribbon saying the broker session is off; every other screen is unaffected. Readers on any other broker never see this step.

## Inside Obsidian

Tell the agent: *put the desk inside my Obsidian vault*. If you would rather do it yourself: in Obsidian, Settings, Core plugins, switch on **Web Viewer**; copy the note `obsidian/Live Desk.md` from the desk folder into your vault; open that note and the desk appears in it as a tab next to your notes. The optional `obsidian/desk.css` file, dropped into your vault's `.obsidian/snippets/` folder and enabled under Appearance, lets the note use the full width.

## Two desks for two markets

The desk has two account screens, and they are built differently on purpose.

- **Desk · US** is the US market read from the public record and the optional feed: a book of US positions priced live, the earnings countdown, the insider tape from Form 4 filings with cluster buys, and a market pulse. It needs no broker, so it works from anywhere. If you invest in the US, this is your desk.
- **Desk · Home** connects to a broker account in whatever market you trade: holdings, open futures, funds, margin used, an options tape on the index, a results calendar and the alert strip. It talks to the broker through an adapter, and the adapter shipped here is for ICICI Direct in India, because that is the broker the desk was built against. With any other broker the agent rewrites the adapter from your broker's documentation; until then this screen stays dark and everything else works. Because the shipped adapter is Indian, the Home screen's currency, results calendar and index labels are Indian until the adapter is swapped, and the agent changes them along with it.

So a reader in the US uses Desk · US and never opens Home. A reader in Australia has the agent write an ASX broker adapter for Home and uses Desk · US as shipped. A reader in India runs both as they are.

## The twelve screens

- **Desk · Home** and **Desk · US**: above.
- **Risk**: beta, volatility, worst drawdown and correlation for every book against its index, leverage at underlying notional, margin cushion, a 5 percent stress line, sector concentration.
- **Watch · Home, Watch · US, Global**: three watch grids; add a name by typing it. Global takes any symbol from any exchange.
- **Macro**: 23 FRED series in groups and an economic calendar.
- **Funds**: 13F tracker straight from SEC EDGAR, top holdings, quarter-over-quarter changes, share of each company owned, plus the 13D/G activist feed.
- **Flow**: the options tape on every US name from CBOE's free delayed chains: put/call, open-interest walls, expected move, unusual strikes, day-over-day builds.
- **Short**: FINRA short interest and the daily short-volume ratio, kept apart.
- **Capitol**: Senate and House trading disclosures on your names, plus members you track.
- **Chain**: a value-chain map, receipt-graded and priced live.
- **Any ticker**: Cmd+K, type a symbol: chart, valuation, quality, estimates, insiders, dividends, news, and with a feed key six years of statements, ratios, segments, peers and a DCF sandbox.

## What runs with no key at all

With no broker key and no feed key the desk still starts, and ten of the twelve screens are live: Desk · US, Watch · US, Global, Risk, Macro, Funds, Flow, Short, Capitol, Chain, and the ticker page's chart, quote, ratios and insider table. The broker key lights up Desk · Home and Watch · Home. The feed key adds the parsed statements, ratio history, segments, estimates, peers, dividends and news on the ticker page, the 50 and 200 day columns on the US watch grid, a market-wide insider scan, and cleaner Congress rows.

## Changing it

Everything is a plain sentence to the agent. *Add Nvidia to my US watchlist. Follow Pershing Square on the Funds tab. Alert me when any holding moves 5 percent in a day. Add a tab that shows my dividend calendar.*

Your lists also sit as plain text files in the `data/` folder if you prefer to edit them yourself: your US positions (`us_book.json`), the three watch grids, the names for the options tape, the funds you follow, the Congress members you track, your value-chain maps, the alert rules, and optional price levels per holding. Each file has a comment at the top saying what goes in it.

The desk can hold several accounts at the same broker, and a US book next to a home account; the agent adds an account when you ask.

## For the technical reader

Setup by hand, the file map, how to adapt the broker adapter, the data sources in detail, and how the always-on service works on Mac, Windows and Linux: [TECHNICAL.md](TECHNICAL.md).

## Built with an agent

Every line here was written by Claude Code from plain-English descriptions and screenshots. The edition that walks through every screen, the build and the setup around it (the vault, the rulebook, the skills) is [How to Build a One-Person Equity Research Desk (a Mini Bloomberg) with Claude Fable](https://ai.shikshannivesh.com/p/how-to-build-a-one-person-equity) in the *Alpha with AI* newsletter.

The desk sits on top of a different setup, the agent itself as your research analyst: a folder of notes it reads, a rulebook it follows, and the habits of clipping filings into it and asking it questions. That is its own guide, and the place to start if you are new to all of this: [How I Set Up Claude Code as My Investment Research Analyst](https://ai.shikshannivesh.com/p/how-i-set-up-claude-code-as-my-investment), with its rebuild inside Obsidian in [How I Set Up Claude Code as My Investment Research Analyst 2.0](https://ai.shikshannivesh.com/p/how-i-set-up-claude-code-as-my-investment-c2b). The desk works with or without that setup.

## Disclaimer

This is an investment research tool, published for educational purposes by Alpha with AI and Shubham Borkar. Nothing in this repository, and nothing the desk shows, is investment, legal, or tax advice, a recommendation to buy, sell, or hold any security, or tailored to anyone's situation. The author is not a registered investment adviser or research analyst. The desk reads broker accounts, public filings, market data feeds and news sources; every number on it can be wrong, late, or misread, because feeds change, filings get restated, and code has bugs, so verify against the primary source before acting on anything. This copy places no orders. If you extend it to trade, you do so entirely at your own risk and under your own broker's and regulator's rules. Data from third-party providers is subject to their terms; a paid feed's data may not be redistributed. AI coding tools, including Claude Code, wrote this software. Investing carries risk, including the loss of capital. Do your own research and consult a qualified professional in your jurisdiction before making investment decisions.

## Licence

MIT, copyright Alpha with AI and Shubham Borkar. See `LICENSE`.
