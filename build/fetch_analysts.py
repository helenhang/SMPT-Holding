"""Download sell-side analyst price targets and ratings for every equity in the latest
holdings snapshot.

Also covers the stocks in data/watchlist.json.

Usage: python build/fetch_analysts.py
Needs network access and `yfinance`. Writes data/analysts.json:
  asof    date the data was fetched
  co      {ticker: {n, mean, median, high, low, rec: [strong buy, buy, hold, sell, strong sell],
                    firms: [{f, d, g, pt, prev}] newest first}}
Targets are in the listing currency. `firms` keeps each firm's latest action within the last
MONTHS months that carries a price target, for US listings and ADRs only: for TSX names Yahoo
returns the firms' targets for the US listing in USD, which don't match the CAD price, so TSX
names keep the consensus (which is in CAD) alone.
"""
import datetime as dt
import json
import os
import time

import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENAME = {'FI': 'FISV', 'ERJ': 'EMBJ'}  # same as build_site.py
YF_HOLD = {'BRKB': 'BRK-B', 'CSST.WT': None}  # same as fetch_sectors.py
EQUITY = ('Canadian Common', 'United States', 'American Depos', 'Real Estate')
MONTHS = 12


def yahoo(t, ca):
    return t.replace('.', '-') + ('.TO' if ca else '')


def retry(f, tries=3):
    for attempt in range(tries):
        try:
            return f()
        except Exception as e:
            if attempt == tries - 1:
                print(f'  failed: {e}')
                return None
            time.sleep(15 * (attempt + 1))


def num(v):
    return None if v is None or v != v else round(float(v), 2)


holdings = json.load(open(os.path.join(ROOT, 'data', 'holdings.json')))
last = holdings[max(holdings)]
cutoff = dt.datetime.now() - dt.timedelta(days=round(MONTHS * 30.44))
out = dict(asof=dt.date.today().isoformat(), months=MONTHS, co={}, missing=[])
todo = {}
for x in last['hold']:
    if any(k in (x['sec'] or '') for k in EQUITY) and x['mv']:
        t = RENAME.get(x['sym'], x['sym'])
        todo[t] = YF_HOLD.get(t, yahoo(t, 'Canadian' in x['sec'] or 'Real Estate' in x['sec']))
for w in json.load(open(os.path.join(ROOT, 'data', 'watchlist.json'))):  # stocks under research, not held
    todo.setdefault(w['t'], w.get('yf', w['t']))
for t, s in todo.items():
    if not s:
        continue
    ca = s.endswith('.TO')
    tk = yf.Ticker(s)
    pt = retry(lambda: tk.analyst_price_targets) or {}
    if not pt.get('mean'):
        out['missing'].append(t)
        print(f'{t:8} {s:10} no targets')
        continue
    info = retry(lambda: tk.info) or {}
    rs = retry(lambda: tk.recommendations_summary)
    rec = None
    if rs is not None and len(rs):
        r0 = rs[rs['period'] == '0m'].iloc[0] if (rs['period'] == '0m').any() else rs.iloc[0]
        rec = [int(r0[k]) for k in ('strongBuy', 'buy', 'hold', 'sell', 'strongSell')]
    firms = {}
    ud = None if ca else retry(lambda: tk.upgrades_downgrades)
    if ud is not None and len(ud):
        for d, row in ud.sort_index(ascending=False).iterrows():
            d = d.to_pydatetime().replace(tzinfo=None)
            if d < cutoff:
                break
            if row['Firm'] in firms or not row.get('currentPriceTarget'):
                continue
            firms[row['Firm']] = dict(f=row['Firm'], d=d.date().isoformat(), g=row['ToGrade'] or None,
                                      pt=num(row['currentPriceTarget']), prev=num(row.get('priorPriceTarget')) or None)
    out['co'][t] = dict(n=info.get('numberOfAnalystOpinions'), mean=num(pt.get('mean')), median=num(pt.get('median')),
                        high=num(pt.get('high')), low=num(pt.get('low')), rec=rec, firms=list(firms.values()))
    print(f'{t:8} {s:10} median {pt.get("median")}  {len(firms)} firms')
    time.sleep(0.5)

json.dump(out, open(os.path.join(ROOT, 'data', 'analysts.json'), 'w'), ensure_ascii=False, indent=1)
print(f'{len(out["co"])} companies; no targets: {out["missing"]}')
