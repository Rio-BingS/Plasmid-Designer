"""复核队列（goal v3 W14；SC6 在 W6/W7 闸门提前接线最小形态）

复核队列最小化（goal §0.7）：只收「人工不看就无法收口」的条目——
read_shortfall 缺口（引物够得到但没测到）、minor_af_site 低丰度双峰、
范围外关键元件确证差异等 pending 级阻断。pass/fail 的明确结论不进队列。
每项带 evidence（缺口归因、逐 read 证据），供峰图核对与 W14 的一键
确认留痕扩展。
"""

from typing import Dict, List

# 进队列的 blocking kind（pending 级待人工条目；fail/pass 不进）
_QUEUE_KINDS = {
    "scope_read_shortfall", "scope_uncovered", "scope_beyond_primer",
    "minor_af_site", "critical_element_unverified", "roi_unverified",
    "critical_element_variant", "unresolved_site", "unresolved_variant",
    "n_in_cds", "poly_unreliable", "junction_variant",
}


def build_queue(res: Dict) -> List[Dict]:
    """从 analyze 结果生成复核队列（纯函数，只读）。

    返回 [{kind, ref_start, ref_end, element, detail, action, evidence}]：
    evidence 为该条目的核对素材（缺口带 coverage.gaps 的 E 归因；双峰带
    af 与逐 read 名单；位点带 reads）。"""
    queue: List[Dict] = []
    vd = res.get("verdict") or {}
    gaps = {g.get("start"): g for g in
            (res.get("coverage") or {}).get("gaps") or []}
    for b in vd.get("blocking") or []:
        if b.get("kind") not in _QUEUE_KINDS:
            continue
        ev: Dict = {"refs": b.get("refs") or []}
        rs = b.get("ref_start")
        if b.get("kind") in ("scope_read_shortfall", "scope_beyond_primer",
                             "scope_uncovered") and rs in gaps:
            g = gaps[rs]
            ev["gap"] = {"start": g.get("start"), "end": g.get("end"),
                         "length": g.get("length"), "cause": g.get("cause"),
                         "detail": g.get("detail")}
            ev["expected_ranges"] = (res.get("coverage") or {}).get(
                "expected_ranges") or []
        if b.get("kind") == "minor_af_site":
            ev["af"] = b.get("af")
        queue.append({
            "kind": b.get("kind"), "ref_start": b.get("ref_start"),
            "ref_end": b.get("ref_end"), "element": b.get("element"),
            "detail": b.get("detail"), "action": b.get("action"),
            "evidence": ev,
        })
    return queue
