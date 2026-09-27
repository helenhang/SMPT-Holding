"""Extract holdings from the RBC "Portfolio Holdings & Analysis" workbooks.

Usage: python build/parse_holdings.py <folder with the holdings workbooks>
Writes data/holdings.json. Any file named "YYYY MM DD Portfolio Holdings*.xlsx"
in the folder becomes one snapshot; sheet 1 must be the "01 Holdings" sheet.
"""
import glob
import json
import os
import re
import sys

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if len(sys.argv) < 2:
    sys.exit('Usage: python build/parse_holdings.py <folder with the holdings workbooks>')
SRC = sys.argv[1]


def parse(path):
    ws = openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    hi = next(i for i, r in enumerate(rows) if 'Symbol' in [str(c).strip() if c else '' for c in r])
    hdr = [str(c).strip() if c else '' for c in rows[hi]]
    sc, qc, mc = hdr.index('Symbol'), hdr.index('Quantity'), hdr.index('CAD Dollars')
    sec, hold, total = None, [], None
    for r in rows[hi + 1:]:
        if isinstance(r[0], str) and re.match(r'^[IVX]+\.', r[0].strip()):
            sec = r[0].strip()
            continue
        if any('Total value of the portfolio' in str(c) for c in r if c is not None):
            total = [c for c in r if isinstance(c, (int, float))][-1]
            break
        if r[sc] is not None and isinstance(r[qc], (int, float)):
            hold.append(dict(sec=sec, sym=str(r[sc]).strip(), name=str(r[sc + 1]).strip(), qty=r[qc], mv=r[mc]))
    return dict(total=total, hold=hold)


out = {}
for f in sorted(glob.glob(os.path.join(SRC, '*Portfolio Holdings*.xlsx'))):
    name = os.path.basename(f)
    m = re.match(r'(\d{4}) (\d{2}) (\d{2})', name)
    if name.startswith('~$') or not m:
        continue
    out['-'.join(m.groups())] = parse(f)
    print(name, '->', len(out['-'.join(m.groups())]['hold']), 'rows')

json.dump(out, open(os.path.join(ROOT, 'data', 'holdings.json'), 'w'), default=str, indent=1)
