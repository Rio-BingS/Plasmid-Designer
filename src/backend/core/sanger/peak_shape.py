"""峰形退化区段检测（goal v3.1 X1；RB P0-1）

真实 read 尾端常见「峰展宽、分辨率下降」而 Q 值未必掉——相邻峰在半高处
相互叠加，产生看似「次峰」的信号，被 W4 的 af 分带直接判成混合（误报，
且给出「重新挑单克隆」的错误动作）。本模块给出 read 级的「峰形退化区段」，
由 pipeline 把区段内的双峰剔除出 mixed 判定、并把区段计入可信区外。

判定口径（goal §3.1）：
- 逐碱基局部峰宽 = 主通道半高宽（`signal.peak_widths`），与**相邻峰间距**
  相比（`width / spacing`）——这是**分辨率**的倒数：FWHM 达到峰间距即
  相邻两峰在半高处无法分开。峰间距是仪器固有量（实测 10–14 采样点），
  与样品无关，因此该比值跨数据集可比；
- 退化条件：`width / spacing > 1.0`（默认 `resolution` 阈值 1.0，留 0.85
  实测下限作为参数可调范围）；
- 连续退化 ≥ `min_bp`（默认 15bp）才算区段（单个宽峰是噪声，不足以改判）；
- 区段以 read 坐标（修剪后 1-based）给出，两端各外扩 1bp（相邻峰窗必然重叠，
  退化段前一碱基的次峰同样来自宽峰）；
- **不平滑**（`smooth_window=1`）：窗口平滑会把孤立宽峰抹成连续段，在真实
  数据上虚构退化区（见下方阈值来源）。

阈值来源（`params/peak_shape.json`，`_doc` 有同样说明）：

| 数据集 | `width/spacing` 中位 | 判据 1.0 的结果 |
|---|---|---|
| 合成 `tail_width` ×2.0 | 2.00（×2 档） | 退化段全覆盖（read 400–末尾） |
| 合成 ×2.5 / ×3.0 | 4.83 / 5.00 | 同上 |
| 合成 SC10（×3.0） | — | [1049,1080]（read 尾 32bp） |
| 真实 MX 三条 read | 0.58 / 0.62 / 0.62 | **零退化段**（p90 仅 1.54–1.67，孤立单点） |

前两版判据（`width / 全read中位数 > 1.6`、9 点滑窗平滑、10bp 外扩）在真实
MX 上产生 3 个虚构退化段并误杀 7–10 个真实双峰位点（把 MX 的 35/15 降到
32/13）；本判据在 MX 上零退化段，而合成退化场景全部检出。分布数据见
PROGRESS_v3.1「X1 峰形阈值来源与分布」。
"""

from typing import Dict, List, Optional

from core.sanger.signal import peak_widths, sliding_median

# 默认阈值（params/peak_shape.json 缺失时使用；两者必须一致，测试锁定）
# FWHM / 相邻峰间距 的阈值（分辨率倒数；见模块 docstring 的实测分布）
DEFAULT_RESOLUTION = 1.0
# 兼容旧名（width / 全read中位宽）：该口径在真实数据上有虚构退化段的风险，
# 仅保留作参数可选项，默认不走
DEFAULT_RATIO = 1.6
DEFAULT_MIN_BP = 15
# 只外扩 1bp：相邻碱基峰窗必然重叠（峰间距 10–14 采样点 vs 半高宽 7），退化段
# 前一碱基的次峰同样来自宽峰、同样不可分辨；而扩 10bp 会把峰宽**正常**的位点
# 一并剔除（MX 实测误杀 6 个 width/base 仅 1.14–1.43 的）
DEFAULT_PAD = 1
# 不做平滑：9 点滑窗中位数把孤立宽峰抹成连续段——MX 原始标记零个 ≥15bp
# 连续段，平滑后造出 [921,937] 等 3 段；而 min_run_bp 已足够滤单点噪声
DEFAULT_SMOOTH = 1

_PARAMS_PATH = None


def _params_path():
    global _PARAMS_PATH
    if _PARAMS_PATH is None:
        from pathlib import Path
        _PARAMS_PATH = Path(__file__).parent / "params" / "peak_shape.json"
    return _PARAMS_PATH


def load_params() -> Dict:
    """读 params/peak_shape.json（缺文件/坏文件回退默认值）"""
    import json
    try:
        with open(_params_path(), encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:  # noqa: BLE001 参数表缺失不阻断分析
        raw = {}
    return {
        "resolution": float(raw.get("resolution", DEFAULT_RESOLUTION)),
        "width_ratio": float(raw.get("width_ratio", DEFAULT_RATIO)),
        "min_run_bp": int(raw.get("min_run_bp", DEFAULT_MIN_BP)),
        "pad_bp": int(raw.get("pad_bp", DEFAULT_PAD)),
        "smooth_window": int(raw.get("smooth_window", DEFAULT_SMOOTH)),
    }


def _median(vals: List[float]) -> float:
    s = sorted(v for v in vals if v > 0)
    if not s:
        return 0.0
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


# 峰间距下限（采样点）：低于此值意味着 trace 不是按真实采样密度生成的
# （make_ab1 默认每碱基 1 个采样点，峰间距=1，"FWHM/间距"退化成恒 ≥1 的
# 无意义量）。真实 ab1 每碱基 10–25 采样点，峰间距远大于 4。
MIN_SPACING = 4.0


def _spacings(peaks: List[int], n: int) -> List[float]:
    """每位点与下一个峰心的间距（采样点）；末位点沿用前一个间距"""
    out: List[float] = []
    for i in range(len(peaks)):
        if i + 1 < len(peaks) and peaks[i] and peaks[i + 1]:
            out.append(float(peaks[i + 1] - peaks[i]))
        else:
            out.append(out[-1] if out else 12.0)
    return out


def _flags_density(trace, peaks, widths, p) -> Optional[List[int]]:
    """逐位点的退化标记（1=退化）。

    主判据 **width / spacing > resolution**（分辨率倒数，默认 1.0）：FWHM
    达到相邻峰间距即两峰在半高处无法分开，这是「测序未达」的物理定义，
    与样品/信号强度无关（峰间距是仪器固有量）。

    `width_ratio` 非空且 `resolution` 未显式给出时退回旧口径（width / 全 read
    中位宽 > ratio）——仅为参数兼容，实测在真实数据上有虚构退化段风险。
    """
    if len(widths) != len(peaks):
        return None
    spac = _spacings(peaks, len(widths))
    res = p.get("resolution")
    win = int(p.get("smooth_window", DEFAULT_SMOOTH))
    if res is not None:
        if not spac or _median(spac) < MIN_SPACING:
            # 采样密度不足（合成 trace 每碱基 1 采样点）：分辨率判据无意义，
            # 退回旧口径而非误判整条 read 退化
            res = None
    if res is not None:
        vals = [(widths[i] / spac[i]) if (spac[i] > 0 and widths[i] > 0) else 0.0
                for i in range(len(widths))]
        if win > 1:
            vals = sliding_median(vals, win)
        return [1 if v > res else 0 for v in vals]
    # —— 旧口径（参数兼容）——
    base = _median(widths)
    if base <= 0:
        return None
    ratio = p["width_ratio"]
    vals = [w if w > 0 else base for w in widths]
    if win > 1:
        vals = sliding_median(vals, win)
    return [1 if v > base * ratio else 0 for v in vals]


def degraded_runs(trace: Dict[str, List[int]], peaks: List[int],
                  params: Optional[Dict] = None,
                  exclude: Optional[set] = None) -> List[List[int]]:
    """峰形退化区段（read 坐标，1-based 闭区间；无峰图返回 []）

    trace/peaks 为**修剪后**的峰图与峰位（与 trimmed_bases 同索引）。

    返回**未扩张**的检测段：用于「实测覆盖到哪里」（只剔除真正检出退化的
    碱基）；mixed 剔除用 expand_runs 再向外扩 pad_bp。

    exclude（read 坐标 1-based）给出已知的**非**尾端展宽区段——poly 压缩
    高台（poly_merged_zones）与短滑移 echo 处峰宽同样会变大，但那是同聚物
    结构本身的形态，已有专门处理，不属「测序未达」。
    """
    p = params or load_params()
    min_bp = p["min_run_bp"]
    if not trace or not peaks:
        return []
    try:
        widths = peak_widths(trace, [pk if pk is not None else -1 for pk in peaks])
    except Exception:  # noqa: BLE001 峰型缺失不阻断
        return []
    if len(widths) != len(peaks):
        return []
    thresholds = _flags_density(trace, peaks, widths, p)
    if thresholds is None:
        return []
    ex = exclude or set()
    flags = [1 if (f and (i + 1) not in ex) else 0
             for i, f in enumerate(thresholds)]
    runs: List[List[int]] = []
    i, n = 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flags[j + 1]:
            j += 1
        if j - i + 1 >= min_bp:
            runs.append([i + 1, j + 1])
        i = j + 1
    return runs


def expand_runs(runs, read_len, params=None):
    """把退化区段两端各扩 pad_bp（mixed 剔除用；覆盖裁剪不用）

    实测取 0（不扩张）：goal §3.1 只要求「区段内的双峰」不判混，未要求
    外扩；而外扩会把峰宽正常（< ratio 阈值）的位点一并剔除——MX 真实数据
    上 10bp pad 误杀 6 个 width/base 仅 1.14–1.43 的位点。函数保留扩张
    能力（params 可调），默认关闭。
    """
    pad = (params or load_params())["pad_bp"]
    out = []
    for s_, e_ in runs:
        lo, hi = max(1, s_ - pad), min(read_len, e_ + pad)
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def read_degraded(trace, peaks, params: Optional[Dict] = None,
                  exclude: Optional[set] = None) -> List[List[int]]:
    """对单条 read 计算退化区段（pipeline 调用入口，带异常兜底）"""
    try:
        return degraded_runs(trace, peaks, params, exclude)
    except Exception:  # noqa: BLE001 峰形计算失败不阻断（退回无退化区段）
        return []


def in_degraded(pos: int, runs: List[List[int]]) -> bool:
    """read 坐标（1-based）是否落在任一退化区段内"""
    for s, e in runs or []:
        if s <= pos <= e:
            return True
    return False


def degraded_text(first_pos: int) -> str:
    """退化区段的证据行话术（goal §3.1：evidence 给 read 一句话）"""
    return f"自 {first_pos} 起峰形退化，已按测序未达处理"
