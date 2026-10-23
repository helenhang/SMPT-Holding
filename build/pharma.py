"""Vendored from the stockscan project (stockscan/pharma.py); keep the two copies in sync.

药企数据：已上市药物（openFDA）+ 在研管线（ClinicalTrials.gov）+ 治疗领域 + 竞争对手。

两个都是美国政府公开接口，不需要账号：
  - openFDA：不带 key 每个 IP 每天 1000 次请求 → 公司级只拉 drugsfda（1–2 次），
    说明书（适应症）在用户点开某个药时才拉，结果长期缓存
  - ClinicalTrials.gov v2：没有明确的日限额，一家大药企 1–2 页（每页 1000 个试验）

所有结果存在 cache_dir 下的 JSON 文件，默认 30 天刷新。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

import requests

CT_API = "https://clinicaltrials.gov/api/v2/studies"
FDA_API = "https://api.fda.gov/drug"
OPENFDA_API_KEY = ""          # 可选：在 open.fda.gov 免费申请，填上后每天额度 12 万次
TTL = 30 * 86400
LABEL_TTL = 90 * 86400        # 说明书很少变

# 只有这些 yfinance 行业算「药企」（医疗器械、医院、保险等没有药物数据）
PHARMA_INDUSTRIES = {"Biotechnology", "Drug Manufacturers - General",
                     "Drug Manufacturers - Specialty & Generic"}

# 动物保健（兽药）公司：不算进人用药的治疗领域收入
ANIMAL_HEALTH = {"ZTS", "ELAN", "PAHC"}

# 子公司 / 别名：临床试验的申办方、FDA 的生产商常挂在子公司名下。
# 没列的公司按公司名自动匹配；exclude 用来排除同名的其他公司（Merck 美国 vs 德国默克）
ALIASES: dict[str, dict] = {
    "LLY":   {"names": ["Eli Lilly", "Lilly", "Loxo Oncology", "Prevail Therapeutics", "Avid Radiopharm", "Verve Therapeutics", "Morphic"]},
    "NVO":   {"names": ["Novo Nordisk"]},
    "JNJ":   {"names": ["Janssen", "Johnson & Johnson", "Actelion", "Intra-Cellular", "Momenta"]},
    "MRK":   {"names": ["Merck Sharp & Dohme", "Merck & Co", "MSD", "Organon & Co", "Acceleron", "Prometheus Biosciences"],
              "exclude": ["KGaA", "Darmstadt", "Serono"]},
    "PFE":   {"names": ["Pfizer", "Wyeth", "Seagen", "Arena Pharmaceuticals", "Global Blood Therapeutics", "Hospira"]},
    "ABBV":  {"names": ["AbbVie", "Allergan", "Pharmacyclics", "ImmunoGen", "Cerevel"]},
    "BMY":   {"names": ["Bristol-Myers Squibb", "Bristol Myers Squibb", "Celgene", "Juno Therapeutics", "MyoKardia", "Karuna", "Mirati", "RayzeBio"]},
    "AZN":   {"names": ["AstraZeneca", "Alexion", "MedImmune", "Acerta"]},
    "NVS":   {"names": ["Novartis", "Advanced Accelerator Applications", "Endocyte"]},
    "SNY":   {"names": ["Sanofi", "Genzyme", "Bioverativ", "Principia"]},
    "GSK":   {"names": ["GlaxoSmithKline", "GSK", "ViiV", "Tesaro"]},
    "RHHBY": {"names": ["Genentech", "Hoffmann-La Roche", "Roche", "Chugai", "Spark Therapeutics"]},
    "AMGN":  {"names": ["Amgen", "Horizon Therapeutics", "ChemoCentryx"]},
    "GILD":  {"names": ["Gilead", "Kite Pharma", "Kite, A Gilead", "Immunomedics", "CymaBay"]},
    "REGN":  {"names": ["Regeneron"]},
    "VRTX":  {"names": ["Vertex Pharmaceuticals", "Alpine Immune"]},
    "BIIB":  {"names": ["Biogen", "Reata"]},
    "TAK":   {"names": ["Takeda", "Shire", "Millennium Pharmaceuticals"]},
    "BAYRY": {"names": ["Bayer"]},
    "TEVA":  {"names": ["Teva"]},
    "MRNA":  {"names": ["ModernaTX", "Moderna"]},
    "BNTX":  {"names": ["BioNTech"]},
    "ALNY":  {"names": ["Alnylam"]},
    "INCY":  {"names": ["Incyte"]},
    "BMRN":  {"names": ["BioMarin"]},
    "NBIX":  {"names": ["Neurocrine"]},
    "JAZZ":  {"names": ["Jazz Pharmaceuticals", "GW Research", "GW Pharma"]},
    "UTHR":  {"names": ["United Therapeutics"]},
    "VTRS":  {"names": ["Viatris", "Mylan", "Upjohn"]},
    "OGN":   {"names": ["Organon"]},
}

_STATUSES = "NOT_YET_RECRUITING,RECRUITING,ENROLLING_BY_INVITATION,ACTIVE_NOT_RECRUITING"
_CT_FIELDS = ("NCTId,BriefTitle,Phase,OverallStatus,LeadSponsorName,ArmGroupType,ArmGroupInterventionName,"
              "InterventionName,InterventionType,InterventionOtherName,Condition,ConditionMeshTerm,ConditionAncestorTerm")
_DRUG_TYPES = {"DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT", "RADIATION"}
_PHASE_RANK = {"EARLY_PHASE1": 0, "PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 4}

# 不是「某公司的药」的试验组成分：安慰剂、标准治疗、常见化疗 / 背景用药
_NOT_A_DRUG = re.compile(r"placebo|vehicle|saline|standard of care|soc\b|best supportive|usual care|"
                         r"investigator'?s choice|physician'?s choice|sham|no intervention|diluent|"
                         r"^(biological|drug|vaccine|biological/vaccine|experimental|active|treatment)$", re.I)
_BACKBONE = {
    "carboplatin", "cisplatin", "oxaliplatin", "paclitaxel", "nab-paclitaxel", "docetaxel", "gemcitabine",
    "pemetrexed", "fluorouracil", "5-fluorouracil", "capecitabine", "irinotecan", "etoposide",
    "cyclophosphamide", "doxorubicin", "vincristine", "prednisone", "prednisolone", "dexamethasone",
    "methylprednisolone", "fulvestrant", "letrozole", "anastrozole", "exemestane", "tamoxifen",
    "leucovorin", "folfox", "folfiri", "bevacizumab", "rituximab", "lenalidomide", "bortezomib",
    "azacitidine", "cytarabine", "metformin", "insulin glargine", "methotrexate", "radiation",
    "radiotherapy", "radiation therapy", "stereotactic body radiation therapy", "chemotherapy",
    "abiraterone", "enzalutamide", "temozolomide", "filgrastim", "pegfilgrastim", "ondansetron",
}

# 治疗领域：按 MeSH 疾病分类（试验条件 + 上层分类）匹配，排在前面的优先
AREAS = [
    ("oncology",    r"neoplasm|cancer|carcinoma|tumou?r|lymphoma|leukemia|myeloma|sarcoma|melanoma|glioma"),
    ("metabolic",   r"obesity|overweight|diabetes|metabolic|nutritional|lipid|dyslipid|hypercholesterol|insulin|glycemic|weight"),
    ("cardio",      r"cardiovascular|heart|cardiac|hypertension|vascular disease|atrial|coronary|thrombo|stroke"),
    ("neuro",       r"nervous system|alzheimer|parkinson|dementia|epilep|migraine|neuropath|multiple sclerosis|brain disease|neurodegenerat|amyotrophic|huntington|neuromuscular"),
    ("psych",       r"mental disorder|schizophren|depress|bipolar|anxiety|psychotic|autism|adhd|attention deficit|substance"),
    ("infectious",  r"infection|virus|viral|bacterial|hiv|hepatitis [a-e]\b|influenza|covid|sars-cov|vaccin|tuberculosis|fungal|rsv|respiratory syncytial"),
    ("immunology",  r"autoimmune|immune system|rheumat|arthritis|lupus|psoria|crohn|colitis|inflammatory bowel|atopic|dermatitis|eczema|sjogren|vasculitis"),
    ("respiratory", r"respiratory|asthma|pulmonary|copd|lung disease|cystic fibrosis|bronch"),
    ("hematology",  r"hemic|hematolog|anemia|hemophilia|sickle|thalass|blood coagulation|neutropenia|platelet"),
    ("ophthalmo",   r"eye disease|retina|macular|glaucoma|uveitis|ocular|vision"),
    ("renal",       r"kidney|renal|urolog|nephro|bladder|urinary"),
    ("digestive",   r"digestive|liver|hepat|gastro|intestin|pancrea|nash|mash|steatohepatitis"),
    ("rare",        r"congenital|hereditary|genetic disease|inborn|lysosomal|muscular dystrophy|spinal muscular|rare"),
    ("musculo",     r"musculoskeletal|bone|osteopor|muscle|joint"),
    ("women",       r"pregnan|endometrios|menopaus|contracept|infertil|uterine|ovarian"),
    ("derm",        r"skin disease|acne|alopecia|hidradenitis|vitiligo|urticaria"),
]
_AREA_RE = [(k, re.compile(p, re.I)) for k, p in AREAS]

# FDA 作用机制（EPC）→ 领域，说明书还没拉到时的估计
_EPC_AREA = [
    ("oncology",   r"(?<!janus )kinase inhibitor|programmed death|pd-1|pd-l1|ctla|antineoplastic|alkylat|topoisomerase|antibody-drug|her2|egfr|parp|cd20|cd38|bcma|androgen receptor inhibitor|estrogen receptor antagonist|aromatase|bispecific"),
    ("metabolic",  r"glp-1|insulin|glucose|sglt|dpp-4|dipeptidyl|amylin|incretin|lipid|hmg-coa|pcsk9"),
    ("infectious", r"antiviral|antibacterial|antiretroviral|vaccine|integrase|protease inhibitor|antifungal|nucleoside|cephalosporin|macrolide"),
    ("immunology", r"tumor necrosis|tnf|interleukin|janus kinase|jak|integrin|calcineurin|immunosuppress|s1p"),
    ("neuro",      r"cgrp|calcitonin gene|anticonvulsant|dopamine agonist|cholinesterase|amyloid|serotonin-1f"),
    ("psych",      r"antipsychotic|selective serotonin|serotonin and norepinephrine|antidepress|central nervous system stimulant"),
    ("cardio",     r"anticoagulant|factor xa|beta adrenergic|angiotensin|calcium channel|diuretic|antiplatelet|endothelin"),
    ("respiratory",r"bronchodilator|leukotriene|anticholinergic|cystic fibrosis transmembrane"),
    ("hematology", r"erythropoie|thrombopoietin|coagulation factor|complement inhibitor"),
]
_EPC_AREA_RE = [(k, re.compile(p, re.I)) for k, p in _EPC_AREA]


def area_of(texts) -> str | None:
    blob = " | ".join(t for t in texts if t)
    for k, rx in _AREA_RE:
        if rx.search(blob):
            return k
    return None


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _strip_corp(name: str) -> str:
    """'Vertex Pharmaceuticals Incorporated' → 'Vertex Pharmaceuticals'"""
    n = re.sub(r"[.,]", " ", name or "")
    n = re.sub(r"\b(inc|incorporated|corp|corporation|co|company|plc|ltd|limited|llc|sa|ag|nv|se|holdings?|group|the)\b",
               " ", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip()


def company_names(ticker: str, long_name: str | None) -> tuple[list[str], list[str]]:
    a = ALIASES.get(ticker.upper())
    if a:
        return a["names"], a.get("exclude", [])
    core = _strip_corp(long_name or "")
    return ([core] if core else []), []


def _sponsor_match(sponsor: str, names: list[str], exclude: list[str]) -> bool:
    s = (sponsor or "").lower()
    if any(x.lower() in s for x in exclude):
        return False
    return any(re.search(r"(^|\W)" + re.escape(n.lower()) + r"(\W|$)", s) for n in names)


def sponsor_to_ticker(sponsor: str, pharma_names: dict[str, str]) -> str | None:
    """申办方 / 生产商名字 → 股票代码。pharma_names = {ticker: 公司名}（股票池里的药企）"""
    for tk, a in ALIASES.items():
        if _sponsor_match(sponsor, a["names"], a.get("exclude", [])):
            return tk
    s = _norm(_strip_corp(sponsor))
    if len(s) < 4:
        return None
    for tk, nm in pharma_names.items():
        c = _norm(_strip_corp(nm))
        if len(c) >= 4 and (s == c or s.startswith(c) or c.startswith(s)):
            return tk
    return None


# ── HTTP ─────────────────────────────────────────────────────────
_fda_lock = threading.Lock()
_fda_last = [0.0]

def _fda_get(path: str, params: dict) -> dict | None:
    if OPENFDA_API_KEY:
        params = {**params, "api_key": OPENFDA_API_KEY}
    with _fda_lock:                       # 每秒最多 4 次（不带 key 的限速是每分钟 240）
        wait = 0.25 - (time.time() - _fda_last[0])
        if wait > 0:
            time.sleep(wait)
        _fda_last[0] = time.time()
    r = requests.get(f"{FDA_API}/{path}.json", params=params, timeout=30)
    if r.status_code == 404:              # openFDA 用 404 表示「没有结果」
        return {"results": [], "meta": {"results": {"total": 0}}}
    r.raise_for_status()
    return r.json()


def _ct_get(params: dict, max_pages: int = 3) -> list[dict]:
    out, token = [], None
    for _ in range(max_pages):
        p = {**params, "pageSize": 1000, "format": "json"}
        if token:
            p["pageToken"] = token
        r = requests.get(CT_API, params=p, timeout=60)
        r.raise_for_status()
        d = r.json()
        out += d.get("studies", [])
        token = d.get("nextPageToken")
        if not token:
            break
    return out


def _or_query(names: list[str]) -> str:
    return " OR ".join(f'"{n}"' for n in names)


# ── 缓存文件 ─────────────────────────────────────────────────────
class _Store:
    def __init__(self, cache_dir: str):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".json")

    def get(self, name: str, ttl: float) -> dict | None:
        try:
            with open(self._path(name)) as f:
                d = json.load(f)
            return d if time.time() - d.get("ts", 0) < ttl else None
        except Exception:
            return None

    def put(self, name: str, data: dict) -> dict:
        data = {**data, "ts": time.time()}
        tmp = self._path(name) + ".tmp"
        with self._lock:
            with open(tmp, "w") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._path(name))
        return data


# ── 已上市药物（FDA） ─────────────────────────────────────────────
def _approved_drugs(names: list[str], exclude: list[str]) -> list[dict]:
    # 两个字段都查：较新的批准记录常常没有 openfda 字段，只有申办方名字（大写，如 ELI LILLY AND CO）
    wild = [re.sub(r"[^A-Z0-9]+", "*", n.upper()).strip("*") + "*" for n in names]
    q = " OR ".join([f'openfda.manufacturer_name:"{n}"' for n in names] + [f"sponsor_name:{w}" for w in wild if len(w) > 3])
    seen: dict[str, dict] = {}
    skip = 0
    while True:
        d = _fda_get("drugsfda", {"search": q, "limit": 100, "skip": skip})
        res = d.get("results", [])
        for r in res:
            of = r.get("openfda", {}) or {}
            mfr = (of.get("manufacturer_name") or [r.get("sponsor_name", "")])
            if not any(_sponsor_match(m, names, exclude) for m in mfr) and \
               not _sponsor_match(r.get("sponsor_name", ""), names, exclude):
                continue
            orig = [s.get("submission_status_date") for s in r.get("submissions", []) or []
                    if s.get("submission_type") == "ORIG" and s.get("submission_status_date")]
            for p in r.get("products", []) or []:
                if p.get("marketing_status") != "Prescription":
                    continue
                brand = (p.get("brand_name") or "").strip()
                if not brand:
                    continue
                key = _brand_key(brand)
                gen = ", ".join(sorted({a.get("name", "").title() for a in p.get("active_ingredients", []) or []}))
                nice = brand.title() if brand.isupper() else brand
                if key in seen and len(nice) < len(seen[key]["brand"]):
                    seen[key]["brand"] = nice
                e = seen.setdefault(key, {"brand": nice,
                                          "generic": gen, "epc": [], "route": p.get("route"),
                                          "approved": min(orig) if orig else None,
                                          "app": r.get("application_number")})
                for g in gen.split(", "):
                    if g and g not in e["generic"].split(", "):
                        e["generic"] = (e["generic"] + ", " + g).strip(", ")
                for c in of.get("pharm_class_epc") or []:
                    c = c.replace(" [EPC]", "")
                    if c not in e["epc"]:
                        e["epc"].append(c)
                if orig and (not e["approved"] or min(orig) < e["approved"]):
                    e["approved"] = min(orig)
        skip += len(res)
        total = (d.get("meta", {}).get("results", {}) or {}).get("total", 0)
        if not res or skip >= total or skip >= 500:
            break
    drugs = list(seen.values())
    for e in drugs:
        e["area"] = _epc_area(e["epc"])
    return sorted(drugs, key=lambda e: e.get("approved") or "", reverse=True)


_DEVICE_WORDS = re.compile(r"\b(kwik ?pen|pen|autoinjector|auto-injector|prefilled|pre-filled|syringe|copackaged|"
                           r"junior|tempo|vial|cartridge|kit|single-dose|multi-dose|flextouch|solostar|inhaler|"
                           r"mix ?\d+/\d+|\d+/\d+|u-\d+)\b", re.I)

def _brand_key(brand: str) -> str:
    """同一品牌的不同包装 / 装置合并（Zepbound KwikPen → zepbound），不同品牌分开（Keytruda Qlex ≠ Keytruda）"""
    b = re.sub(r"\(.*?\)", " ", brand or "")
    return _norm(_DEVICE_WORDS.sub(" ", b)) or _norm(brand)


def _epc_area(epcs: list[str]) -> str | None:
    blob = " | ".join(epcs)
    for k, rx in _EPC_AREA_RE:
        if rx.search(blob):
            return k
    return None


# ── 在研管线（ClinicalTrials.gov） ──────────────────────────────────
def _study_parts(s: dict) -> dict:
    ps = s.get("protocolSection", {})
    arms = ps.get("armsInterventionsModule", {}) or {}
    ivs = arms.get("interventions", []) or []
    types = {iv.get("name"): iv.get("type") for iv in ivs}
    others = {iv.get("name"): iv.get("otherNames") or [] for iv in ivs}
    exp: list[str] = []
    for g in arms.get("armGroups", []) or []:
        if g.get("type") == "EXPERIMENTAL":
            for n in g.get("interventionNames", []) or []:
                n = n.split(":", 1)[-1].strip()
                if n not in exp:
                    exp.append(n)
    if not exp:                          # 有些试验没写试验组：用全部药物
        exp = [iv.get("name") for iv in ivs]
    exp = [n for n in exp if n and types.get(n, "DRUG") in _DRUG_TYPES and not _NOT_A_DRUG.search(n)]
    phases = (ps.get("designModule", {}) or {}).get("phases", []) or []
    br = (s.get("derivedSection", {}) or {}).get("conditionBrowseModule", {}) or {}
    mesh = [m.get("term") for m in br.get("meshes", []) or []]
    anc = [m.get("term") for m in br.get("ancestors", []) or []]
    conds = (ps.get("conditionsModule", {}) or {}).get("conditions", []) or []
    return {
        "nct": ps.get("identificationModule", {}).get("nctId"),
        "title": ps.get("identificationModule", {}).get("briefTitle"),
        "status": ps.get("statusModule", {}).get("overallStatus"),
        "sponsor": ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name"),
        "phase": max((_PHASE_RANK.get(p, -1) for p in phases), default=-1),
        "drugs": exp, "others": {n: others.get(n, []) for n in exp}, "conditions": conds, "mesh": mesh,
        "area": area_of(mesh + anc + conds) or "other",
    }


def _drug_key(name: str) -> str:
    # 'LY3437943 (Retatrutide)' 和 'Retatrutide' 算同一个；去掉剂量、给药方式
    n = re.sub(r"\b\d+(\.\d+)?\s*(mg|mcg|µg|ug|ml|iu|units?)\b.*", "", name, flags=re.I)
    m = re.search(r"\(([^)]+)\)", n)
    if m and re.search(r"[a-z]{5,}", m.group(1), re.I) and not re.search(r"\d", m.group(1)):
        n = m.group(1)
    n = re.sub(r"\(.*?\)", "", n)
    n = re.sub(r"\b(injection|tablets?|capsules?|oral|iv|subcutaneous|sc|infusion|solution|drug|biological|dose|low|high)\b", "", n, flags=re.I)
    n = re.sub(r"-[a-z]{4}$", "", n.strip(), flags=re.I)             # 生物药后缀：mirikizumab-mrkz
    n = re.sub(r"\b(" + _SALTS + r")\b", "", n, flags=re.I)         # 盐：orforglipron calcium
    return _norm(n)


_SALTS = ("calcium|sodium|potassium|magnesium|hydrochloride|hcl|succinate|mesylate|maleate|tartrate|citrate|"
          "acetate|phosphate|sulfate|besylate|fumarate|tosylate|bromide|dihydrate|monohydrate|hydrobromide|"
          "dimesylate|meglumine|disodium|trihydrate|hemifumarate|free base")


_CODE_RE = re.compile(r"^[A-Z]{1,6}[- ]?\d{2,}", re.I)


def _pipeline(names: list[str], exclude: list[str], own_generics: set[str]) -> tuple[list[dict], list[str], int]:
    studies = _ct_get({"query.spons": _or_query(names), "filter.overallStatus": _STATUSES,
                       "filter.advanced": "AREA[Phase](PHASE1 OR PHASE2 OR PHASE3)", "fields": _CT_FIELDS})
    trials = [t for t in map(_study_parts, studies) if _sponsor_match(t["sponsor"], names, exclude)]
    # 第一遍：同一个药在不同试验里写法不同（代号 / 通用名 / 别名），并查集把它们归成一组
    parent: dict[str, str] = {}
    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def cands_of(t, n):
        al = [o for o in t["others"].get(n, []) if o and len(o) < 40]
        return al, [k for k in [_drug_key(n)] + [_drug_key(o) for o in al] if k]
    for t in trials:
        for n in t["drugs"]:
            _, cands = cands_of(t, n)
            for c in cands[1:]:
                parent[find(c)] = find(cands[0])
    groups: dict[str, set] = {}
    for x in list(parent):
        groups.setdefault(find(x), set()).add(x)
    canon = {}
    for root, members in groups.items():
        # 代表名：已上市药的通用名 > 不带数字的通用名 > 其他
        pick = next((m for m in members if m in own_generics), None) or \
               min(members, key=lambda m: (bool(re.search(r"\d", m)), len(m)))
        for m in members:
            canon[m] = pick

    drugs: dict[str, dict] = {}
    single: set[str] = set()       # 至少在一个试验里是唯一的试验药 → 这家公司自己的药
    for t in trials:
        keys = []
        for n in t["drugs"]:
            al, cands = cands_of(t, n)
            if not cands or cands[0] in _BACKBONE or _norm(n) in _BACKBONE:
                continue
            k = canon.get(cands[0], cands[0])
            keys.append(k)
            word = next((o for o in al if re.fullmatch(r"[A-Za-z][a-z-]{4,}", o)), None)
            nice = f"{word.title()} ({n})" if _CODE_RE.match(n) and word else n
            e = drugs.setdefault(k, {"name": nice, "phase": -1, "trials": [], "areas": {}, "conds": {}, "mesh": {}})
            if _CODE_RE.match(e["name"]) and not _CODE_RE.match(nice):
                e["name"] = nice
            e["phase"] = max(e["phase"], t["phase"])
            e["trials"].append({"nct": t["nct"], "phase": t["phase"], "status": t["status"], "title": t["title"]})
            e["areas"][t["area"]] = e["areas"].get(t["area"], 0) + 1
            for c in t["conditions"][:3]:
                e["conds"][c] = e["conds"].get(c, 0) + 1
            for m in t["mesh"][:3]:
                e["mesh"][m] = e["mesh"].get(m, 0) + 1
        if len(set(keys)) == 1:
            single.add(keys[0])
    own, partners = [], []
    for k, e in drugs.items():
        mine = k in single or bool(_CODE_RE.match(e["name"])) or k in own_generics
        top = lambda d, n: [x for x, _ in sorted(d.items(), key=lambda kv: -kv[1])[:n]]
        nice = re.sub(r"^(SC|IV|PO)\s+|\s+(Drug|Biological|Injection)$", "", e["name"], flags=re.I)
        item = {"name": nice, "key": k, "phase": e["phase"], "n": len(e["trials"]),
                "area": top(e["areas"], 1)[0], "conditions": top(e["conds"], 4), "mesh": top(e["mesh"], 2),
                "trials": sorted(e["trials"], key=lambda x: -x["phase"])[:12]}
        (own if mine else partners).append(item)
    own.sort(key=lambda x: (-x["phase"], -x["n"]))
    partners.sort(key=lambda x: -x["n"])
    return own, [p["name"] for p in partners[:20]], len(trials)


# ── 对外接口 ─────────────────────────────────────────────────────
class Pharma:
    def __init__(self, cache_dir: str):
        self.store = _Store(cache_dir)
        self.job = {"running": False, "done": 0, "total": 0}

    def company(self, ticker: str, long_name: str | None, force: bool = False) -> dict:
        tk = ticker.upper()
        if not force:
            hit = self.store.get("co_" + tk, TTL)
            if hit:
                return hit
        names, exclude = company_names(tk, long_name)
        if not names:
            return {"ticker": tk, "error": "no company name"}
        approved, err = [], None
        try:
            approved = _approved_drugs(names, exclude)
        except Exception as e:           # FDA 当天额度用完等：管线照样给
            err = f"FDA: {e}"
            old = self.store.get("co_" + tk, 10 * TTL)
            approved = (old or {}).get("approved", [])
        gens = {_drug_key(g) for d in approved for g in (d["generic"] or "").split(", ")} | \
               {_drug_key(d["brand"]) for d in approved}
        own, partners, n_trials = _pipeline(names, exclude, gens)
        # 已上市的药还在做新适应症试验：并到上市药上，并用试验的疾病分类修正领域
        by_gen = {_drug_key(g): d for d in approved for g in (d["generic"] or "").split(", ")}
        by_gen.update({_drug_key(d["brand"]): d for d in approved})
        pipeline = []
        for p in own:
            d = by_gen.get(p["key"])
            if d:
                d.setdefault("newTrials", 0)
                d["newTrials"] += p["n"]
                if p["area"] != "other":
                    d["area"] = d.get("area") or p["area"]
                d.setdefault("mesh", p["mesh"])
            else:
                pipeline.append(p)
        for d in approved:
            lab = self.store.get("label_" + _drug_key(d["brand"]), LABEL_TTL)
            if lab and lab.get("area"):
                d["area"] = lab["area"]
        return self.store.put("co_" + tk, {
            "ticker": tk, "names": names, "approved": approved, "pipeline": pipeline,
            "partners": partners, "nTrials": n_trials, "warning": err})

    def label(self, brand: str, strict: bool = False) -> dict:
        """说明书：适应症（第一句）、作用机制、领域。按品牌名缓存 90 天。
        strict=True：年报里的产品名不一定是药名（「GENERAL」「TRAUMA」是器械分类），
        只接受说明书品牌名和它一致或以它开头的结果。"""
        key = "label_" + _drug_key(brand)
        hit = self.store.get(key, LABEL_TTL)
        if hit:
            return hit
        d = _fda_get("label", {"search": f'openfda.brand_name:"{brand}"', "limit": 5})
        res = (d or {}).get("results") or []
        if strict:
            q = _norm(brand)
            res = [r for r in res if any(_norm(b) == q or _norm(b).startswith(q)
                                         for b in (r.get("openfda", {}) or {}).get("brand_name") or [])]
        if not res:
            return self.store.put(key, {"brand": brand, "indication": None})
        # 同名前缀的品牌（搜 Keytruda 也会出 Keytruda Qlex）：品牌名完全一致的优先
        q = _norm(brand)
        res.sort(key=lambda r: not any(_norm(b) == q for b in (r.get("openfda", {}) or {}).get("brand_name") or []))
        r = res[0]
        txt = " ".join(r.get("indications_and_usage") or [])
        txt = re.sub(r"^\s*1\s*INDICATIONS AND USAGE\s*", "", txt, flags=re.I)
        txt = re.sub(r"^\s*INDICATIONS (AND|&) USAGE\s*", "", txt, flags=re.I)
        txt = re.sub(r"\s+", " ", txt).strip()
        first = re.split(r"(?<=[a-z\)])\.\s+(?=[A-Z0-9])", txt)[0][:400] if txt else None
        epc = [c.replace(" [EPC]", "") for c in (r.get("openfda", {}) or {}).get("pharm_class_epc") or []]
        return self.store.put(key, {"brand": brand, "indication": first, "full": txt[:2000], "epc": epc,
                                    "area": area_of([txt[:600]]) or _epc_area(epc)})

    def class_competitors(self, epc: str, pharma_names: dict) -> dict:
        """同一作用机制（FDA EPC）的上市药：哪些公司、哪些品牌。"""
        key = "epc_" + _norm(epc)
        hit = self.store.get(key, TTL)
        if hit:
            return hit
        d = _fda_get("drugsfda", {"search": f'openfda.pharm_class_epc:"{epc} [EPC]" AND products.marketing_status:"Prescription"',
                                  "limit": 100})
        by: dict[str, dict] = {}
        for r in d.get("results", []):
            if (r.get("application_number") or "").startswith("ANDA"):   # 仿制药不算竞争对手
                continue
            sp = ((r.get("openfda") or {}).get("manufacturer_name") or [r.get("sponsor_name", "")])[0]
            tk = sponsor_to_ticker(sp, pharma_names) or sponsor_to_ticker(r.get("sponsor_name", ""), pharma_names)
            k = tk or _norm(_strip_corp(sp))
            e = by.setdefault(k, {"sponsor": sp, "ticker": tk, "brands": []})
            for p in r.get("products", []) or []:
                b = (p.get("brand_name") or "").strip()
                b = b.title() if b.isupper() else b
                if p.get("marketing_status") == "Prescription" and b and b not in e["brands"]:
                    e["brands"].append(b)
        rows = sorted(by.values(), key=lambda e: (e["ticker"] is None, -len(e["brands"])))
        return self.store.put(key, {"epc": epc, "companies": rows[:30]})

    def condition_competitors(self, term: str, pharma_names: dict) -> dict:
        """同一疾病（MeSH 词）正在做二、三期试验的公司（只算企业申办）。"""
        key = "cond_" + _norm(term)
        hit = self.store.get(key, TTL)
        if hit:
            return hit
        studies = _ct_get({"query.cond": f'"{term}"', "filter.overallStatus": _STATUSES,
                           "filter.advanced": "AREA[Phase](PHASE2 OR PHASE3) AND AREA[LeadSponsorClass]INDUSTRY",
                           "fields": _CT_FIELDS}, max_pages=2)
        by: dict[str, dict] = {}
        for t in map(_study_parts, studies):
            tk = sponsor_to_ticker(t["sponsor"], pharma_names)
            k = tk or _norm(_strip_corp(t["sponsor"]))
            e = by.setdefault(k, {"sponsor": t["sponsor"], "ticker": tk, "phase": -1, "n": 0, "drugs": []})
            e["phase"] = max(e["phase"], t["phase"])
            e["n"] += 1
            for n in t["drugs"]:
                if _drug_key(n) not in _BACKBONE and n not in e["drugs"] and len(e["drugs"]) < 4:
                    e["drugs"].append(n)
        rows = sorted(by.values(), key=lambda e: (-e["phase"], e["ticker"] is None, -e["n"]))
        return self.store.put(key, {"term": term, "total": len(studies), "companies": rows[:40]})

    def label_areas(self) -> dict:
        """已查过说明书的品牌 {品牌 key: 领域}"""
        out = {}
        for fn in os.listdir(self.store.dir):
            if fn.startswith("label_") and fn.endswith(".json"):
                try:
                    with open(os.path.join(self.store.dir, fn)) as f:
                        d = json.load(f)
                except Exception:
                    continue
                if d.get("area") and d.get("brand"):
                    out[_norm(d["brand"])] = d["area"]
                    out[_norm(re.split(r"[\s(/-]", d["brand"])[0])] = d["area"]
        return {k: v for k, v in out.items() if len(k) >= 5 and k not in _SKIP_WORDS}

    def has_label(self, brand: str) -> bool:
        return self.store.get("label_" + _drug_key(brand), LABEL_TTL) is not None

    def cached_companies(self) -> dict:
        out = {}
        for fn in os.listdir(self.store.dir):
            if fn.startswith("co_") and fn.endswith(".json"):
                try:
                    with open(os.path.join(self.store.dir, fn)) as f:
                        d = json.load(f)
                    out[d["ticker"]] = d
                except Exception:
                    pass
        return out

    def fill(self, todo: list[tuple[str, str]]) -> None:
        """后台：给还没数据的药企逐个建档（给治疗领域总览用）。"""
        if self.job["running"]:
            return
        self.job.update(running=True, done=0, total=len(todo))
        try:
            for tk, name in todo:
                try:
                    self.company(tk, name)
                except Exception:
                    pass
                self.job["done"] += 1
        finally:
            self.job["running"] = False


# ── 收入按治疗领域分配（SEC 年报的产品收入 → 领域）──────────────────────
# 公司自己的分类名 / 疫苗等 FDA 药品库里没有的产品 → 领域
_CATEGORY_AREA = [
    ("oncology",    r"oncolog|cancer|tumou?r|hematologic ?malignan|cell ?therap"),
    ("metabolic",   r"cardiometabolic|diabet|obesity|metabolic|insulin|glp ?1"),
    ("cardio",      r"cardiovascular|cardiology|cardio|pulmonary ?hypertension"),
    ("neuro",       r"neuroscience|neurolog|alzheimer|migraine|multiple ?sclerosis"),
    ("psych",       r"psychiatr|mental"),
    ("infectious",  r"vaccin|infectious|virolog|\bhiv\b|antiviral|anti ?infective|hepatitis|covid|"
                    r"gardasil|prevnar|shingrix|varivax|rotateq|pneumovax|vaxneuvance|capvaxive|proquad|mmr|arexvy|comirnaty|spikevax"),
    ("immunology",  r"immunolog|inflammation|immunoscience|rheumat|dermatolog"),
    ("respiratory", r"respiratory|asthma|copd|cystic ?fibrosis"),
    ("hematology",  r"hematolog|blood ?disorder|hemophilia|haemophilia"),
    ("rare",        r"rare ?disease"),
    ("ophthalmo",   r"ophthalm|retina|eye ?care"),
    ("digestive",   r"liver ?disease|gastro|hepatolog"),
]
_CATEGORY_AREA_RE = [(k, re.compile(p, re.I)) for k, p in _CATEGORY_AREA]
_NOT_HUMAN_RX = re.compile(r"animal|livestock|companion|poultry|swine|cattle|aquacult", re.I)
_GENERIC_MEMBER = re.compile(r"^(us-gaap|srt|ifrs-full):|^(total|product|products|productsales|service|services|"
                             r"royalt|collaborat|alliance ?revenue$|licens|other ?revenue|other ?products?$|"
                             r"contract ?manufactur|manufactured)", re.I)
_SKIP_WORDS = {"insulin", "human", "product", "products", "other", "total", "sales", "injection", "tablets",
               "kwikpen", "vaccine", "oral", "pharmaceutical"}
# 年报里常见的非药名（器械、业务分类、合同条款）：不拿去查说明书
NOT_BRAND_WORDS = {
    "general", "advanced", "trauma", "device", "devices", "bulk", "proprietary", "event", "upfront", "hips",
    "knees", "surgical", "surgery", "foam", "spine", "sports", "contact", "lenses", "vision", "orthopaedics",
    "orthopedics", "electrophysiology", "cardiovascular", "interventional", "solutions", "salesbasedmilestone",
    "milestone", "royalty", "royalties", "license", "licensing", "collaboration", "alliance", "consumer",
    "generic", "generics", "brands", "brand", "legacy", "growth", "established", "specialty", "international",
    "biosimilars", "biosimilar", "api", "services", "service", "contract", "manufacturing", "partner", "partnered",
    "medical", "medicines", "medicine", "health", "healthcare", "care", "diagnostics", "nutrition", "animal",
    "plasma", "hospital", "retail", "wholesale", "segment", "segments", "net", "gross", "revenue", "revenues",
    "sales", "product", "products", "other", "total", "pharmaceutical", "pharmaceuticals", "innovative",
}


def brand_index(companies: dict, label_areas: dict | None = None) -> dict:
    """所有已建档药企的上市药：{品牌/通用名 key: 领域}（用来认 SEC 年报里的产品名）。
    label_areas = 按说明书适应症判断的 {品牌 key: 领域}，比按机制 / 试验推断的更准，优先用。"""
    idx: dict[str, str] = dict(label_areas or {})
    for d in companies.values():
        common = {}
        for a in d.get("approved", []) or []:
            if a.get("area"):
                common[a["area"]] = common.get(a["area"], 0) + 1
        n = sum(common.values())
        main = max(common, key=common.get) if common else None
        if main and common[main] < 0.7 * n:
            main = None                           # 多领域公司不能拿「主要领域」兜底（Eliquis 不是肿瘤药）
        for a in d.get("approved", []) or []:
            area = a.get("area") or main          # FDA 没标机制的（如 Vertex 的 Trikafta）：单一领域公司按主要领域
            if not area:
                continue
            keys = {_norm(a["brand"]), _norm(re.split(r"[\s(/-]", a["brand"])[0])}
            keys |= {_drug_key(g) for g in (a.get("generic") or "").split(", ")}
            for k in keys:
                if len(k) >= 5 and k not in _SKIP_WORDS and k not in NOT_BRAND_WORDS and k != "pharma":
                    idx.setdefault(k, area)
    return {k: v for k, v in idx.items() if k not in NOT_BRAND_WORDS and k != "pharma"}


def _category_area(name: str) -> str | None:
    for k, rx in _CATEGORY_AREA_RE:
        if rx.search(name):
            return k
    return None


def allocate_revenue(rev: dict, co: dict | None, idx: dict, humanize) -> dict | None:
    """一家公司的年报产品收入 → {areas: {领域: 金额}, products: [[名字, 金额, 领域]], method, coverage}。
    金额是年报货币。不重复计算：优先用能认出具体药品的项（以及「其他肿瘤」这类剩余项）；
    这样覆盖不到总收入一半时改用公司自己的大类；都没有但公司只有一个领域的上市药时，整家公司算进那个领域。"""
    if not rev or rev.get("error") or not rev.get("total"):
        return None
    total = rev["total"]
    members = rev.get("members") or {}
    leaves, cats, unknown = [], [], []
    for mem, v in members.items():
        if v <= 0:
            continue
        name = humanize(mem)
        if _NOT_HUMAN_RX.search(name):
            continue
        key = _norm(name)
        first = (re.split(r"[^A-Za-z0-9]+", name) or [""])[0].lower()
        # 「Pharmaceutical Products」「Net Product Sales」这类汇总项不拿去匹配药名
        area = None if first in NOT_BRAND_WORDS else next((a for k, a in idx.items() if k in key), None)
        if area:
            leaves.append([name, v, area, mem])
            continue
        cat = _category_area(name)
        generic = bool(_GENERIC_MEMBER.search(mem.split(":", 1)[-1]) or _GENERIC_MEMBER.search(mem))
        if cat and re.match(r"other", name, re.I):
            leaves.append([name, v, cat, mem])             # 「其他肿瘤」= 该领域里没单列的剩余部分
        elif cat and cat == "infectious" and re.search(r"gardasil|prevnar|shingrix|varivax|rotateq|pneumovax|vaxneuvance|capvaxive|proquad|arexvy|comirnaty|spikevax", name, re.I):
            leaves.append([name, v, cat, mem])             # 疫苗是具体产品（FDA 药品库里没有疫苗）
        elif cat and not generic and not re.match(r"total", name, re.I):
            cats.append([name, v, cat, mem])
        elif not cat and not generic and not re.match(r"total|other", name, re.I):
            unknown.append([name, v, "other", mem])        # 认不出的：可能是具体产品，也可能是「成长品牌」之类的汇总
    # 「A & B」这类小计：等于另外两项之和、名字里又有那两项的首词，去掉（REGN 的 EYLEA HD & EYLEA）
    parts = leaves + unknown
    words = lambda n: {w.lower() for w in re.split(r"[^A-Za-z0-9]+", n) if w}
    first_word = lambda n: (re.split(r"[^A-Za-z0-9]+", n) or [""])[0].lower()

    def _subtotal(x):
        ws = words(x[0])
        return any(abs(a[1] + b[1] - x[1]) <= 0.002 * x[1] and first_word(a[0]) in ws and first_word(b[0]) in ws
                   for i, a in enumerate(parts) if a is not x for b in parts[i + 1:] if b is not x)
    subs = [x for x in parts if _subtotal(x)]
    leaves = [x for x in leaves if x not in subs]
    unknown = [x for x in unknown if x not in subs]
    leaf_sum = sum(x[1] for x in leaves)
    # 从小到大加进「其他」，加了会超过总收入就跳过（汇总项一般都很大）
    run = leaf_sum
    for u in sorted(unknown, key=lambda x: x[1]):
        if run + u[1] <= total * 1.01:
            leaves.append(u)
            run += u[1]
    method, used = "products", leaves
    if leaf_sum < 0.5 * total and cats:
        # 大类之间也可能嵌套（「糖尿病」含「GLP-1」）：同一领域只取最大的一项
        best: dict[str, list] = {}
        for c in cats:
            if c[2] not in best or c[1] > best[c[2]][1]:
                best[c[2]] = c
        if sum(c[1] for c in best.values()) > leaf_sum:
            method, used = "categories", list(best.values())
    if not used:
        areas = {a.get("area") for a in (co or {}).get("approved", []) if a.get("area")}
        if len(areas) == 1:
            area = areas.pop()
            return {"areas": {area: total}, "products": [["*", total, area, "*"]],    # "*" = 整家公司的收入
                    "method": "single", "coverage": 1.0}
        return {"areas": {}, "products": [], "method": "none", "coverage": 0.0}
    areas: dict[str, float] = {}
    for x in used:
        areas[x[2]] = areas.get(x[2], 0) + x[1]
    used.sort(key=lambda x: -x[1])
    return {"areas": areas, "products": used[:40], "method": method,
            "coverage": round(min(1.0, sum(x[1] for x in used if x[2] != "other") / total), 3)}


def brand_query(name: str) -> str | None:
    """年报产品名 → 拿去查说明书的品牌名；看起来不像药名的返回 None。
    'Novo Seven' → 'NovoSeven'；'Pomalyst Imnovid' → 'Pomalyst'"""
    if not name or name == "*":
        return None
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    if not words or words[0].lower() in NOT_BRAND_WORDS:
        return None
    q = "".join(words[:2]) if len(words) == 2 and len(words[1]) <= 6 and words[1][0].isupper() and words[0][0].isupper() \
        and not any(w.lower() in NOT_BRAND_WORDS for w in words) else words[0]
    return q if len(q) >= 4 and q.lower() not in NOT_BRAND_WORDS else None


# ── 专利 / 独占期到期（FDA Orange Book：小分子药；Purple Book：生物药）────────────
# Orange Book：每月更新的 zip（products / patent / exclusivity 三个 ~ 分隔的文本）
# Purple Book：每月一个 CSV，含法定独占期、孤儿药独占期；生物类似药（351(k)）会写明参照药
ORANGE_BOOK_URL = "https://www.fda.gov/media/76860/download"
PURPLE_BOOK_URL = "https://www.accessdata.fda.gov/drugsatfda_docs/PurpleBook/{y}/purplebook-search-{m}-data-download.csv"
LOE_TTL = 30 * 86400


def _pdate(s: str) -> str | None:
    """'Aug 24, 2026' / 'August 24, 2026' → '2026-08-24'"""
    s = (s or "").strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return time.strftime("%Y-%m-%d", time.strptime(s, fmt))
        except ValueError:
            pass
    return None


def _build_loe() -> dict:
    """下载并汇总 → {"nda": {申请号: {...}}, "bla": {申请号: {...}}, "brands": {品牌 key: ("nda"|"bla", 申请号)}}"""
    import csv
    import io
    import zipfile
    out = {"nda": {}, "bla": {}, "brands": {}}
    # Orange Book
    z = zipfile.ZipFile(io.BytesIO(requests.get(ORANGE_BOOK_URL, timeout=120, headers={"User-Agent": "Mozilla/5.0"}).content))
    def rows(name):
        txt = z.read(name).decode("latin-1").splitlines()
        hdr = txt[0].split("~")
        return [dict(zip(hdr, l.split("~"))) for l in txt[1:] if l]
    ingredient_of, generics = {}, {}
    for r in rows("products.txt"):
        if r.get("Type") == "DISCN":
            continue
        ing = (r.get("Ingredient") or "").upper()
        if r.get("Appl_Type") == "A":
            generics[ing] = generics.get(ing, set()) | {r.get("Applicant_Full_Name") or r.get("Applicant")}
        elif r.get("Appl_Type") == "N":
            ingredient_of[r["Appl_No"]] = ing
            out["brands"].setdefault(_norm(r.get("Trade_Name")), ("nda", r["Appl_No"]))
    for r in rows("patent.txt"):
        d = _pdate(r.get("Patent_Expire_Date_Text"))
        if not d or r.get("Delist_Flag") == "Y":
            continue
        e = out["nda"].setdefault(r["Appl_No"], {"patents": 0})
        e["patents"] += 1
        if d > e.get("patent", ""):
            e["patent"] = d
        if r.get("Drug_Substance_Flag") == "Y" and d > e.get("substance", ""):
            e["substance"] = d                            # 化合物（活性成分）专利：通常决定仿制药什么时候能上
    for r in rows("exclusivity.txt"):
        d = _pdate(r.get("Exclusivity_Date"))
        if not d:
            continue
        e = out["nda"].setdefault(r["Appl_No"], {"patents": 0})
        if d > e.get("excl", ""):
            e["excl"], e["exclCode"] = d, r.get("Exclusivity_Code")
    for appl, ing in ingredient_of.items():
        e = out["nda"].setdefault(appl, {"patents": 0})
        e["generics"] = len(generics.get(ing, ()))
    # Purple Book：从本月往前找最近一份
    t0 = time.localtime()
    for back in range(0, 4):
        y, mo = t0.tm_year, t0.tm_mon - back
        while mo <= 0:
            y, mo = y - 1, mo + 12
        r = requests.get(PURPLE_BOOK_URL.format(y=y, m=time.strftime("%B", (y, mo, 1, 0, 0, 0, 0, 1, -1))),
                         timeout=120, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and "BLA Number" in r.text[:3000]:
            break
    else:
        return out
    lines = r.content.decode("utf-8-sig", "replace").splitlines()
    hi = next(i for i, l in enumerate(lines) if "BLA Number" in l)
    bios: dict[str, set] = {}
    for row in csv.DictReader(lines[hi:]):
        bla = (row.get("BLA Number") or "").strip()
        if not bla:
            continue
        if (row.get("License Type") or "").startswith("351(k)"):
            ref = _norm(row.get("Ref. Product Proprietary Name"))
            if ref:
                bios.setdefault(ref, set()).add(bla)
            continue
        e = out["bla"].setdefault(bla, {"patents": 0})
        out["brands"].setdefault(_norm(row.get("Proprietary Name")), ("bla", bla))
        first = _pdate(row.get("Date of First Licensure")) or _pdate(row.get("Approval Date"))
        for col, key in (("Ref. Product Exclusivity Exp. Date", "excl"), ("Exclusivity Expiration Date", "excl"),
                         ("Orphan Exclusivity Exp. Date", "orphan")):
            d = _pdate(row.get(col))
            if d and d > e.get(key, ""):
                e[key] = d
        if first and (not e.get("first") or first < e["first"]):
            e["first"] = first
        e["brand"] = _norm(row.get("Proprietary Name"))
    for bla, e in out["bla"].items():
        if not e.get("excl") and e.get("first"):          # 法定 12 年独占期（没写明时按首次批准日推算）
            y, rest = int(e["first"][:4]), e["first"][4:]
            e["excl"], e["exclCalc"] = f"{y + 12}{rest}", True
        e["biosimilars"] = len(bios.get(e.get("brand") or "", ()))
    return out


class LoeStore:
    def __init__(self, cache_dir: str):
        self.path = os.path.join(cache_dir, "loe.json")
        self._data = None
        self._lock = threading.Lock()

    def data(self) -> dict:
        with self._lock:
            if self._data and time.time() - self._data.get("ts", 0) < LOE_TTL:
                return self._data
            try:
                with open(self.path) as f:
                    d = json.load(f)
                if time.time() - d.get("ts", 0) < LOE_TTL:
                    self._data = d
                    return d
            except Exception:
                d = None
            try:
                d = {**_build_loe(), "ts": time.time()}
                with open(self.path + ".tmp", "w") as f:
                    json.dump(d, f)
                os.replace(self.path + ".tmp", self.path)
            except Exception:
                if not d:
                    d = {"nda": {}, "bla": {}, "brands": {}, "ts": 0}
            self._data = d
            return d

    def lookup(self, app: str | None, brand: str) -> dict | None:
        """上市药 → {kind, patent, patents, excl, exclCode, orphan, generics, biosimilars, loe}；loe = 最晚的到期日"""
        d = self.data()
        kind = num = None
        # 先按品牌名精确查：同一品牌下可能合并了新剂型（Keytruda + 2025 年的 Keytruda Qlex），
        # 申请号可能是新剂型的，独占期会晚很多
        hit = d["brands"].get(_norm(brand))
        if hit:
            kind, num = hit
        m = re.match(r"(NDA|BLA|ANDA)0*(\d+)", app or "")
        if not kind and m and m.group(1) in ("NDA", "BLA"):
            kind, num = m.group(1).lower(), m.group(2).zfill(6)
            if num not in d[kind]:
                kind = num = None
        if not kind:
            hit = d["brands"].get(_norm(re.split(r"[\s(/-]", brand)[0]))
            if hit:
                kind, num = hit
        e = d[kind].get(num) if kind else None
        if not e:
            return None
        # 估计的独占结束日（loss of exclusivity）：
        #   小分子：化合物专利和独占期里较晚的（没有化合物专利时用最晚的专利）；后续的用法/装置专利常被挑战，不算
        #   生物药：法定 12 年独占期；孤儿药独占只覆盖某个罕见病适应症，不算；Purple Book 不列专利日期
        if kind == "nda":
            dates = [x for x in (e.get("substance") or e.get("patent"), e.get("excl")) if x]
        else:
            dates = [x for x in (e.get("excl"),) if x]
        return {**{k: v for k, v in e.items() if k != "brand"}, "kind": kind, "loe": max(dates) if dates else None}
