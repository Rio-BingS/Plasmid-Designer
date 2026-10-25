"""同聚物 / 重复序列判读（从 pipeline.py 迁出，纯搬运）

职责：poly 结构的发现（find_repeat_runs / find_homopolymers）、峰图证据
评估（_poly_peak_*）、跨 read 反证与可信区校验（_cross_region_quality /
_anchor_verdict_for_read）、以及报告文案（run_label / peak_verdict_phrase /
region_quality_text）。pipeline 经 re-export 维持全部旧导入路径。
"""

import re
from typing import Dict, List, Optional, Tuple

from core.sanger.aligner import fold_pos
from core.sanger.signal import DROPOUT_WINDOW_BASES, peak_heights
from core.sanger.reliable_zone import in_zone


HOMOPOLYMER_MIN = 20     # poly 结构判定：≥20bp 连续同一碱基视为 poly 结构（如 polyA）
POLY_OBSERVE_MIN = 8     # 观察级：8-19bp 同聚物入报告（不触发结论告警）
POLY_MIN_UNITS = 4       # 二/三核苷酸重复：≥4 个重复单元入报告
POLY_COMPRESSION_RATIO = 0.5  # poly 区峰幅 / 局部主峰幅 低于此值视为峰压缩
POLY_PEAK_HEIGHT_RATIO = 0.3  # 峰图计数：低于窗口中位峰高 30% 的波动视为噪声
CROSS_CHECK_MIN_Q = 20   # 跨 read 反证：junction 处 Q 低于此值时"未报"不构成反证
# 跨 read 反证看的是验证 read 在该位点周边一段区域的整体质量，不只是单个
# 碱基的 Q：单碱基 Q 高但周围信号塌陷（峰压缩、拖尾、信号衰减段）时，
# 这条 read 在此处同样不可靠。区域 = 位点两侧各 CROSS_REGION_HALF bp
# （±5bp 太窄，反映不出该引物这一段的测序质量；取 ±25bp 约 50bp 窗口，
# 与 Phred 质量窗口/修剪窗口同量级），截掉 read 首尾不可信区后至少要有
# CROSS_REGION_MIN_COLS 列。窗口宽了，单个低 Q 碱基不该一票否决，改用
# "≥Q20 的碱基占比"衡量整体；位点本身 ±CROSS_CORE_HALF 的核心区仍要求每个碱基 ≥Q20
CROSS_REGION_HALF = 25
CROSS_REGION_MIN_COLS = 30
CROSS_REGION_MEAN_Q = 30   # 区域平均 Q 下限
CROSS_REGION_HQ_FRAC = 0.9  # 区域内 ≥Q20 碱基占比下限
CROSS_CORE_HALF = 2
CROSS_CLEAN_HALF = 5       # 验证 read 自身在位点 ±5bp 内报了差异 → 不作反证


def _circ_len(aln: Dict) -> int:
    """环状比对的参考长度（线性比对返回 0：坐标不折回）"""
    return int(aln.get("ref_length") or 0) if aln.get("circular") else 0


def find_repeat_runs(seq: str, min_len: int = HOMOPOLYMER_MIN,
                     observe_min: int = POLY_OBSERVE_MIN,
                     min_units: int = POLY_MIN_UNITS) -> List[Dict]:
    """扫描重复结构（B3）：period=1 同聚物 + period=2/3 二/三核苷酸重复

    - period=1：≥min_len 为 poly 结构（tier="poly"），observe_min..min_len-1
      为"观察级"（tier="observed"，入报告但不触发结论告警——basecaller 的
      整段偏差风险主要出现在 ≥20bp，短 run 只作观察）；
    - period=2/3：≥min_units 个完整重复单元入报告；
    - TRF 类工具会把同一 span 以多个 period 冗余报出（如 AAAAA 也匹配
      period 2 的 AA）：只保留能解释该 span 的最小 period。

    返回 run 字典：{base(unit 首碱基；period>1 为 None), unit, period,
    start, end(1-based 闭区间), length(bp), repeat_count(单元数), lus, tier}。
    lus = 无中断重复数（本扫描只报最大纯 span，故等于 repeat_count；
    L31：LUS 比总长更能预测 stutter 水平）。
    """
    n = len(seq)
    raw: List[Dict] = []
    for period in (1, 2, 3):
        i = 0
        while i < n:
            unit = seq[i:i + period]
            if len(unit) < period or not all(c in "ACGT" for c in unit):
                i += 1
                continue
            j = i + period
            while j + period <= n and seq[j:j + period] == unit:
                j += period
            span = j - i
            units = span // period
            appended = False
            if period == 1:
                if span >= observe_min:
                    raw.append({"unit": unit, "period": 1, "start": i, "span": span,
                                "repeat_count": span})
                    appended = True
            elif units >= min_units:
                raw.append({"unit": unit, "period": period, "start": i, "span": span,
                            "repeat_count": units})
                appended = True
            # 命中 run 后跳到 run 末尾；未命中必须逐位前进（period 2/3 的
            # run 可从任意相位开始，跨步会漏掉错位起点）
            i = j if appended else i + 1
    # 去冗余：span 被更小 period 的 run 完全覆盖（且单元循环一致）时丢弃
    raw.sort(key=lambda r: (r["period"], r["start"]))
    runs: List[Dict] = []
    for r in raw:
        redundant = False
        for q in runs:
            if q["period"] < r["period"] and q["start"] <= r["start"] \
                    and r["start"] + r["span"] <= q["start"] + q["span"]:
                redundant = True
                break
        if not redundant:
            runs.append(r)
    out: List[Dict] = []
    for r in runs:
        length = r["span"]
        tier = "poly" if length >= min_len else "observed"
        out.append({
            "base": r["unit"][0] if r["period"] == 1 else None,
            "unit": r["unit"],
            "period": r["period"],
            "start": r["start"] + 1,          # 1-based 闭区间（沿用现有约定）
            "end": r["start"] + length,
            "length": length,
            "repeat_count": r["repeat_count"],
            "lus": r["repeat_count"],
            "tier": tier,
        })
    return out


def find_homopolymers(seq: str, min_len: int = HOMOPOLYMER_MIN) -> List[Dict]:
    """扫描同聚物（poly）结构：≥min_len 连续同一碱基，1-based 闭区间

    find_repeat_runs 的 period=1 视图（含 period/tier/repeat_count/lus 字段）。
    """
    return [r for r in find_repeat_runs(seq, min_len=min_len, observe_min=min_len)
            if r["period"] == 1]


def _overlapping_run(runs: List[Dict], pos: int, end: int, base: Optional[str] = None) -> Optional[Dict]:
    """返回与 [pos, end] 相交（可选要求碱基匹配）的第一个 poly 结构"""
    for r in runs:
        if r["start"] <= end and r["end"] >= pos and (base is None or r["base"] == base):
            return r
    return None


def _annotate_homopolymer(ref: str, variants: List[Dict],
                          runs: List[Dict]) -> None:
    """为变体标注所在/相邻重复结构，并计算参考与测得的重复数目

    - 替换落在 run 内：标注结构，重复数不变；
    - 插入：插入序列为相邻 run 的整倍单元（B3：poly 的 AAA 也好、(CAG)n
      的 CAG 也好，统一按"插入单元 = 重复单元"归属），测得重复数 =
      参考重复数 + 插入单元数；
    - 缺失：删除序列为所在 run 的整倍单元，测得重复数 =
      参考重复数 - 删除单元数；
    - 跨 run 的复杂 indel 不算重复结构变异。
    """
    for v in variants:
        pos = int(v["ref_pos"])
        if v["type"] == "substitution":
            run = _overlapping_run(runs, pos, pos)
            if run:
                v["homopolymer"] = {**run, "ref_repeat_count": run["repeat_count"],
                                    "observed_repeat_count": run["repeat_count"]}
            continue
        if v["type"] == "insertion":
            alt = (v.get("alt_base") or "").upper()
            if alt and all(c in "ACGT" for c in alt):
                run = _unit_run(runs, pos, pos + 1, alt)
                if run:
                    k = len(alt) // run["period"]
                    v["homopolymer"] = {**run, "ref_repeat_count": run["repeat_count"],
                                        "observed_repeat_count": run["repeat_count"] + k}
            continue
        # deletion
        vlen = int(v.get("length") or 1)
        deleted = ref[pos - 1:pos - 1 + vlen].upper()
        if deleted and all(c in "ACGT" for c in deleted):
            run = _unit_run(runs, pos, pos + vlen - 1, deleted)
            if run and run["length"] >= vlen:
                k = vlen // run["period"]
                v["homopolymer"] = {**run, "ref_repeat_count": run["repeat_count"],
                                    "observed_repeat_count": run["repeat_count"] - k}


def _unit_run(runs: List[Dict], pos: int, end: int, seq: str) -> Optional[Dict]:
    """返回与 [pos, end] 相交、且 seq 为其重复单元整倍数的第一个 run（B3）

    整倍判定：len(seq) 是 period 的整倍数且 seq == unit × k。period 小者
    优先（runs 按 period 升序传入），同 poly 插入优先归同聚物结构。
    """
    for r in runs:
        if r["start"] <= end and r["end"] >= pos:
            period = r["period"]
            if len(seq) % period == 0 and seq == r["unit"] * (len(seq) // period):
                return r
    return None


def _poly_peak_amplitude_ratio(trace: Dict[str, List[int]], peaks: List[int],
                               read_pos: int, run_len: int,
                               local: bool = False) -> Optional[float]:
    """poly 区峰幅 / 主峰幅 — 峰压缩程度（重复数计数可靠性）

    分子：run 各峰在峰窗内取 max（A1：PLOC 与真实峰顶常差 1-2 采样点，
    单点采样会系统性读低弱信号区峰高）后取中位数。
    分母：默认全 read 主峰幅中位数；local=True（B1）改取 run 中间峰附近
    ±4 个有效峰的滑动中位数 H(run)——信号沿 read 衰减时，全局分母会把
    read 后段的正常 poly 误判为压缩（前/后段同 poly 的比值有系统性位置
    差）；有效峰 < 5 个时退回全局口径。
    返回 None 表示无法评估（视为可靠）。
    """
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or not peaks or read_pos is None or read_pos < 1:
        return None
    heights = peak_heights(trace, peaks)
    i0 = read_pos - 1
    i1 = min(read_pos - 1 + max(run_len, 1), len(heights))
    if i0 >= len(heights):
        return None
    run_h = [h for h in heights[i0:i1] if h > 0]
    all_idx = [i for i, h in enumerate(heights) if h > 0]
    if not run_h or not all_idx:
        return None
    med_all = sorted(heights[i] for i in all_idx)[len(all_idx) // 2]
    denom = med_all
    if local and len(all_idx) >= 5:
        mid = (i0 + i1 - 1) // 2
        mi = min(range(len(all_idx)), key=lambda k: abs(all_idx[k] - mid))
        near = [heights[all_idx[k]] for k in range(max(0, mi - 4), min(len(all_idx), mi + 5))]
        denom = sorted(near)[len(near) // 2]
    if denom <= 0:
        return None
    return round(sorted(run_h)[len(run_h) // 2] / denom, 2)


def _poly_peak_count(trace: Dict[str, List[int]], peaks: List[int],
                     read_pos: int, run_len: int) -> Optional[int]:
    """poly 窗口内原始 trace 可分辨峰个数 — 独立于 basecaller 的重复数证据

    basecaller 在长同聚物区会整段多读/少读碱基（Thermo Fisher：>8-9 个连续
    同碱基即出现 n±1），只看调用碱基无法发现。本函数在 read_pos 起 run_len 个
    调用碱基对应的 trace 区间（两侧各延一个峰间距）内直接数局部极大峰：
    高度 ≥ 窗口中位峰高的 30%，间隔 ≥ 中位峰间距的一半。峰合并（压缩）使
    计数偏少，滑移肩峰使其偏多。采样密度不足（合成/退化数据）返回 None。
    """
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or not peaks or read_pos is None or read_pos < 1 or run_len < 1:
        return None
    i0 = read_pos - 1
    i1 = min(read_pos - 1 + run_len - 1, len(peaks) - 1)
    if i0 >= len(peaks) or i1 < i0:
        return None
    s0, s1 = peaks[i0], peaks[i1]
    if s0 is None or s1 is None or s1 < s0:
        return None
    spacing = (s1 - s0) / (i1 - i0) if i1 > i0 else 0.0
    if spacing < 2:
        return None  # 每碱基不足 2 个采样点，无法独立于调用结果数峰
    pad = max(1, int(spacing // 4))  # 覆盖峰顶抖动，不外扩到相邻碱基的峰

    def _h(p: int) -> int:
        return max((trace[b][p] for b in "ACGT" if b in trace and 0 <= p < len(trace[b])),
                   default=0)

    lo, hi = max(0, s0 - pad - 1), min(n - 1, s1 + pad + 1)
    hs = [_h(p) for p in range(lo, hi + 1)]
    pos_h = sorted(x for x in hs if x > 0)
    if not pos_h:
        return None
    thr = pos_h[len(pos_h) // 2] * POLY_PEAK_HEIGHT_RATIO
    min_dist = max(2, int(spacing / 2))
    count = 0
    last = -(10 ** 9)
    for k in range(1, len(hs) - 1):
        y = hs[k]
        if y < thr or y < hs[k - 1] or y <= hs[k + 1]:
            continue
        if k - last >= min_dist:
            count += 1
            last = k
    return count


_COMPLEMENT = {"A": "T", "T": "A", "G": "C", "C": "G"}


def _aligned_run_window(aln: Dict, cs: int, ce: int,
                        base: str) -> Optional[Tuple[int, int, int]]:
    """从逐列对齐提取参考区间 [cs,ce] 对应的 read 峰窗与段内调用数

    返回 (i0, i1, called)：i0/i1 为 read 原始方向 0-based 索引（峰窗两端），
    called 为窗口内与参考链 run 碱基一致的调用数（read_aligned 一律按参考
    方向展示，正反 read 通用）。线性偏移映射在 read 带上游 indel 时错位、
    且反向 read 会数错互补碱基（表现为调用 0），逐列对齐两种问题都避免。
    区间内无对齐列（对齐视图缺失/退化）时返回 None。
    """
    aligned = aln.get("aligned") or {}
    ref_s = (aligned.get("ref_aligned") or "").upper()
    read_s = (aligned.get("read_aligned") or "").upper()
    if not ref_s or len(ref_s) != len(read_s):
        return None
    ref_pos = int(aligned.get("ref_start") or aln["ref_start"])
    L = _circ_len(aln)  # 环状跨原点：展开坐标折回后再与 [cs,ce] 比较
    total = sum(1 for c in read_s if c != "-")
    first = last = None
    called = 0
    idx = 0  # 参考方向的 read 碱基序号（非 gap 才前进）
    for k, rch in enumerate(ref_s):
        b = read_s[k]
        if b != "-":
            if rch != "-":
                cur = fold_pos(ref_pos, L) if L else ref_pos
                ref_pos += 1
                if cs <= cur <= ce:
                    if first is None:
                        first = idx
                    last = idx
                    if b == base:
                        called += 1
            idx += 1
    if first is None:
        return None
    if aln.get("direction") == "-":
        # read_aligned 为参考方向，原始 read（trace/peaks）方向相反
        first, last = total - 1 - last, total - 1 - first
    return first, last, called


def pos_ranges_str(positions: List[int], max_ranges: int = 6) -> str:
    """位点列表压缩成连续区段（如 81–140、201–260）：结论给范围概览，
    超出 max_ranges 段截断并注明总段数，逐位细节在峰图里核对"""
    if not positions:
        return ""
    spans: List[List[int]] = []
    for p in positions:
        if spans and p == spans[-1][1] + 1:
            spans[-1][1] = p
        else:
            spans.append([p, p])
    parts = [f"{a}–{b}" if b > a else str(a) for a, b in spans[:max_ranges]]
    out = "、".join(parts)
    if len(spans) > max_ranges:
        out += f" 等 {len(spans)} 段"
    return out


def _read_ref_maps(r: Dict, fold: bool = True) -> Tuple[Dict[int, int], Dict[int, Dict]]:
    """单条 read 的两张坐标映射（基于 alignment_view 的参考方向逐列视图）：

    - read2ref: 原始电泳 read 坐标(1-based, 修剪后) -> 参考坐标(1-based)；
      插入列无参考坐标，不入表
    - ref2call: 参考坐标 -> {base, q, read_pos}（base 为参考方向判读碱基，
      q 为该列 Q 值；同位点多次比对列取首列）

    反向 read 的 query 是 revcomp：query 列索引 qi ↔ 原始电泳坐标 n-qi
    （1-based，与 aligner 的 read_pos 镜像同式），mixed_detail 的 pos 即
    原始电泳坐标，经此表即可与参考坐标互换。

    终审 A-20：局部比对会把 read 端部的 junk/低质量段软剪切掉——对齐内
    相对列号不是 trimmed read 坐标，必须加上 query_start 偏移（aligner
    已输出对齐块在原始 query 内的 1-based 起止）。

    环状参考跨原点的 read：逐列视图沿展开坐标（可 > L）递增，fold=True
    时两张表的参考坐标折回 1..L；fold=False 保留展开坐标（单调，供
    线性内插用）。"""
    aligned = (r.get("alignment") or {}).get("aligned") or {}
    ref_s = (aligned.get("ref_aligned") or "").upper()
    read_s = (aligned.get("read_aligned") or "").upper()
    if not ref_s or len(ref_s) != len(read_s):
        return {}, {}
    aln = r.get("alignment") or {}
    L = _circ_len(aln) if fold else 0
    direction = aln.get("direction", "+")
    n = len(r.get("trimmed_bases") or "")
    q_aligned = aligned.get("q_aligned") or []
    ref_pos = int(aligned.get("ref_start") or aln.get("ref_start") or 1)
    # 对齐块在原始（修剪后）read 内的 1-based 起点：正向 = query_start；
    # 反向 read 的 query 是 revcomp，对齐块第 0 列对应原始电泳的 query_end 位置
    qs = aln.get("query_start")
    qe = aln.get("query_end")
    if qs is not None and qe is not None:
        block_first = int(qs) if direction == "+" else int(qe)
    else:
        block_first = 1  # 旧记录回退（无软剪切时恰为 1）
    read2ref: Dict[int, int] = {}
    ref2call: Dict[int, Dict] = {}
    qi = -1
    for i, rb in enumerate(ref_s):
        qb = read_s[i]
        if qb != "-":
            qi += 1
            # 原始电泳 1-based 坐标：正向从 block_first 递增；
            # 反向 read 对齐块第 0 列位于 block_first（=query_end），向左递减
            orig = (block_first - qi) if direction == "-" else (block_first + qi)
        else:
            orig = None
        if rb != "-":
            if qb != "-" and orig is not None:
                rp = fold_pos(ref_pos, L) if L else ref_pos
                read2ref[orig] = rp
                if rp not in ref2call:
                    ref2call[rp] = {
                        "base": qb,
                        "q": q_aligned[i] if i < len(q_aligned) else 0,
                        "read_pos": orig,
                    }
            ref_pos += 1
    return read2ref, ref2call


def _cross_region_quality(r2c: Dict[int, Dict], lo: int, hi: int, L: int,
                          zone: Optional[Dict]) -> Optional[Dict]:
    """验证 read 在参考区间 [lo-HALF, hi+HALF] 的区域质量。

    区域截掉验证 read 可信区之外的部分与未覆盖列后统计：平均 Q、
    最低 Q、≥Q20 占比、列数；核心区（位点 ±CROSS_CORE_HALF）任一列缺失或
    落进不可信区返回 None——位点本身都测不好，谈不上反证。"""
    def call(p: int) -> Optional[Dict]:
        c = r2c.get(fold_pos(p, L) if L else p)
        if not c or not in_zone(zone, c["read_pos"]):
            return None
        return c
    core: List[int] = []
    for p in range(lo - CROSS_CORE_HALF, hi + CROSS_CORE_HALF + 1):
        c = call(p)
        if c is None:
            return None
        core.append(int(c["q"] or 0))
    qs: List[int] = []
    for p in range(lo - CROSS_REGION_HALF, hi + CROSS_REGION_HALF + 1):
        c = call(p)
        if c is not None:
            qs.append(int(c["q"] or 0))
    return {
        "region_mean_q": round(sum(qs) / len(qs), 1),
        "region_min_q": min(qs),
        "region_hq_frac": round(sum(1 for q in qs if q >= CROSS_CHECK_MIN_Q) / len(qs), 3),
        "region_cols": len(qs),
        "core_min_q": min(core),
    }


def _region_ok(rq: Optional[Dict]) -> bool:
    return (rq is not None
            and rq["region_cols"] >= CROSS_REGION_MIN_COLS
            and rq["region_mean_q"] >= CROSS_REGION_MEAN_Q
            and rq["region_hq_frac"] >= CROSS_REGION_HQ_FRAC
            and rq["core_min_q"] >= CROSS_CHECK_MIN_Q)


def region_quality_text(ev: Dict) -> str:
    """验证 read 区域质量的文字（结论/悬停共用）"""
    return (f"该区 {ev.get('region_cols', '?')}bp 平均 Q{ev['region_mean_q']:g}、"
            f"≥Q20 占 {round(ev.get('region_hq_frac', 0) * 100)}%")


def _clean_cover_reads(v: Dict, read_results: List[Dict],
                       ref2call_by_read: Dict[str, Dict[int, Dict]],
                       keys_by_read: Dict[str, set],
                       weak: Optional[List[Dict]] = None) -> List[Dict]:
    """跨 read 干净覆盖反证：哪些其他 read 完整跨过该 indel 的 junction
    （且在质量可靠区内）却未报该 indel。

    clonal 样品中真实存在的变异会被每条覆盖该位的 read 独立报出；仅单
    read 报告、而另一条 read 干净跨过同一位点，是滑移/比对补偿伪影的
    高发形态（短同聚物边缘的 1bp 插入尤其常见，poly 机制只覆盖 ≥20bp
    的 run，更短的 run 落在空白区）。junction 落在验证 read 可信区之外
    或 Q 过低时，"未报"不构成反证；该 read 自己在此区域报了其他 indel
    时既不算支持也不算干净（它反对这个 call，但同样不支持参考序列）。
    """
    anchor = int(v["ref_pos"])
    vlen = max(1, int(v.get("length") or 1))
    if v.get("type") == "insertion":
        # 插入点在 anchor 与 anchor+1 之间：read 无此插入时两位恰好相邻
        junctions = [(anchor, anchor + 1)]
        win = (anchor - 1, anchor + 1)
    else:
        # 缺失 anchor..anchor+vlen-1：跨过即左翼 anchor-1 与右翼 anchor+vlen
        # 相邻；缺失紧贴 read 覆盖起点时右翼侧 junction 亦可
        junctions = [(anchor - 1, anchor + vlen),
                     (anchor + vlen, anchor + vlen + 1)]
        win = (anchor - 1, anchor + vlen + 1)
    by_name = {r["filename"]: r for r in read_results}
    src = by_name.get(v.get("read", ""))
    L = _circ_len(src["alignment"]) if src is not None else 0
    if L:
        # 环状参考：原点两侧（L 与 1）相邻，junction 坐标按环折回
        junctions = [(fold_pos(a, L), fold_pos(b, L)) for a, b in junctions]
    hits: List[Dict] = []
    for fname, r2c in ref2call_by_read.items():
        if fname == v.get("read"):
            continue
        # 验证 read 自己在该区域报了任何差异：区域不干净，不作反证
        if any(win[0] - CROSS_CLEAN_HALF <= k[0] <= win[1] + CROSS_CLEAN_HALF
               for k in keys_by_read.get(fname, ())):
            continue
        vzone = by_name[fname].get("reliable_zone")
        for a, b in junctions:
            c0, c1 = r2c.get(a), r2c.get(b)
            if not c0 or not c1 or abs(c0["read_pos"] - c1["read_pos"]) != 1:
                continue
            rq = _cross_region_quality(r2c, min(win), max(win), L, vzone)
            if not _region_ok(rq):
                if rq is not None and weak is not None:
                    weak.append({"read": fname, **rq})
                continue
            hits.append({"read": fname, **rq})
            break
    hits.sort(key=lambda h: -h["region_mean_q"])
    return hits


def _clean_cover_reads_sub(v: Dict, read_results: List[Dict],
                           ref2call_by_read: Dict[str, Dict[int, Dict]],
                           keys_by_read: Dict[str, set],
                           mixed_by_read: Dict[str, set],
                           weak: Optional[List[Dict]] = None) -> List[Dict]:
    """替换的跨 read 反证：哪些其他 read 在该位点（多碱基替换为整段）
    以可靠质量判读出参考碱基、峰形单一且自身未报差异。

    与 indel 反证同口径：克隆样品中真实的替换会被每条覆盖该位的 read
    独立读出；另一条引物在信号可靠区干净地读出参考碱基，说明这处差异
    是源 read 自己的判读误差。该 read 在此位点报了其他差异、落在其
    可信区之外、Q 不足或本身双峰时不构成反证。"""
    pos0 = int(v["ref_pos"])
    ref_b = (v.get("ref_base") or "").upper()
    if not ref_b or any(c not in "ACGT" for c in ref_b):
        return []
    by_name = {r["filename"]: r for r in read_results}
    src = by_name.get(v.get("read", ""))
    L = _circ_len(src["alignment"]) if src is not None else 0
    positions = [pos0 + k for k in range(len(ref_b))]
    if L:
        positions = [fold_pos(p, L) for p in positions]
    hits: List[Dict] = []
    lo, hi = pos0, pos0 + len(ref_b) - 1
    for fname, r2c in ref2call_by_read.items():
        if fname == v.get("read"):
            continue
        if any(lo - CROSS_CLEAN_HALF <= k[0] <= hi + CROSS_CLEAN_HALF
               for k in keys_by_read.get(fname, ())):
            continue
        vzone = by_name[fname].get("reliable_zone")
        mixed = mixed_by_read.get(fname, set())
        ok = True
        for p, rb in zip(positions, ref_b):
            c = r2c.get(p)
            if (not c or (c.get("base") or "").upper() != rb
                    or c["read_pos"] in mixed):
                ok = False
                break
        if not ok:
            continue
        rq = _cross_region_quality(r2c, lo, hi, L, vzone)
        if not _region_ok(rq) and rq is not None and weak is not None:
            weak.append({"read": fname, **rq})
        if _region_ok(rq):
            hits.append({"read": fname, **rq})
    hits.sort(key=lambda h: -h["region_mean_q"])
    return hits


def _corroborate_mixed(read_results: List[Dict]) -> Dict:
    """跨 read 双峰互检：一条引物的双峰位点映射到参考坐标后，看其他引物
    在同一位点的表现——用户视角的关键一层：另一条引物测过且峰形单一、
    判读碱基一致 → 多半是这条 read 自己的信号噪声（常见于引物首端）；
    其他引物同样报双峰 → 倾向真实混合；无其他引物覆盖 → 无法互检。

    返回 {by_read: {filename: {clean, multi, uncovered, multi_ref, clean_ref,
    uncovered_ref}},
          multi_sites: [(ref_pos, [filenames])], total, clean_total,
          multi_total, uncov_total}；单条 read 的分析互检无意义，字段照常
          返回（clean/multi 均为 0），由结论层判断是否展示。"""
    maps = [(r, *_read_ref_maps(r)) for r in read_results]
    # 环状跨原点 read 的折回坐标在原点处不单调，内插用展开坐标再折回
    unfolded = {r["filename"]: (_read_ref_maps(r, fold=False)[0], _circ_len(r["alignment"]))
                for r in read_results if _circ_len(r.get("alignment") or {})}

    def _approx(r: Dict, r2ref: Dict[int, int], pos: int) -> Optional[int]:
        u = unfolded.get(r["filename"])
        if u is None:
            return _approx_ref(r2ref, pos)
        rp = _approx_ref(u[0], pos)
        return fold_pos(rp, u[1]) if rp is not None else None

    def _approx_ref(r2ref: Dict[int, int], pos: int) -> Optional[int]:
        """位点在 read2ref 查不到时的近似定位。局部比对会在 read 端部留
        悬空（poly-A 同聚物区两端尤其常见），悬空段的双峰位点若直接归
        "待核"，会把实际被邻居 read 覆盖、可互检的位点误报成无法互检。
        以比对块两端（原始 read 坐标 ↔ 参考坐标）为锚点线性内插/外推
        ——Sanger read 与参考共线，锚点间斜率近似 ±1（反向 read 同式）"""
        if not r2ref:
            return None
        k_lo, k_hi = min(r2ref), max(r2ref)
        if k_hi == k_lo:
            return r2ref[k_lo]
        return round(r2ref[k_lo] + (pos - k_lo) * (r2ref[k_hi] - r2ref[k_lo])
                     / (k_hi - k_lo))

    # 各 read 报了双峰的参考坐标集合（互检时判"同报双峰"用）
    mixed_ref_by_read: Dict[str, set] = {}
    for r, r2ref, _c in maps:
        s = set()
        for e in r.get("mixed_detail") or []:
            rp = r2ref.get(e["pos"])
            if rp is None:
                rp = _approx(r, r2ref, e["pos"])
            if rp is not None:
                s.add(rp)
        mixed_ref_by_read[r["filename"]] = s

    by_read: Dict[str, Dict] = {}
    multi_sites: Dict[int, List[str]] = {}
    for r, r2ref, _c in maps:
        fname = r["filename"]
        clean: List[int] = []
        multi: List[int] = []
        uncovered: List[int] = []
        uncovered_ref: List[int] = []
        for e in r.get("mixed_detail") or []:
            rp = r2ref.get(e["pos"])
            if rp is None:
                rp = _approx(r, r2ref, e["pos"])
                if rp is None:
                    uncovered.append(e["pos"])
                    continue
            others_multi = [f for f, s in mixed_ref_by_read.items()
                            if f != fname and rp in s]
            if others_multi:
                multi.append(e["pos"])
                multi_sites.setdefault(rp, []).append(fname)
                continue
            # 其他引物在该位点峰形单一（自己没报双峰）且判读碱基一致、
            # Q≥20 → 判"被覆盖复核"
            ok = False
            for o, _o2ref, ocall in maps:
                if o["filename"] == fname:
                    continue
                call = ocall.get(rp)
                if call and call["base"] == _c.get(rp, {}).get("base") \
                        and call["q"] >= 20 \
                        and rp not in mixed_ref_by_read[o["filename"]]:
                    ok = True
                    break
            (clean if ok else uncovered).append(e["pos"])
            if not ok:
                uncovered_ref.append(rp)
        by_read[fname] = {
            "clean": len(clean), "multi": len(multi), "uncovered": len(uncovered),
            "clean_ref": sorted({r2ref[p] for p in clean if p in r2ref}),
            "multi_ref": sorted({r2ref[p] for p in multi if p in r2ref}),
            "uncovered_ref": sorted(set(uncovered_ref)),
        }
    total = sum(d["clean"] + d["multi"] + d["uncovered"] for d in by_read.values())
    return {
        "by_read": by_read,
        "multi_sites": sorted(multi_sites.items()),
        "total": total,
        "clean_total": sum(d["clean"] for d in by_read.values()),
        "multi_total": sum(d["multi"] for d in by_read.values()),
        "uncov_total": sum(d["uncovered"] for d in by_read.values()),
    }


def _mixed_check_phrase(chk: Dict) -> str:
    """单条 read 互检摘要短语（仅统计非零项，全零返回空串）"""
    parts = []
    if chk["multi"]:
        parts.append(f"{chk['multi']} 处其他引物同报双峰（倾向真实混合）")
    if chk["clean"]:
        parts.append(f"{chk['clean']} 处其他引物覆盖且峰形单一（倾向噪声）")
    if chk["uncovered"]:
        ref_txt = pos_ranges_str(chk.get("uncovered_ref") or [])
        parts.append(f"{chk['uncovered']} 处无其他引物覆盖待核"
                     + (f"（参考位置 {ref_txt}）" if ref_txt else ""))
    return "互检：" + "、".join(parts) if parts else ""


def run_label(run: Dict) -> str:
    """重复结构标签：period=1 为 poly(A) 同聚物；period>1 为 (CAG)n 型重复"""
    if run.get("period", 1) == 1 and run.get("base"):
        return f"poly({run['base']}) 同聚物"
    unit = run.get("unit") or ""
    if unit:
        return f"({unit}){run.get('ref_repeat_count', '?')} 重复"
    return "重复序列"


def peak_verdict_phrase(e: Dict) -> str:
    """峰图主判读短语（以可分辨峰为准，basecaller 调用数只在逐 read 里出现）"""
    ref = e["ref_repeat_count"]
    m = e.get("peak_measured")
    if m is None:
        ci = e.get("length_ci")
        est = f"宽度法估计 {e.get('length_estimate')} 个"
        if ci:
            est += f"（区间 {ci[0]}–{ci[1]}）"
        return f"峰图计数不可用，{est}"
    if e.get("peak_missing"):
        return f"缺失 {e['peak_missing']} 个 {e['base']}：实测 {m} 个，参考 {ref} 个"
    if e.get("peak_inserted"):
        return f"插入 {e['peak_inserted']} 个 {e['base']}：实测 {m} 个，参考 {ref} 个"
    return f"poly({e['base']}) 碱基类型完整：实测 {m} 个，参考 {ref} 个"


# 锚定分级劣序（rank 越大越差）：取投票 read 的最差分级用
_ANCHOR_RANK = {"reliable": 0, "one-sided": 1, "marginal": 2, "unreliable": 3}


def _poly_anchor_notes(homopolymer_report: List[Dict]) -> List[str]:
    """accepted 但投票 read 的锚定分级不可靠的 run → 「计数确证但定位存疑」

    run_verdict=accepted 只由可分辨峰计数互证产生，确证的是「重复数」；
    run 起止定位依赖两侧路标碱基（锚），锚踩在信号异常区（边缘/低 Q/
    混合峰/压缩区）时位置仍可能整体偏移。此前该信息只藏在逐 read 的
    anchor_grade 字段里，结论不体现——用户容易误以为位置也确证了。
    """
    notes: List[str] = []
    for e in homopolymer_report:
        if e.get("run_verdict") != "accepted":
            continue
        g = e.get("vote_anchor_grade") or "reliable"
        if _ANCHOR_RANK.get(g, 1) < _ANCHOR_RANK["marginal"]:
            continue
        reason = ("路标落在信号异常区" if g == "unreliable"
                  else "路标处于信号边缘或单侧锚定")
        notes.append(
            f"注意：{run_label(e)} {e['start']}-{e['end']} 重复数已由峰图"
            f"计数确证（{e.get('observed_repeat_count', '?')} 个），但该 run"
            f"的锚定路标不可靠（{reason}）——计数确证但定位存疑，run 起止"
            "可能整体偏移数 bp，建议对照图谱核对边界")
    return notes


def _anchor_quality(r: Dict, pos: int) -> Tuple[str, List[str]]:
    """单个路标碱基的信号可靠度分级（run 定位置信度 = 两侧路标中较差者）

    pos 为 1-based 原始 read 碱基序号（路标在 poly run 外侧紧邻处）。风险
    逐项检查（用户点出的关键：锚可靠性「借自」路标碱基质量，路标本身落在
    信号异常区时整段定位悄悄出错）：
    - read 可信区之外（首尾信号爬升/衰减区）→ 边缘
    - QV 修剪边界外（basecaller 低置信）→ 边缘
    - 路标落在另一 poly/重复结构内部（撞上第三种歧义）→ 边缘
    - poly 下游 DROPOUT_WINDOW_BASES 窗内且骤降未恢复 → 不可靠
    - 混合峰位点（次级通道可见的异常）→ 不可靠
    - 路标峰与相邻峰间距 < 0.6×中位峰距（同通道压缩合并）→ 不可靠
    返回 ("reliable"|"marginal"|"unreliable", 原因列表)。
    """
    issues: List[str] = []
    marginal: List[str] = []
    unreliable: List[str] = []
    off = pos - 1                      # 0-based 原始 read 坐标
    trim_start = r.get("trim_start") or 0
    n_trim = len(r["trimmed_bases"])
    ti = off - trim_start              # 0-based 修剪后坐标（与 mixed/dropout 同系）
    if r.get("reliable_zone") and not in_zone(r["reliable_zone"], ti + 1):
        marginal.append("read 可信区之外（首尾信号爬升/衰减区）")
    if ti < 0 or ti >= n_trim:
        marginal.append("QV 修剪边界外")
    if any(e["pos"] == pos for e in r.get("mixed_detail") or []):
        unreliable.append("混合峰位点")
    for d in r.get("post_poly_dropouts") or []:
        d0 = d.get("read_pos")
        if d0 is not None and d0 < ti <= d0 + DROPOUT_WINDOW_BASES \
                and not d.get("end_truncation"):
            unreliable.append("poly 下游信号骤降区")
            break
    # 路标峰与相邻峰合并（压缩）：峰距显著小于全 read 中位峰距时峰型不可
    # 分辨，路标本身可能被错位调用/挪位——整段定位会悄悄偏移。混合峰检查
    # 只覆盖"次级通道可见"的形态，这里补上"同通道压缩合并"的形态
    tp = r.get("trimmed_peaks") or []
    if 0 <= ti < len(tp) and tp[ti] is not None:
        _gaps = sorted(b - a for a, b in zip(tp, tp[1:])
                       if a is not None and b is not None and b > a)
        _med = _gaps[len(_gaps) // 2] if _gaps else 0
        if _med >= 2:
            _near = None
            if ti > 0 and tp[ti - 1] is not None and tp[ti] > tp[ti - 1]:
                _near = tp[ti] - tp[ti - 1]
            if ti + 1 < len(tp) and tp[ti + 1] is not None and tp[ti + 1] > tp[ti]:
                _near = min(_near, tp[ti + 1] - tp[ti]) if _near else tp[ti + 1] - tp[ti]
            if _near is not None and _near < 0.6 * _med:
                unreliable.append("路标峰与相邻峰合并（压缩区）")
    issues = marginal + unreliable
    if unreliable:
        return "unreliable", issues
    if marginal:
        return "marginal", issues
    return "reliable", issues


def _read_run_verdict(called: Optional[int],
                      pc: Optional[int], est: Optional[Dict],
                      anchor_grade: str,
                      anchor_issues: List[str]) -> Dict:
    """单条 read 对一个 poly run 的判读（Step A，先单条分析）

    五档 resolution：
    - resolvable：可分辨峰数 == 调用数（峰图证据完整，有投票权）；
    - deficit：0 < 峰数 < 调用数——可见缺失证据（B4 口径：峰只会被压缩
      合并、不会凭空多出，段内可分辨峰即实测），主判读以峰图为准；
    - merged：峰完全合并（pc==0）或宽度法救回（峰数 < 调用数且 B2 判定
      合并形态）——该 read 对此 run「不可判定」，峰窗内逐位双峰不作证据；
    - noisy：峰数 > 调用数（疑似滑移肩峰），计数不可作证据；
    - no_evidence：峰计数不可用（采样密度不足，如稀疏合成数据）——无矛盾
      证据，维持调用数（caller_only），不做任何翻判。
    定位置信度 = 锚分级：锚不可靠时 position_confidence 降为 marginal。
    """
    if called is None:
        resolution, why = "unavailable", "覆盖段无法提取"
    elif pc is None:
        resolution, why = "no_evidence", "峰图计数不可用（采样密度不足）"
    elif pc == 0:
        resolution, why = "merged", "峰完全合并，无可分辨峰"
    elif pc == called:
        resolution, why = "resolvable", ""
    elif pc < called and est is not None \
            and est.get("method") in ("width", "second_derivative"):
        resolution, why = "merged", f"峰合并（宽度法救回，可见 {pc} < 调用 {called}）"
    elif pc < called:
        resolution, why = "deficit", f"可见峰 {pc} < 调用 {called}（段内缺失/部分合并）"
    else:
        resolution, why = "noisy", f"峰数 {pc} > 调用 {called}（疑似滑移肩峰）"
    verdict: Dict = {
        "resolution": resolution,
        "reason": why,
        "called_count": called,
        "peak_count": pc,
        "length_estimate": est["n"] if est else None,
        "length_ci": ([est["ci_low"], est["ci_high"]] if est else None),
        "anchor_grade": anchor_grade,
        "anchor_issues": anchor_issues,
        "position_confidence": (
            "marginal" if anchor_grade != "reliable" else "reliable"),
    }
    if est is not None and resolution == "merged":
        # 宽度法是模型估计而非可见证据：保留数值供参考，但明确它不是证据
        verdict["width_estimate_only"] = True
    return verdict


def _anchor_verdict_for_read(r: Dict, run: Dict, aln: Dict) -> Tuple[str, List[str]]:
    """run 两侧路标碱基的可靠度综合（定位可靠度 = 两侧路标中最差者）

    路标 = 原始 read 上紧邻 run 连续同碱基段外侧的第一位非重复碱基——
    它钉死 run 的起止坐标，把「可滑动」变「可计数」；路标自身踩在信号
    异常区（边缘/低 Q/混合峰/骤降）时，整段定位会悄悄出错，必须降级。
    返回 (grade, issues)：grade ∈ reliable / marginal / unreliable /
    one-sided（单侧锚定或两侧均无路标）。"""
    b_read = run["base"] if aln["direction"] == "+" else _COMPLEMENT[run["base"]]
    win = _aligned_run_window(aln, run["start"], run["end"], run["base"])
    if win is None:
        return "unreliable", ["run 窗口无法定位"]
    i0, i1, _ = win
    s_off = r.get("trim_start") or 0
    raw = (r.get("raw_bases") or "").upper()
    if not raw:
        return "unreliable", ["原始碱基串缺失"]
    r0, r1 = s_off + i0, s_off + i1
    while r0 > 0 and raw[r0 - 1] == b_read:
        r0 -= 1
    while r1 < len(raw) - 1 and raw[r1 + 1] == b_read:
        r1 += 1
    grades: List[str] = []
    issues: List[str] = []
    if r0 > 0 and raw[r0 - 1] not in (b_read, "N"):
        g, iss = _anchor_quality(r, r0)          # 左路标：raw 0-based r0-1 → 1-based r0
        grades.append(g)
        issues += [f"左路标：{x}" for x in iss]
    if r1 < len(raw) - 1 and raw[r1 + 1] not in (b_read, "N"):
        g, iss = _anchor_quality(r, r1 + 2)      # 右路标：raw 0-based r1+1 → 1-based r1+2
        grades.append(g)
        issues += [f"右路标：{x}" for x in iss]
    if not grades:
        return "one-sided", ["两侧均无路标（read 截断在 run 内）"]
    order = {"reliable": 0, "marginal": 1, "unreliable": 2}
    worst = max(grades, key=lambda g: order[g])
    if len(grades) == 1 and worst == "reliable":
        return "one-sided", issues + ["单侧锚定"]
    return worst, issues
