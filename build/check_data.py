"""Guard for the scheduled data update: compare freshly fetched data files with the committed
versions and restore any file that came back worse, so a partial Yahoo or Wikipedia outage
never replaces good data with incomplete data.

Usage: python build/check_data.py
A file is restored (git checkout) when it covers fewer companies or series than before, lists
more of them as missing, or is dated earlier. Restored files are reported; the exit code is 0
so the files that passed can still be published.
"""
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def committed(path):
    r = subprocess.run(['git', 'show', f'HEAD:{path}'], cwd=ROOT, capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def problems(path, old, new):
    if path == 'data/prices.json':
        return [m for m, bad in (
            (f'{len(new["px"])} series, was {len(old["px"])}', len(new['px']) < len(old['px'])),
            (f'missing {sorted(set(new["missing"]) - set(old["missing"]))}', set(new['missing']) - set(old['missing'])),
            (f'ends {new["end"]}, was {old["end"]}', new['end'] < old['end'])) if bad]
    if path == 'data/sectors.json':
        return [f'{k} has {new["idx"].get(k, {}).get("n", 0)} companies, was {v["n"]}'
                for k, v in old['idx'].items() if new['idx'].get(k, {}).get('n', 0) < 0.98 * v['n']] + \
               [f'{len(new["hold"])} holdings, was {len(old["hold"])}' for _ in [0] if len(new['hold']) < len(old['hold'])]
    # fundamentals.json, analysts.json
    return [m for m, bad in (
        (f'{len(new["co"])} companies, was {len(old["co"])}', len(new['co']) < len(old['co'])),
        (f'missing {sorted(set(new["missing"]) - set(old["missing"]))}', set(new['missing']) - set(old['missing'])),
        (f'dated {new["asof"]}, was {old["asof"]}', new['asof'] < old['asof'])) if bad]


restored = []
for path in ('data/prices.json', 'data/fundamentals.json', 'data/analysts.json', 'data/sectors.json'):
    old = committed(path)
    try:
        new = json.load(open(os.path.join(ROOT, path)))
    except (OSError, ValueError) as e:
        new, issues = None, [f'unreadable: {e}']
    else:
        issues = problems(path, old, new) if old else []
    if issues:
        subprocess.run(['git', 'checkout', '--', path], cwd=ROOT, check=True)
        restored.append(path)
        print(f'RESTORED {path}: ' + '; '.join(issues))
    else:
        print(f'ok       {path}')
print(f'{len(restored)} file(s) restored' if restored else 'all data files passed')
