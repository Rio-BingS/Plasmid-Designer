"""信号驱动可信区（goal T12；A 1.1）

旧口径（reliable_zone 三段式位置先验）的病根：>700bp 一律不可信、位置
直接把变体判 low——好 read 的 700-1000bp 段白白丢掉（REF_MX 123bp 实测
verified<100% 恒真），而首端 30bp 硬区里峰证据齐全的真变异又被"位置先验"
压成伪影（S1 假 ✓ 与 S9 假阴的公共病根）。

新口径：**可信由 read 自身信号决定**。20bp 滑窗（步长 5）逐窗计算：

- mean Q ≥ TRUST_MEAN_Q；
- 峰距变异系数 ≤ SPACING_CV_MAX（峰数据缺失跳过）；
- 半高宽相对中段基准 ≤ WIDTH_RATIO_MAX（同上）；
- 主峰/次峰面积比：窗内次级/主峰面积比中位 ≤ SECONDARY_RATIO_MAX（衰减
  段次级占比抬升，是峰宽之外的第二个衰减信号）。

四项（可用的）都达标 → 该窗可信。可信区 = 首个可信窗起点（且不早于
front_hard+1 硬区，起点碱基本身须达标）到最后一个可信窗终点的单一区间
（窗内间断容忍：N 列/poly 压缩由各自口径独立扣除，不做二级分段）。无峰图
（旧记录重放/无 trace）时窗口只看 mean Q 并以 tail_hard 兜底封顶——
700bp 硬顶取消，tail_hard 仅此场景生效。位置不再直接把变体判 low
（_variant_confidence 仅保留 front 硬区强制），可信区作为 T15 的特征与
verified 覆盖的边界（coverage.verified = measured ∩ zone − N）。

reliable_zone.compute_reliable_zone 保留原签名委托本模块；ZoneParams
（front_hard/front_gray/tail_gray/tail_hard）仍是配置载体——front_hard
硬区保留，tail_gray 不再封顶（仅作 zone_reason 文案的灰区参照），
tail_hard 仅无峰图兜底。
"""

from typing import Dict, List, Optional

from core.sanger.signal import peak_widths

# 窗信任阈值（与旧灰区/衰减判定同源口径，goal T12）
WINDOW = 20
STEP = 5
TRUST_MEAN_Q = 25.0
SPACING_CV_MAX = 0.3
WIDTH_RATIO_MAX = 1.5
SECONDARY_RATIO_MAX = 0.5   # 窗内次级/主峰面积比中位上限
MIN_GAPS = 3                # 峰距 CV 最小样本数


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _spacing_cv(peaks: List[Optional[int]]) -> Optional[float]:
    pk = [p for p in peaks if p is not None]
    gaps = [b - a for a, b in zip(pk, pk[1:]) if b > a]
    if len(gaps) < MIN_GAPS:
        return None
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return None
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    return (var ** 0.5) / mean


def compute_signal_zone(raw_len: int, trim_start: int, trimmed_q: List[int],
                        trace: Optional[Dict[str, List[int]]] = None,
                        trimmed_peaks: Optional[List[Optional[int]]] = None,
                        params=None) -> Dict:
    """逐窗信号判定的可信区（reliable_zone.compute_reliable_zone 的实现本位）。

    返回 {start, end, raw_start, raw_end, front_reason, tail_reason,
    params}（坐标口径与旧实现一致：start/end 为修剪后 1-based 闭区间，
    raw_* 为原始 read 坐标）；无峰图时窗口只看 mean Q 并以 tail_hard 兜底。"""
    from core.sanger.reliable_zone import ZoneParams

    zp = params or ZoneParams()
    n = len(trimmed_q)
    s = trim_start
    if n == 0:
        return {"start": 1, "end": 0, "raw_start": s + 1, "raw_end": s,
                "front_reason": "空 read", "tail_reason": "",
                "params": zp.as_dict()}

    widths: List[float] = []
    if trace and trimmed_peaks and len(trimmed_peaks) == n:
        try:
            widths = peak_widths(trace, [p if p is not None else -1
                                         for p in trimmed_peaks])
        except Exception:  # noqa: BLE001 峰型缺失时退回只看 Q
            widths = []
    has_trace = bool(trace) and bool(trimmed_peaks) and len(trimmed_peaks) == n

    def t_idx(raw_pos: int) -> int:
        return raw_pos - 1 - s

    # 中段峰宽基准：read 中段（1/3..2/3）有效峰宽中位数；过短回退全 read
    lo3, hi3 = n // 3, max(n * 2 // 3, n // 3 + 1)
    mid = [widths[i] for i in range(lo3, min(hi3, n))
           if widths and 0 < widths[i] <= WIDTH_RATIO_MAX * 4]
    if len(mid) < 10:
        mid = [w for w in widths if w > 0]
    base_w = _median(mid) if mid else 0.0

    # 通道对表（主/次峰面积比）：仅在有峰图时构建
    table: List[Optional[tuple]] = _pair_table_for_zone(trace, trimmed_peaks, n) \
        if has_trace else []
    first_raw = s + 1
    last_raw = s + n

    def window_stats(p: int):
        """原始坐标 p 起的窗统计 → (mean_q, cv, width_ratio, sec_ratio)"""
        i0, i1 = t_idx(p), min(n - 1, t_idx(p) + WINDOW - 1)
        qs = trimmed_q[max(0, i0):i1 + 1]
        mq = sum(qs) / len(qs) if qs else 0.0
        cv = _spacing_cv(trimmed_peaks[max(0, i0):i1 + 1]) if trimmed_peaks else None
        wr = None
        if base_w:
            ws = [widths[i] for i in range(max(0, i0), i1 + 1)
                  if i < len(widths) and widths[i] > 0]
            wr = _median(ws) / base_w if ws else None
        sec = None
        if table:
            vals = [max(table[i][1].values())
                    for i in range(max(0, i0), i1 + 1)
                    if i < len(table) and table[i]]
            # 0 是合法观测（校正后干净位点次级占比恰为 0），必须入中位数；
            # 只取非零会把窗内少数混合位点的占比当成全窗水平
            sec = _median(vals) if vals else None
        return mq, cv, wr, sec

    def trusted(p: int) -> bool:
        mq, cv, wr, sec = window_stats(p)
        if mq < TRUST_MEAN_Q:
            return False
        if cv is not None and cv > SPACING_CV_MAX:
            return False
        if wr is not None and wr > WIDTH_RATIO_MAX:
            return False
        # sec = 窗内次级/主峰面积比中位（衰减段抬升，是峰宽之外的第二个
        # 衰减信号）；干净信号 ~0.01-0.08，阈值 0.5
        if sec is not None and sec > SECONDARY_RATIO_MAX:
            return False
        return True

    hard_lo = zp.front_hard + 1
    hard_hi = raw_len if has_trace else min(raw_len, s + zp.tail_hard)
    # 可信窗起点集合（原始坐标）；步长 STEP 扫描
    starts = [p for p in range(max(hard_lo, first_raw), min(hard_hi, last_raw) - WINDOW + 2, STEP)
              if trusted(p)]
    if not starts:
        return {"start": 1, "end": 0, "raw_start": max(hard_lo, first_raw),
                "raw_end": max(hard_lo, first_raw) - 1,
                "front_reason": "无可信信号窗（全 read 未达可信标准）",
                "tail_reason": "", "params": zp.as_dict()}
    zone_lo = max(hard_lo, starts[0])
    # 起点碱基本身要达标（窗口均值掩盖不了起点就是坏碱基；旧首端灰区同款守卫）
    for p in starts:
        i0 = t_idx(p)
        w0 = widths[i0] if i0 < len(widths) else 0.0
        if trimmed_q[i0] >= 20 and (not base_w or w0 <= 0 or w0 / base_w <= WIDTH_RATIO_MAX):
            zone_lo = max(hard_lo, p)
            break
    # 终点：最后一个可信窗的窗末（无峰图时已以 tail_hard 封顶）
    zone_hi = min(hard_hi, starts[-1] + WINDOW - 1)
    zone_hi = min(zone_hi, last_raw)

    front_reason = f"首端 1–{zp.front_hard}bp 为硬不可信区" if zp.front_hard else ""
    if zone_lo > max(hard_lo, first_raw):
        front_reason = (f"首端至 {zone_lo - 1}bp 信号未稳定"
                        "（窗口 Q/峰距/峰宽未达可信标准）")
    tail_reason = ""
    if zone_hi < last_raw:
        mq_n, _cvn, _wrn, _secn = window_stats(zone_hi + 1)
        if mq_n is not None and mq_n < TRUST_MEAN_Q:
            # Q 先行失格（无峰图时是唯一判据；有峰图时同样可能是 Q 衰减）
            why = f"窗口平均 Q{mq_n:.0f}"
            if _wrn is not None:
                why += f"、峰宽为中段 {_wrn:.1f} 倍"
            if _secn is not None:
                why += f"、次级占比中位 {round(_secn * 100)}%"
            tail_reason = f"{zone_hi + 1}bp 起信号未达可信标准（{why}）"
        elif not has_trace:
            tail_reason = (f"{zone_hi + 1}bp 之后无峰图可判"
                           f"（按 tail_hard={zp.tail_hard} 兜底封顶）")
        else:
            _, cv, wr, sec = window_stats(zone_hi + 1)
            why = f"窗口平均 Q{mq_n:.0f}"
            if wr is not None:
                why += f"、峰宽为中段 {wr:.1f} 倍"
            if sec is not None:
                why += f"、次级占比中位 {round(sec * 100)}%"
            tail_reason = f"{zone_hi + 1}bp 起信号未达可信标准（{why}）"
    elif last_raw < raw_len:
        tail_reason = f"质量修剪止于 {last_raw}bp"

    return {
        "start": zone_lo - s,
        "end": zone_hi - s,
        "raw_start": zone_lo,
        "raw_end": zone_hi,
        # 核心区终点（2026-11-10）：tail_gray（默认 700bp，原始坐标）之内。
        # 核心区之外、信号区之内为「尾端扩展区」：计入覆盖（T12 原意：好
        # read 的 700bp 之后不白丢），但单 read 差异不确证、双峰不计入混合
        # 分级——真实 read 在该段 Q45-57、峰宽 1.3-1.4 倍仍出现尾端滑移
        # 插入与成串次峰（work/local_signal.py），信号指标区分不开
        "core_end": max(0, min(zone_hi, zp.tail_gray) - s),
        "raw_core_end": min(zone_hi, zp.tail_gray),
        "front_reason": front_reason,
        "tail_reason": tail_reason,
        "params": zp.as_dict(),
    }


def _pair_table_for_zone(trace, trimmed_peaks, n: int) -> List[Optional[tuple]]:
    """可信区用的主/次峰面积比表（复用 mixed_model 的窗口面积口径，
    不做 main_floor/退化窗口过滤——此处只需要逐位次级占比）。"""
    from core.sanger.abif_reader import peak_window

    n_samples = min(len(v) for v in trace.values()) if trace else 0
    out: List[Optional[tuple]] = []
    for i in range(n):
        if i >= len(trimmed_peaks) or trimmed_peaks[i] is None \
                or not (0 <= trimmed_peaks[i] < n_samples):
            out.append(None)
            continue
        lo, hi = peak_window(trimmed_peaks, i, n_samples)
        areas = {c: sum(trace[c][lo:hi]) for c in "ACGT" if c in trace}
        if len(areas) < 2:
            out.append(None)
            continue
        main_b = max(areas, key=areas.get)
        main_a = areas[main_b]
        if main_a <= 0:
            out.append(None)
            continue
        out.append((main_b, {c: areas[c] / main_a for c in areas if c != main_b},
                    areas))
    return out


# 尾端扩展区单 read 替换的「干净」判据（2026-11-10）：局部峰宽 ≤ 中段 1.2 倍。
# 真实 read 700bp 后峰宽 1.14-1.43 倍且伴随滑移/成串次峰，合成干净峰 1.0
TAIL_CLEAN_WIDTH_MAX = 1.2


def local_width_ratio(read: Dict, rp: int, half: int = 10) -> Optional[float]:
    """修剪后 read 坐标 rp（1-based）±half 内峰宽中位数 / 中段峰宽中位数。
    结果按 read 缓存（read["_width_cache"]）；无 trace 返回 None"""
    trace = read.get("trace")
    peaks = read.get("trimmed_peaks")
    if not trace or not peaks or rp is None:
        return None
    w = read.get("_width_cache")
    if w is None:
        w = peak_widths(trace, [p if p is not None else -1 for p in peaks])
        read["_width_cache"] = w
    n = len(w)
    def _med(a):
        a = sorted(x for x in a if x and x > 0)
        return a[len(a) // 2] if a else 0
    base = _med(w[n // 3: 2 * n // 3])
    i = rp - 1
    if not base or not (0 <= i < n):
        return None
    return round(_med(w[max(0, i - half): i + half + 1]) / base, 2)
