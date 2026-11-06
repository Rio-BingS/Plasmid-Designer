"""局部噪声本底与变体信噪比（goal T13；A 1.2）

旧 `_peak_snr` 的噪声本底取「变体通道全轨迹中位数」：Sanger 通道大面积
静默（真实文件 T 通道过半采样点为 0，如 3730.ab1）时中位恰为 0，SNR
只能返回 None；而置信度分级把 None 当「响亮」（loud = snr is None or
snr >= 10），本底噪声淹没的弱峰被当成强证据（A 1.2 的病根）。

新口径：

- 噪声本底从**局部**估计：变体位点 ±10 峰内，逐峰取**非调用通道**的
  峰窗面积（调用通道有真信号，不算噪声；位点自身也排除——替换通道
  面积是待测信号），noise = max(1, 1.4826 × MAD)。
  max(1, ·) 保证分母非零：全零通道也给出确定的 SNR（goal 期望
  3730.ab1 的 T 通道位点 SNR 非 None）。
- SNR = 变体通道峰窗面积 / 噪声本底（面积对面积，同量纲）；真实替换
  峰面积远高于邻通道串扰 → SNR 高，伪影峰接近本底 → SNR 落在个位数。
- snr is None 视为**不确定**：不再当响亮（pipeline 的高置信门槛要求
  snr 有值且 ≥10），reason_codes 记 `snr_unavailable`。
"""

from typing import Dict, List, Optional

from core.sanger.abif_reader import peak_window

MAD_SCALE = 1.4826    # MAD → σ 的正态一致性常数
NOISE_FLOOR = 1.0     # max(1, ·)：分母非零兜底
HALF_WINDOW = 10      # 噪声采样窗：位点 ±10 峰


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def local_noise(trace: Dict[str, List[int]], peak_indices: List[Optional[int]],
                i: int, called_base: str,
                half_window: int = HALF_WINDOW) -> Optional[float]:
    """位点 i（0 基）的局部噪声本底。

    ±half_window 峰内（不含位点自身）逐峰取非调用通道的峰窗面积作为噪声
    样本，noise = max(1, 1.4826 × MAD(样本))；MAD 对个别混合位点/串扰
    峰稳健。无有效样本（峰缺失/无通道）→ None。"""
    n_samples = min(len(v) for v in trace.values()) if trace else 0
    if n_samples == 0:
        return None
    lo = max(0, i - half_window)
    hi = min(len(peak_indices), i + half_window + 1)
    samples: List[float] = []
    for j in range(lo, hi):
        if j == i:
            continue
        pk = peak_indices[j]
        if pk is None or not (0 <= pk < n_samples):
            continue
        called_j = called_base[j] if 0 <= j < len(called_base) else ""
        l2, h2 = peak_window(peak_indices, j, n_samples)
        for ch, vals in trace.items():
            if ch == called_j:
                continue
            samples.append(sum(vals[l2:h2]))
    if not samples:
        return None
    med = _median(samples)
    mad = _median([abs(x - med) for x in samples])
    return max(NOISE_FLOOR, MAD_SCALE * mad)


def peak_snr(trace: Dict[str, List[int]], peak_indices: List[Optional[int]],
             read_pos: int, alt_base: str, called_bases: str = "",
             half_window: int = HALF_WINDOW) -> Optional[float]:
    """变体通道峰窗面积 / 局部噪声本底（read_pos 1-based）。

    本底 max(1, ·) 保证分母非零——全零通道（旧口径中位=0 → None → 被当
    响亮）也给出确定的 SNR。read_pos 越界/峰缺失/本底不可估 → None
    （调用方按「不确定」处理，不当响亮）。"""
    if alt_base not in (trace or {}) or trace[alt_base] is None:
        return None
    if read_pos is None or read_pos < 1 or read_pos > len(peak_indices):
        return None
    pk = peak_indices[read_pos - 1]
    n_samples = min(len(v) for v in trace.values())
    if pk is None or not (0 <= pk < n_samples):
        return None
    lo, hi = peak_window(peak_indices, read_pos - 1, n_samples)
    signal = sum(trace[alt_base][lo:hi])
    # 传整个调用碱基串：local_noise 逐峰按该峰的调用通道排除
    noise = local_noise(trace, peak_indices, read_pos - 1, called_bases, half_window)
    if noise is None:
        return None
    return round(signal / noise, 1)
