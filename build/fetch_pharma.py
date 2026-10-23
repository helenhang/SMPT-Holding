"""Download drug data for every drug maker in data/fundamentals.json (held or on the watchlist):
approved drugs (openFDA), revenue by product over five fiscal years (SEC 10-K / 20-F XBRL),
patent and exclusivity expiry (FDA Orange Book for small molecules, Purple Book for biologics)
and the clinical pipeline (ClinicalTrials.gov).

Usage: SEC_CONTACT=you@example.com python build/fetch_pharma.py
Needs network access and `requests`. SEC asks for a contact e-mail in the User-Agent; it is read
from SEC_CONTACT (a repository secret in GitHub Actions) and never written to the repo.
Raw responses are cached in build/.cache/ (gitignored) for 30 days, so reruns are cheap.
Writes data/pharma.json:
  asof    date the data was fetched
  co      {ticker: {rev, drugs, pipe, nT}}
    rev   {ccy, form, url, fy: [5 fiscal years], tot: [total revenue per year],
           p: [{n, a, b, v: [revenue per year]}]} or null when the filing has no product-level revenue
          (n = product as named in the filing, a = therapeutic area, b = matched approved brand)
    drugs [{b, g, a, y, tr, k, loe, src, gen}] approved prescription drugs: brand, generic, area,
          approval year, active trials for new uses, nda/bla, estimated loss of exclusivity (YYYY-MM-DD), what it rests on
          (substance patent / patent / exclusivity / 12-year biologic exclusivity) and the number of
          approved generics or biosimilars
    pipe  [{n, ph, a, c, k}] drugs in phase 2 or 3 that aren't approved yet: name, highest phase,
          area, top conditions, number of active trials
    nT    number of active trials sponsored by the company
  missing tickers whose fetch failed
"""
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pharma as ph  # noqa: E402  (vendored from stockscan)
import sec_revenue as sr  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'build', '.cache')
YEARS = 5
NAME_FIX = {'ELYEAHD': 'EYLEA HD'}  # typos in the filers' XBRL member names (REGN)


def match_brands(drugs, prods):
    """Each revenue line goes to at most one approved drug: full brand name first (Keytruda, not
    Keytruda Qlex), then the brand's first word, then the generic name."""
    def keys_of(d, level):
        if level == 0:
            return {ph._brand_key(d['brand'])}
        if level == 1:
            return {ph._norm(re.split(r'[\s(/-]', d['brand'])[0])}
        return {ph._drug_key(g) for g in (d.get('generic') or '').split(', ')}
    used = set()
    for level in range(3):
        for d in drugs:
            if 'rev' in d:
                continue
            ks = {k for k in keys_of(d, level) if len(k) >= 5}
            i = next((i for i, p in enumerate(prods) if i not in used and any(k in ph._norm(p['n']) for k in ks)), None)
            if i is not None:
                used.add(i)
                d['rev'] = prods[i]['v']
                prods[i]['b'] = d['brand']
                if not d.get('area') and prods[i]['a'] != 'other':
                    d['area'] = prods[i]['a']
                elif d.get('area') and prods[i]['a'] == 'other':
                    prods[i]['a'] = d['area']


def revenue(tk, co, have, idx):
    rv = sec.cached(tk)
    if not rv or rv.get('error') or not rv.get('years'):
        return None
    a = ph.allocate_revenue(rv, co, idx, sr.humanize) or {}
    if a.get('method') in (None, 'none', 'single'):
        return None  # no product-level split in the filing (e.g. ABBV)
    fys = sorted(rv['years'])[-YEARS:]
    num = lambda v: None if v is None else round(v / 1e6, 1)  # millions
    prods = [dict(n=NAME_FIX.get(name, name), a=area, b=None, v=[num(rv['years'][fy].get(mem)) for fy in fys])
             for name, _, area, mem in a['products']]
    return dict(ccy=rv['currency'], form=rv.get('form'), url=rv.get('url'), fy=fys,
                tot=[num(rv['totals'].get(fy)) for fy in fys], p=prods)


def loe_of(d):
    x = loe.lookup(d.get('app'), d['brand'])
    if not x:
        return {}
    if x['kind'] == 'bla':
        src = 'bla'
    elif x.get('loe') and x.get('loe') == x.get('excl') and x.get('loe') != (x.get('substance') or x.get('patent')):
        src = 'excl'
    else:
        src = 'sub' if x.get('substance') else 'pat'
    return dict(k=x['kind'], loe=x.get('loe'), src=src, gen=x.get('generics', x.get('biosimilars')))


fd = json.load(open(os.path.join(ROOT, 'data', 'fundamentals.json')))
todo = {t: c['name'] for t, c in fd['co'].items() if c.get('ind') in ph.PHARMA_INDUSTRIES}
print('drug makers:', ', '.join(todo))
store = ph.Pharma(os.path.join(CACHE, 'pharma'))
sec = sr.SecRevenue(os.path.join(CACHE, 'sec'))
loe = ph.LoeStore(os.path.join(CACHE, 'pharma'))
out = dict(asof=dt.date.today().isoformat(), co={}, missing=[])

companies = {}
for t, name in todo.items():
    try:
        companies[t] = store.company(t, name)
        if not sec.fresh(t):
            sec.fetch(t)
    except Exception as e:
        print(f'  {t} failed: {e}')
        out['missing'].append(t)

# Revenue lines the approved drugs don't explain: look up the FDA label for their area (once per brand)
idx = ph.brand_index(companies, store.label_areas())
for t, co in companies.items():
    rv = sec.cached(t)
    a = ph.allocate_revenue(rv, co, idx, sr.humanize) if rv and not rv.get('error') else None
    for name, _, area, *_ in (a or {}).get('products', []):
        word = ph.brand_query(name)
        if area == 'other' and word and not store.has_label(word):
            try:
                store.label(word, strict=True)
            except Exception:
                break  # FDA daily quota: try again next run
idx = ph.brand_index(companies, store.label_areas())

for t, co in companies.items():
    if co.get('error'):
        print(f'  {t}: {co["error"]}')
        out['missing'].append(t)
        continue
    drugs = [dict(d) for d in co.get('approved', [])]
    rev = revenue(t, co, companies, idx)
    if rev:
        match_brands(drugs, rev['p'])
    last = lambda d: (d.get('rev') or [None])[-1] or 0
    drugs.sort(key=lambda d: (-last(d), -int(d.get('approved') or 0)))
    out['co'][t] = dict(
        rev=rev,
        drugs=[dict(b=d['brand'], g=d.get('generic'), a=d.get('area'), y=(d.get('approved') or '')[:4] or None,
                    tr=d.get('newTrials', 0), **loe_of(d))
               for d in drugs],
        pipe=[dict(n=p['name'], ph=p['phase'], a=p.get('area'), c=(p.get('conditions') or [])[:3], k=p.get('n'))
              for p in sorted(co.get('pipeline', []), key=lambda p: (-p['phase'], -p.get('n', 0))) if p['phase'] >= 2],
        nT=co.get('nTrials'))
    print(f'  {t}: {len(drugs)} approved, {len(out["co"][t]["pipe"])} in phase 2-3, '
          + (f'{len(rev["p"])} revenue lines FY{rev["fy"][0]}-{rev["fy"][-1]}' if rev else 'no product revenue in the filing'))

out['missing'] = sorted(set(out['missing']))
with open(os.path.join(ROOT, 'data', 'pharma.json'), 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f'data/pharma.json: {len(out["co"])} companies, missing {out["missing"]}')
