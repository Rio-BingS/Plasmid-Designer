"""measured / verified 双覆盖口径（goal T3；审计 B P0-4、P1-7）

「实测过」不等于「验证过」：read 首尾不可信区、N/模糊调用列虽然计入
measured 覆盖，却不构成「该位点已按高质量证据读过」。verified 覆盖 =

    measured ∩ Σ(read 可信区折算到参考坐标) − N/模糊调用列

逐 read 逐位判定（read 坐标进 in_zone、参考坐标折回合并），支持多 read
互补：任一 read 在可信区内以确定碱基读过该位即 verified。输出 goal §3
的 Coverage 结构。本模块只做纯计算，组装在 pipeline.analyze。
"""

from typing import Dict, List, Optional, Set, Tuple

from core.sanger.aligner import fold_pos
from core.sanger.homopolymer import _read_ref_maps
from core.sanger.reliable_zone import in_zone


def _verified_positions(read_results: List[Dict], ref_length: int,
                        circular: bool) -> Tuple[Set[int], Set[int], Set[int]]:
    """返回 (verified 参考位点集, 因 N/模糊调用而未验证的位点集,
    trusted∩measured 位点集)。

    逐 read 遍历比对列：read 坐标落在可信区 → 该参考位计入 trusted∩measured
    （CDS full 的第二判据分母）；其中调用为确定碱基（ACGT）→ verified；
    N/IUPAC 调用列 → 记入 n 位点（「没读清」，与 measured 的差集来源之一）。"""
    verified: Set[int] = set()
    n_pos: Set[int] = set()
    trusted: Set[int] = set()
    for r in read_results:
        zone = r.get("reliable_zone")
        if not zone:
            continue
        bases = (r.get("trimmed_bases") or "").upper()
        r2ref, _ = _read_ref_maps(r)
        for i in range(1, len(bases) + 1):
            rf = r2ref.get(i)
            if not rf:
                continue
            if circular:
                rf = fold_pos(rf, ref_length)
            if not (1 <= rf <= ref_length):
                continue
            if not in_zone(zone, i):
                continue
            trusted.add(rf)
            if bases[i - 1] in "ACGT":
                verified.add(rf)
            else:
                n_pos.add(rf)
    # 多 read 压制 N：该位在别的 read 处 verified 后不再是 n 位点
    n_pos -= verified
    return verified, n_pos, trusted


def _to_ranges(positions: Set[int]) -> List[Tuple[int, int]]:
    """排序位点集 → 闭区间列表"""
    if not positions:
        return []
    out: List[Tuple[int, int]] = []
    for p in sorted(positions):
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def compute_coverage(read_results: List[Dict], ref_length: int,
                     measured_ranges: List[Tuple[int, int]],
                     circular: bool = False) -> Dict:
    """goal §3 Coverage：measured/verified 双口径（百分比保留两位）"""
    measured_bp = sum(e - s + 1 for s, e in measured_ranges)
    verified_set, n_set, trusted_set = _verified_positions(
        read_results, ref_length, circular)
    # verified ⊆ measured（只统计实测范围内的 verified 位点）
    verified_set = {p for p in verified_set
                    if any(s <= p <= e for s, e in measured_ranges)}
    trusted_set = {p for p in trusted_set
                   if any(s <= p <= e for s, e in measured_ranges)}
    verified_ranges = _to_ranges(verified_set)
    verified_bp = len(verified_set)
    n_columns = sorted(n_set - verified_set)
    pct = (lambda bp: round(bp / ref_length * 100, 2) if ref_length else 0.0)
    return {
        "measured_ranges": [list(r) for r in measured_ranges],
        "verified_ranges": [list(r) for r in verified_ranges],
        # trusted∩measured（CDS full 第二判据的分子；含 N/模糊调用列）
        "trusted_ranges": [list(r) for r in _to_ranges(trusted_set)],
        "trusted_bp": len(trusted_set),
        "measured_percent": pct(measured_bp),
        "verified_percent": pct(verified_bp),
        "verified_bp": verified_bp,
        "n_columns": n_columns,
    }



def ref_to_cons_index(consensus: Dict, ref_length: int) -> Dict[int, int]:
    """参考坐标 → 共识序列下标（0-based）。共识含插入/缺失时按 diffs 重建：
    插入字符（ref_base=\"-\"）挂在其 diff 给出的 cons_index 起、归属锚点
    参考位；缺失位无共识字符（不在映射中）；替换/普通位按推移计算。
    共识导出按参考位点标注（verified 小写）用。"""
    diffs = consensus.get("diffs") or []
    ins_chars: Dict[int, int] = {}      # cons idx -> ref pos
    sub_idx: Dict[int, int] = {}        # ref pos -> cons idx
    dels: Set[int] = set()
    for d in diffs:
        rp = int(d["ref_pos"])
        if d.get("ref_base") == "-":    # 插入：k 个字符挂在锚点位上
            for k in range(len(d.get("cons_base") or "")):
                ins_chars[int(d["cons_index"]) + k] = rp
        elif d.get("cons_base") == "-":
            dels.add(rp)
        else:
            sub_idx[rp] = int(d["cons_index"])
    mapping: Dict[int, int] = {}
    ptr = 0                              # 当前共识下标
    for p in range(1, ref_length + 1):
        while ptr in ins_chars:          # 先消费锚定在此处的插入字符
            mapping[ins_chars[ptr]] = p
            ptr += 1
        if p in dels:
            continue
        mapping[p] = sub_idx.get(p, ptr)
        ptr += 1
    return mapping


def _coverage_gaps(covered_ranges: List[Tuple[int, int]], length: int,
                   min_len: int = 100, cap: int = 10,
                   circular: bool = False) -> List[Dict]:
    """覆盖缺口（未覆盖区间）按长度降序，供补测建议

    circular=True：环状参考的尾部缺口 [x, L] 与头部缺口 [1, y] 在原点处
    相连，先合并成一段跨原点缺口再按 min_len 过滤——分开过滤会把两侧
    各不足 min_len、合起来已够长的缺口整段漏掉。跨原点缺口与跨原点 read
    同口径：start/end 为折回坐标（start > end），wraps_origin=True，
    ref_segments 给出 [[x, L], [1, y]] 两段；length 为合并后总长。
    线性参考（及未跨原点的缺口）不带新字段，结果与此前完全一致。"""
    raw: List[Dict] = []
    prev = 0
    for s, e in covered_ranges:
        if s - prev - 1 > 0:
            raw.append({"start": prev + 1, "end": s - 1, "length": s - prev - 1})
        prev = max(prev, e)
    if length - prev > 0:
        raw.append({"start": prev + 1, "end": length, "length": length - prev})
    if (circular and len(raw) >= 2 and raw[0]["start"] == 1
            and raw[-1]["end"] == length):
        head, tail = raw[0], raw.pop()
        raw[0] = {
            "start": tail["start"], "end": head["end"],
            "length": tail["length"] + head["length"],
            "wraps_origin": True,
            "ref_segments": [[tail["start"], length], [1, head["end"]]],
        }
    gaps = [g for g in raw if g["length"] >= min_len]
    gaps.sort(key=lambda g: -g["length"])
    return gaps[:cap]


def _gap_span_text(gap: Dict, sep: str = "-") -> str:
    """缺口位置文本：线性「s-e」；跨原点「x-L、1-y」（与 read 区段同口径）"""
    segs = gap.get("ref_segments")
    if segs:
        return "、".join(f"{a}{sep}{b}" for a, b in segs)
    return f"{gap['start']}{sep}{gap['end']}"


def _coverage_ranges_text(covered_ranges: List[Tuple[int, int]]) -> str:
    """实测覆盖区间的紧凑文本（结论首行用）：部分测序是常规策略，
    结论围绕引物实际覆盖的区域组织，把"测的是哪段"直接摆出来。
    逗号前缀形态，可无缝嵌入既有括号短语（如「覆盖 5.0%，实测区间 21-110」）"""
    if not covered_ranges:
        return ""
    spans = "、".join(f"{s}-{e}" for s, e in covered_ranges[:3])
    if len(covered_ranges) > 3:
        spans += f" 等 {len(covered_ranges)} 段"
    return f"，实测区间 {spans}"


def _mask_uncovered(seq: str, segs: List[Tuple[int, int]]) -> str:
    """未测位置按 N 屏蔽（区间为 1 起始闭区间，坐标与特征不位移）。

    线性复杂度：此前逐碱基扫描全部区间是 O(len × 区间数)，200 kb 共识
    配上碎片化覆盖时可达秒级"""
    out = ["N"] * len(seq)
    for s, e in segs:
        lo, hi = max(s, 1) - 1, min(e, len(seq))
        if lo < hi:
            out[lo:hi] = seq[lo:hi]
    return "".join(out)


def mark_unverified_bases(seq: str, consensus: Dict, ref_length: int,
                          verified_ranges: List) -> str:
    """verified 覆盖外的实测碱基小写标注（goal T3）：导出物脱离本系统后
    仍能区分「验证过」与「只是测到」。seq 为共识序列（含插入/缺失），
    参考位点经 ref_to_cons_index 映射到共识下标。"""
    verified = {p for s, e in verified_ranges for p in range(int(s), int(e) + 1)}
    r2c = ref_to_cons_index(consensus, ref_length)
    chars = list(seq)
    for rp, ci in r2c.items():
        if 0 <= ci < len(chars) and rp not in verified:
            chars[ci] = chars[ci].lower()
    return "".join(chars)
