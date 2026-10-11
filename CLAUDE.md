# CLAUDE.md

Static single-page site that shows holdings snapshots and trade proposals for the COMM 371 Student Managed Portfolio Trust (SMPT). It is served from GitHub Pages as `index.html`. There is no framework, bundler, package.json or test suite.

## Layout

- `build/parse_holdings.py`: reads the RBC "Portfolio Holdings & Analysis" `.xlsx` workbooks and writes `data/holdings.json`. Needs `openpyxl`.
- `build/parse_perf.py`: reads the "HPR since inception" workbook and sheets 06/08/09 of each holdings workbook, and writes `data/performance.json`. Needs `openpyxl`.
- `build/fetch_prices.py`: downloads adjusted daily closes from Yahoo Finance (`yfinance`, needs network) for every ticker in a BUY/SELL trade plus the benchmark ETFs XIC.TO and SPY, and writes `data/prices.json`. TSX listings are detected from the holdings' asset class plus `CA_TRADE_ONLY`; odd Yahoo symbols go in `YF_OVERRIDE`. Tickers it can't find are listed under `missing`.
- `build/fetch_sectors.py`: downloads S&P 500 and S&P/TSX Composite constituents with GICS sectors (Wikipedia, via `requests` + `beautifulsoup4`), market caps and 1-year returns (Yahoo), classifies every equity holding by GICS, and writes `data/sectors.json`. Needs network. Yahoo rate-limits bursts, so `caps()` retries failures with growing pauses.
- `build/fetch_fundamentals.py`: downloads four years of annual statements (revenue, EBIT, D&A, capex, NWC change, tax rate, interest, debt, cash), price, market cap, beta net income to common, common equity, dividends and net buybacks, and analyst revenue-growth estimates from Yahoo for every equity in the latest snapshot, and writes `data/fundamentals.json` for the DCF tab. Needs network. `kind` is `op`, `fin` (banks, insurers, asset managers; FCFF DCF doesn't apply) or `reit`. `fx` converts the statement currency (`fccy`) into the listing currency (`ccy`). Analyst estimates are dropped for statements outside USD/CAD because Yahoo mixes currencies for ADRs.
- `build/build_site.py`: merges `data/*.json` into `build/template.html` and writes `index.html`. Uses only the standard library.
- `build/template.html`: the real source of the page (HTML, CSS and JS in one file). It holds the literal placeholder `__DATA__`, which the build replaces with inline JSON.
- `data/holdings.json`: generated. It is keyed by snapshot date (`YYYY-MM-DD`), and each value is `{total, hold:[{sec, sym, name, qty, mv, ind}]}`.
- `data/trades.json`: **maintained by hand**. It is a list of `{date, year, report, action, ticker, security, proposed, actual, executed, note}`.
- `index.html`: **generated build output, committed so Pages can serve it.** Never edit it directly. Edit `template.html` and rebuild.

## Commands

```bash
pip install openpyxl
python build/parse_holdings.py <folder-with-workbooks>   # e.g. ./portfolioAnalysis → rewrites data/holdings.json
python build/parse_perf.py <folder-with-workbooks>      # → rewrites data/performance.json
python build/fetch_prices.py                              # → rewrites data/prices.json (network)
python build/fetch_sectors.py                             # → rewrites data/sectors.json (network)
python build/fetch_fundamentals.py                        # → rewrites data/fundamentals.json (network)
python build/build_site.py                                # rewrites index.html
```

The build is deterministic, so running `build_site.py` on unchanged inputs leaves `git status` clean. That is a good check after template edits. To preview, open `index.html` in a browser. It needs network access only for Google Fonts.

## Privacy: raw sources must never be committed

The `.xlsx`, `.xls` and `.pdf` files contain the RBC account number and students' names, and `.gitignore` excludes them. Local copies live in the working tree but are untracked:
- `portfolioAnalysis/`: the RBC holdings and HPR workbooks, plus `SMPT Trade Timeline & Holdings by Period.xlsx`
- `InvestoryReport/24-25`, `InvestoryReport/25-26`: the trade proposal PDFs that `trades.json` is transcribed from

Do not copy names, account numbers or other raw-file content into `data/`, `template.html` or commits. Do not use `git add -f` on these files.

## Data pipeline details

**parse_holdings.py**
- It matches `*Portfolio Holdings*.xlsx` whose name begins with `YYYY MM DD`. It skips `~$` lock files. Both "& Analysis" and "and Analysis" filenames match.
- It reads only the **first worksheet** ("01 Holdings"). It finds the header row by the `Symbol` cell, then uses the `Symbol`, `Quantity` and `CAD Dollars` columns. The name is the column right after Symbol.
- Rows starting with a Roman numeral (`I.`, `II.`, …) set the current section (`sec`). Parsing stops at the row containing "Total value of the portfolio".
- `ind` is the raw RBC industry code from the worksheet whose title contains "Industries" ("Assets/Value Across Industries"). Its layout differs between workbooks, so each row is matched by symbol and the first text cell after the name is taken as the code. Some bonds are missing from that sheet (`ind: null`).

**parse_perf.py**
- `hpr` comes from the first sheet of the newest `*HPR since inception*.xlsx`: period end, months, SMPT gross HPR (a percent in the sheet, stored as a decimal) and the TSX / S&P 500 total-return index changes. The first dated row is the base (`start`).
- `periods` has one entry per holdings workbook. Sheet 09 gives the per-class representative index returns, the composite benchmark ("Total Holdings" row), the HPR net of FX and dividends, and the abnormal return. Columns are found through the header row ("Asset Class", "of Total", "Representative Index", "N days"), because trailing blank cells shift positions. The class weights × returns should reproduce the composite exactly. Sheet 08 gives the gross HPR. Sheet 06 gives the equity beta; it is `null` when the sheet title says "Not Ready" (values copied from the previous period) or the value is 0.

**build_site.py**
- `asset_class()` maps RBC section headings to the 7 classes in `ORDER` (Cash, Money Market, Bonds, REITs, Canadian Equity, US Equity, ADRs (Intl)). This matching is fragile: any section it doesn't recognize falls into "ADRs (Intl)". The order of `ORDER` also sets the chart colours (`--s1`…`--s7`).
- `RENAME = {'FI': 'FISV', 'ERJ': 'EMBJ'}` merges ticker changes so each security stays one row across snapshots. Add new ticker changes here.
- Industries: `IND_ALIAS` merges spelling variants (`BONDS→BOND`, `PHARM→PHRM`, `NBNK→NBF`, `RTLT→RTLR`), and a missing code falls back by asset class (`IND_BY_CLASS`). Each holding keeps the industry of its own snapshot, since RBC sometimes reclassifies (e.g. HON, NEM). `tind` (ticker → industry, latest snapshot wins) drives the trade tags and the drawer. `TRADE_IND` classifies tickers that only appear in trades. The build prints a `WARNING` for any trade ticker without an industry, so add it to `TRADE_IND`. Every code needs a name in `I18N.*.ind`, or the raw code is shown.
- `PERF_CLASS` maps sheet-09 class names to short keys (named in `I18N.*.pcls`) and short index names. An unknown class prints a `WARNING`.
- `post_perf()` adds `pp` to each BUY/SELL trade: per ticker, stock and benchmark total return from the first trading day on or after the proposal date over `HORIZONS` (3/6/12 months) and to date. A date given only to the month is measured from the 15th (`approx`). `weekly()` downsamples each series to Friday closes for the drawer chart (`DATA.px.w`). The build is only deterministic while `prices.json` is unchanged.
- GICS: share classes of one company are merged (S&P by CIK, TSX by name) and each top-15 row carries every class's key (`US:GOOGL`, `US:GOOG`), so a holding of either class is marked as held. Holdings that are index members take the index's sector; others use Yahoo's sector via `YAHOO_GICS`, or `HOLD_OVERRIDE`. Index sector weights use total (not float-adjusted) market cap. `build_site.py` warns about any non-zero equity holding without a GICS sector; rerun `fetch_sectors.py` after adding a snapshot.
- ALL-CAPS names are converted to title case.
- It prints a `WARNING` if the rows don't add up to the RBC total within $1. If you see one, investigate; don't ignore it.
- It shortens the keys in the embedded JSON (`t, n, c, i, q, v` for holdings; `rep, act, tk, sec, prop, act_, ex` for trades). The template reads these short names, so a rename has to be made in both files.

**trades.json conventions** (these fields are parsed by the template, so keep the values consistent)
- `date` is the **proposal date, not the execution date**. `year` is the school year, `"24-25"` or `"25-26"`.
- `action` must be one of `BUY`, `SELL`, `MATURED`, `CORP ACTION`, `POLICY`, `WITHDRAWAL`. Each needs an `act_<ACTION>` i18n key.
- `ticker` can hold several tickers joined by `/`. `—` and `CAD` are ignored when linking trades to holdings.
- `executed` is free text that `exOf()` in the template sorts into these buckets:
  - `n/a` → N/A
  - starts with `Not` → Not seen
  - exactly `Yes`, or `Yes (900…` / `Yes (~…` → Executed
  - **anything else** → Differs. This includes "Likely", "Partial", "Yes (staged)" and similar.

  If you add a new wording, check which bucket it lands in.
- Execution status is inferred by comparing the share counts in the snapshots before and after the proposal.

## Front end (template.html)

- The page is bilingual. The `I18N` object has `zh` and `en` tables, and **every key must be added to both**. Strings are looked up with `T(key, ...args)`, and some values are functions. Static elements use the `data-i18n`, `data-i18n-ph` and `data-i18n-aria` attributes. The chosen language is saved in `localStorage['smpt-lang']`, and the browser language is the fallback.
- Theming uses CSS custom properties on `:root`. Dark mode comes from `prefers-color-scheme`, and a `data-theme` override takes precedence. When you change a colour, update both the light and dark token sets.
- The main UI pieces:
  - snapshot tiles (`renderSnaps`)
  - asset-mix bar (`renderMix`)
  - Holdings tab (`renderHold`), with search, a group-by toggle (asset class / industry, `hgrp`) whose chips filter that dimension, sortable columns including industry, a change-vs-previous-snapshot column (`chgOf`) and an exited-positions list
  - Trade timeline tab (`renderTrades`), with action and execution filters, an industry dropdown (`tInd`) and industry tags (`indsOf`)
  - Analysis tab (`renderAna`), with a window toggle (since inception / 5y / 3y, `aWin`): growth-of-$1,000 line chart and per-period excess-return bars (hand-built SVG), the RBC composite-benchmark table, and risk metrics. `stats()` and `rel()` annualize from log returns weighted by period length, because the HPR periods run 3–7 months. Chart colours are `--c-smpt/--c-tsx/--c-sp`, which alias `--s3/--s1/--s2` (validated for CVD in both themes).
  - GICS sector card (`renderSec`), in the Analysis tab: latest-snapshot equity weights by sector against the S&P 500, the S&P/TSX and a blend weighted by SMPT's Canadian share (over/under bars use `--c-over/--c-under`), expandable top-15 lists for both indices with held companies marked, and sector weights across snapshots.
  - Proposal performance card (`renderPP`), in the Analysis tab: horizon / action / sort toggles, tiles, a by-school-year table and a per-ticker list. "Directional excess" is stock − benchmark for a buy and the negative of that for a sell.
  - Valuation tab (`renderDCF`, `renderDSum`): a two-stage FCFF DCF for each stock in the latest snapshot. `dBase()` derives defaults from `DATA.fd` (3-year averages as % of revenue, growth from analyst estimates or the 3-year CAGR fading to terminal growth, capex and D&A converging to the lower of the two by the last year, Blume-adjusted beta, `RF`/`ERP`/`TG` set by hand). `dModel()` applies manual inputs on top and `dValue()` discounts (amounts in millions of `fccy`, per-share value × `fx`). Manual inputs are stored only as differences from the defaults, in `DStore` (localStorage key `smpt-dcf`), and pass through `dClean()` on every read and import. `DStore` is the single seam for a future shared backend. Financials (`kind: 'fin'`) go to `dModelEq()` / `dValueEq()` instead: a total-payout DDM (dividends + net buybacks) with a residual income split, at the cost of equity, book equity rolling forward by clean surplus. The terminal payout defaults to 1 − g/ROE, which makes DDM and RI equal by construction; they differ only if `tpo` is set by hand. REITs still use FCFF.
  - per-ticker drawer (`openTk`), with holdings by snapshot, a weekly price chart against the rebased benchmark with proposal markers (`drawPx`), and related trades with their directional excess per horizon (`ppLine`)
- Cash and Money Market rows are excluded from position counts and from the change and new-position logic.
- Escape any data you interpolate into HTML with `esc()`.

## Conventions

- The README and UI copy are written primarily in Chinese, with English as a toggle. Commit messages are in English.
- To add a new snapshot:
  1. Place the workbook in the source folder.
  2. Rerun `parse_holdings.py`.
  3. Add any new trades to `trades.json` by hand.
  4. Rerun `build_site.py`.
  5. Update the snapshot and date-range text in `I18N.intro` (zh and en) and in the README if they changed.
