"""Build index.html from data/holdings.json, data/trades.json and build/template.html.

Usage: python build/build_site.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENAME = {'FI': 'FISV', 'ERJ': 'EMBJ'}  # ticker changes, merged into one row
ORDER = ['Cash', 'Money Market', 'Bonds', 'REITs', 'Canadian Equity', 'US Equity', 'ADRs (Intl)']
# RBC industry codes; the spelling varies between workbooks, so merge the aliases.
IND_ALIAS = {'BONDS': 'BOND', 'PHARM': 'PHRM', 'NBNK': 'NBF', 'RTLT': 'RTLR'}
IND_BY_CLASS = {'Cash': 'CASH', 'Money Market': 'MMF', 'Bonds': 'BOND', 'REITs': 'REIT'}
# Tickers that only appear in trades.json (never in a snapshot), classified by hand
# with the same codes RBC uses for their peers.
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
        print(f'WARNING {date}: rows sum to {total:,.2f} but RBC total is {snap["total"]:,.2f}')
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

site_trades = [dict(date=t['date'], yr=t['year'], rep=t['report'], act=t['action'], tk=t['ticker'],
                    sec=t['security'], prop=t['proposed'], act_=t['actual'], ex=t['executed'], note=t['note'])
               for t in trades]

data = json.dumps(dict(snaps=snaps, trades=site_trades, classes=ORDER, tind=tick_ind, perf=site_perf), separators=(',', ':'), ensure_ascii=False)
html = open(os.path.join(ROOT, 'build', 'template.html')).read().replace('__DATA__', data)
open(os.path.join(ROOT, 'index.html'), 'w').write(html)
print(f'index.html: {len(snaps)} snapshots, {len(site_trades)} trades')
