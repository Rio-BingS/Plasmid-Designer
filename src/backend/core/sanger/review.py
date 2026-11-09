"""复核队列与人工确认（goal v3 W14；PL §F）

复核队列最小化（goal §0.7）：只收「人工不看就无法收口」且在声明范围内的
条目（PL F1）——确证差异（fail_impact）、范围内覆盖缺口（read_shortfall
与未知原因缺口）、未决位点（PENDING_KINDS）。`scope_beyond_primer` 不入队
（引物够不到的缺口动作明确：加引物——blocking 条目自身带 action 建议）；
混合样品另有逐 read 混合详情展示，读取失败单独成段，均不进队列。

W8 起 blocking 本身就只含声明范围内的问题（范围外差异走 out_of_scope
info，不判不阻断），队列从 blocking 派生即天然「范围内」，无需再过滤。

一键确认（PL F3）：对队列项做「伪影 / 突变」定夺，写入 review_decisions
（ref_pos/decision/by/at/note）留痕；build_verdict 接收 decisions 重算——
伪影条目移出 blocking（info 留痕），突变条目升级确证（fail_impact），
headline 追加「人工确认 N 处」。已确认条目不再进队列。
"""

from typing import Dict, List, Optional

from core.sanger.verdict import PENDING_KINDS

# prio 顺序（PL F4）：确证差异 > 覆盖缺口 > 未决位点
_PRIO_ORDER = ("fail", "read_shortfall", "pending")

# prio → 收哪些 blocking kind
_PRIO_KINDS = {
    "fail": None,  # None = 按 fail_impact 标记收（确证差异，kind 不限）
    "read_shortfall": {"scope_read_shortfall", "scope_uncovered"},
    "pending": PENDING_KINDS,
}


def _prio_of(b: Dict) -> Optional[str]:
    if b.get("fail_impact"):
        return "fail"
    for p in _PRIO_ORDER[1:]:
        if b.get("kind") in _PRIO_KINDS[p]:
            return p
    return None


def build_queue(res: Dict, review_decisions: Optional[List[Dict]] = None
                ) -> List[Dict]:
    """从 analyze 结果生成复核队列（纯函数，只读）。

    返回 [{prio, kind, ref_start, ref_end, element, detail, action,
    evidence}]，按 fail > read_shortfall > pending 排序（组内按位点）。
    evidence 为该条目的核对素材（缺口带 coverage.gaps 的 E 归因；双峰带
    af；位点带 refs——±10bp 峰图由前端证据面板按 ref_start 拉取）。

    review_decisions（PL F3）：已人工确认的位点不再进队列（伪影条目
    已被 build_verdict 移出 blocking，自然不出现；突变条目在此剔除）。
    """
    done = {d.get("ref_pos") for d in review_decisions or []}
    queue: List[Dict] = []
    vd = res.get("verdict") or {}
    gaps = {g.get("start"): g for g in
            (res.get("coverage") or {}).get("gaps") or []}
    for b in vd.get("blocking") or []:
        prio = _prio_of(b)
        if prio is None:
            continue
        if b.get("ref_start") in done:
            continue
        ev: Dict = {"refs": b.get("refs") or []}
        rs = b.get("ref_start")
        if b.get("kind") in _PRIO_KINDS["read_shortfall"] and rs in gaps:
            g = gaps[rs]
            ev["gap"] = {"start": g.get("start"), "end": g.get("end"),
                         "length": g.get("length"), "cause": g.get("cause"),
                         "detail": g.get("detail")}
            ev["expected_ranges"] = (res.get("coverage") or {}).get(
                "expected_ranges") or []
        if b.get("kind") == "minor_af_site":
            ev["af"] = b.get("af")
        queue.append({
            "prio": prio,
            "kind": b.get("kind"), "ref_start": b.get("ref_start"),
            "ref_end": b.get("ref_end"), "element": b.get("element"),
            "detail": b.get("detail"), "action": b.get("action"),
            "evidence": ev,
        })
    queue.sort(key=lambda q: (_PRIO_ORDER.index(q["prio"]),
                              q["ref_start"] if q["ref_start"] is not None else -1))
    return queue


def apply_review_decision(decisions: List[Dict], ref_pos: int, decision: str,
                          by: str, note: str = "") -> Dict:
    """追加一条人工确认记录并返回它（PL F3：reviewed_by/at 留痕）。

    decision ∈ {"artifact", "mutation"}；调用方负责校验并持久化。"""
    from datetime import datetime
    item = {
        "ref_pos": int(ref_pos),
        "decision": decision,
        "by": by,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    if note:
        item["note"] = note
    decisions.append(item)
    return item
