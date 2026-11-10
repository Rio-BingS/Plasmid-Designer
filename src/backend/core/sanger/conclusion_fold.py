# -*- coding: utf-8 -*-
"""结论正文的行预算与折叠（goal v3.1 §3.2，RB P0-2）

网页结论只写「头行 + 最多 5 行要点」＝ `CONCLUSION_BUDGET` 行（含头行），
逐条核对素材全部进 `conclusion_groups` 折叠组（前端可展开）与复核队列。
判定逻辑不读文案：可折叠与否由**构建端的结构化归属**决定——
`fold_tier` 只认「这一行是否属于某个折叠组」，不由前缀字符串猜分组。

折叠优先级（超预算时从低到高移走）：

| 级 | 内容 | 去向 |
|---|---|---|
| 0 | 低置信未决变体行（「要看峰图才能收口」的条目） | `low` |
| 1 | ↳ 逐 read 双峰位点 / poly 逐 run 明细 | `mixed_detail` |
| 2 | 区间裁决行（互检已排除的差异） | `resolved` |
| 3 | 「注意：」测量与覆盖说明 | `notes` |
| 4 | ⚠ 混合汇总行（X2 起由 read 级队列项承载） | `mixed_detail` |

**永不折叠**：确证差异行、差异摘要行、变异注记行（↳ 酶切位点）、CDS 结论——
它们是判读要点，不是核对素材。预算内的短结论（常规单样品、MX）不触发折叠，
明细照旧直接可读。

`conclusion_detail`（全量正文，原顺序）由调用方另存：报告与导出按它逐条列，
折叠只影响网页正文的展示。
"""

from typing import Dict, List, Optional, Sequence, Set, Tuple

# 正文行数上限（含头行）：头行 + 最多 5 行要点（goal v3.1 §3.2）
CONCLUSION_BUDGET = 6

# 折叠级别 → 目标折叠组
_TIER_GROUP = {
    0: "low",
    1: "mixed_detail",
    2: "resolved",
    3: "notes",
    4: "mixed_detail",
}


def fold_tier(line: str, groups: Dict[str, List[str]],
              mixed_lines: Sequence[str],
              mixed_signal_lines: Sequence[str]) -> Optional[Tuple[int, str]]:
    """该行可否折叠 → `(优先级, 目标组)`；判读要点返回 None。

    `groups` 是构建端给出的结构化分组（`low`/`resolved`/…），`mixed_lines`
    与 `mixed_signal_lines` 是混合判读的原文行——两者都是对象身份级别
    的归属，不做文案匹配（唯一例外是「↳ 酶切位点」注记行，它挂在确证
    差异下，属要点而非素材）。
    """
    stripped = line.strip()
    if line in set(groups.get("low") or []):
        return (0, _TIER_GROUP[0])
    # 变异注记行（↳ 酶切位点）：挂在确证差异下，属判读要点
    if stripped.startswith("↳") and "酶切位点" in stripped:
        return None
    if stripped.startswith("↳"):
        return (1, _TIER_GROUP[1])
    if line in set(groups.get("resolved") or []):
        return (2, _TIER_GROUP[2])
    if stripped.startswith("注意："):
        return (3, _TIER_GROUP[3])
    if line in set(mixed_lines) or line in set(mixed_signal_lines):
        return (4, _TIER_GROUP[4])
    return None


def apply_budget(conclusion: str, groups: Dict[str, List[str]],
                 mixed_lines: Sequence[str] = (),
                 mixed_signal_lines: Sequence[str] = (),
                 budget: int = CONCLUSION_BUDGET) -> str:
    """按预算把明细行移入折叠组，返回新的正文（就地更新 `groups`）。

    只移动超出预算的行数，且按 `fold_tier` 的优先级从低到高挑；要点行
    不参与挑选，因此正文里要点永远不会被裁掉。未超预算时原样返回。
    """
    lines = [ln for ln in conclusion.split("\n") if ln.strip()]
    over = len(lines) - budget
    if over <= 0:
        return conclusion
    candidates: List[Tuple[int, str, str]] = []
    for ln in lines:
        tier = fold_tier(ln, groups, mixed_lines, mixed_signal_lines)
        if tier is not None:
            candidates.append((tier[0], ln, tier[1]))
    candidates.sort(key=lambda c: c[0])
    moved: Set[str] = set()
    for _prio, ln, target in candidates:
        if over <= 0:
            break
        groups.setdefault(target, []).append(ln)
        moved.add(ln)
        over -= 1
    if not moved:
        return conclusion
    # 移走的行在原位置会留下空串（「a\n\nb」）——按非空行收口，避免正文
    # 尾部多出一个空行把行数算多一行
    return "\n".join(ln for ln in conclusion.split("\n")
                     if ln not in moved and ln.strip())
