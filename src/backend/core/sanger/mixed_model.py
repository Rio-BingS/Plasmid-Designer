"""双峰自适应背景模型与 read 级混合分级（goal T11；A 3.1；B P2-14）

旧口径的病根（审计 A 3.1）：次级峰比 > 固定 0.30 即记双峰，位点数 ≥5 就升
「疑似混合样品」——染料肩峰（3730）与信号衰减段（3100）都是"局部成串的
0.3-0.4 比值小峰"，计数达标即误报混合。

本模块两层：

1. **站点层（自适应背景）**：每条 read 按通道对（主→次，如 G→A）在 ±window
   峰滑窗内估计次峰比的局部中位数与 MAD，站点双峰判据
   `ratio > max(ratio_floor, local_median + k·mad_scale·MAD)`，输出 `mixed_z`。
   局部样本不足时回退全 read 同对比，再不足回退绝对下限。局部背景抬升的
   区段（信号衰减/无信号窗口）阈值随之抬升，衰减段小峰不再成串误报；
   背景干净的合成数据阈值即绝对下限，单点 35%/50%/65% 混合照常检出。

2. **read 层（混合分级）**：绝对计数不再决定结论（MIXED_* 常量退役为兼容
   re-export），判「疑似混合样品（widespread）」须满足证据形态学：
   a) 离散一致散布——位点数 ≥ widespread_min、比值 CV < ratio_cv_max、
      前/中/后三段至少两段有分布且跨度 > segment_span_max；
   b) 自熄广布混合——z 模型对均匀混合会"把背景学到手上"而全部自熄，
      改用未过 z 的 floor 位点（ratio > ratio_floor）密度
      ≥ density_min_per100bp 且 CV/散布同上（全 read 均匀 30%/50% 混合）；
   c) 连续双峰段 ≥ stretch_min（indel 介导混合，集中但真实）。
   三者都要求双峰位点的 Q 中位数 ≥ 全 read 有效 Q 中位数 × site_q_ratio_min
   （3730/3100 实测：肩峰/衰减位点 Q 中位只有 read 中位的 0.54-0.59，
   真混合合成数据为 1.0——低主信号区的比值是噪声主导，不作为混合升级依据）。
   未达 widespread 时按形态归因：集中单一区段 → local（局部信号异常，
   info）；散布但低 Q 或比值不一致 → noise（信号质量，info）；2-4 位点 →
   scattered（个别双峰）；≤1 → none。

参数表 core/sanger/params/mixed_<型号>.json 按 ABIF MODL/MCHN 选择，
缺失回退 mixed_default.json（与 reliable_zone 同为可配置口径）。
"""

import json
import re
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.sanger.abif_reader import peak_window
from core.sanger.reliable_zone import in_zone

PARAMS_DIR = Path(__file__).resolve().parent / "params"

# pull-up（染料拖影）抑制：与旧版同式（饱和主峰 + 低占比次级 + 同期出现）
PULLUP_HEIGHT_FACTOR = 3.0
PULLUP_MAX_RATIO = 0.5
# tracy 印证的 read 末端豁免（原 pipeline.END_MARGIN，随函数迁入）
END_MARGIN = 20

_IUPAC_EXPAND = {"A": "A", "C": "C", "G": "G", "T": "T", "R": "AG", "Y": "CT",
                 "S": "CG", "W": "AT", "K": "GT", "M": "AC", "B": "CGT",
                 "D": "AGT", "H": "ACT", "V": "ACG"}


@dataclass(frozen=True)
class MixedParams:
    """双峰模型参数（JSON 可覆盖；缺省与 mixed_default.json 一致）"""
    ratio_floor: float = 0.30
    adaptive_k: float = 4.0
    mad_scale: float = 1.4826
    window: int = 25
    min_local_samples: int = 8
    widespread_min: int = 5
    stretch_min: int = 8
    density_min_per100bp: float = 5.0
    ratio_cv_max: float = 0.35
    segment_span_max: float = 0.35
    site_q_ratio_min: float = 0.7


_DEFAULT_PARAMS = MixedParams()
# (文件, mtime, size) → 参数；改 JSON 即时生效（与手测工作流匹配）
_PARAMS_CACHE: Dict[Tuple[str, float, int], MixedParams] = {}


def _median(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def load_params(instrument: Optional[str] = None) -> MixedParams:
    """按仪器型号加载参数表：mixed_<型号>.json 优先，回退 mixed_default.json。

    型号取 MODL/MCHN 文本中的 3-4 位数字（3730xl→3730、3130xl→3130）；
    无仪器信息或文件缺失时用默认表。字段校验失败按缺失处理（回退默认）。"""
    model = ""
    if instrument:
        m = re.search(r"\d{3,4}", str(instrument))
        model = m.group(0) if m else ""
    for name in ([f"mixed_{model}.json"] if model else []) + ["mixed_default.json"]:
        p = PARAMS_DIR / name
        if not p.is_file():
            continue
        try:
            st = p.stat()
            key = (str(p), st.st_mtime, st.st_size)
            if key in _PARAMS_CACHE:
                return _PARAMS_CACHE[key]
            data = json.loads(p.read_text(encoding="utf-8"))
            known = set(MixedParams.__dataclass_fields__)
            params = MixedParams(**{k: v for k, v in data.items() if k in known})
            _PARAMS_CACHE[key] = params
            return params
        except (ValueError, OSError, TypeError):
            continue
    return _DEFAULT_PARAMS


def _pair_table(bases: str, trace: Dict[str, List[int]],
                peak_indices: List[int]) -> List[Optional[Tuple[
                    str, Dict[str, float], Dict[str, int]]]]:
    """每位点的 (主通道, {次通道: 面积比}, {通道: 面积}) 表；无信号/退化
    窗口为 None。

    与旧检测同口径：主峰面积须高于全 read 主峰面积中位 25%（called 碱基
    无信号的退化窗口四通道面积全相等，不设门槛会整段误判）；四通道面积
    接近相等（终审 A-19：第四名通道仍显著 = 无主导信号）剔除。"""
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0:
        return []
    windows: List[Optional[Dict[str, int]]] = []
    for i, base in enumerate(bases):
        if base not in "ACGT" or i >= len(peak_indices):
            windows.append(None)
            continue
        pk = peak_indices[i]
        if pk is None or pk < 0 or pk >= n:
            windows.append(None)
            continue
        lo, hi = peak_window(peak_indices, i, n)
        windows.append({b: sum(trace[b][lo:hi]) for b in "ACGT"})
    tops = sorted(max(w.values()) for w in windows if w)
    pos_tops = [t for t in tops if t > 0]
    main_floor = 0.25 * pos_tops[len(pos_tops) // 2] if pos_tops else 0.0
    table: List[Optional[Tuple[str, Dict[str, float], Dict[str, int]]]] = []
    for i, base in enumerate(bases):
        areas = windows[i]
        if areas is None:
            table.append(None)
            continue
        sorted_a = sorted(areas.values(), reverse=True)
        if sorted_a[0] < main_floor or sorted_a[0] <= 0 or sorted_a[3] >= 0.8 * sorted_a[0]:
            table.append(None)
            continue
        ratios = {c: areas[c] / sorted_a[0] for c in "ACGT" if c != base}
        table.append((base, ratios, areas))
    return table


def _pair_background(table, i: int, main: str, sec: str,
                     params: MixedParams) -> Tuple[Optional[float], Optional[float]]:
    """位点 i 的 (主→sec) 通道对比值背景：±window 峰内同对样本的中位数与
    MAD；局部样本不足回退全 read 同对；全 read 也不足 → (None, None)。"""
    w = params.window

    def collect(limit: Optional[int]) -> List[float]:
        out = []
        for j, t in enumerate(table):
            if not t or t[0] != main or j == i:
                continue
            if limit is not None and abs(j - i) > limit:
                continue
            v = t[1].get(sec)
            if v is not None:
                out.append(v)
        return out

    local = collect(w)
    if len(local) < params.min_local_samples:
        wide = collect(None)
        if len(wide) >= params.min_local_samples:
            local = wide
        else:
            return None, None
    med = _median(local)
    mad = _median([abs(x - med) for x in local]) if med is not None else None
    return med, mad


def detect_mixed_detail(bases: str, trace: Dict[str, List[int]],
                        peak_indices: List[int],
                        params: Optional[MixedParams] = None) -> List[Dict]:
    """逐位双峰细节：[{pos(1-based), ratio, secondary_base, pullup, mixed_z,
    pair, local_median, local_mad}]（goal T11 自适应口径）。

    阈值 = max(ratio_floor, 局部中位 + k·mad_scale·MAD)；mixed_z =
    (ratio − 局部中位) / (mad_scale·MAD)（MAD 取 1e-3 下限防 0 除；无背景
    样本时 None）。pull-up 拖影抑制与旧版同式：主峰近饱和、次级占比低、
    峰尖同期（±1 采样点）出现。"""
    p = params or _DEFAULT_PARAMS
    return _detect_from_table(_pair_table(bases, trace, peak_indices),
                              bases, trace, peak_indices, p)


def _detect_from_table(table, bases: str, trace: Dict[str, List[int]],
                       peak_indices: List[int],
                       p: MixedParams) -> List[Dict]:
    if not table:
        return []
    n = min(len(v) for v in trace.values()) if trace else 0
    all_heights = sorted(t[2][t[0]] for t in table if t)
    med_height = all_heights[len(all_heights) // 2] if all_heights else 0.0
    detail: List[Dict] = []
    for i, t in enumerate(table):
        if t is None:
            continue
        base, ratios, areas = t
        sec = max(ratios, key=ratios.get)
        ratio = ratios[sec]
        if ratio <= p.ratio_floor:
            continue
        med, mad = _pair_background(table, i, base, sec, p)
        if med is None or mad is None:
            thr, z = p.ratio_floor, None
        else:
            thr = max(p.ratio_floor, med + p.adaptive_k * p.mad_scale * mad)
            z = round((ratio - med) / max(p.mad_scale * mad, 1e-3), 1)
        if ratio <= thr:
            continue
        entry = {"pos": i + 1, "ratio": round(ratio, 3), "secondary_base": sec,
                 "pullup": False, "mixed_z": z, "pair": f"{base}>{sec}",
                 "local_median": round(med, 4) if med is not None else None,
                 "local_mad": round(mad, 4) if mad is not None else None}
        # T24.4：调用碱基不在峰面积前两通道 → caller 与 trace 分歧，明示标注
        # （secondary_base 仍取前两通道中不等于调用碱基者——上面 max 已是）
        top2 = {c for c, _ in sorted(areas.items(), key=lambda kv: kv[1],
                                     reverse=True)[:2]}
        if base not in top2:
            entry["call_disagrees_with_trace"] = True
        # pull-up：主峰近饱和、次级占比低、次级峰尖与主峰同期（±1 采样点）
        if med_height > 0 and areas[base] >= PULLUP_HEIGHT_FACTOR * med_height \
                and ratio <= PULLUP_MAX_RATIO:
            pk = peak_indices[i] if i < len(peak_indices) else None
            if pk is not None and 0 <= pk < n:
                lo, hi = peak_window(peak_indices, i, n)
                main_apex = max(range(lo, hi), key=lambda x: trace[base][x])
                sec_apex = max(range(lo, hi),
                               key=lambda x: trace.get(sec, [0] * n)[x])
                entry["pullup"] = abs(sec_apex - main_apex) <= 1
        detail.append(entry)
    return detail


def detect_and_classify(bases: str, trace: Dict[str, List[int]],
                        peak_indices: List[int], *, quality: Optional[List[int]] = None,
                        zone: Optional[Dict] = None, tracy_calls: Optional[Dict] = None,
                        params: Optional[MixedParams] = None,
                        exclude: Optional[set] = None) -> Tuple[List[Dict], Dict]:
    """管线单点入口：一次构图完成 检出 → 过滤 → 分级（通道对比值表只算
    一遍，floor 兜底路径与站点检测共用）。

    过滤：exclude（poly 滑移 echo、合并压缩 run 窗位点）与可信区外（zone
    给出时，in_zone 口径——无可信区视为在内）剔除；kept 位点附 called_base
    供 tracy 印证。返回 (kept_entries, profile)：kept 含 pullup 标记位点
    （调用方区分展示），profile 为 classify_mixed 产物。"""
    p = params or load_params()
    table = _pair_table(bases, trace, peak_indices)
    ex = exclude or set()
    kept: List[Dict] = []
    for e in _detect_from_table(table, bases, trace, peak_indices, p):
        if e["pos"] in ex or not in_zone(zone, e["pos"]):
            continue
        i = e["pos"] - 1
        e["called_base"] = bases[i] if 0 <= i < len(bases) else None
        kept.append(e)
    profile = classify_mixed(kept, len(bases), tracy_calls=tracy_calls,
                             peaks=peak_indices, zone=zone, quality=quality,
                             table=table, params=p, exclude=ex)
    return kept, profile


def detect_mixed_positions(bases: str, trace: Dict[str, List[int]],
                           peak_indices: List[int],
                           params: Optional[MixedParams] = None) -> List[int]:
    """兼容入口：双峰位点 1-based 位置列表（已剔 pull-up）"""
    return [e["pos"] for e in detect_mixed_detail(bases, trace, peak_indices,
                                                  params) if not e["pullup"]]


def _ratio_cv(rs: List[float]) -> Optional[float]:
    if len(rs) < 2:
        return None
    mean = sum(rs) / len(rs)
    if mean <= 0:
        return None
    var = sum((x - mean) ** 2 for x in rs) / len(rs)
    return (var ** 0.5) / mean


def _thirds(positions: List[int], read_len: int) -> List[int]:
    return [sum(1 for p in positions if min(2, (p - 1) * 3 // read_len) == t)
            for t in range(3)]


def _q_ratio(positions: List[int], quality: Optional[List[int]]) -> float:
    """双峰位点 Q 中位 / 全 read 有效 Q 中位（无质量值 → 1.0 不设限）"""
    if not quality:
        return 1.0
    site_q = [quality[p - 1] for p in positions if 0 <= p - 1 < len(quality)]
    read_q = [q for q in quality if q and q > 0]
    if not site_q or not read_q:
        return 1.0
    rq = _median([float(x) for x in read_q]) or 0.0
    if rq <= 0:
        return 1.0
    return (_median([float(x) for x in site_q]) or 0.0) / rq


def _tracy_mixed_corroboration(entries: List[Dict], peaks: List[int],
                               tracy_calls: Dict,
                               zone: Optional[Dict] = None) -> Dict:
    """tracy 次级 basecall 对院内逐位双峰的独立印证（审计 #8）

    tracy 与 ABI caller 的碱基序列不一一对应，按峰在 trace 上的采样坐标
    对位：取与院内峰最近的 tracy 调用，距离不超过半个峰间距才算同一位点。
    每个院内双峰位点（pull-up 已剔除的不计）标记 agree / disagree /
    unmatched；tracy_only 为院内可判读区间内 tracy 独报的双峰数。
    只作证据记录，不改院内分级。"""
    primary = tracy_calls.get("primary") or ""
    secondary = tracy_calls.get("secondary") or ""
    tpos = tracy_calls.get("positions") or []
    nt = min(len(primary), len(secondary), len(tpos))
    out = {"agree": 0, "disagree": 0, "unmatched": 0, "tracy_only": 0,
           "agreement": None, "version": tracy_calls.get("version")}
    valid = [p for p in peaks if p is not None and p >= 0]
    if nt == 0 or len(valid) < 2:
        out["unmatched"] = sum(1 for e in entries if not e.get("pullup"))
        return out
    gaps = sorted(b - a for a, b in zip(valid, valid[1:]) if b > a)
    tol = max(1.0, 0.5 * gaps[len(gaps) // 2]) if gaps else 1.0

    def nearest(pk: int) -> Optional[int]:
        j = bisect_left(tpos, pk, 0, nt)
        best = None
        for c in (j - 1, j):
            if 0 <= c < nt and (best is None or abs(tpos[c] - pk) < abs(tpos[best] - pk)):
                best = c
        return best if best is not None and abs(tpos[best] - pk) <= tol else None

    def alleles(c: int) -> set:
        return {primary[c]} | set(_IUPAC_EXPAND.get(secondary[c], ""))

    matched: set = set()
    for e in entries:
        if e.get("pullup"):
            continue
        i = e["pos"] - 1
        pk = peaks[i] if 0 <= i < len(peaks) else None
        c = nearest(pk) if pk is not None else None
        if c is None:
            e["tracy"] = "unmatched"
            out["unmatched"] += 1
            continue
        matched.add(c)
        het = secondary[c] != primary[c] and secondary[c] != "N"
        mine = {b for b in (e.get("called_base"), e.get("secondary_base")) if b}
        if het and mine and mine <= alleles(c):
            e["tracy"] = "agree"
            out["agree"] += 1
        else:
            e["tracy"] = "disagree"
            out["disagree"] += 1
    n = len(peaks)
    if zone:
        lo_i, hi_i = zone["start"] - 1, zone["end"] - 1
    else:
        lo_i, hi_i = END_MARGIN, n - END_MARGIN - 1
    if 0 <= lo_i <= hi_i < n and peaks[lo_i] is not None and peaks[hi_i] is not None:
        lo, hi = peaks[lo_i], peaks[hi_i]
        out["tracy_only"] = sum(
            1 for c in range(nt)
            if c not in matched and lo <= tpos[c] <= hi
            and secondary[c] != primary[c] and secondary[c] != "N")
    judged = out["agree"] + out["disagree"]
    if judged:
        out["agreement"] = round(out["agree"] / judged, 3)
    return out


def classify_mixed(entries: List[Dict], read_len: int,
                   tracy_calls: Optional[Dict] = None,
                   peaks: Optional[List[int]] = None,
                   zone: Optional[Dict] = None,
                   quality: Optional[List[int]] = None,
                   table: Optional[List] = None,
                   params: Optional[MixedParams] = None,
                   exclude: Optional[set] = None) -> Dict:
    """把逐位双峰聚合成 read 级分级（goal T11 证据形态学，见模块 docstring）。

    entries 为调用方已过滤（滑移/可信区/合并压缩区剔除、called_base 就位）
    的检出位点；table（_pair_table 产物）给出时启用 floor 密度路径
    （z 模型对均匀混合自熄的兜底），exclude 的位点同样不计入 floor 统计。
    分级只看证据形态，绝对计数不再决定结论；MIXED_* 旧常量退役为兼容
    re-export。"""
    p = params or _DEFAULT_PARAMS
    kept = [e for e in entries if not e["pullup"]]
    count = len(kept)
    profile = {
        "count": count,
        "pullup_excluded": len(entries) - count,
        "positions": [e["pos"] for e in kept],
        "median_ratio": None,
        "minor_fraction": None,
        "span": None,
        "longest_stretch": 0,
        "class": "none",
        "class_reason": None,
        # 形态统计（T11 新增，前端/报告可选展示）
        "ratio_cv": None, "site_q_ratio": None,
        "thirds": [0, 0, 0], "span_frac": None,
        "sample_density_per100bp": None,
        # tracy 次级 basecall 的独立印证；tracy 未运行为 None。分级仍按院内口径
        "tracy_corroboration": (
            _tracy_mixed_corroboration(entries, peaks or [], tracy_calls, zone)
            if tracy_calls is not None else None),
    }
    if not kept:
        return profile
    ratios = sorted(e["ratio"] for e in kept)
    med = ratios[len(ratios) // 2]
    profile["median_ratio"] = round(med, 3)
    if 0 < med:
        # 次峰/主峰 = f/(1-f)（f 为次要克隆占比）→ f = r/(1+r)；50/50 混合
        # 时 r=1 → f=0.5
        profile["minor_fraction"] = round(med / (1 + med), 3)
    positions = profile["positions"]
    profile["span"] = [positions[0], positions[-1]]
    stretch = best = 1
    for a, b in zip(positions, positions[1:]):
        stretch = stretch + 1 if b - a == 1 else 1
        best = max(best, stretch)
    profile["longest_stretch"] = best

    zc = _ratio_cv([float(e["ratio"]) for e in kept])
    thirds = _thirds(positions, read_len)
    span_frac = (positions[-1] - positions[0]) / read_len if read_len else 0.0
    q_ratio = _q_ratio(positions, quality)
    multi_thirds = sum(1 for t in thirds if t) >= 2
    spread = multi_thirds and span_frac > p.segment_span_max
    profile.update({"ratio_cv": round(zc, 3) if zc is not None else None,
                    "site_q_ratio": round(q_ratio, 3), "thirds": thirds,
                    "span_frac": round(span_frac, 3)})

    def _floor_stats() -> Tuple[int, float, Optional[float], bool, float]:
        """未过 z 的 floor 位点统计（z 自熄兜底路径）；无 table 时全零。"""
        if not table:
            return 0, 0.0, None, False, 1.0
        pullup_pos = {e["pos"] for e in entries if e.get("pullup")}
        ex = exclude or set()
        fp: List[int] = []
        fr: List[float] = []
        for i, t in enumerate(table):
            if not t:
                continue
            pos = i + 1
            if pos in pullup_pos or pos in ex \
                    or max(t[1].values()) <= p.ratio_floor:
                continue
            if zone and not (zone.get("start", 1) <= pos <= zone.get("end", read_len)):
                continue
            fp.append(pos)
            fr.append(max(t[1].values()))
        if not fp:
            return 0, 0.0, None, False, 1.0
        dens = len(fp) / read_len * 100 if read_len else 0.0
        fth = _thirds(fp, read_len)
        f_span = (fp[-1] - fp[0]) / read_len if read_len else 0.0
        f_spread = sum(1 for t in fth if t) >= 2 and f_span > p.segment_span_max
        return len(fp), dens, _ratio_cv(fr), f_spread, _q_ratio(fp, quality)

    f_n, f_dens, f_cv, f_spread, f_q = _floor_stats()
    profile["sample_density_per100bp"] = round(f_dens, 2) if table else None

    q_ok = q_ratio >= p.site_q_ratio_min
    reason = None
    if best >= p.stretch_min:
        cls = "widespread"  # indel 介导：连续双峰段集中但真实
    elif (f_n >= p.widespread_min and f_dens >= p.density_min_per100bp
          and f_cv is not None and f_cv < p.ratio_cv_max and f_spread
          and f_q >= p.site_q_ratio_min):
        cls = "widespread"  # 自熄广布混合（均匀 30%/50%）
    elif (count >= p.widespread_min and zc is not None and zc < p.ratio_cv_max
          and spread and q_ok):
        cls = "widespread"  # 离散一致散布
    elif count >= p.widespread_min:
        if not multi_thirds or span_frac <= p.segment_span_max:
            cls, reason = "local", "concentrated_segment"
        elif not q_ok:
            cls, reason = "noise", "low_site_q"
        else:
            cls, reason = "noise", "inconsistent_ratio"
    elif count >= 2:
        cls = "scattered"
    else:
        cls = "none"
    profile["class"] = cls
    profile["class_reason"] = reason
    return profile
