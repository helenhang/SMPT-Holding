"""Download annual financial statements for every equity in the latest holdings snapshot,
for the DCF calculator.

Usage: python build/fetch_fundamentals.py
Needs network access and `yfinance`. Writes data/fundamentals.json:
  asof    date the data was fetched
  co      {ticker: {yf, name, kind, ccy, fccy, fx, px, mcap, beta, ind, gics (Yahoo's sector as GICS), fye,
                    y: [fiscal year end...] oldest first, and per year (millions of fccy):
                    rev, ebit, da, capex, dnwc, tax (effective rate), int,
                    ni (net income to common), bv (common equity at year end),
                    div (common dividends paid), bb (net share buybacks), for the DDM / residual income model,
                    nwc (operating working capital: current assets less cash, minus current
                         liabilities less short-term debt and leases),
                    debt, cash, lease (lease liabilities, included in debt) from the latest balance
                         sheet, millions of fccy; usgaap: domiciled in the US, so operating lease
                         cost sits in operating income and the page leaves leases out of debt,
                    est: [growth this fiscal year, next fiscal year] from analysts, or null
                         (always null for statements outside USD/CAD)}}
  missing tickers Yahoo returned no statements for
The stocks in data/watchlist.json are included too, marked watch: true.
kind is 'op' (operating company, FCFF DCF applies), 'fin' (bank, insurer, asset manager)
or 'reit'. ccy is the listing currency of px; fccy the currency of the statements; fx
converts one unit of fccy into ccy. capex is positive; dnwc is the increase in net working
capital (a cash outflow when positive).
"""
import datetime as dt
import json
import math
import os
import time

import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENAME = {'FI': 'FISV', 'ERJ': 'EMBJ'}  # same as build_site.py
YF_HOLD = {'BRKB': 'BRK-B', 'CSST.WT': None}  # same as fetch_sectors.py
EQUITY = ('Canadian Common', 'United States', 'American Depos', 'Real Estate')
FIN = ('Banks', 'Insurance', 'Capital Markets', 'Asset Management', 'Mortgage Finance')
YEARS = 4
# Yahoo's sector names -> GICS, same as fetch_sectors.py; used to group stocks that aren't in sectors.json
YAHOO_GICS = {'Technology': 'Information Technology', 'Financial Services': 'Financials', 'Healthcare': 'Health Care',
              'Consumer Cyclical': 'Consumer Discretionary', 'Consumer Defensive': 'Consumer Staples',
              'Communication Services': 'Communication Services', 'Industrials': 'Industrials', 'Energy': 'Energy',
              'Basic Materials': 'Materials', 'Utilities': 'Utilities', 'Real Estate': 'Real Estate'}


def yahoo(t, mkt):
    return t.replace('.', '-') + ('.TO' if mkt == 'CA' else '')


def retry(f, tries=3):
    for attempt in range(tries):
        try:
            return f()
        except Exception as e:
            if attempt == tries - 1:
                print(f'  failed: {e}')
                return None
            time.sleep(15 * (attempt + 1))


def row(df, *names):
    """{year: value in millions}, each year taken from the first of `names` that has it:
    Yahoo often leaves single years of a line item blank."""
    out = {}
    for n in names:
        if df is not None and n in df.index:
            for c, v in df.loc[n].items():
                if v == v and c.year not in out:
                    out[c.year] = float(v) / 1e6
    return out


def kind_of(info, sec):
    if 'Real Estate' in sec or 'REIT' in (info.get('industry') or ''):
        return 'reit'
    if any(k in (info.get('industry') or '') for k in FIN):
        return 'fin'
    return 'op'


def fx_rate(f, c, cache={}):
    if f == c:
        return 1.0
    if (f, c) not in cache:
        h = retry(lambda: yf.Ticker(f'{f}{c}=X').history(period='5d'))
        cache[f, c] = float(h['Close'].dropna().iloc[-1]) if h is not None and len(h) else None
    return cache[f, c]


holdings = json.load(open(os.path.join(ROOT, 'data', 'holdings.json')))
last = holdings[max(holdings)]
hold = {}
for x in last['hold']:
    if any(k in (x['sec'] or '') for k in EQUITY) and x['mv']:
        t = RENAME.get(x['sym'], x['sym'])
        hold[t] = (YF_HOLD.get(t, yahoo(t, 'CA' if ('Canadian' in x['sec'] or 'Real Estate' in x['sec']) else 'US')), x['sec'])

out = dict(asof=dt.date.today().isoformat(), snap=max(holdings), co={}, missing=[])
# Stocks the team is researching but doesn't hold (data/watchlist.json, maintained by hand).
watch = {w['t']: (w.get('yf', w['t']), 'Watchlist') for w in json.load(open(os.path.join(ROOT, 'data', 'watchlist.json')))
         if w['t'] not in hold}
hold.update(watch)
for t, (s, sec) in sorted(hold.items()):
    if not s:
        out['missing'].append(t)
        continue
    tk = yf.Ticker(s)
    info = retry(lambda: tk.info) or {}
    inc, cf, bs = retry(lambda: tk.income_stmt), retry(lambda: tk.cashflow), retry(lambda: tk.balance_sheet)
    rev = row(inc, 'Total Revenue', 'Operating Revenue')
    if not rev or not info.get('currentPrice'):
        out['missing'].append(t)
        print(f'{t:8} {s:10} MISSING')
        continue
    years = sorted(rev)[-YEARS:]
    ebit = row(inc, 'Operating Income', 'EBIT')
    da = row(cf, 'Depreciation Amortization Depletion', 'Depreciation And Amortization')
    da = {**row(inc, 'Reconciled Depreciation'), **da}
    capex = row(cf, 'Capital Expenditure', 'Purchase Of PPE')
    wc = row(cf, 'Change In Working Capital')
    taxp, pre = row(inc, 'Tax Provision'), row(inc, 'Pretax Income')
    intr = row(inc, 'Interest Expense', 'Interest Expense Non Operating')
    ni, bv = row(inc, 'Net Income Common Stockholders', 'Net Income'), row(bs, 'Common Stock Equity', 'Stockholders Equity')
    div = row(cf, 'Common Stock Dividend Paid', 'Cash Dividends Paid')
    bb = {y: max(0.0, -v) for y, v in row(cf, 'Net Common Stock Issuance', 'Repurchase Of Capital Stock').items()}
    # operating working capital: current assets less cash, minus current liabilities less short-term debt and leases
    ca, cl = row(bs, 'Current Assets'), row(bs, 'Current Liabilities')
    stc = row(bs, 'Cash Cash Equivalents And Short Term Investments', 'Cash And Cash Equivalents')
    std = row(bs, 'Current Debt And Capital Lease Obligation', 'Current Debt')
    nwc = {y: (ca[y] - stc.get(y, 0)) - (cl[y] - std.get(y, 0)) for y in ca if y in cl}
    lease = row(bs, 'Capital Lease Obligations')
    debt, cash = row(bs, 'Total Debt'), row(bs, 'Cash Cash Equivalents And Short Term Investments', 'Cash And Cash Equivalents')
    ly = max(debt) if debt else None
    r = lambda d, y, f=lambda v: v: None if d.get(y) is None or math.isnan(d[y]) else round(f(d[y]), 2)
    est = None
    re_ = retry(lambda: tk.revenue_estimate)
    # Yahoo's estimates for ADRs mix the listing and reporting currencies, so skip them.
    if re_ is not None and 'growth' in re_.columns and info.get('financialCurrency') in (None, 'USD', 'CAD'):
        g = [re_.loc[k, 'growth'] if k in re_.index else None for k in ('0y', '+1y')]
        est = [None if v is None or v != v else round(float(v), 4) for v in g]
    ccy, fccy = info.get('currency'), info.get('financialCurrency') or info.get('currency')
    fye = dt.date.fromisoformat(str(inc.columns[0])[:10]).isoformat() if inc is not None else None
    out['co'][t] = dict(
        yf=s, name=info.get('longName') or info.get('shortName'), kind=kind_of(info, sec), ind=info.get('industry'),
        gics=YAHOO_GICS.get(info.get('sector')),
        ccy=ccy, fccy=fccy, fx=fx_rate(fccy, ccy), px=info['currentPrice'],
        mcap=round(info['marketCap'] / 1e6, 1) if info.get('marketCap') else None,
        beta=info.get('beta'), fye=fye, y=years,
        rev=[r(rev, y) for y in years], ebit=[r(ebit, y) for y in years], da=[r(da, y) for y in years],
        capex=[r(capex, y, abs) for y in years], dnwc=[r(wc, y, lambda v: -v) for y in years],
        tax=[round(taxp[y] / pre[y], 4) if taxp.get(y) is not None and pre.get(y) else None for y in years],
        int=[r(intr, y, abs) for y in years],
        ni=[r(ni, y) for y in years], bv=[r(bv, y) for y in years], div=[r(div, y, abs) for y in years], bb=[r(bb, y) for y in years],
        nwc=[r(nwc, y) for y in years], lease=r(lease, ly) if ly and lease.get(ly) is not None else 0,
        usgaap=info.get('country') == 'United States',
        debt=r(debt, ly) if ly else 0, cash=r(cash, ly) if ly else 0, est=est)
    c = out['co'][t]
    if t in watch:
        c['watch'] = True
    print(f'{t:8} {s:10} {c["kind"]:4} {fccy}->{ccy} rev {c["rev"][-1]:>12,.0f}  est {est}')
    time.sleep(0.5)

json.dump(out, open(os.path.join(ROOT, 'data', 'fundamentals.json'), 'w'), ensure_ascii=False, indent=1)
print(f'{len(out["co"])} companies; missing: {out["missing"]}')
