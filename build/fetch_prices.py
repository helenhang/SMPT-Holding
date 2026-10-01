"""Download daily prices for every ticker in a BUY/SELL trade, plus the benchmark ETFs.

Usage: python build/fetch_prices.py
Needs network access and `yfinance`. Writes data/prices.json:
  {start, end, bench: {CA: sym, US: sym},
   px: {ticker: {yf, mkt: 'CA'|'US', d: [YYYY-MM-DD...], c: [adjusted close...]}},
   missing: [ticker...]}
Closes are adjusted for splits and dividends, so returns are total returns in the
listing currency. TSX-listed tickers are compared with XIC.TO and everything else
(US stocks and ADRs) with SPY, both in their own currency.
"""
import json
import os
import re
from datetime import date

import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = {'CA': 'XIC.TO', 'US': 'SPY'}
ALIAS = {'FI': 'FISV', 'ERJ': 'EMBJ'}  # same as RENAME in build_site.py
# Tickers that never appear in a snapshot, so their listing can't be read from the holdings.
CA_TRADE_ONLY = {'XIU', 'XEG', 'CM', 'AC', 'BCE', 'SOBO', 'BEP.UN', 'GRT.UN', 'LMN'}
# Yahoo symbols that don't follow the default rule; several candidates are tried in order.
YF_OVERRIDE = {'LMN': ['LMN.V'], 'FISV': ['FISV', 'FI'], 'EMBJ': ['EMBJ', 'ERJ'], 'BRKB': ['BRK-B']}
CA_CLASSES = ('Canadian Common', 'Real Estate')


def candidates(tk, mkt):
    if tk in YF_OVERRIDE:
        return YF_OVERRIDE[tk]
    return [tk.replace('.', '-') + '.TO'] if mkt == 'CA' else [tk.replace('.', '-')]


def fetch(sym, start):
    df = yf.download(sym, start=start, interval='1d', auto_adjust=True, progress=False)
    if df is None or df.empty:
        return None
    s = df['Close'].squeeze().dropna()
    return dict(d=[i.strftime('%Y-%m-%d') for i in s.index], c=[round(float(v), 4) for v in s.values])


trades = json.load(open(os.path.join(ROOT, 'data', 'trades.json')))
holdings = json.load(open(os.path.join(ROOT, 'data', 'holdings.json')))
ca = {h['sym'] for snap in holdings.values() for h in snap['hold'] if any(k in h['sec'] for k in CA_CLASSES)}
ca = {ALIAS.get(t, t) for t in ca} | CA_TRADE_ONLY

tickers = set()
for t in trades:
    if t['action'] not in ('BUY', 'SELL'):
        continue
    for k in (x.strip() for x in t['ticker'].split('/')):
        if k and k != '—' and not re.fullmatch(r'\d\w+', k):  # skip bond codes like 5CPMFB0
            tickers.add(ALIAS.get(k, k))

start = min(t['date'][:7] for t in trades if re.match(r'\d{4}-\d{2}', t['date'])) + '-01'
out = dict(start=start, end=date.today().isoformat(), bench=BENCH, px={}, missing=[])
for mkt, sym in BENCH.items():
    out['px'][sym] = dict(yf=sym, mkt=mkt, **fetch(sym, start))
for tk in sorted(tickers):
    mkt = 'CA' if tk in ca else 'US'
    got = None
    for sym in candidates(tk, mkt):
        got = fetch(sym, start)
        if got:
            break
    if got:
        out['px'][tk] = dict(yf=sym, mkt=mkt, **got)
        print(f'{tk:8} {sym:10} {mkt} {got["d"][0]} → {got["d"][-1]} ({len(got["d"])} days)')
    else:
        out['missing'].append(tk)
        print(f'{tk:8} MISSING (tried {candidates(tk, mkt)})')

json.dump(out, open(os.path.join(ROOT, 'data', 'prices.json'), 'w'), separators=(',', ':'))
print(f'{len(out["px"])} series, {len(out["missing"])} missing: {out["missing"]}')
