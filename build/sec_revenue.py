"""Vendored from the stockscan project (stockscan/sec_revenue.py); keep the two copies in sync.
The only change: the SEC contact e-mail comes from the SEC_CONTACT environment variable.

从 SEC 年报（10-K / 20-F）的 XBRL 数据里取「按产品披露的收入」。

流程：ticker → CIK（company_tickers.json）→ 最近一份年报 → XBRL 实例文件 →
收入科目里带「产品 / 服务」维度的数值。只用最近一个完整财年。

SEC 公平访问规定：请求头必须带联系邮箱，每秒不超过 10 次。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

import requests

# SEC 要求请求头里带联系邮箱：从环境变量 SEC_CONTACT 读（GitHub Actions 里是仓库 Secret），不写进代码
SEC_UA = f"SMPT Portfolio Ledger research {os.environ.get('SEC_CONTACT', '').strip()}"
TTL = 30 * 86400          # 30 天检查一次有没有新年报（新年报一年才一份）
SCHEMA = 2                # 2：加了历年（years / totals），旧缓存会重新读

_lock = threading.Lock()
_last = [0.0]


def _get(url: str, **kw) -> requests.Response:
    if "@" not in SEC_UA:
        raise RuntimeError("set SEC_CONTACT to a contact e-mail (SEC requires one in the User-Agent)")
    with _lock:                                   # 每秒最多约 6 次
        wait = 0.16 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    r = requests.get(url, headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}, timeout=90, **kw)
    r.raise_for_status()
    return r


_REV_EL = re.compile(r"^(us-gaap|ifrs-full):(RevenueFromContractWithCustomerExcludingAssessedTax|"
                     r"RevenueFromContractWithCustomerIncludingAssessedTax|Revenues|Revenue|"
                     r"RevenueFromContractsWithCustomers|SalesRevenueNet|SalesRevenueGoodsNet)$")
_PRODUCT_AXIS = re.compile(r"ProductOrServiceAxis|ProductsAndServicesAxis", re.I)
_GEO_AXIS = re.compile(r"GeographicalAxis|GeographicalAreasAxis|SegmentsGeographical", re.I)
# 产品所属的业务分部 / 合并抵销：只是说明这个产品在哪个分部，不是另一种拆分，忽略
_SEG_AXIS = re.compile(r"SegmentsAxis|SubsegmentsAxis|ConsolidationItemsAxis", re.I)


def _local(member: str) -> str:
    """'lly:MounjaroMember' → 'Mounjaro'"""
    n = member.split(":", 1)[-1]
    return re.sub(r"Member$", "", n)


def humanize(member: str) -> str:
    n = _local(member)
    n = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", n)
    return n.replace(" And ", " & ").strip()


def parse_instance(xml: str) -> dict:
    """XBRL 实例 → 最近一个完整财年的 {total, currency, end, members: {member: value}}。
    members 只收「产品 / 服务」维度；同一个产品按地区拆开时把 美国 + 美国以外 加起来。"""
    units = {}
    for m in re.finditer(r'<(?:xbrli:)?unit id="([^"]+)">(.*?)</(?:xbrli:)?unit>', xml, re.S):
        cur = re.search(r"iso4217:([A-Z]{3})", m.group(2))
        if cur and "divide" not in m.group(2):
            units[m.group(1)] = cur.group(1)
    ctx = {}
    for m in re.finditer(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', xml, re.S):
        body = m.group(2)
        per = re.search(r"<(?:xbrli:)?startDate>\s*([^<\s]+)\s*</(?:xbrli:)?startDate>\s*"
                        r"<(?:xbrli:)?endDate>\s*([^<\s]+)\s*<", body)
        if not per:
            continue
        dims = tuple(re.findall(r'<xbrldi:explicitMember[^>]*dimension="([^"]+)"[^>]*>\s*([^<\s]+)\s*<', body))
        ctx[m.group(1)] = (per.group(1), per.group(2), dims)
    facts = []
    for m in re.finditer(r"<([\w-]+:[\w]+)\b([^>]*?)>([^<]+)</\1>", xml):
        if not _REV_EL.match(m.group(1)):
            continue
        attrs = m.group(2)
        c = re.search(r'contextRef="([^"]+)"', attrs)
        u = re.search(r'unitRef="([^"]+)"', attrs)
        if not c or c.group(1) not in ctx or not u or u.group(1) not in units:
            continue
        try:
            v = float(m.group(3).strip())
        except ValueError:
            continue
        s, e, dims = ctx[c.group(1)]
        facts.append((s, e, dims, v, units[u.group(1)]))
    if not facts:
        return {}
    # 最近一个完整财年：结束日最晚、时长约一年
    def days(s, e):
        try:
            return (time.mktime(time.strptime(e, "%Y-%m-%d")) - time.mktime(time.strptime(s, "%Y-%m-%d"))) / 86400
        except Exception:
            return 0
    yearly = [f for f in facts if 350 <= days(f[0], f[1]) <= 380]
    if not yearly:
        return {}
    currency = max({f[4] for f in yearly}, key=lambda c: sum(1 for f in yearly if f[4] == c))
    yearly = [f for f in yearly if f[4] == currency]
    # 一份年报里通常有 3 个财年（今年 + 前两年对比）：每年分别算
    years, totals = {}, {}
    for e_ in sorted({f[1] for f in yearly}):
        fy = fiscal_year(e_)
        rows = [f for f in yearly if f[1] == e_]
        tot = [f[3] for f in rows if not f[2]]
        if tot:
            totals[fy] = max(tot)
        years[fy] = _members_of(rows)
    end = max(f[1] for f in yearly)
    latest = fiscal_year(end)
    return {"end": end, "currency": currency, "total": totals.get(latest), "members": years.get(latest, {}),
            "years": years, "totals": totals}


def fiscal_year(end: str) -> str:
    """财年标签：截止日在 1–3 月的算上一年（如 2026-01-31 → 2025）"""
    y, m = int(end[:4]), int(end[5:7])
    return str(y if m >= 4 else y - 1)


def _members_of(rows: list) -> dict:
    """同一财年的收入数值 → {产品成员: 金额}。
    每个产品：优先「只有产品维度」的值；否则 美国 + 美国以外；再不行取各地区里最大的一个（宁可少算）"""
    by: dict[str, dict] = {}
    for s, e, dims, v, cur in rows:
        prod = [d for d in dims if _PRODUCT_AXIS.search(d[0])]
        if len(prod) != 1:
            continue
        others = [d for d in dims if not _PRODUCT_AXIS.search(d[0]) and not _SEG_AXIS.search(d[0])]
        mem = prod[0][1]
        e2 = by.setdefault(mem, {"only": None, "geo": {}})
        if not others:
            e2["only"] = max(e2["only"] or 0, v)
        elif len(others) == 1 and _GEO_AXIS.search(others[0][0]):
            e2["geo"][others[0][1]] = max(e2["geo"].get(others[0][1], 0), v)
    members = {}
    for mem, e2 in by.items():
        if e2["only"] is not None:
            members[mem] = e2["only"]
            continue
        geo = e2["geo"]
        us = next((v for k, v in geo.items() if k.endswith(":US") or re.search(r"UnitedStates", k)), None)
        ex = next((v for k, v in geo.items() if re.search(r"NonUs|OutsideUnitedStates|International|ExUs|RestOfWorld|OutsideUs", k, re.I)), None)
        if us is not None and ex is not None:
            members[mem] = us + ex
        elif geo:
            members[mem] = max(geo.values())
    return members


class SecRevenue:
    def __init__(self, cache_dir: str):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.job = {"running": False, "done": 0, "total": 0}
        self._cik = None

    def _path(self, name):
        return os.path.join(self.dir, re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".json")

    def _read(self, name, ttl):
        try:
            with open(self._path(name)) as f:
                d = json.load(f)
            return d if time.time() - d.get("ts", 0) < ttl else None
        except Exception:
            return None

    def _write(self, name, d):
        d = {**d, "ts": time.time()}
        tmp = self._path(name) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, self._path(name))
        return d

    def cik_map(self) -> dict:
        if self._cik is None:
            hit = self._read("company_tickers", TTL)
            if not hit:
                raw = _get("https://www.sec.gov/files/company_tickers.json").json()
                hit = self._write("company_tickers", {"map": {v["ticker"].upper(): v["cik_str"] for v in raw.values()}})
            self._cik = hit["map"]
        return self._cik

    def cached(self, ticker: str) -> dict | None:
        return self._read("rev_" + ticker.upper(), 10 * 365 * 86400)

    def fresh(self, ticker: str) -> bool:
        d = self._read("rev_" + ticker.upper(), TTL)
        if d and not d.get("error") and d.get("v") != SCHEMA:
            return False
        if d and d.get("error") and d["error"] not in ("not SEC-registered", "no annual report"):
            return time.time() - d.get("ts", 0) < 86400          # 网络错误之类：一天后重试
        return d is not None

    def fetch(self, ticker: str) -> dict:
        """最近一份年报的产品收入；已经解析过同一份年报就只更新时间戳。"""
        tk = ticker.upper()
        cik = self.cik_map().get(tk.replace(".", "-")) or self.cik_map().get(tk)
        if not cik:
            return self._write("rev_" + tk, {"ticker": tk, "error": "not SEC-registered"})
        sub = _get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json").json()
        rec = sub.get("filings", {}).get("recent", {})
        idx = next((i for i, f in enumerate(rec.get("form", [])) if f in ("10-K", "20-F", "40-F")), None)
        if idx is None:
            return self._write("rev_" + tk, {"ticker": tk, "error": "no annual report"})
        acc = rec["accessionNumber"][idx]
        old = self.cached(tk)
        if old and old.get("accession") == acc and not old.get("error") and old.get("v") == SCHEMA:
            return self._write("rev_" + tk, old)
        parsed = self._parse_filing(cik, acc)
        if parsed is None:
            return self._write("rev_" + tk, {"ticker": tk, "error": "no XBRL instance", "accession": acc})
        # 两年前那份年报：再补两个更早的财年（共 5 年）
        annual = [i for i, f in enumerate(rec.get("form", [])) if f in ("10-K", "20-F", "40-F")]
        if len(annual) >= 3 and parsed.get("years"):
            try:
                older = self._parse_filing(cik, rec["accessionNumber"][annual[2]]) or {}
                if older.get("currency") == parsed.get("currency"):
                    for fy, mem in (older.get("years") or {}).items():
                        parsed["years"].setdefault(fy, mem)
                    for fy, v in (older.get("totals") or {}).items():
                        parsed["totals"].setdefault(fy, v)
            except Exception:
                pass
        return self._write("rev_" + tk, {"ticker": tk, "v": SCHEMA, "accession": acc, "form": rec["form"][idx],
                                         "reportDate": rec.get("reportDate", [None] * (idx + 1))[idx],
                                         "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{rec['primaryDocument'][idx]}",
                                         **parsed})

    def _parse_filing(self, cik, acc) -> dict | None:
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
        files = [x["name"] for x in _get(base + "/index.json").json().get("directory", {}).get("item", [])]
        inst = next((f for f in files if f.endswith("_htm.xml")), None) or \
               next((f for f in files if f.endswith(".xml") and not re.search(r"FilingSummary|_(cal|def|lab|pre)\.xml$", f)), None)
        return parse_instance(_get(f"{base}/{inst}").text) if inst else None

    def fill(self, tickers: list[str]) -> None:
        if self.job["running"]:
            return
        self.job.update(running=True, done=0, total=len(tickers))
        try:
            for tk in tickers:
                try:
                    self.fetch(tk)
                except Exception as e:
                    self._write("rev_" + tk.upper(), {"ticker": tk.upper(), "error": str(e)[:200]})
                self.job["done"] += 1
        finally:
            self.job["running"] = False
