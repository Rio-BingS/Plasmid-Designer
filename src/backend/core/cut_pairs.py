"""双酶切切点对的枚举、评分与选择（可胶回收片段清单）

多切点酶切在真实实验里=「酶切后胶回收两端恰为所选两酶的功能大片段，与
插入片段重组」——规划默认按该几何选最外侧切点对（内部同酶位点吸进废
片段）；预览同时枚举全部候选片段供用户显式选择（选择权在用户）。

评分口径：(MCS penalty, 保留骨架残留同酶位点数, 切除长度) 升序。
"""

from typing import Dict, List, Optional, Sequence, Tuple

from core.enzyme_sites import find_enzyme_sites


def physical_sites(sequence: str, enzyme: str, *, circular: bool = True) -> List[Dict]:
    """某酶的全部物理位点（每个位点一条；回文酶正反链重复已去重，按位置排序）"""
    if not sequence:
        return []
    seen: set = set()
    out: List[Dict] = []
    for s in find_enzyme_sites(sequence, [enzyme], circular=circular):
        key = (s["position"], s["cut_fwd"], s["cut_rev"])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    out.sort(key=lambda s: s["position"])
    return out


def _forward_dist(a: int, b: int, length: int, circular: bool) -> int:
    """从切点 a 走到切点 b 的距离（即被切掉的 (a, b] 长度）"""
    return (b - a) % length if circular else (b - a)


def _contains(a: int, dist: int, anchor: int, length: int, circular: bool) -> bool:
    off = (anchor - a - 1) % length if circular else (anchor - a - 1)
    return 0 <= off < dist


def _in_window(pos: int, window: Optional[Tuple[int, int]], length: int,
               circular: bool) -> bool:
    if not window:
        return True
    lo, hi = window
    if circular and lo > hi:
        return pos >= lo or pos <= hi
    return lo <= pos <= hi


def _overlaps_window(a: int, dist: int, window: Optional[Tuple[int, int]],
                     length: int, circular: bool) -> bool:
    if not window:
        return True
    lo, hi = window
    return any(_in_window((a + i) % length if circular else a + i, window, length, circular)
               for i in range(1, dist + 1))


def _excised_between(c: int, a: int, b: int) -> bool:
    """c 是否落在被切除区间 (a, b] 内（a < b 直接比；a >= b 跨原点）"""
    return (a < c <= b) if a < b else (c > a or c <= b)


def _pair_score(a: int, b: int, window: Optional[Tuple[int, int]], length: int, circular: bool,
                leftover_cuts: int = 0) -> Optional[Tuple[int, int, int]]:
    """(penalty, 保留骨架残留同酶位点数, 切除长度)；a==b 返回 None。

    残留位点数升序：多切点按胶回收几何选最外侧对，把内部同酶位点吸进废
    片段（选邻近对会残留同酶位点把骨架切碎，不可胶回收）。"""
    d = _forward_dist(a, b, length, circular)
    if d <= 0:
        return None
    both_in = (_in_window(a, window, length, circular)
               and _in_window(b, window, length, circular))
    if both_in:
        penalty = 0
    elif _overlaps_window(a, d, window, length, circular):
        penalty = 1
    else:
        penalty = 2
    return (penalty, leftover_cuts, d)


def _candidate_pairs(cuts_a: Sequence[int], cuts_b: Sequence[int],
                     length: int, circular: bool, allow_long: bool):
    """枚举候选切点对；allow_long=False 时先排除超过半圈的方向。

    环状载体的废片段一定比半圈短：反向那对切点算出来是「长度-废片段」
    （pET-30a 实测 188 bp 被算成 5234 bp），按它组装等于扔掉整条主链。"""
    for a in cuts_a:
        for b in cuts_b:
            d = _forward_dist(a, b, length, circular)
            if d <= 0:
                continue
            if circular and not allow_long and d * 2 > length:
                continue
            yield a, b


def _best_ordered_pair(
    groups: Sequence[Tuple[Sequence[int], Sequence[int], str]],
    window: Optional[Tuple[int, int]], length: int, circular: bool,
    interior_sets: Optional[Sequence[Sequence[int]]] = None,
) -> Optional[Tuple[Tuple[int, int, int], int, int, str]]:
    """在若干 (cuts_a, cuts_b, tag) 组合里挑最优切点对 → (score, a, b, tag)。

    interior_sets 与 groups 一一对应（该组合两把酶的全体切点并集），统计
    保留骨架残留的同酶位点数（score 第二键）。先只考虑不超过半圈的切除
    区间，挑不出来再放宽到整圈（调用方据此告警）。"""
    best: Optional[Tuple[Tuple[int, int, int], int, int, str]] = None
    for allow_long in (False, True):
        for gi, (cuts_a, cuts_b, tag) in enumerate(groups):
            interior = interior_sets[gi] if interior_sets else ()
            for a, b in _candidate_pairs(cuts_a, cuts_b, length, circular, allow_long):
                leftover = sum(c not in (a, b) and not _excised_between(c, a, b)
                               for c in interior)
                score = _pair_score(a, b, window, length, circular, leftover)
                if score is None:
                    continue
                if best is None or score < best[0]:
                    best = (score, a, b, tag)
        if best is not None:
            return best
    return None


def enumerate_cut_pairs(
    sequence: str, enzyme_5: str, enzyme_3: str, *,
    window: Optional[Tuple[int, int]] = None, circular: bool = True,
) -> List[Dict]:
    """某酶对在载体上的全部可行切点对（= 可胶回收的骨架片段清单）。

    每项 {left_enzyme, left_cut, right_enzyme, right_cut, removed_bp,
    kept_bp, leftover_cuts}：removed = 被切走的废片段，kept = 胶回收的
    骨架大片段（removed 的补集），leftover_cuts = 保留骨架上残留的同酶
    位点数（0 = 骨架完整）。按自动评分升序（首位即规划器的默认选择），
    供预览的「选择所需片段」下拉与用户指定 cut_pair 校验。"""
    sites5 = physical_sites(sequence, enzyme_5, circular=circular)
    sites3 = physical_sites(sequence, enzyme_3, circular=circular)
    if not sites5 or not sites3:
        return []
    cuts5 = sorted({s["cut_fwd"] for s in sites5})
    cuts3 = sorted({s["cut_fwd"] for s in sites3})
    length = len(sequence)
    union = sorted(set(cuts5) | set(cuts3))
    groups = ((cuts5, cuts3, enzyme_5), (cuts3, cuts5, enzyme_3))
    out: List[Dict] = []
    scored: List[Tuple[Tuple[int, int, int], int, int, str]] = []
    for allow_long in (False, True):
        for gi, (cuts_a, cuts_b, tag) in enumerate(groups):
            for a, b in _candidate_pairs(cuts_a, cuts_b, length, circular, allow_long):
                leftover = sum(c not in (a, b) and not _excised_between(c, a, b)
                               for c in union)
                score = _pair_score(a, b, window, length, circular, leftover)
                if score is None:
                    continue
                scored.append((score, a, b, tag))
        if scored:
            break   # 短区间候选非空即停（与 _best_ordered_pair 同口径）
    scored.sort(key=lambda t: t[0])
    for score, a, b, tag in scored:
        removed = _forward_dist(a, b, length, circular)
        out.append({
            "left_enzyme": tag, "left_cut": a, "right_enzyme":
                enzyme_3 if tag == enzyme_5 else enzyme_5, "right_cut": b,
            "removed_bp": removed, "kept_bp": length - removed,
            "leftover_cuts": score[1],
        })
    return out


def pick_flanking(
    cuts_a: Sequence[int], cuts_b: Sequence[int], *,
    window: Optional[Tuple[int, int]] = None, length: int, circular: bool = True,
) -> Optional[Tuple[int, int, int]]:
    """挑一对切点，返回 (left, right, penalty)。

    penalty 0/1/2 = 两切点都在 MCS 区间内 / 区间与之相交 / 完全在外（调用方
    据此告警）。排序 (penalty, 切除长度)。**不能只看「包住 MCS 中点」**——
    pET-28a 的 BamHI/XhoI 切点 159/199 不包含 MCS 中点 229，只看中点会把
    5329 bp 主链当废片段；也**不能奖励「MCS 点落在整圈长区间里」**（方向
    正好是反的，见 _candidate_pairs）。window 为 None 时按最短区间取。"""
    best = _best_ordered_pair([(cuts_a, cuts_b, '')], window, length, circular)
    if best is not None:
        return best[1], best[2], best[0][0]

    for a in cuts_a:
        if a in cuts_b:
            return a, a, 0
    return None
