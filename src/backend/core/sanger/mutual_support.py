# -*- coding: utf-8 -*-
"""双侧独立单 read indel 的互证（goal v3.1 X4；RB P1-4）

跨原点 CDS 的常见构建形态是「F 引物 read 带 ATG 前插入、R 引物 read 带
终止后插入」——两处各由不同 read 报出、分布在对侧，而逐位互检的前提
「clonal 变异必被每条覆盖 read 独立复现」在这里不成立：两条 read 并不
冗余覆盖同一事件。

单点滑移是整条 read 的局部现象，不会在另一端独立复发，因此「两处同时
是伪影」的概率极低，可以互相佐证、免于逐位互检的定案。

关键约束：**只有都处于互检矛盾状态的变体**才能互相佐证。真实变异（被
其他 read 支持、无矛盾，如 MX 的 5076 插入）不得给伪影背书——否则 MX
的 6653/6666 两处伪影缺失会因为「和 5076 位置相距够远」而被豁免，破坏
真实数据的互检排除基线。
"""

from typing import Dict, List, Optional

# 双侧互证的最小相对间距（占两处位置跨度的比例）：要求分布在对侧，
# 而不是相邻同聚物上的两个滑移点
MUTUAL_SIDE_FRAC = 0.5


def opposite_side_single_indels(variants: List[Dict],
                                conflicted: Optional[set] = None
                                ) -> Dict[int, List[str]]:
    """找出「对侧各自独立的单 read indel」两两互证组。

    返回 {id(变体): [伙伴位置文本, …]}；不在互证组里的变体不出现。

    判据（全部成立）：
    - 两处都是 indel（insertion/deletion）；
    - 各自只被 1 条 read 支持；
    - 支持它们的 read **不同**（同一 read 的两处独立 indel 由 poly/merged
      抑制负责，不在此列）；
    - 两处位置相距 ≥ `MUTUAL_SIDE_FRAC` × 两处位置跨度（分布在对侧）；
    - 两处都处于逐位互检矛盾状态（`conflicted` 给出这些变体的 id 集）。
    """
    singles = [v for v in variants
               if v.get("type") in ("insertion", "deletion")
               and (v.get("support_reads") or 1) == 1
               and v.get("ref_pos") is not None
               and (conflicted is None or id(v) in conflicted)]
    if len(singles) < 2:
        return {}
    span = (max(int(v["ref_pos"]) for v in singles)
            - min(int(v["ref_pos"]) for v in singles))
    if span <= 0:
        return {}
    out: Dict[int, List[str]] = {}
    for a in singles:
        partners = []
        for b in singles:
            if b is a:
                continue
            if (a.get("read") or "") == (b.get("read") or ""):
                continue
            if abs(int(a["ref_pos"]) - int(b["ref_pos"])) < span * MUTUAL_SIDE_FRAC:
                continue
            partners.append(f"{b['type']} {b['ref_pos']}")
        if partners:
            out[id(a)] = partners
    return out
