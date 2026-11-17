# -*- coding: utf-8 -*-
"""S0a 元件补全（二期 10e①，refactor_spec §3；纯函数，不改入参）

三级补全，前一级缺失时才用后一级：
1. 参考里的标注（source="annotated"）：类别按 type / 名称定。
2. 自动识别（source="auto"）：复用 core.plasmid_map.annotate（元件库 66 条）。
   一致度 ≥ AUTO_IDENTITY_MIN 且覆盖 ≥ AUTO_COVERAGE_MIN 时与标注同等对待；
   达不到时 capped=True（影响分级最高 P2）。与已标注同类元件重叠的命中跳过。
3. 从 CDS 推定（source="inferred"，capped=True）：以 ATG 为锚点，
   SD −15..−5（原核）、Kozak −6..+4（真核）、上游 −150..−1 推定调控区。
   宿主推断：见 T7/lac/tac/trc/araBAD 等 → 原核（SD）；见 CMV/EF1a/SV40/
   CAG/PGK 等 → 真核（Kozak）；两类都有或推断不出 → 两个都不推定。

元件按重要性排序（IMPORTANCE），下游第二层报告按此排序。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

ELEMENTS_VERSION = "e1"

# 用户 2026-11-17 21:39 拍板：自动识别阈值一致度 ≥90%、覆盖 ≥80%
AUTO_IDENTITY_MIN = 0.90
AUTO_COVERAGE_MIN = 0.80

SD_WINDOW = (-15, -5)       # 相对 ATG 首碱基（A=0）
KOZAK_WINDOW = (-6, 4)
UPSTREAM_WINDOW = (-150, -1)

# 类别 → 重要性（越小越重要）
IMPORTANCE = {
    "ori": 0, "resistance": 1, "marker": 2, "promoter": 3, "rbs": 4,
    "recomb": 5, "cds": 6, "terminator": 7, "polya": 8, "tag": 9,
    "primer_bind": 10, "regulatory_inferred": 11, "other": 12,
}
CATEGORY_LABEL = {
    "ori": "复制原点", "resistance": "抗性基因", "marker": "筛选标记",
    "promoter": "启动子", "rbs": "SD/Kozak", "recomb": "重组位点",
    "cds": "CDS", "terminator": "终止子", "polya": "polyA 信号",
    "tag": "标签", "primer_bind": "引物结合区",
    "regulatory_inferred": "推定调控区", "other": "元件",
}
SOURCE_LABEL = {"annotated": "标注", "auto": "自动识别", "inferred": "推定"}

_RES_RE = re.compile(r"(?<![a-z])(ampr|amp|bla|kanr|kan|nptii|neor|cmr|cat|"
                     r"hygr|hph|puror|pac|zeor|sh ble|bsd|bsr|gmr|tcr|tetr?|"
                     r"specr|aada|strr)(?![a-z])", re.I)
# 酵母营养缺陷标记（是否算关键元件待用户确认，暂按 marker，影响同抗性 P1）
_AUX_RE = re.compile(r"(?<![a-z])(ura3|his3|leu2|trp1|lys2|ade2|met15)(?![a-z])", re.I)
_ORI_RE = re.compile(r"(?<![a-z])(ori|origin|puc ori|pbr322 ori|colE1|p15a|f1 ori|"
                     r"2μ|2 ?micron|sv40 ori|pmb1)(?![a-z])", re.I)
_RECOMB_RE = re.compile(r"(?<![a-z])(loxp|lox2272|loxn|frt|att[bplr]\d?|itr|tn7[lr])(?![a-z])", re.I)
_TAG_RE = re.compile(r"(?<![a-z])(?:\d+\s*x\s*)?(his|flag|ha|myc|strep|gst|mbp|v5|"
                     r"t7 tag|acv5|sumo|avi)(?:[-_ ]?tag)?(?:[-_ ]?ii)?(?![a-z])", re.I)
_RBS_RE = re.compile(r"(?<![a-z])(rbs|kozak|shine.?dalgarno|sd)(?![a-z])", re.I)

_PROK_PROM_RE = re.compile(r"(?<![a-z])(t7|lac|lacuv5|tac|trc|ara|arabad|pbad|"
                           r"t5|lambda|pl|pr|tet|rham|j231\d\d)(?![a-z])", re.I)
_EUK_PROM_RE = re.compile(r"(?<![a-z])(cmv|ef-?1a|ef1α|sv40|cag|pgk|ubc|"
                          r"rsv|gal1|tef1?|gpd|adh1|cyc1|polyhedrin|u6|h1)(?![a-z])", re.I)


def category_of(ftype: str, name: str) -> str:
    t = (ftype or "").lower()
    n = name or ""
    if t == "rep_origin" or (t in ("misc_feature", "") and _ORI_RE.search(n)):
        return "ori"
    if t == "promoter":      # 「AmpR promoter」是启动子，不是抗性基因
        return "promoter"
    if t == "marker" or (t in ("cds", "gene", "misc_feature", "") and _RES_RE.search(n)):
        return "resistance" if (_RES_RE.search(n) or not _AUX_RE.search(n)) else "marker"
    if _AUX_RE.search(n) and t in ("cds", "marker"):
        return "marker"
    if t == "promoter":
        return "promoter"
    if t in ("rbs",) or (_RBS_RE.search(n) and t not in ("cds", "gene")):
        return "rbs"
    if t == "misc_recomb" or _RECOMB_RE.search(n):
        return "recomb"
    if t == "cds":           # gene 特征常是区段标注（如 polyA 尾），不按 CDS 定后果
        return "cds"
    if t == "terminator":
        return "terminator"
    if t in ("polya_signal", "polya_site"):
        return "polya"
    if t == "tag" or _TAG_RE.search(n):
        return "tag"
    if t == "primer_bind":
        return "primer_bind"
    return "other"


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0) + 1)


@lru_cache(maxsize=64)
def _auto_hits(ref: str, circular: bool) -> Tuple[Dict, ...]:
    if os.environ.get("SANGER_ELEMENTS_AUTO", "1") != "1":
        return ()
    try:
        from core.plasmid_map.annotate import annotate
        return tuple(annotate(ref, circular))
    except Exception:  # pragma: no cover - 元件库缺失时退化为只用标注
        return ()


def infer_host(elements: List[Dict]) -> str:
    """prokaryotic / eukaryotic / unknown（两类都有或都无 → unknown）"""
    prok = euk = False
    for e in elements:
        if e["category"] != "promoter":
            continue
        n = e["name"] or ""
        if _EUK_PROM_RE.search(n):
            euk = True
        elif _PROK_PROM_RE.search(n):
            prok = True
    if prok and not euk:
        return "prokaryotic"
    if euk and not prok:
        return "eukaryotic"
    return "unknown"


def _cds_starts(features: List[Dict]) -> List[Tuple[str, int, str]]:
    """(名称, ATG 首碱基参考坐标, 链)；负链取 end"""
    out = []
    for f in features or []:
        if (f.get("type") or "") != "CDS":
            continue
        try:
            s, e = int(f["start"]), int(f["end"])
        except (KeyError, TypeError, ValueError):
            continue
        strand = f.get("strand", "+")
        neg = strand in ("-", -1, "-1")
        out.append((f.get("name") or "CDS", e if neg else s, "-" if neg else "+"))
    return out


def _window(anchor: int, strand: str, lo: int, hi: int, L: int) -> Optional[Tuple[int, int]]:
    if strand == "+":
        a, b = anchor + lo, anchor + hi
    else:
        a, b = anchor - hi, anchor - lo
    a, b = max(1, a), min(L, b)
    return (a, b) if a <= b else None


def complete_elements(ref: str, features: Optional[List[Dict]] = None,
                      circular: bool = False) -> Dict:
    """返回 {"elements": [...], "host": str, "version": str}。

    元件：{name, type, category, start, end, strand, source, capped,
    identity?, coverage?, importance}。按 (importance, start) 排序。
    """
    L = len(ref or "")
    feats = features or []
    els: List[Dict] = []
    for f in feats:
        try:
            s, e = int(f["start"]), int(f["end"])
        except (KeyError, TypeError, ValueError):
            continue
        cat = category_of(f.get("type") or "", f.get("name") or "")
        els.append({"name": f.get("name") or CATEGORY_LABEL[cat], "type": f.get("type"),
                    "category": cat, "start": s, "end": e,
                    "strand": f.get("strand", "+"), "source": "annotated",
                    "capped": False})
    for h in _auto_hits((ref or "").upper(), bool(circular)):
        s, e = int(h["start"]), int(h["end"])
        if e < s:      # 跨原点命中：本阶段不拆段，跳过
            continue
        cat = category_of(h.get("type") or "", h.get("name") or "")
        if any(x["source"] == "annotated" and x["category"] == cat
               and _overlap(s, e, x["start"], x["end"]) > 0 for x in els):
            continue
        a = h.get("auto") or {}
        ok = (float(a.get("identity") or 0) >= AUTO_IDENTITY_MIN
              and float(a.get("coverage") or 0) >= AUTO_COVERAGE_MIN)
        els.append({"name": h.get("name"), "type": h.get("type"), "category": cat,
                    "start": s, "end": e, "strand": h.get("strand"),
                    "source": "auto", "capped": not ok,
                    "identity": a.get("identity"), "coverage": a.get("coverage")})
    host = infer_host(els)
    if L:
        for name, anchor, strand in _cds_starts(feats):
            wins = [("regulatory_inferred", f"{name} 上游推定调控区", UPSTREAM_WINDOW)]
            if host == "prokaryotic":
                wins.append(("rbs", f"{name} SD（推定）", SD_WINDOW))
            elif host == "eukaryotic":
                wins.append(("rbs", f"{name} Kozak（推定）", KOZAK_WINDOW))
            for cat, nm, (lo, hi) in wins:
                w = _window(anchor, strand, lo, hi, L)
                if not w:
                    continue
                # 已有标注/自动识别的同类元件覆盖该窗口 → 不再推定
                if any(x["category"] in (cat, "promoter", "rbs") and x["source"] != "inferred"
                       and _overlap(w[0], w[1], x["start"], x["end"]) > 0 for x in els):
                    if cat == "rbs":
                        continue
                if any(x["source"] == "annotated" and x["category"] == cat
                       and _overlap(w[0], w[1], x["start"], x["end"]) > 0 for x in els):
                    continue
                els.append({"name": nm, "type": "inferred", "category": cat,
                            "start": w[0], "end": w[1], "strand": strand,
                            "source": "inferred", "capped": True})
    for e in els:
        e["importance"] = IMPORTANCE.get(e["category"], 99)
    els.sort(key=lambda e: (e["importance"], e["start"], e["end"]))
    return {"elements": els, "host": host, "version": ELEMENTS_VERSION}
