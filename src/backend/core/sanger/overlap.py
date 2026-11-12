"""重叠区分歧与双链覆盖（goal T21；A 4.4）

多条 read 覆盖同一参考位却报出**不同的非参考碱基**（且近平局）时，谁对
谁错无法由数据本身裁决——旧口径按投票顺序静默取一边（M3：C>A 与 C>G
同位同权，共识悄悄写 A，两条 read 各自 confirmed high 双双判 ✗，真相
反而被掩盖）。

- 近平局分歧（票差 ≤ DISCORDANCE_TOP_FRAC × 头名票，且双方都是非参考
  碱基、非 exclude read）→ 共识 IUPAC 合并码 + unresolved 位点（pending）+
  冲突变体双方降 unresolved；票差记录随位点下发（各 read 调用/Q）。
  alt-vs-ref 的近平局不在此列——单 read 变体 vs 他 read 参考走既有互检
  （T8）与置信链路，多数票压制与今日一致。
- double_strand_fraction：ROI 内正负两链都被 read 覆盖的 bp 占比，写入
  verdict.roi（此前恒 None）。「ROI 必须双链覆盖」策略可配置
  （REQUIRE_DOUBLE_STRAND，默认关闭——单引物测整质粒是常规策略，强制
  双链的门槛待 T23 基准集标定后再启用）。
"""

from typing import Dict, List, Optional, Tuple

from core.sanger.aligner import aln_segments, merge_coverage

# 近平局判定：top2 票差 ≤ 头名票 × 20%（M3 形态 gap=0；多数票 2:1 的
# gap = 50% 头名票，不触发）
DISCORDANCE_TOP_FRAC = 0.2
# 「ROI 必须双链覆盖」策略开关（默认关闭，见模块 docstring）
REQUIRE_DOUBLE_STRAND = False

_IUPAC = {
    frozenset("AG"): "R", frozenset("CT"): "Y", frozenset("CG"): "S",
    frozenset("AT"): "W", frozenset("GT"): "K", frozenset("AC"): "M",
    frozenset("CGT"): "V", frozenset("AGT"): "D", frozenset("ACT"): "H",
    frozenset("ACG"): "B",
}


def iupac_merge(a: str, b: str) -> str:
    """两个明确碱基的 IUPAC 合并码（相同碱基原样返回）"""
    if a == b:
        return a
    return _IUPAC.get(frozenset({a, b}), "N")


def find_discordances(voter_info: Dict[int, List[Dict]],
                      reference: str) -> List[Dict]:
    """共识投票明细中的近平局分歧位点（_build_consensus 调用）。

    voter_info：0-based 投票下标 → [{read, call, q, w}]（非参考票全记，
    已热位点上的参考票也记）。返回按 ref_pos 升序的分歧记录：
    {ref_pos, calls, vote_gap, top_bases, consensus}。
    """
    out: List[Dict] = []
    for i in sorted(voter_info):
        entries = voter_info[i]
        if len(entries) < 2:
            continue
        ref_base = reference[i].upper() if 0 <= i < len(reference) else ""
        tally: Dict[str, float] = {}
        for e in entries:
            k = e["call"]
            tally[k] = tally.get(k, 0.0) + e["w"]
        subs = {k: w for k, w in tally.items() if len(k) == 1 and k in "ACGT"}
        nonref = {k: w for k, w in subs.items() if k != ref_base}
        if len(nonref) < 2:
            continue
        ranked = sorted(nonref.items(), key=lambda kv: -kv[1])
        (b1, w1), (b2, w2) = ranked[0], ranked[1]
        if w1 < tally.get(ref_base, 0.0):
            continue  # 参考侧仍占优：常规多数票，不判分歧
        # 插入/缺失票 ≥ 头名票：indel 主导该位，不按替换分歧处理
        if any(w >= w1 for k, w in tally.items() if k not in subs):
            continue
        if (w1 - w2) > DISCORDANCE_TOP_FRAC * w1:
            continue  # 票差明确：头名胜出，与今日行为一致
        calls = sorted(({"read": e["read"], "call": e["call"], "q": e["q"]}
                        for e in entries), key=lambda c: c["read"])
        out.append({
            "ref_pos": i + 1,
            "calls": calls,
            "vote_gap": round(w1 - w2, 3),
            "top_bases": [b1, b2],
            "consensus": iupac_merge(b1, b2),
            "detail": (
                f"重叠区 {i + 1} 高质量分歧："
                + "、".join(f"{c['read']} 调用 {c['call']}（Q{c['q']}）"
                            for c in calls[:4])
                + ("等" if len(calls) > 4 else "")
                + f"；加权票差 {round(w1 - w2, 3)}，共识按 IUPAC "
                f"{iupac_merge(b1, b2)} 合并——无法由数据裁决，需核对峰图或补测对侧链"),
        })
    return out


def downgrade_overlap_conflicts(variants: List[Dict],
                                discordances: List[Dict]) -> int:
    """兼容入口：分歧位点降级已归 SiteCall（sitecall.apply_overlap_discordance）"""
    from core.sanger.sitecall import apply_overlap_discordance
    return apply_overlap_discordance(variants, discordances)


def _intersect(a: List[Tuple[int, int]], b: List[Tuple[int, int]],
               ) -> List[Tuple[int, int]]:
    """两个已合并区段列表的交集"""
    out: List[Tuple[int, int]] = []
    i = j = 0
    while i < len(a) and j < len(b):
        s = max(a[i][0], b[j][0])
        e = min(a[i][1], b[j][1])
        if s <= e:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def double_strand_fraction(read_results: List[Dict],
                           roi_start: Optional[int],
                           roi_end: Optional[int]) -> Optional[float]:
    """ROI 内正负两链都被 read 覆盖的 bp 占比（%）。

    无 ROI 或 ROI 非法 → None；无负链 read → 0.0。环状 read 的跨原点
    区段由 aln_segments 拆分后参与合并。"""
    if not roi_start or not roi_end or int(roi_end) < int(roi_start):
        return None
    s0, e0 = int(roi_start), int(roi_end)
    fwd: List[Tuple[int, int]] = []
    rev: List[Tuple[int, int]] = []
    for r in read_results:
        aln = r.get("alignment") or {}
        if int(aln.get("ref_end") or 0) <= 0:
            continue
        spans = [(max(s0, a), min(e0, b)) for a, b in aln_segments(aln)
                 if b >= s0 and a <= e0]
        (fwd if (aln.get("direction") or "+") == "+" else rev).extend(spans)
    if not fwd or not rev:
        return 0.0
    both = _intersect(merge_coverage(fwd, e0), merge_coverage(rev, e0))
    total = sum(e - s + 1 for s, e in both)
    return round(min(total, e0 - s0 + 1) / (e0 - s0 + 1) * 100, 2)
