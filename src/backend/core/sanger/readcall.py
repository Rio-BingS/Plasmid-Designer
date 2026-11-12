"""ReadCall：read 级套峰判定（重构二期第一块，契约 READ_MIXED_TEMPLATE /
READ_SHIFT_AFTER_POINT）

问题（2026-11-12 金标准核查）：整条 read 套峰（CRISPR 嵌合、非单克隆）或
某点之后整体套峰时，位点级逻辑把套峰里的主峰差异逐个当成确证（Fully_albino_3
判 ✗「14 处确证差异」），并把每个次峰逐条记成 mixed_variant（单样品数百条）。
mixed_model 的 widespread/noise 分级以「比值一致性」为主，密集但比值离散的
read 会落 noise，而低密度长 read 又可能落 widespread，不适合作屏蔽依据。

本模块只看硬检出双峰位点（mixed_profile.positions：已排除 pull-up、滑移、
合并压缩、峰形退化与核心区外）的**局部密度**：
- 滑窗（window bp，步长 step）内位点 ≥ min_sites_window 的窗合并为候选段；
- 候选段收缩到首末双峰位点，长度 ≥ min_seg_len、位点 ≥ seg_sites_min、
  段内密度 ≥ seg_density_min（每 100bp）才算「套峰段」；
- 分级：首个套峰段起点距核心区起点 > shift_offset → SHIFT_AFTER_POINT
  （断点 = 段起点）；否则套峰段覆盖核心区 ≥ template_cov → MIXED_TEMPLATE；
  其余 → LOCAL_MIXED；无套峰段 → OK。

尾端衰减（AcGFP/ECFP 尾端 10–15 处/100bp、次峰占比 ~0.17）达不到段密度
门槛，不会被当成套峰。阈值来自金标准 30 例（套峰 read 段密度 21–65/100bp，
正常 read 段密度 ≤15/100bp），集中在 ReadMixParams，可整体关闭：
SANGER_READ_MIXED_CALL=0 回到旧口径。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Sequence, Tuple

OK = "OK"
MIXED_TEMPLATE = "MIXED_TEMPLATE"
SHIFT_AFTER_POINT = "SHIFT_AFTER_POINT"
LOCAL_MIXED = "LOCAL_MIXED"
MIXED_STATES = (MIXED_TEMPLATE, SHIFT_AFTER_POINT, LOCAL_MIXED)


@dataclass(frozen=True)
class ReadMixParams:
    window: int = 60
    step: int = 10
    min_sites_window: int = 5
    min_seg_len: int = 80
    seg_sites_min: int = 25
    seg_density_min: float = 20.0
    shift_offset: int = 100
    template_cov: float = 0.5


PARAMS = ReadMixParams()


def enabled() -> bool:
    return os.environ.get("SANGER_READ_MIXED_CALL", "1") != "0"


def mixed_segments(positions: Sequence[int], start: int, end: int,
                   p: ReadMixParams = PARAMS) -> List[Tuple[int, int, int]]:
    """套峰段 [(首位, 末位, 位点数)]（read 坐标，1-based，闭区间）"""
    pos = sorted(x for x in positions if start <= x <= end)
    if len(pos) < p.seg_sites_min:
        return []
    wins: List[List[int]] = []
    s = start
    i0 = 0
    while s <= max(start, end - p.window + 1):
        e = s + p.window - 1
        while i0 < len(pos) and pos[i0] < s:
            i0 += 1
        n = 0
        j = i0
        while j < len(pos) and pos[j] <= e:
            n += 1
            j += 1
        if n >= p.min_sites_window:
            if wins and s <= wins[-1][1]:
                wins[-1][1] = max(wins[-1][1], e)
            else:
                wins.append([s, e])
        s += p.step
    out: List[Tuple[int, int, int]] = []
    for a, b in wins:
        ps = [x for x in pos if a <= x <= b]
        if not ps:
            continue
        a, b, n = ps[0], ps[-1], len(ps)
        length = b - a + 1
        if (length >= p.min_seg_len and n >= p.seg_sites_min
                and 100.0 * n / length >= p.seg_density_min):
            out.append((a, b, n))
    return out


def classify_read_mixing(positions: Sequence[int],
                         zone: Optional[Dict], read_len: int,
                         p: ReadMixParams = PARAMS) -> Dict:
    """read 级套峰判定。zone 为核心区 {start, end}（缺省整条 read）。"""
    zs = int((zone or {}).get("start") or 1)
    ze = int((zone or {}).get("end") or read_len or 0)
    res = {"state": OK, "segments": [], "breakpoint": None,
           "coverage": 0.0, "params": asdict(p)}
    if not enabled() or ze < zs:
        return res
    segs = mixed_segments(positions, zs, ze, p)
    if not segs:
        return res
    cov = sum(b - a + 1 for a, b, _n in segs) / float(ze - zs + 1)
    res["segments"] = [[a, b, n] for a, b, n in segs]
    res["coverage"] = round(cov, 3)
    first = segs[0][0]
    if first - zs > p.shift_offset:
        res["state"] = SHIFT_AFTER_POINT
        res["breakpoint"] = first
    elif cov >= p.template_cov:
        res["state"] = MIXED_TEMPLATE
    else:
        res["state"] = LOCAL_MIXED
    return res


def overlapping(read_mixing: Optional[Dict]) -> bool:
    """全程或某点后套峰（read 级结论受影响）"""
    return bool(read_mixing) and read_mixing.get("state") in (
        MIXED_TEMPLATE, SHIFT_AFTER_POINT)


def in_mixed_segment(read_mixing: Optional[Dict], read_pos) -> bool:
    if not read_mixing or read_mixing.get("state") not in MIXED_STATES:
        return False
    if not read_pos:
        return False
    rp = int(read_pos)
    # 全程套峰：整条 read 是混合信号，段外的「干净」位置同样不能单独确证
    if read_mixing.get("state") == MIXED_TEMPLATE:
        return True
    for a, b, _n in read_mixing.get("segments") or []:
        if a <= rp <= b:
            return True
    # 某点后套峰：断点之后整段不可信（段末之后多为衰减，同样不予确证）
    bp = read_mixing.get("breakpoint")
    return bool(read_mixing.get("state") == SHIFT_AFTER_POINT and bp and rp >= bp)


def describe(read_mixing: Dict) -> str:
    st = read_mixing.get("state")
    segs = read_mixing.get("segments") or []
    n = sum(s[2] for s in segs)
    if st == MIXED_TEMPLATE:
        return f"全程套峰（{n} 处双峰）"
    if st == SHIFT_AFTER_POINT:
        return f"第 {read_mixing.get('breakpoint')} 位起整体套峰（{n} 处双峰）"
    if st == LOCAL_MIXED:
        rng = "、".join(f"{a}–{b}" for a, b, _n in segs[:3])
        return f"局部套峰（read 第 {rng} 位，{n} 处双峰）"
    return ""


def annotate_ref_spans(read_results: List[Dict]) -> None:
    """套峰段映射到参考坐标（read_mixing.ref_spans，[[lo, hi], ...]），供
    verdict 做跨 read 互检：不同 read 在同一参考区段都套峰才指向模板。"""
    from core.sanger.readmap import ReadRefMap
    for r in read_results:
        rm = r.get("read_mixing") or {}
        if rm.get("state") not in MIXED_STATES:
            continue
        try:
            rmap = ReadRefMap.from_alignment(r)
        except Exception:  # noqa: BLE001 — 比对缺失时不做互检
            continue
        spans = []
        for a, b, _n in rm.get("segments") or []:
            mapped = [rmap.read_to_ref(x) for x in range(a, b + 1)]
            mapped = [m for m in mapped if m]
            if mapped:
                spans.append([min(mapped), max(mapped)])
        rm["ref_spans"] = spans


def corroborated(read_results: List[Dict], min_overlap: int = 30) -> List[str]:
    """套峰（全程/某点后）read 中，另有一条不同 read 的套峰段在参考上与之
    重叠 ≥ min_overlap bp 的文件名——指向模板问题而非单条测序问题。"""
    hit = [r for r in read_results
           if (r.get("read_mixing") or {}).get("state")
           in (MIXED_TEMPLATE, SHIFT_AFTER_POINT)
           and (r.get("read_mixing") or {}).get("ref_spans")]
    out = []
    for r in hit:
        for o in hit:
            if o is r or o.get("filename") == r.get("filename"):
                continue
            if any(min(b1, b2) - max(a1, a2) + 1 >= min_overlap
                   for a1, b1 in r["read_mixing"]["ref_spans"]
                   for a2, b2 in o["read_mixing"]["ref_spans"]):
                out.append(r.get("filename"))
                break
    return out
