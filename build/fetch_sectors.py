"""Download GICS sector data for the S&P 500 and the S&P/TSX Composite, and classify the
SMPT equity holdings by GICS sector.

Usage: python build/fetch_sectors.py
Needs network access, `yfinance`, `requests` and `beautifulsoup4`. Writes data/sectors.json:
  asof       date the data was fetched
  idx        {US|CA: {name, ccy, n, sectors: {sector: {cap, n, r1, top: [{t, name, cap, r1, keys}]}}}}
             cap is total market cap (not float-adjusted) in the index currency; r1 is the
             cap-weighted 1-year total return of the sector's members; top is the 15 largest.
             keys lists every share class of the company ('US:GOOGL', 'US:GOOG') to match holdings.
  hold       {ticker: {g: sector, key: 'US:BRK.B' if an index member else null, r1}}
             for every equity holding in data/holdings.json.
  missing    tickers without market cap or sector data
Constituents and GICS sectors come from Wikipedia; market caps and prices from Yahoo Finance.
"""
import datetime as dt
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import bs4
import requests
import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {'User-Agent': 'SMPT-course-project/1.0 (educational)'}
GICS = ['Information Technology', 'Financials', 'Health Care', 'Consumer Discretionary', 'Communication Services',
        'Industrials', 'Consumer Staples', 'Energy', 'Materials', 'Utilities', 'Real Estate']
# Yahoo's sector names -> GICS, for holdings that are in neither index (mostly ADRs).
YAHOO_GICS = {'Technology': 'Information Technology', 'Financial Services': 'Financials', 'Healthcare': 'Health Care',
              'Consumer Cyclical': 'Consumer Discretionary', 'Consumer Defensive': 'Consumer Staples',
              'Communication Services': 'Communication Services', 'Industrials': 'Industrials', 'Energy': 'Energy',
              'Basic Materials': 'Materials', 'Utilities': 'Utilities', 'Real Estate': 'Real Estate'}
# Holdings Yahoo can't classify (e.g. warrants), set by hand.
HOLD_OVERRIDE = {'CSST.WT': 'Information Technology'}
RENAME = {'FI': 'FISV', 'ERJ': 'EMBJ'}  # same as build_site.py
YF_HOLD = {'BRKB': 'BRK-B', 'CSST.WT': None}
EQUITY = ('Canadian Common', 'United States', 'American Depos', 'Real Estate')
TOP = 15


def wiki_table(url, first_header):
    soup = bs4.BeautifulSoup(requests.get(url, headers=UA, timeout=30).text, 'html.parser')
    for t in soup.find_all('table'):
        rows = [[c.get_text(' ', strip=True) for c in tr.find_all(['th', 'td'])] for tr in t.find_all('tr')]
        if rows and rows[0] and rows[0][0] == first_header:
            return rows
    raise SystemExit(f'No table starting with {first_header!r} at {url}')


def yahoo(t, mkt):
    return t.replace('.', '-') + ('.TO' if mkt == 'CA' else '')


def caps(syms):
    """Market caps, retrying failures with a pause: Yahoo rate-limits bursts of requests."""
    def one(s):
        try:
            return s, yf.Ticker(s).fast_info['market_cap']
        except Exception:
            return s, None
    out, todo = {}, list(syms)
    for attempt in range(4):
        with ThreadPoolExecutor(4) as ex:
            got = dict(ex.map(one, todo))
        out.update({k: v for k, v in got.items() if v})
        todo = [k for k, v in got.items() if not v]
        if not todo:
            break
        print(f'  {len(todo)} failed, retrying in {30 * (attempt + 1)}s…')
        time.sleep(30 * (attempt + 1))
    return out


def yahoo_sector(s):
    for attempt in range(3):
        try:
            return YAHOO_GICS.get(yf.Ticker(s).info.get('sector'))
        except Exception:
            time.sleep(20 * (attempt + 1))
    return None


def returns_1y(syms):
    start = (dt.date.today() - dt.timedelta(days=372)).isoformat()
    df = yf.download(list(syms), start=start, interval='1d', auto_adjust=True, progress=False, threads=True)['Close']
    out = {}
    for s in syms:
        c = df[s].dropna() if s in df else None
        if c is not None and len(c) > 200:
            out[s] = float(c.iloc[-1] / c.iloc[0] - 1)
    return out


def norm_sector(s):
    s = re.sub(r'\s*\[.*?\]', '', s).strip()
    return {'Information technology': 'Information Technology', 'Health care': 'Health Care',
            'Consumer discretionary': 'Consumer Discretionary', 'Communication services': 'Communication Services',
            'Consumer staples': 'Consumer Staples', 'Real estate': 'Real Estate', 'Healthcare': 'Health Care'}.get(s, s)


# --- index constituents -------------------------------------------------------------
sp = wiki_table('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', 'Symbol')
h = sp[0]
members = {'US': [dict(t=r[h.index('Symbol')], name=r[h.index('Security')], g=norm_sector(r[h.index('GICS Sector')]),
                       co=r[h.index('CIK')]) for r in sp[1:]]}
tsx = wiki_table('https://en.wikipedia.org/wiki/S%26P/TSX_Composite_Index', 'Ticker')
h = [norm_sector(x) for x in tsx[0]]
gi = next(i for i, x in enumerate(h) if x.startswith('Sector'))
members['CA'] = [dict(t=r[0], name=r[1], g=norm_sector(r[gi]), co=re.sub(r'\s*\(?class [a-z]\)?|\s+inc\.?$', '', r[1].lower()))
                 for r in tsx[1:] if len(r) > gi]
for mkt, ms in members.items():
    bad = {m['g'] for m in ms} - set(GICS)
    if bad:
        print(f'WARNING {mkt}: unknown sectors {bad}')

# --- holdings -----------------------------------------------------------------------
holdings = json.load(open(os.path.join(ROOT, 'data', 'holdings.json')))
hold = {}
for snap in holdings.values():
    for x in snap['hold']:
        if any(k in (x['sec'] or '') for k in EQUITY):
            t = RENAME.get(x['sym'], x['sym'])
            hold[t] = 'CA' if ('Canadian' in x['sec'] or 'Real Estate' in x['sec']) else 'US'
idx_by_key = {(mkt, m['t'].replace('.', '').replace('-', '')): m for mkt, ms in members.items() for m in ms}

# --- market data --------------------------------------------------------------------
syms = {yahoo(m['t'], mkt): (mkt, m) for mkt, ms in members.items() for m in ms}
print(f'fetching market caps for {len(syms)} constituents…')
cap = caps(syms)
hold_yf = {t: YF_HOLD.get(t, yahoo(t, mkt)) for t, mkt in hold.items()}
print('fetching 1-year returns…')
r1 = returns_1y(set(syms) | {s for s in hold_yf.values() if s})

out = dict(asof=dt.date.today().isoformat(), gics=GICS, idx={}, hold={}, missing=[])
for mkt, name, ccy in (('US', 'S&P 500', 'USD'), ('CA', 'S&P/TSX Composite', 'CAD')):
    # one row per company (share classes such as GOOG/GOOGL report the same company cap)
    best, classes = {}, {}
    for m in members[mkt]:
        classes.setdefault(m['co'], []).append(f'{mkt}:{m["t"]}')
        s = yahoo(m['t'], mkt)
        if not cap.get(s):
            out['missing'].append(s)
            continue
        if m['co'] not in best or cap[s] > best[m['co']][1]:
            best[m['co']] = (m, cap[s], r1.get(s))
    sectors = {}
    for m, c, r in best.values():
        g = sectors.setdefault(m['g'], dict(cap=0, n=0, rc=0, rw=0, rows=[]))
        g['cap'] += c
        g['n'] += 1
        if r is not None:
            g['rc'] += c * r
            g['rw'] += c
        g['rows'].append(dict(t=m['t'], name=m['name'], cap=round(c / 1e9, 2), r1=None if r is None else round(r, 4),
                              keys=classes[m['co']]))
    out['idx'][mkt] = dict(name=name, ccy=ccy, n=len(best), sectors={
        g: dict(cap=round(v['cap'] / 1e9, 2), n=v['n'], r1=round(v['rc'] / v['rw'], 4) if v['rw'] else None,
                top=sorted(v['rows'], key=lambda x: -x['cap'])[:TOP]) for g, v in sectors.items()})

for t, mkt in sorted(hold.items()):
    m = idx_by_key.get((mkt, t.replace('.', '').replace('-', '')))
    g, key = (m['g'], f'{mkt}:{m["t"]}') if m else (HOLD_OVERRIDE.get(t), None)
    s = hold_yf[t]
    if not g and s:
        g = yahoo_sector(s)
    if not g:
        out['missing'].append(t)
        print(f'WARNING: no sector for holding {t}; add it to HOLD_OVERRIDE')
    out['hold'][t] = dict(g=g, key=key, r1=None if s not in r1 else round(r1[s], 4))

json.dump(out, open(os.path.join(ROOT, 'data', 'sectors.json'), 'w'), ensure_ascii=False, indent=1)
for mkt, v in out['idx'].items():
    tot = sum(s['cap'] for s in v['sectors'].values())
    print(mkt, v['name'], v['n'], 'companies;',
          ', '.join(f'{g[:12]} {s["cap"] / tot:.1%}' for g, s in sorted(v['sectors'].items(), key=lambda x: -x[1]['cap'])))
print('holdings:', len(out['hold']), '; missing:', out['missing'])
