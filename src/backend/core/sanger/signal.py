"""poly 区信号处理（docs/poly-final-actionable-list.md A2 / B1 / B2 / B4 / B5）

A2 基线校正：ab1 的 DATA 通道除真实信号外还叠加随位置缓慢漂移的加性本底
（仪器基线、染料残留），峰面积裸加和会把本底计入峰强。逐通道滑动窗口取
低分位为本底估计、扣除（Giddings 类基线校正的简化实现）。

两条防坑护栏（缺一不可，均经真实 ab1/合成数据验证）：
1. 窗口必须远宽于最宽的合并峰平台——窗口窄时平台内部"本底"就是信号自身，
   会被整段扣平（polyA 恰是本项目的核心对象）；
2. 本底估计额外受「局部信号 P90 的一半」封顶——正常区本底远低于峰高、
   封顶不生效；只有在平台/信号塌陷区（低分位 ≈ 信号自身）才把扣减幅度
   压到一半，保证压缩比分子分母同比例缩放、不产生假性"信号消失"。

B1 property map：对每个已判峰测 (峰高, 半高宽)，峰间滑动中位数后插值回每
采样点，得到 H(x)/W(x) 包络——B2 宽度法与 B5 骤降检测的公共前置。
H(x) 衰减的参数化拟合（Andrade & Manolakos，log 空间共享 α）作为独立工具
提供，用于验证/量化 read 级衰减，不直接参与判定。

B2 estimate_run_length：峰完全合并时峰数法失效（返回 ≈0），宽度法用
「连通峰块在 H(x)/2 处的宽度 − 单峰宽 W」除以峰间距 Δt 反推碱基数。

B4 detect_phase_shift / B5 detect_post_poly_dropout：poly 末端下游的
滑移 echo 序列与信号骤降检测（纯信号函数，管线负责接线与口径）。
"""

import math
from typing import Dict, List, Optional, Tuple

from core.sanger.abif_reader import peak_window

# —— A2 基线校正参数（真实 ab1 标定，见 docs/poly-final-actionable-list.md A2）——
# 窗口窄（≈10 碱基）才能跟上基线漂移（线性漂移下残余本底 ∝ 0.4×win×斜率）；
# 窄窗在合并峰平台内部会把信号当本底——由平台护栏（跨通道共模）插值兜底
BASELINE_WIN = 200    # 滑动窗口（采样点）
BASELINE_PCT = 0.10   # 窗口内低分位（本底候选）
BASELINE_STEP = 50    # 窗口中心步长（采样点），窗口间线性插值回每采样点
BASELINE_CAP_PCT = 0.90  # 全轨迹高分位（信号尺度参考）
BASELINE_CAP = 0.5    # 护栏1：窗口 P10 ≥ 本通道全轨迹 P90×此值 → 平台，本底改由邻窗插值
BASELINE_XTALK = 2.0  # 护栏2（跨通道共模）：本底为四通道共有，某通道 P10 高出其他
                      # 通道中位 P10 的倍数以上 → 该通道此处的"本底"实为信号平台

# —— B4 滑移识别参数（法医 STR 数值先验，作判定带）——
# N−1 ≈ 亲峰 5–15%（压缩区可更高）、N−2 ≈ N−1²；echo 高于亲峰 60% 视为
# 独立信号（疑似真实混合）而非滑移伪影。
SLIP_MIN_RATIO = 0.05
SLIP_MAX_RATIO = 0.60
SLIP_DECAY = 0.9        # 相邻 echo 单调不增（后一个 ≤ 前一个 × 0.9）
SLIP_MIN_ECHOES = 2
SLIP_WINDOW_BASES = 40  # poly 末端后向前搜索的窗口（碱基数）
SLIP_FIRST_WITHIN = 4   # 首 echo 必须紧贴 poly 末端（碱基数）

# —— B5 骤降参数 ——
DROPOUT_FLOOR_RATIO = 0.35  # 滑动峰幅中位数跌破 poly 前水平的 35% 视为骤降
DROPOUT_MIN_SAMPLES = 10    # 持续 ≥10 采样点（排除单点抖动）
DROPOUT_WINDOW_BASES = 40   # poly 末端后向前检测的窗口（碱基数）
DROPOUT_END_MARGIN = 40     # read 末端 N 碱基内的塌陷记 end_truncation 而非骤降


def _percentile(sorted_vals: List[float], pct: float) -> float:
    """线性插值分位数（输入需已升序排序）"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = pct * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    return s[len(s) // 2] if s else 0.0


def _fill_invalid(centers: List[int], floor_vals: List[Optional[float]],
                  fallback: float, n: int) -> List[float]:
    """无效窗口由最近有效窗口插值；全无效时退回 fallback；再插值回每采样点"""
    valid = [i for i, v in enumerate(floor_vals) if v is not None]
    if not valid:
        filled = [fallback] * len(centers)
    else:
        filled = []
        for i, v in enumerate(floor_vals):
            if v is not None:
                filled.append(v)
                continue
            prev_i = max((j for j in valid if j < i), default=None)
            next_i = min((j for j in valid if j > i), default=None)
            if prev_i is None:
                filled.append(floor_vals[next_i])
            elif next_i is None:
                filled.append(floor_vals[prev_i])
            else:
                a, b = floor_vals[prev_i], floor_vals[next_i]
                filled.append(a + (b - a) * (i - prev_i) / (next_i - prev_i))
    out: List[float] = []
    for i, c in enumerate(centers):
        nxt = centers[i + 1] if i + 1 < len(centers) else c
        a = filled[i]
        b = filled[i + 1] if i + 1 < len(filled) else a
        span = max(1, nxt - c)
        for x in range(c, min(nxt + 1, n)):
            out.append(a + (b - a) * (x - c) / span)
    return out[:n]


def baseline_correct(trace: Dict[str, List[int]], win: int = BASELINE_WIN,
                     pct: float = BASELINE_PCT, step: int = BASELINE_STEP,
                     iterations: int = 3) -> Dict[str, List[int]]:
    """逐通道扣除加性本底（A2），扣减后截断于 0

    本底估计（每通道滑动窗口低分位）经两道平台护栏校验，无效窗口由两侧
    有效窗口插值兜底（加性本底不会突变，平台外本底即平台内本底）：
    1. 电平：候选 ≥ 本通道当前轨迹 P90×BASELINE_CAP；
    2. 共模：本底是四通道共有的加性量，信号才是通道特异的——某通道的
       候选高出其他通道中位候选 BASELINE_XTALK 倍以上时，它不是本底而是
       该通道的信号平台（典型：合并 polyA 平台）。

    迭代收敛：漂移信号下低分位天然滞后约半个窗口（第一遍扣除后残余近似
    恒定），重复估计直至本底归零（≤3 遍；平台护栏每遍独立生效）。
    窗口自适应：数据长度不足 win 时退化为全轨迹单窗（合成数据/短 read）。
    """
    if not trace:
        return {}
    n = min(len(v) for v in trace.values())
    if n == 0:
        return {b: list(ch) for b, ch in trace.items()}
    half = max(1, win) // 2
    step = max(1, step)
    centers = list(range(0, n, step))
    if centers[-1] != n - 1:
        centers.append(n - 1)
    bases_out = list(trace.keys())
    current: Dict[str, List[int]] = {b: list(ch[:n]) for b, ch in trace.items()}
    total: Dict[str, List[float]] = {b: [0.0] * n for b in bases_out}
    for _ in range(max(1, iterations)):
        global_refs = {b: _percentile(sorted(current[b]), BASELINE_CAP_PCT)
                       for b in bases_out}
        cand: Dict[str, List[float]] = {}
        for b in bases_out:
            ch = current[b]
            cand[b] = [_percentile(sorted(ch[max(0, c - half): min(n, c + half)]), pct)
                       for c in centers]
        moved = False
        for b in bases_out:
            floor_vals: List[Optional[float]] = []
            for i in range(len(centers)):
                v = cand[b][i]
                others = sorted(cand[o][i] for o in bases_out if o != b)
                med_o = others[len(others) // 2] if others else 0.0
                if v >= BASELINE_CAP * global_refs[b] or v > BASELINE_XTALK * med_o:
                    floor_vals.append(None)
                else:
                    floor_vals.append(v)
            base = _fill_invalid(centers, floor_vals,
                                 _percentile(sorted(current[b]), pct), n)
            if max(base, default=0.0) >= 1:
                moved = True
            total[b] = [t + bs for t, bs in zip(total[b], base)]
            current[b] = [max(0, int(round(v - bs))) for v, bs in zip(current[b], base)]
        if not moved:
            break
    out: Dict[str, List[int]] = {}
    for b, ch in trace.items():
        out[b] = [max(0, int(round(v - bs))) for v, bs in zip(ch[:n], total[b])]
        if len(ch) > n:  # 长度异常的通道原样保留尾部（正常 ab1 不会发生）
            out[b] = out[b] + list(ch[n:])
    return out


def composite_signal(trace: Dict[str, List[int]]) -> List[int]:
    """四通道逐点取最大——"主信号"复合轨迹（与通道映射无关）"""
    if not trace:
        return []
    n = min(len(v) for v in trace.values())
    return [max(trace[b][x] for b in "ACGT" if b in trace) for x in range(n)]


def peak_heights(trace: Dict[str, List[int]], peaks: List[int]) -> List[int]:
    """每个已判峰的复合峰高——峰窗内取 max（A1：抗 PLOC ±1-2 采样点抖动）"""
    n = min(len(v) for v in trace.values()) if trace else 0
    out: List[int] = []
    for i, pk in enumerate(peaks):
        if pk is None or not (0 <= pk < n):
            out.append(0)
            continue
        lo, hi = peak_window(peaks, i, n)
        out.append(max((max(trace[b][lo:hi]) for b in "ACGT"
                        if b in trace and lo < len(trace[b])), default=0))
    return out


def peak_widths(trace: Dict[str, List[int]], peaks: List[int],
                heights: Optional[List[int]] = None) -> List[float]:
    """每个已判峰在半高处的宽度（采样点）

    PLOC 与真实峰顶常差 1-2 采样点，先在峰窗内定位 apex、以窗内最大值
    （= peak_heights，A1 口径）的一半为基准向两侧走；退化峰（高 0）宽 0。
    """
    comp = composite_signal(trace)
    n = len(comp)
    if heights is None:
        heights = peak_heights(trace, peaks)
    out: List[float] = []
    for i, pk in enumerate(peaks):
        h = heights[i] if i < len(heights) else 0
        if pk is None or not (0 <= pk < n) or h <= 0:
            out.append(0.0)
            continue
        lo_w, hi_w = peak_window(peaks, i, n)
        apex = max(range(max(0, lo_w), min(n, hi_w)), key=lambda x: comp[x])
        half = h / 2
        lo = apex
        while lo > 0 and comp[lo - 1] >= half:
            lo -= 1
        hi = apex
        while hi < n - 1 and comp[hi + 1] >= half:
            hi += 1
        out.append(float(hi - lo + 1))
    return out


def sliding_median(values: List[float], window: int = 9) -> List[float]:
    """峰序列上的居中滑动中位数（窗口在两端收缩）"""
    if window % 2 == 0:
        window += 1
    half = window // 2
    out: List[float] = []
    for i in range(len(values)):
        seg = sorted(values[max(0, i - half): i + half + 1])
        out.append(seg[len(seg) // 2] if seg else 0.0)
    return out


def _interp_series(centers: List[int], values: List[float], n: int) -> List[float]:
    """峰值位置 → 每采样点的分段线性插值（两端钳位到最近值）"""
    if not centers:
        return [0.0] * n
    out: List[float] = []
    j = 0
    for x in range(n):
        if x <= centers[0]:
            out.append(values[0])
            continue
        if x >= centers[-1]:
            out.append(values[-1])
            continue
        while j + 1 < len(centers) - 1 and centers[j + 1] <= x:
            j += 1
        c0, c1 = centers[j], centers[j + 1]
        v0, v1 = values[j], values[j + 1]
        out.append(v0 + (v1 - v0) * (x - c0) / max(1, c1 - c0))
    return out


def property_maps(trace: Dict[str, List[int]],
                  peaks: List[int]) -> Optional[Dict[str, List[float]]]:
    """B1：H(x)/W(x) property map（每采样点的峰高/峰宽局部包络）

    对每个有信号的已判峰测 (height, width)，峰间滑动中位数平滑后插值回
    每采样点；peaks_h/peaks_w 保留未平滑的每峰原值（B2 的 W 参考取其
    低分位，避免合并平台把"单峰宽"拉大）。有效峰 < 2 个返回 None。
    """
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or not peaks:
        return None
    heights = peak_heights(trace, peaks)
    widths = peak_widths(trace, peaks, heights)
    # 只统计真实信号峰：本底级峰（信号塌陷区/纯本底）的"半高宽"没有意义，
    # 且会把峰宽统计拉到整条轨迹
    h_max = max(heights) if heights else 0
    centers: List[int] = []
    hvals: List[float] = []
    wvals: List[float] = []
    for i, pk in enumerate(peaks):
        if heights[i] >= max(1, 0.25 * h_max) and pk is not None and 0 <= pk < n:
            centers.append(pk)
            hvals.append(float(heights[i]))
            wvals.append(widths[i])
    if len(centers) < 2:
        return None
    h_smooth = sliding_median(hvals, 9)
    w_smooth = sliding_median(wvals, 9)
    return {
        "H": _interp_series(centers, h_smooth, n),
        "W": _interp_series(centers, w_smooth, n),
        "peaks_h": hvals,
        "peaks_w": wvals,
        "centers": centers,
    }


def fit_decay(trace: Dict[str, List[int]], peaks: List[int],
              skip_start: int = 10, skip_end: int = 40) -> Optional[Dict]:
    """H(x) 衰减的参数化拟合（Andrade & Manolakos）：log 空间 y = β_c − α·t

    四通道共享衰减率 α、各自截距 β_c；每通道只取该通道为主导的峰位，
    剔除前 skip_start 碱基的信号爬升区与末尾 skip_end 碱基的下降区；
    普通最小二乘（合并斜率）+ 一轮大残差剔除（>2.5×MAD）。log 化依据：
    峰高不确定度 ∝ 峰高，log 后近似同方差。
    返回 {"alpha"(每采样点), "beta", "n"}；可用峰太少返回 None。
    """
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or not peaks or len(peaks) < skip_start + 5:
        return None
    rows: List[Tuple[str, int, float]] = []  # (主通道碱基, 采样位置, ln 峰高)
    for i, pk in enumerate(peaks):
        if i < skip_start or pk is None or not (0 <= pk < n):
            continue
        lo, hi = peak_window(peaks, i, n)
        areas = {b: sum(trace[b][lo:hi]) for b in "ACGT" if b in trace}
        if not areas:
            continue
        best = max(areas, key=lambda b: areas[b])
        h = max((trace[best][x] for x in range(max(0, lo), min(n, hi))), default=0)
        if h > 0:
            rows.append((best, pk, math.log(h)))
    if skip_end and len(peaks) > skip_end:
        t_cut = peaks[-skip_end]
        rows = [r for r in rows if r[1] < t_cut]
    if len(rows) < 5:
        return None

    def _fit(data: List[Tuple[str, int, float]]):
        tbar = sum(t for _, t, _ in data) / len(data)
        ysum: Dict[str, List[float]] = {}
        for b, _, y in data:
            ysum.setdefault(b, []).append(y)
        ybar = {b: sum(v) / len(v) for b, v in ysum.items()}
        num = sum((t - tbar) * (y - ybar[b]) for b, t, y in data)
        den = sum((t - tbar) ** 2 for _, t, _ in data)
        if den <= 0:
            return None, ybar
        alpha = -num / den  # y = β − α·t → 回归斜率为 −α
        beta = {b: ybar[b] + alpha * tbar for b in ybar}
        return alpha, beta

    alpha, beta = _fit(rows)
    if alpha is None:
        return None
    resid = sorted(abs(y - (beta[b] - alpha * t)) for b, t, y in rows)
    mad = resid[len(resid) // 2]
    if mad > 0:
        kept = [r for r in rows
                if abs(r[2] - (beta[r[0]] - alpha * r[1])) <= 2.5 * mad]
        if len(kept) >= 5:
            alpha2, beta2 = _fit(kept)
            if alpha2 is not None:
                alpha, beta = alpha2, beta2
    return {"alpha": alpha, "beta": beta, "n": len(rows)}


# ==================== B2：宽度法估计 poly 重复数 ====================

def estimate_run_length(trace: Dict[str, List[int]], peaks: List[int],
                        i0: int, i1: int,
                        maps: Optional[Dict[str, List[float]]] = None) -> Optional[Dict]:
    """估计 poly run 的重复碱基数——峰数法失效（完全合并）时的宽度法救援

    i0/i1：run 在 read 上的首末碱基下标（0-based 含端）。步骤：
    1) run 窗口内数局部极大（≥ H/2，最小间距 Δt/2）→ n_peaks；
    2) H(x)/2 之上连通峰块（窄缺口 < Δt/2 视为抖动并入），块宽 Width →
       N = 1 + (Width − W)/Δt 逐块累加；辅以 skyline（块总宽/Δt）按文献
       口径取整：(N−floor) < 0.5 且 (skyline−floor) < 0.6 → floor，否则 ceil；
    3) 块内二阶导强极小个数 > N → 取该个数（Rule 17：合并块内的次级结构）。
    返回 {"n", "method": peaks|width|second_derivative, "ci_low", "ci_high"}。
    """
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or not peaks:
        return None
    i0 = max(0, min(int(i0), len(peaks) - 1))
    i1 = max(0, min(int(i1), len(peaks) - 1))
    if i1 < i0:
        i0, i1 = i1, i0
    s0, s1 = peaks[i0], peaks[i1]
    if s0 is None or s1 is None:
        return None
    # Δt：run 内峰间距；run 只有 1 个调用峰时退化为全 read 中位间距
    if i1 > i0 and s1 > s0:
        dt = (s1 - s0) / (i1 - i0)
    else:
        gaps = sorted(b - a for a, b in zip(peaks, peaks[1:])
                      if a is not None and b is not None and b > a)
        if not gaps:
            return None
        dt = float(gaps[len(gaps) // 2])
    if dt < 1:
        return None
    if maps is None:
        maps = property_maps(trace, peaks)
    if maps is None:
        return None
    comp = composite_signal(trace)
    # pad 只覆盖峰顶抖动（≈Δt/4）：再大就会把相邻碱基的峰整块漏进窗口
    pad = max(1, int(math.ceil(dt / 4)))
    lo, hi = max(0, s0 - pad), min(n, s1 + pad + 1)
    seg = comp[lo:hi]
    if not seg or max(seg) <= 0:
        return None
    h_run = _median([maps["H"][x] for x in range(lo, hi)])
    if h_run <= 0:
        h_run = float(max(seg))

    # W 参考：全 read 单峰宽的低分位（合并平台不参与——peaks_w 是每峰原值，
    # 平台峰的宽只在少数峰上，25 分位仍由正常峰决定）；钳到 [1, 0.9Δt]
    w_ref = _percentile(sorted(maps["peaks_w"]), 0.25) if maps["peaks_w"] else dt / 2
    w_ref = max(1.0, min(w_ref, dt * 0.9))

    # 步骤 1：局部极大
    thr_peaks = h_run / 2
    min_dist = max(1, int(dt / 2))
    n_peaks = 0
    last = -(10 ** 9)
    for k in range(1, len(seg) - 1):
        y = seg[k]
        if y < thr_peaks or y < seg[k - 1] or y <= seg[k + 1]:
            continue
        if k - last >= min_dist:
            n_peaks += 1
            last = k

    # 步骤 2：H(x)/2 之上连通块（前瞻整个缺口，缺口 < Δt/2 视为抖动并入）
    half_h = [maps["H"][x] / 2 for x in range(lo, hi)]
    blocks: List[int] = []
    k, L = 0, len(seg)
    while k < L:
        if seg[k] > 0 and seg[k] >= half_h[k]:
            j = k
            while j + 1 < L:
                if seg[j + 1] > 0 and seg[j + 1] >= half_h[j + 1]:
                    j += 1
                    continue
                g = j + 1
                while g < L and not (seg[g] > 0 and seg[g] >= half_h[g]):
                    g += 1
                if g < L and (g - j - 1) < dt / 2:
                    j = g  # 窄缺口并入
                else:
                    break
            blocks.append(j - k + 1)
            k = j + 1
        else:
            k += 1
    if not blocks:
        return None
    total_f = sum(max(1.0, 1 + (w - w_ref) / dt) for w in blocks)
    skyline_f = sum(blocks) / dt
    frac_t = total_f - math.floor(total_f)
    frac_s = skyline_f - math.floor(skyline_f)
    n_width = math.floor(total_f) if (frac_t < 0.5 and frac_s < 0.6) else math.ceil(total_f)
    merged = any(w > w_ref + dt / 2 for w in blocks)

    # 步骤 3：块内二阶导强极小（合并块内的次级峰结构，Rule 17）
    sd_count = 0
    smooth = list(seg)
    for k in range(1, L - 1):
        smooth[k] = (seg[k - 1] + seg[k] + seg[k + 1]) / 3
    for k in range(1, L - 1):
        d2 = smooth[k - 1] - 2 * smooth[k] + smooth[k + 1]
        if d2 < -0.15 * h_run and smooth[k] >= h_run / 3 \
                and d2 < smooth[k - 1] and d2 <= smooth[k + 1]:
            sd_count += 1

    if merged:
        if sd_count > n_width:
            return {"n": sd_count, "method": "second_derivative",
                    "ci_low": max(1, math.floor(n_width) - 1),
                    "ci_high": math.ceil(n_width) + 1,
                    "n_peaks": n_peaks}
        return {"n": n_width, "method": "width",
                "ci_low": max(1, math.floor(total_f) - 1),
                "ci_high": math.ceil(total_f) + 1,
                "n_peaks": n_peaks}
    n_est = n_peaks if n_peaks >= 1 else max(1, n_width)
    return {"n": n_est, "method": "peaks",
            "ci_low": max(1, min(n_est, n_width) - 1),
            "ci_high": max(n_est, n_width) + 1,
            "n_peaks": n_peaks}


# ==================== B4：poly 下游滑移（stutter）识别 ====================

def detect_phase_shift(trace: Dict[str, List[int]], peaks: List[int],
                       run_i0: int, run_i1: int, run_base: str,
                       window: int = SLIP_WINDOW_BASES) -> List[int]:
    """检测 poly 结束后 window 内 n−1/n−2 几何衰减次峰序列（B4 滑移伪影）

    run_i0/i1：run 在 read 上的首末碱基下标（0-based 含端）。在 run_base
    通道上检查 run 末端之后的碱基位：峰高落在 [5%, 60%]×H(run) 且逐个
    单调衰减、≥2 个 → 判滑移 echo 序列，返回 echo 的 read 位置（1-based）。
    主峰延续（≥60%，真实同碱基延续或下一个正常峰）即中断序列。
    """
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or not peaks or run_base not in trace:
        return []
    i0 = max(0, int(run_i0))
    i1 = min(int(run_i1), len(peaks) - 1)
    if i1 < i0:
        return []
    heights = peak_heights(trace, peaks)
    run_h = [h for h in heights[i0:i1 + 1] if h > 0]
    if not run_h:
        return []
    h_run = _median(run_h)
    if h_run <= 0:
        return []
    ch = trace[run_base]
    echoes: List[int] = []
    prev: Optional[float] = None
    for offset, i in enumerate(range(i1 + 1, min(len(peaks), i1 + 1 + window))):
        if offset >= SLIP_FIRST_WITHIN and not echoes:
            break  # 首 echo 必须紧贴 run 末端
        lo, hi = peak_window(peaks, i, n)
        h = max(ch[lo:hi]) if lo < len(ch) else 0
        if h >= SLIP_MAX_RATIO * h_run:
            break  # 主峰延续：不是 echo 序列
        if h < SLIP_MIN_RATIO * h_run:
            continue  # 本底级信号：跳过但不中断序列
        if prev is not None and h > prev * SLIP_DECAY:
            break  # 增高：非几何衰减序列
        echoes.append(i + 1)  # 1-based read 位置
        prev = h
    if len(echoes) < SLIP_MIN_ECHOES:
        return []
    return echoes


# ==================== B5：poly 后信号骤降 ====================

def _called_base(trace: Dict[str, List[int]], peaks: List[int], i: int) -> Optional[str]:
    """第 i 个峰位的主通道碱基（峰窗内面积最大的通道）"""
    n = min(len(v) for v in trace.values()) if trace else 0
    if i < 0 or i >= len(peaks) or peaks[i] is None or not (0 <= peaks[i] < n):
        return None
    lo, hi = peak_window(peaks, i, n)
    areas = {b: sum(trace[b][lo:hi]) for b in "ACGT" if b in trace}
    if not areas:
        return None
    return max(areas, key=lambda b: areas[b])


def detect_post_poly_dropout(trace: Dict[str, List[int]], peaks: List[int],
                             run_i0: int, run_i1: int,
                             bases: Optional[str] = None,
                             window: int = DROPOUT_WINDOW_BASES,
                             end_margin: int = DROPOUT_END_MARGIN) -> Optional[Dict]:
    """检测 poly 结束后的信号骤降（B5）：{pos, ratio, recovered, end_truncation}

    poly 前水平取 run 峰幅中位数；run 末端后 window 内逐碱基取峰幅
    （峰窗内最大值的 3 峰滑动中位数，抗单点抖动），跌破 35% 且持续
    ≥10 采样点即命中。read 末端 end_margin 碱基内（或一路塌到 read 末尾
    不回升）的塌陷记 end_truncation——read 末端信号下降属正常现象；此时
    若塌陷前最后强峰是孤立 A（V27：Taq 在 PCR 末端加非模板 A 后信号骤降
    的典型形态），不报。
    bases：与 peaks 一一对应的调用碱基（read 坐标，供 V27 判定用）；
    缺省时从 trace 通道主峰推断。
    """
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or not peaks:
        return None
    i0 = max(0, int(run_i0))
    i1 = min(int(run_i1), len(peaks) - 1)
    if i1 < i0:
        return None
    heights = peak_heights(trace, peaks)
    run_h = [h for h in heights[i0:i1 + 1] if h > 0]
    if not run_h:
        return None
    h_run = _median(run_h)
    if h_run <= 0:
        return None
    gaps = sorted(b - a for a, b in zip(peaks, peaks[1:])
                  if a is not None and b is not None and b > a)
    dt = float(gaps[len(gaps) // 2]) if gaps else 4.0
    need = max(1, int(math.ceil(DROPOUT_MIN_SAMPLES / max(dt, 1.0))))

    # 逐碱基峰幅（峰窗内最大值）+ 3 峰滑动中位数平滑：峰顶只占峰窗约一半
    # 样本，取"峰窗中位数"会恒等于本底、把正常区全判成骤降
    def _amp(i: int) -> float:
        seg = [heights[j] for j in range(max(0, i - 1), min(len(heights), i + 2))]
        return _median(seg)

    meds: List[Tuple[int, float]] = [(i, _amp(i))
                                     for i in range(i1 + 1, min(len(peaks), i1 + 1 + window))]

    floor_i: Optional[int] = None
    run = 0
    for idx, (i, med) in enumerate(meds):
        if med < DROPOUT_FLOOR_RATIO * h_run:
            run += 1
            if run >= need:
                floor_i = meds[idx - run + 1][0]
                break
        else:
            run = 0
    if floor_i is None:
        return None

    drop = [med for i, med in meds if floor_i <= i and med < DROPOUT_FLOOR_RATIO * h_run]
    ratio = (min(drop) / h_run) if drop else None
    recovered = any(med >= 0.7 * h_run for i, med in meds if i > floor_i)
    # 仅 read 末端附近的塌陷记 end_truncation（末端信号下降属正常现象）；
    # read 中段的骤降即便不回升也是真骤降，不归类为末端截断
    end_truncation = len(peaks) - floor_i <= end_margin
    result = {"pos": floor_i,
              "ratio": round(ratio, 2) if ratio is not None else None,
              "recovered": recovered,
              "end_truncation": end_truncation}
    if not end_truncation:
        return result
    # V27 护栏：塌陷前最后强峰是孤立 A + 近末端 → Taq 加尾形态，不报
    def _base_at(k: int) -> Optional[str]:
        if bases is not None and 0 <= k < len(bases):
            return bases[k].upper()
        return _called_base(trace, peaks, k)

    last_strong = None
    for k in range(min(floor_i, len(heights) - 1), max(-1, floor_i - 7), -1):
        if heights[k] >= 0.6 * h_run:
            last_strong = k
            break
    if last_strong is not None and _base_at(last_strong) == "A":
        before = [_base_at(j) for j in range(max(0, last_strong - 3), last_strong)]
        if before and all(b != "A" for b in before):
            return None
    return result


def continuous_read_length(quality: List[int], threshold: int = 20) -> int:
    """CRL（Genewiz 口径）：QV>20 的最长连续段（碱基数）"""
    best = cur = 0
    for q in quality:
        if q > threshold:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best
