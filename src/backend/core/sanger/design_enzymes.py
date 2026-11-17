# -*- coding: utf-8 -*-
"""S0a 设计酶集合（二期 10e②③，用户 2026-11-17 21:39 拍板方案 C；纯函数）

按优先级取第一个非空的来源：
1. 用户指定（source="user"）：请求参数 enzymes ∪ 参考里的 pd_enzyme 标注
   （本项目构建导出写入的 misc_feature /note="pd_enzyme role=…"）。
2. 插入片段 / 声明范围两端的单切酶（source="flank"）：常用酶菜单中在全质粒
   只切一次、且位点落在范围边界外 FLANK_OUT bp 到内 FLANK_IN bp 内的酶。
3. 全质粒单切酶（source="plasmid"）：常用酶菜单中在全质粒只切一次的酶。

另：参考文件级 COMMENT「pd_design_enzymes: avoid=A,B」给出的避开酶（无位点），
不论哪一级都并入 avoid；变异新引入 avoid 酶位点判 P2（IMP_ENZYME_CREATED）。

常用酶菜单即 core.enzyme_sites.DEFAULT_ENZYMES（51 种，全站唯一酶库）。
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DESIGN_ENZYMES_VERSION = "d1"
FLANK_OUT = 100
FLANK_IN = 20

_PD_NOTE_RE = re.compile(r"pd_enzyme\s+role=([a-z_|]+)", re.I)
_AVOID_RE = re.compile(r"pd_design_enzymes:\s*avoid=([A-Za-z0-9_,\s-]*)")


def parse_enzyme_names(raw) -> List[str]:
    """'EcoRI, BamHI' / ['EcoRI'] → 规范拼写列表（未知酶原样保留，扫描时跳过）"""
    from core.enzyme_sites import canonical_enzyme_name
    if not raw:
        return []
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[,;，；\s]+", str(raw))
    out = []
    for x in items:
        x = str(x).strip()
        if x:
            n = canonical_enzyme_name(x)
            if n not in out:
                out.append(n)
    return out


def annotated_enzymes(features: Sequence[Dict]) -> List[Tuple[str, str]]:
    """参考里 pd_enzyme 标注 → [(酶名, role)]"""
    out = []
    for f in features or []:
        if f.get("pd_enzyme_role"):
            out.append((str(f.get("name")), str(f["pd_enzyme_role"])))
            continue
        q = f.get("qualifiers") or {}
        note = " ".join(str(x) for x in (q.get("note") if isinstance(q.get("note"), list)
                                         else [q.get("note") or f.get("note") or ""]))
        m = _PD_NOTE_RE.search(note)
        if not m:
            continue
        label = q.get("label") or f.get("name")
        if isinstance(label, list):
            label = label[0] if label else None
        if label:
            out.append((str(label), m.group(1).lower()))
    return out


def avoid_from_comment(comment: Optional[str]) -> List[str]:
    m = _AVOID_RE.search(comment or "")
    return parse_enzyme_names(m.group(1)) if m else []


def _sites(ref: str, names: Optional[Iterable[str]], circular: bool) -> Dict[str, List[int]]:
    from core.enzyme_sites import find_enzyme_sites
    by: Dict[str, set] = {}
    for s in find_enzyme_sites(ref, list(names) if names else None, circular):
        by.setdefault(s["name"], set()).add(int(s["position"]))
    return {k: sorted(v) for k, v in by.items()}


def build_design_enzymes(ref: str, features: Optional[Sequence[Dict]] = None,
                         circular: bool = False,
                         scope_ranges: Optional[Sequence[Tuple[int, int]]] = None,
                         user_enzymes=None, comment: Optional[str] = None) -> Dict:
    ref = (ref or "").upper()
    avoid = avoid_from_comment(comment)
    ann = annotated_enzymes(features or [])
    user = parse_enzyme_names(user_enzymes)
    roles: Dict[str, str] = {}
    for n, role in ann:
        roles.setdefault(n, role)
    for n in user:
        roles.setdefault(n, "user")
    level = None
    chosen: Dict[str, List[int]] = {}
    if roles:
        level = "user"
        sites = _sites(ref, roles.keys(), circular)
        chosen = {n: sites.get(n, []) for n in roles}
    else:
        all_sites = _sites(ref, None, circular)
        single = {n: p for n, p in all_sites.items() if len(p) == 1}
        if scope_ranges:
            flank = {}
            for n, p in single.items():
                pos = p[0]
                for s_, e_ in scope_ranges:
                    if (s_ - FLANK_OUT <= pos <= s_ + FLANK_IN
                            or e_ - FLANK_IN <= pos <= e_ + FLANK_OUT):
                        flank[n] = p
                        break
            if flank:
                level, chosen = "flank", flank
        if level is None and single:
            level, chosen = "plasmid", single
        roles = {n: level for n in chosen}
    return {
        "level": level,
        "enzymes": [{"name": n, "role": roles.get(n), "sites": chosen[n]}
                    for n in sorted(chosen)],
        "avoid": avoid,
        "version": DESIGN_ENZYMES_VERSION,
    }


def design_names(design: Optional[Dict]) -> set:
    return {e["name"] for e in (design or {}).get("enzymes") or []}
