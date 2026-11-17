# -*- coding: utf-8 -*-
"""Impact 影响分级（二期 10e，impact_contract.md v1.0；纯函数）

输入：变体（S4 位点结论，已带 S5 后果注释）、S0a 元件（elements.complete_elements）、
设计酶集合（design_enzymes.build_design_enzymes）。输出 ImpactItem 列表。
不含范围与层级字段——分层由 S6（verdict）负责。

- 一个对象一条：元件 / 设计酶位点 / 骨架。同一对象内多处差异合并，level 取最高。
- rejected 位点不参与分级。
- 结构级 poly 由 PolyCall 进主结论，不产生条目（调用方传入的变体已不含 poly 计数）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

IMPACT_VERSION = "i1"
LEVEL_RANK = {"P1": 0, "P2": 1, "P3": 2}

# 元件类别 → (级别, 规则码)
_ELEMENT_RULE = {
    "ori": ("P1", "IMP_ORI"),
    "resistance": ("P1", "IMP_RESISTANCE"),
    "marker": ("P1", "IMP_RESISTANCE"),
    "promoter": ("P1", "IMP_PROMOTER"),
    "rbs": ("P1", "IMP_RBS"),
    "recomb": ("P1", "IMP_RECOMB_SITE"),
    "regulatory_inferred": ("P2", "IMP_REG_INFERRED"),
    "terminator": ("P2", "IMP_TERMINATOR"),
    "polya": ("P2", "IMP_POLYA"),
    "tag": ("P2", "IMP_TAG"),
    "primer_bind": ("P2", "IMP_PRIMER_SITE"),
    "other": ("P2", "IMP_ELEMENT_OTHER"),
}
RULE_TEXT = {
    "IMP_ORI": "复制原点内差异", "IMP_RESISTANCE": "抗性/筛选基因内差异",
    "IMP_PROMOTER": "启动子内差异", "IMP_RBS": "SD/Kozak 窗口内差异",
    "IMP_RECOMB_SITE": "重组位点内差异", "IMP_CDS_FRAMESHIFT": "移码",
    "IMP_CDS_NONSENSE": "无义突变", "IMP_CDS_START_LOST": "起始密码子丢失",
    "IMP_CDS_STOP_LOST": "终止密码子丢失", "IMP_ENZYME_DESTROYED": "设计酶位点被破坏",
    "IMP_CDS_MISSENSE": "错义", "IMP_CDS_INFRAME_INDEL": "整码 indel",
    "IMP_REG_INFERRED": "推定调控区内差异", "IMP_TERMINATOR": "终止子内差异",
    "IMP_POLYA": "polyA 信号内差异", "IMP_TAG": "标签内差异",
    "IMP_PRIMER_SITE": "引物结合区内差异", "IMP_ENZYME_CREATED": "新引入设计酶/避开酶位点",
    "IMP_ELEMENT_OTHER": "元件内差异", "IMP_CAPPED": "元件来源未确证，封顶 P2",
    "IMP_CDS_SYNONYMOUS": "同义", "IMP_BACKBONE": "无注释骨架区差异",
}


def _vstatus(v: Dict) -> str:
    from core.sanger.sitecall import site_status
    return site_status(v)


def _span(v: Dict):
    rp = int(v.get("ref_pos") or 0)
    n = int(v.get("length") or 1) if v.get("type") == "deletion" else 1
    return rp, rp + n - 1


def _cds_rule(v: Dict):
    """非目标 CDS 内的后果 → (级别, 规则)；无后果信息返回 None"""
    if v.get("frameshift"):
        return ("P1", "IMP_CDS_FRAMESHIFT")
    aa = str(v.get("aa_change") or "")
    if aa:
        body = aa.split(":", 1)[-1]
        if body.endswith("*"):
            return ("P1", "IMP_CDS_NONSENSE")
        if body.startswith("M1") or body.startswith("x1"):
            return ("P1", "IMP_CDS_START_LOST")
        if body.startswith("*"):
            return ("P1", "IMP_CDS_STOP_LOST")
        return ("P2", "IMP_CDS_MISSENSE")
    if v.get("type") in ("insertion", "deletion"):
        return ("P2", "IMP_CDS_INFRAME_INDEL")
    if v.get("synonymous"):
        return ("P3", "IMP_CDS_SYNONYMOUS")
    return ("P3", "IMP_CDS_SYNONYMOUS") if v.get("type") == "substitution" else None


def _better(a, b):
    return a if LEVEL_RANK[a[0]] <= LEVEL_RANK[b[0]] else b


def build_impact_items(variants: Sequence[Dict], elements: Sequence[Dict],
                       design: Optional[Dict] = None,
                       target_cds: Optional[set] = None,
                       host: str = "unknown") -> List[Dict]:
    design = design or {}
    dnames = {e["name"] for e in design.get("enzymes") or []}
    avoid = set(design.get("avoid") or [])
    target_cds = target_cds or set()
    items: Dict[str, Dict] = {}

    def _add(item_id, target, source, coverage_hint, v, lvl_rule, capped=False):
        it = items.get(item_id)
        if it is None:
            it = items[item_id] = {
                "item_id": item_id, "target": target, "source": source,
                "coverage": None, "site_ids": [], "consequences": [],
                "level": lvl_rule[0], "rule": lvl_rule[1], "cap_applied": capped,
                "certainty": "suspected", "af_max": None, "host": host,
                "params_version": IMPACT_VERSION, "_lv": {}}
        st = _vstatus(v)
        rp = int(v.get("ref_pos") or 0)
        if rp not in it["site_ids"]:
            it["site_ids"].append(rp)
            it["consequences"].append({
                "ref_pos": rp, "type": v.get("type"), "status": st,
                "consequence": v.get("consequence") or v.get("aa_change"),
                "level": lvl_rule[0], "rule": lvl_rule[1]})
        best = _better(lvl_rule, (it["level"], it["rule"]))
        if (best[0], best[1]) != (it["level"], it["rule"]):
            it["level"], it["rule"] = best
            it["cap_applied"] = capped
        conf = st == "confirmed" or bool(v.get("mixed_af") or v.get("is_mixed"))
        it["_lv"].setdefault(lvl_rule[0], False)
        it["_lv"][lvl_rule[0]] = it["_lv"][lvl_rule[0]] or conf
        af = v.get("mixed_af")
        if af is not None:
            it["af_max"] = max(float(af), it["af_max"] or 0.0)

    for v in variants or []:
        st = _vstatus(v)
        if st == "rejected" or v.get("artifact"):
            continue
        a, b = _span(v)
        hit_any = False
        for e in elements or []:
            if not (e["start"] <= b and a <= e["end"]):
                continue
            hit_any = True
            eid = f"element:{e['category']}:{e['name']}:{e['start']}-{e['end']}"
            tgt = {"kind": "element", "name": e["name"], "category": e["category"],
                   "start": e["start"], "end": e["end"]}
            if e["category"] == "cds":
                if e["name"] in target_cds:
                    continue
                lr = _cds_rule(v)
                if lr is None:
                    continue
                capped = False
                if e.get("capped") and lr[0] == "P1":
                    lr, capped = ("P2", "IMP_CAPPED"), True
                _add(eid, tgt, e["source"], None, v, lr, capped)
                continue
            lr = _ELEMENT_RULE.get(e["category"], ("P2", "IMP_ELEMENT_OTHER"))
            capped = False
            if e.get("capped") and lr[0] == "P1":
                lr, capped = ("P2", "IMP_CAPPED"), True
            _add(eid, tgt, e["source"], None, v, lr, capped)
        for name in v.get("enzyme_sites_lost") or []:
            n = name.split("@")[0] if isinstance(name, str) else str(name)
            if n in dnames:
                hit_any = True
                _add(f"enzyme:{n}", {"kind": "enzyme_site", "name": n, "category": "enzyme"},
                     "design", None, v, ("P1", "IMP_ENZYME_DESTROYED"))
        for name in v.get("enzyme_sites_gained") or []:
            n = name.split("@")[0] if isinstance(name, str) else str(name)
            if n in dnames or n in avoid:
                hit_any = True
                _add(f"enzyme:{n}", {"kind": "enzyme_site", "name": n, "category": "enzyme"},
                     "design", None, v, ("P2", "IMP_ENZYME_CREATED"))
        if not hit_any:
            _add(f"backbone:{a}", {"kind": "backbone", "name": "骨架", "category": "backbone",
                                   "start": a, "end": b},
                 "none", None, v, ("P3", "IMP_BACKBONE"))
    out = []
    for it in items.values():
        it["certainty"] = "confirmed" if it["_lv"].get(it["level"]) else "suspected"
        del it["_lv"]
        out.append(it)
    out.sort(key=lambda i: (LEVEL_RANK[i["level"]], 0 if i["certainty"] == "confirmed" else 1,
                            min(i["site_ids"] or [0])))
    return out
