"""Build index.html from data/holdings.json, data/trades.json and build/template.html.

Usage: python build/build_site.py
"""
import bisect
import json
import os
import re
import datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENAME = {'FI': 'FISV', 'ERJ': 'EMBJ'}  # ticker changes, merged into one row
ORDER = ['Cash', 'Money Market', 'Bonds', 'REITs', 'Canadian Equity', 'US Equity', 'ADRs (Intl)']
# Industry codes from the holdings workbooks; the spelling varies between workbooks, so merge the aliases.
IND_ALIAS = {'BONDS': 'BOND', 'PHARM': 'PHRM', 'NBNK': 'NBF', 'RTLT': 'RTLR'}
IND_BY_CLASS = {'Cash': 'CASH', 'Money Market': 'MMF', 'Bonds': 'BOND', 'REITs': 'REIT'}
# Tickers that only appear in trades.json (never in a snapshot), classified by hand
# with the same codes the workbooks use for their peers.
TRADE_IND = {'XIU': 'ETF', 'IVV': 'ETF', 'QQQ': 'ETF', 'FLIN': 'ETF', 'XEG': 'O&G',
             'C': 'BANK', 'CM': 'BANK', 'AC': 'TRANS', 'MMM': 'INDS', 'RNMBY': 'INDS', 'BA': 'INDS',
             'LMN': 'TECH', 'FL': 'RTLR', 'TGT': 'RTLR', 'PG': 'DISC', 'BCE': 'TCOM',
             'SOBO': 'TRANS', 'BEP.UN': 'UTIL', 'GRT.UN': 'REIT'}


# Asset-class rows of sheet 09 "Return compared to index" -> short key (named in I18N.pcls)
# and a short name for its representative index.
PERF_CLASS = [('Cash', 'CASH', '—'), ('CAD Dollar Money', 'MMF_CA', 'RBF 2010'), ('US Dollar Money', 'MMF_US', 'RBF 2014'),
              ('CAD Dollar Bond', 'BOND_CA', 'XBB'), ('US Bond', 'BOND_US', 'AGG'),
              ('Canadian Common', 'EQ_CA', 'S&P/TSX'), ('US Common', 'EQ_US', 'S&P 500'),
              ('Chinese', 'ADR_CN', 'Hang Seng'), ('Indian', 'ADR_IN', 'NIFTY 50'), ('Japanese', 'ADR_JP', 'Nikkei 225'),
              ('European', 'ADR_EU', 'DAX / CAC 40'), ('Taiwan', 'ADR_TW', 'TAIEX'), ('Brazil', 'ADR_BR', 'IBOVESPA'),
              ('Korean', 'ADR_KR', 'KOSPI')]


def perf_class(name):
    for key, code, index in PERF_CLASS:
        if key in name:
            return code, index
    print(f'WARNING: unknown class in sheet 09: {name!r}; add it to PERF_CLASS')
    return name, ''


def industry(code, cls):
    code = IND_ALIAS.get(code, code)
    return code or IND_BY_CLASS.get(cls, 'OTHER')


def asset_class(section):
    s = section.split('. ', 1)[1] if '. ' in section else section
    if 'Cash' in s: return 'Cash'
    if 'Money Market' in s: return 'Money Market'
    if 'Bond' in s: return 'Bonds'
    if 'REIT' in s or 'Real Estate' in s: return 'REITs'
    if 'Canadian Common' in s: return 'Canadian Equity'
    if 'United States' in s: return 'US Equity'
    return 'ADRs (Intl)'


holdings = json.load(open(os.path.join(ROOT, 'data', 'holdings.json')))
trades = json.load(open(os.path.join(ROOT, 'data', 'trades.json')))

snaps = []
for date, snap in holdings.items():
    rows = []
    for h in snap['hold']:
        name = h['name'].title() if h['name'].isupper() else h['name']
        cls = asset_class(h['sec'])
        rows.append(dict(t=RENAME.get(h['sym'], h['sym']), n=name, c=cls, i=industry(h.get('ind'), cls),
                         q=h['qty'], v=round(h['mv'] or 0, 2)))
    total = sum(r['v'] for r in rows)
    if abs(total - snap['total']) > 1:
        print(f'WARNING {date}: rows sum to {total:,.2f} but the workbook total is {snap["total"]:,.2f}')
    snaps.append(dict(date=date, total=round(snap['total'], 2), h=rows))

# Industry per ticker for trades and the drawer: the latest snapshot that holds it wins.
tick_ind = dict(TRADE_IND)
for snap in snaps:
    tick_ind.update({r['t']: r['i'] for r in snap['h']})
for t in trades:
    for k in (x.strip() for x in t['ticker'].split('/')):
        if k and k not in ('—', 'CAD') and RENAME.get(k, k) not in tick_ind:
            print(f'WARNING: no industry for trade ticker {k}; add it to TRADE_IND')

perf_path = os.path.join(ROOT, 'data', 'performance.json')
perf = json.load(open(perf_path)) if os.path.exists(perf_path) else dict(hpr=dict(start=None, rows=[]), periods=[])
rd = lambda v: None if v is None else round(v, 6)
site_perf = dict(
    start=perf['hpr']['start'],
    hpr=[dict(e=r['end'], m=r['months'], s=rd(r['smpt']), t=rd(r['tsx']), p=rd(r['sp'])) for r in perf['hpr']['rows']],
    per=[dict(snap=p['snapshot'], a=p['start'], b=p['end'], hpr=rd(p.get('hpr')), gross=rd(p.get('hpr_gross')),
              bench=rd(p.get('bench')), abn=rd(p.get('abn')), abn_ann=rd(p.get('abn_ann')),
              beta=p['beta'] and dict(h=rd(p['beta']['hist']), x=rd(p['beta']['exp'])),
              cls=[dict(zip(('k', 'ix'), perf_class(c['cls'])), w=rd(c['w']), r=rd(c['ret'])) for c in p['classes']])
         for p in perf['periods']])

# Post-proposal performance, from data/prices.json (see fetch_prices.py). One entry per ticker
# in a BUY/SELL trade: total return of the stock and of its market's benchmark ETF from the
# first trading day on or after the proposal date, over each horizon in HORIZONS (months).
HORIZONS = [3, 6, 12]
px_path = os.path.join(ROOT, 'data', 'prices.json')
prices = json.load(open(px_path)) if os.path.exists(px_path) else dict(px={}, bench={}, missing=[])
PX = prices['px']


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, min(d.day, 28))


def at(series, day):
    """Index of the first trading day on or after `day`, or None past the end."""
    i = bisect.bisect_left(series['d'], day.isoformat())
    return i if i < len(series['d']) else None


def post_perf(t):
    m = re.match(r'(\d{4})-(\d{2})(?:-(\d{2}))?', t['date'])
    if t['action'] not in ('BUY', 'SELL') or not m:
        return None
    approx = m.group(3) is None  # month only: measure from mid-month
    d0 = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 15))
    legs = []
    for k in (x.strip() for x in t['ticker'].split('/')):
        k = RENAME.get(k, k)
        if not k or k == '—' or re.fullmatch(r'\d\w+', k):
            continue
        if k not in PX:
            legs.append(dict(k=k, na=1))
            continue
        s, b = PX[k], PX[prices['bench'][PX[k]['mkt']]]
        i0, j0 = at(s, d0), at(b, d0)
        if i0 is None or j0 is None:
            legs.append(dict(k=k, na=1))
            continue
        h = {}
        for n in HORIZONS:
            d1 = add_months(dt.date.fromisoformat(s['d'][i0]), n)
            i1, j1 = at(s, d1), at(b, d1)
            if i1 is not None and j1 is not None:
                h[str(n)] = [round(s['c'][i1] / s['c'][i0] - 1, 4), round(b['c'][j1] / b['c'][j0] - 1, 4)]
        h['now'] = [round(s['c'][-1] / s['c'][i0] - 1, 4), round(b['c'][-1] / b['c'][j0] - 1, 4),
                    (dt.date.fromisoformat(s['d'][-1]) - dt.date.fromisoformat(s['d'][i0])).days]
        legs.append(dict(k=k, mkt=s['mkt'], d0=s['d'][i0], h=h))
    return dict(approx=approx, legs=legs) if legs else None


def weekly(series):
    """Friday closes (carrying the last close forward) for the drawer's price chart."""
    d = dt.date.fromisoformat(series['d'][0])
    d += dt.timedelta(days=(4 - d.weekday()) % 7)
    end, out, i = dt.date.fromisoformat(series['d'][-1]), [], 0
    while d <= end:
        while i + 1 < len(series['d']) and series['d'][i + 1] <= d.isoformat():
            i += 1
        out.append(float(f'{series["c"][i]:.4g}'))
        d += dt.timedelta(days=7)
    first = dt.date.fromisoformat(series['d'][0])
    return dict(s=(first + dt.timedelta(days=(4 - first.weekday()) % 7)).isoformat(), c=out, mkt=series['mkt'])


site_px = dict(bench=prices['bench'], end=prices.get('end'), missing=prices['missing'],
               w={k: weekly(v) for k, v in PX.items()})

# GICS sector data (see fetch_sectors.py): index sector caps and top 15 per sector, and the
# sector of each equity holding.
sec_path = os.path.join(ROOT, 'data', 'sectors.json')
site_sec = None
if os.path.exists(sec_path):
    sec = json.load(open(sec_path))
    site_sec = dict(asof=sec['asof'], gics=sec['gics'], idx=sec['idx'], hold=sec['hold'])
    for snap in snaps:
        for r in snap['h']:
            if r['v'] and r['c'] not in IND_BY_CLASS and r['t'] not in sec['hold']:
                print(f'WARNING: {snap["date"]} {r["t"]} has no GICS sector; rerun fetch_sectors.py')

site_trades = [dict(date=t['date'], yr=t['year'], rep=t['report'], act=t['action'], tk=t['ticker'],
                    sec=t['security'], prop=t['proposed'], act_=t['actual'], ex=t['executed'], note=t['note'],
                    pp=post_perf(t))
               for t in trades]

data = json.dumps(dict(snaps=snaps, trades=site_trades, classes=ORDER, tind=tick_ind, perf=site_perf, px=site_px, sec=site_sec), separators=(',', ':'), ensure_ascii=False)
html = open(os.path.join(ROOT, 'build', 'template.html')).read().replace('__DATA__', data)
open(os.path.join(ROOT, 'index.html'), 'w').write(html)
print(f'index.html: {len(snaps)} snapshots, {len(site_trades)} trades')
