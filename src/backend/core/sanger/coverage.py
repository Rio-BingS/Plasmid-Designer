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


def expected_ranges(read_results: List[Dict],
                    front_gray: int = 30) -> List[Dict]:
    """理论覆盖 E（goal v3 W6，PL B1）：每条 read 的理论窗口 = 比对起点
    沿测序方向按原始 basecall 长度延伸到尾端（首端 front_gray 灰区不构成
    理论覆盖）。basis="read_start"（有引物序列时改以结合位点 3′ 端为起点，
    basis="primer_binding" 随 W7 引物映射接入）。

    返回 [{read, primer, ranges, measured_ranges, direction, basis}]；
    无比对坐标或无原始 basecall 的 read 跳过——E 整体不可得时 verdict
    走 scope_uncovered。反向 read 从 ref_end 端起测，理论窗口向低坐标延伸。"""
    out: List[Dict] = []
    for r in read_results:
        aln = r.get("alignment") or {}
        # 理论长度取原始 basecall（raw_bases 管线内可用；结果载荷已剔除
        # 该键，回退 raw_length——衰减 read 的 raw > trimmed，正是 E 的意义）
        raw_len = len(r.get("raw_bases") or "") or int(r.get("raw_length") or 0)
        if not raw_len:
            continue
        direction = r.get("direction") or "+"
        if direction == "-":
            end_ = aln.get("ref_end")
            if not end_:
                continue
            end_ = int(end_)
            theory = (max(1, end_ - (raw_len - front_gray) + 1), end_)
            measured = (aln.get("ref_start") or end_, end_)
        else:
            start_ = aln.get("ref_start")
            if not start_:
                continue
            start_ = int(start_)
            theory = (start_, start_ + (raw_len - front_gray) - 1)
            measured = (start_, aln.get("ref_end") or start_)
        out.append({
            "read": r.get("filename"), "primer": r.get("primer"),
            "ranges": [list(theory)], "measured_ranges": [list(measured)],
            "direction": direction, "basis": "read_start"})
    return out


def _segments_of(gap: Dict) -> List[Tuple[int, int]]:
    """缺口 → 线段列表（跨原点缺口拆 [x,L]+[1,y]，普通缺口单段）"""
    if gap.get("wraps_origin") and gap.get("ref_segments"):
        return [(int(a), int(b)) for a, b in gap["ref_segments"]]
    return [(int(gap["start"]), int(gap["end"]))]


def _coverage_gaps(covered_ranges: List[Tuple[int, int]], length: int,
                   min_len: int = 100, cap: int = 10,
                   circular: bool = False,
                   expected: Optional[List[Dict]] = None,
                   scope_ranges: Optional[List[Tuple[int, int]]] = None,
                   scope_min_len: int = 20,
                   ) -> List[Dict]:
    """覆盖缺口（未覆盖区间）按长度降序，供补测建议

    circular=True：环状参考的尾部缺口 [x, L] 与头部缺口 [1, y] 在原点处
    相连，先合并成一段跨原点缺口再按 min_len 过滤——分开过滤会把两侧
    各不足 min_len、合起来已够长的缺口整段漏掉。跨原点缺口与跨原点 read
    同口径：start/end 为折回坐标（start > end），wraps_origin=True，
    ref_segments 给出 [[x, L], [1, y]] 两段；length 为合并后总长。
    线性参考（及未跨原点的缺口）不带新字段，结果与此前完全一致。

    goal v3 W6（PL B2）：scope_ranges 给出时（声明范围）缺口只保留 R 内
    部分；expected（理论覆盖 E）给出时逐缺口归因 cause——beyond_primer
    （任何 read/引物的理论窗都够不到 → 加引物）、read_shortfall（理论窗
    可达但实测未达 → 看峰图/重测，detail 带衰减位置）；E 不可得 →
    unknown（verdict 侧落 scope_uncovered）。
    scope_min_len（W8）：声明范围内的归因阈值独立于整参考的 min_len——
    接合区窗口宽 61bp，两侧各 ~30bp 的未测缺口必须可归因，100bp 整参考
    阈值会把它们全部滤掉（scope_uncovered 兜底吞掉 cause）。"""
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
    if scope_ranges:
        # R 内缺口（goal v3 W6）：整参考缺口与声明范围求交，跨原点缺口
        # 逐段求交后按长度重算；范围外部分不归需测范围管。求交在
        # min_len 过滤**之前**做（W8）——接合区两侧 ~30bp 的缺口在整参考
        # 口径下不足 100bp，但属于需测范围必须归因
        scoped: List[Dict] = []
        for g in raw:
            for gs, ge in _segments_of(g):
                for rs, re_ in scope_ranges:
                    s_, e_ = max(gs, rs), min(ge, re_)
                    if s_ <= e_ and e_ - s_ + 1 >= scope_min_len:
                        scoped.append({"start": s_, "end": e_,
                                       "length": e_ - s_ + 1})
        gaps = scoped
    else:
        gaps = [g for g in raw if g["length"] >= min_len]
    for g in gaps:
        g.setdefault("cause", "unknown")
        g.setdefault("detail", "")
        if expected is None:
            continue
        segs = _segments_of(g)
        # 理论窗与缺口相交的 read：有 → 理论可达而实测未达（衰减）；
        # 无 → 可用引物/read 都够不到
        hits = []
        for e in expected:
            for (ts, te) in [(int(a), int(b)) for a, b in e.get("ranges") or []]:
                if any(ts <= ge_ and gs_ <= te for gs_, ge_ in segs):
                    hits.append(e)
                    break
        if not hits:
            g["cause"] = "beyond_primer"
            g["detail"] = "缺口超出全部 read/引物的理论覆盖——需加引物"
            continue
        best = max(hits, key=lambda e: max(
            (te - gs_ + 1) if (ts <= ge_ and gs_ <= te) else 0
            for (ts, te) in [(int(a), int(b)) for a, b in e.get("ranges") or []]
            for gs_, ge_ in segs) or 0)
        # 衰减位置：该 read 实测段止于缺口起点前最近的位置
        decay = None
        for (ms, me) in [(int(a), int(b)) for a, b in best.get("measured_ranges") or []]:
            if me < g["start"] and (decay is None or me > decay):
                decay = me
        theory_end = max(int(b) for a, b in best.get("ranges") or [])
        g["cause"] = "read_shortfall"
        g["detail"] = (
            f"{best.get('read') or 'read'} 在 {decay if decay else '缺口起点'} "
            f"处衰减（理论可达 {theory_end}）")
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
