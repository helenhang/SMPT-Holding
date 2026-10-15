"""Extract a few key figures from Morningstar equity reports into data/research.json, and add
reported stocks the team doesn't hold to data/watchlist.json.

Usage: python build/parse_research.py [folder]     (default: research/morningstar)
Needs `pdftotext` (poppler). The PDFs are licensed content and stay out of git (*.pdf is
ignored); only these figures are written, never report text:
  src     'Morningstar', or 'Morningstar (Quant)' for algorithmic Quantitative reports
  date    date the fair value was set     ccy, fv   fair value in the page's listing currency
  stars   star rating on the report date  moat, unc  economic moat and uncertainty ratings
  wacc / coe   discount rate stated in the text, if any
When several reports cover one stock, the newest wins. A report priced in another currency
than the page (e.g. the NYSE line of a TSX holding) is converted only with a rate entered in
REPORT_FX, taken from the report itself; otherwise it is skipped with a WARNING.
"""
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENAME = {'FI': 'FISV', 'ERJ': 'EMBJ', 'BRK.B': 'BRKB'}  # report symbol -> page ticker
EQUITY = ('Canadian Common', 'United States', 'American Depos', 'Real Estate')
# ticker: (report currency, page currency, multiply by) — from the rate the report states.
# MFC: the NYSE report says "an exchange rate of CAD 0.72/USD 1", i.e. USD 0.72 per CAD.
REPORT_FX = {'MFC': ('USD', 'CAD', 1 / 0.72)}

WACC = re.compile(r"(?:WACC|weighted average cost of capital)(?: estimate)?(?: for [\w&.' -]{1,30}?)?(?: stands)? (?:at|of|to) (\d+(?:\.\d+)?) ?%", re.I)
COE = [re.compile(r'(\d+(?:\.\d+)?) ?% cost of equity', re.I),
       re.compile(r'cost of equity (?:estimate )?(?:of|at) (\d+(?:\.\d+)?) ?%', re.I)]


def day(s):
    return dt.datetime.strptime(' '.join(s.split()[:3]), '%d %b %Y').date().isoformat()


def parse(path):
    text = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True, check=True).stdout
    lines = [l for l in text.split('\n')[:14] if l.strip()]
    flat = re.sub(r'\s+', ' ', text)
    hdr = re.search(r'(?:Report as of|Release:) (\d+ \w+ \d{4}).*?Trading Currency: (\w+) \| Exchange: (.+?)\s{2,}', lines[0] + '  ')
    i, m = next((i, m) for i, l in enumerate(lines[1:4], 1) if (m := re.search(r'(?:^| )([A-Z][A-Z.]*) (Q+)q* \d', l.strip())))
    e = dict(src='Morningstar', report=day(hdr.group(1)), ccy=hdr.group(2), stars=len(m.group(2)), exch=hdr.group(3))
    sym = m.group(1)
    labels, values = re.split(r'\s{2,}', lines[i + 1].strip()), re.split(r'\s{2,}', lines[i + 2].strip())
    col = lambda name: next((values[k] for k, l in enumerate(labels) if l.startswith(name) and k < len(values)), None)
    e['moat'], e['unc'] = col('Economic Moat'), col('Uncertainty')
    if lines[0].startswith('Quantitative'):
        fv = re.search(r'quantitative fair value estimate of \$(\d[\d,]*(?:\.\d+)?)', flat)
        e.update(src='Morningstar (Quant)', fv=float(fv.group(1).replace(',', '')),
                 date=day(re.search(r'Fair Value data as of (\d+ \w+ \d{4})', flat).group(1)))
    else:
        fv, ccy = col('Fair Value Estimate').split()
        e.update(fv=float(fv.replace(',', '')), date=day(re.split(r'\s{2,}', lines[i + 3].strip())[1]))
        assert ccy == e['ccy'], (path, ccy, e['ccy'])
    if w := WACC.search(flat):
        e['wacc'] = float(w.group(1)) / 100
    if c := next((c for p in COE if (c := p.search(flat))), None):
        e['coe'] = float(c.group(1)) / 100
    return RENAME.get(sym, sym), e


folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'research', 'morningstar')
holdings = json.load(open(os.path.join(ROOT, 'data', 'holdings.json')))
held = {}
for x in holdings[max(holdings)]['hold']:
    if any(k in (x['sec'] or '') for k in EQUITY) and x['mv']:
        held[RENAME.get(x['sym'], x['sym'])] = 'CAD' if ('Canadian' in x['sec'] or 'Real Estate' in x['sec']) else 'USD'

best = {}
for path in sorted(glob.glob(os.path.join(folder, '*.pdf'))):
    try:
        t, e = parse(path)
    except Exception as err:
        print(f'WARNING: could not parse {os.path.basename(path)}: {err!r}')
        continue
    if t not in best or e['report'] > best[t]['report']:
        best[t] = e

wl_path = os.path.join(ROOT, 'data', 'watchlist.json')
watch = json.load(open(wl_path))
out, added = {}, []
for t, e in sorted(best.items()):
    page_ccy = held.get(t, e['ccy'])
    if e['ccy'] != page_ccy:
        fx = REPORT_FX.get(t)
        if not fx or fx[:2] != (e['ccy'], page_ccy):
            print(f'WARNING: {t}: report is in {e["ccy"]} but the page prices it in {page_ccy}; add the report’s rate to REPORT_FX')
            continue
        e['note'] = f'{e["ccy"]} {e["fv"]:.2f} converted at the report’s rate'
        e.update(ccy=page_ccy, fv=round(e['fv'] * fx[2], 2))
    if t not in held and not any(w['t'] == t for w in watch):
        yf = t.replace('.', '-') + ('.TO' if 'TORONTO' in e['exch'].upper() else '')
        watch.append(dict(t=t, yf=yf))
        added.append(t)
    out[t] = [{k: e[k] for k in ('src', 'date', 'ccy', 'fv', 'stars', 'moat', 'unc', 'wacc', 'coe', 'note') if e.get(k) is not None}]
    print(f'{t:6} {"held " if t in held else "watch"} {e["src"]:20} {e["date"]} {e["ccy"]} {e["fv"]:>9,.2f}  '
          f'★{e["stars"]} {e["moat"]}/{e["unc"]}  wacc {e.get("wacc")} coe {e.get("coe")}')

json.dump(out, open(os.path.join(ROOT, 'data', 'research.json'), 'w'), ensure_ascii=False, indent=1)
json.dump(watch, open(wl_path, 'w'), indent=1)
print(f'{len(out)} stocks; added to watchlist: {added or "none"}')
