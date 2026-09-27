"""Build index.html from data/holdings.json, data/trades.json and build/template.html.

Usage: python build/build_site.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENAME = {'FI': 'FISV', 'ERJ': 'EMBJ'}  # ticker changes, merged into one row
ORDER = ['Cash', 'Money Market', 'Bonds', 'REITs', 'Canadian Equity', 'US Equity', 'ADRs (Intl)']


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
        rows.append(dict(t=RENAME.get(h['sym'], h['sym']), n=name, c=asset_class(h['sec']),
                         q=h['qty'], v=round(h['mv'] or 0, 2)))
    total = sum(r['v'] for r in rows)
    if abs(total - snap['total']) > 1:
        print(f'WARNING {date}: rows sum to {total:,.2f} but RBC total is {snap["total"]:,.2f}')
    snaps.append(dict(date=date, total=round(snap['total'], 2), h=rows))

site_trades = [dict(date=t['date'], yr=t['year'], rep=t['report'], act=t['action'], tk=t['ticker'],
                    sec=t['security'], prop=t['proposed'], act_=t['actual'], ex=t['executed'], note=t['note'])
               for t in trades]

data = json.dumps(dict(snaps=snaps, trades=site_trades, classes=ORDER), separators=(',', ':'), ensure_ascii=False)
html = open(os.path.join(ROOT, 'build', 'template.html')).read().replace('__DATA__', data)
open(os.path.join(ROOT, 'index.html'), 'w').write(html)
print(f'index.html: {len(snaps)} snapshots, {len(site_trades)} trades')
