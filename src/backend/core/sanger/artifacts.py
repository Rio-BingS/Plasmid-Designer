"""通用伪迹检测（goal T20；A 3.5）

既有信号层检测都是 poly 锚定的（signal.detect_phase_shift 滑移 echo、
poly_merged_zones 合并压缩、post_poly_dropout 骤降）——poly 区之外仍有
三类伪迹没有归属：

- **通用压缩**：峰距 < 0.6 × 局部中位（±7 峰滑窗中位）连续 ≥2——碱基
  呼叫间距塌陷，逐位双峰/峰证据在此区间不可信；
- **dye blob**：read 前 150bp 四通道同时宽峰（各通道半高宽 ≥6 采样点、
  最弱通道 ≥ 最强 40%）且无主通道（最强占比 ≤0.45，真实峰单通道主导
  通常 >0.6）——电泳杂质残留，该区间调用与峰证据一并不可信；
- **短同聚物（3-7bp）滑移**：poly_runs 只收 ≥8bp run，3-7bp run 末端
  同样会有几何衰减 echo（复用 detect_phase_shift，run 锚定）。

只作为 T15 特征与 verdict info，不新增 if 分支直接改结论（goal 原文）：
变体落在伪迹区时 evidence_by_read 项带 artifact 标记（评分特征，权重
待 T23 基准集拟合），read 级伪迹区段进 info——判定状态只由既有链路决定。

坐标约定：peaks 为逐碱基峰位（peaks[i] = 第 i+1 个碱基的采样点，可含
None），区段一律以 read 碱基坐标（1-based 闭区间）下发，与 evidence 的
read_pos 同一坐标系；echo positions 沿用 detect_phase_shift 的 1-based。
"""

from typing import Dict, List, Optional

from core.sanger.signal import (
    detect_phase_shift, peak_window, sliding_median,
)

COMPRESSION_RATIO = 0.6    # 峰距 < 0.6 × 局部中位
COMPRESSION_MIN_RUN = 2    # 连续压缩间距数
COMPRESSION_WIN = 15       # 局部中位滑窗（峰距数，奇数化）
DYE_BLOB_BP = 150          # 只看 read 前 150 碱基
BLOB_WIDTH_MIN = 6.0       # 半高宽 ≥6 采样点（正常峰 2-5）
BLOB_DOMINANCE_MAX = 0.45  # 主通道占比上限（真实峰 >0.6）
BLOB_CHAN_BALANCE = 0.4    # 最弱通道 ≥ 最强通道的比例下限
BLOB_MIN_PEAKS = 2         # 连续 blob 峰数下限
SHORT_RUN_BP = (3, 7)      # 短同聚物长度范围（≥8 归 poly 链路）
SHORT_SLIP_MIN_RATIO = 0.2  # echo 下限高于 poly 链路（5%）：滤相邻峰尾漏（~15%）


def _channels(trace: Dict[str, List[int]]) -> List[str]:
    return [b for b in "ACGT" if b in trace]


def detect_compression(trace: Dict[str, List[int]], peaks: List[int]) -> List[Dict]:
    """通用压缩区：峰距 < 0.6 × 局部中位连续 ≥2 处。

    返回 [{kind, start, end, positions}]（read 碱基坐标 1-based）。峰距
    塌陷（如双峰并成一峰）时间距不变，此检测只抓「峰被吞掉」形态的压缩
    ——与 poly_merged_zones 的窗内峰计数口径互补、不重叠（后者只在 poly
    run 窗内生效）。
    """
    idx = [i for i, p in enumerate(peaks) if p is not None]
    if len(idx) < COMPRESSION_MIN_RUN + 1:
        return []
    deltas = [peaks[b] - peaks[a] for a, b in zip(idx, idx[1:])]
    med = sliding_median([float(d) for d in deltas], COMPRESSION_WIN)
    out: List[Dict] = []
    run: List[int] = []   # 压缩 delta 在 idx 内的左端序号
    def flush():
        if len(run) >= COMPRESSION_MIN_RUN:
            a, b = idx[run[0]], idx[run[-1] + 1]
            out.append({"kind": "compression", "start": a + 1, "end": b + 1,
                        "positions": list(range(a + 1, b + 2))})
    for k, d in enumerate(deltas):
        if med[k] > 0 and d < COMPRESSION_RATIO * med[k]:
            run.append(k)
            continue
        flush()
        run = []
    flush()
    return out


def detect_dye_blob(trace: Dict[str, List[int]], peaks: List[int],
                    called_bases: str) -> List[Dict]:
    """read 前 150bp 的 dye blob：四通道同时宽峰且无主通道。

    在修剪后 read 的前 DYE_BLOB_BP 个碱基的峰上逐峰判定：各通道峰窗内
    半高宽 ≥BLOB_WIDTH_MIN、最弱通道 ≥ 最强 ×BLOB_CHAN_BALANCE、主通道
    占比 ≤BLOB_DOMINANCE_MAX → blob 峰；连续 ≥BLOB_MIN_PEAKS 个 → 区段。
    质量修剪通常已剪掉 blob 主体，残留进入可信区的部分才是要报的。
    """
    chans = _channels(trace)
    if len(chans) < 4 or not peaks:
        return []
    n = min(len(v) for v in trace.values())
    limit = min(len(called_bases or ""), len(peaks), DYE_BLOB_BP)
    blob_idx: List[int] = []
    for i in range(limit):
        pk = peaks[i]
        if pk is None or not (0 <= pk < n):
            continue
        lo, hi = peak_window(peaks, i, n)
        h = {}
        w = {}
        for c in chans:
            seg = trace[c][lo:hi]
            h[c] = max(seg) if seg else 0
            half = h[c] / 2
            w[c] = sum(1 for x in seg if x >= half)
        hmax = max(h.values())
        if hmax <= 0:
            continue
        if min(w.values()) < BLOB_WIDTH_MIN:
            continue
        if min(h.values()) < BLOB_CHAN_BALANCE * hmax:
            continue
        if hmax / sum(h.values()) > BLOB_DOMINANCE_MAX:
            continue
        blob_idx.append(i)
    out: List[Dict] = []
    run: List[int] = []

    def flush():
        if run:
            out.append({"kind": "dye_blob", "start": run[0] + 1,
                        "end": run[-1] + 1, "positions": [p + 1 for p in run]})

    blob_set = set(blob_idx)
    for i in range(limit):
        if i in blob_set:
            run.append(i)
            continue
        flush()
        run = []
    flush()
    return [a for a in out if len(a["positions"]) >= BLOB_MIN_PEAKS]


def detect_short_slippage(trace: Dict[str, List[int]], peaks: List[int],
                          called_bases: str) -> List[Dict]:
    """短同聚物（3-7bp）末端滑移 echo：复用 detect_phase_shift（run 锚定）。

    ≥8bp 的 run 归 poly 链路（homopolymer.py / signal 主流程），此处只补
    短 run——stutter echo 在短 run 末端同样出现，但不进 mixed 剔除逻辑
    （goal：不新增 if 分支直接改结论），仅作特征与 info。
    """
    bases = (called_bases or "").upper()
    if not bases or not peaks:
        return []
    out: List[Dict] = []
    i = 0
    while i < len(bases):
        j = i
        while j + 1 < len(bases) and bases[j + 1] == bases[i]:
            j += 1
        run_len = j - i + 1
        if SHORT_RUN_BP[0] <= run_len <= SHORT_RUN_BP[1] and i < len(peaks):
            echoes = detect_phase_shift(trace, peaks, i, min(j, len(peaks) - 1),
                                        bases[i], min_ratio=SHORT_SLIP_MIN_RATIO)
            if echoes:
                out.append({"kind": "short_slippage", "start": i + 1,
                            "end": min(j, len(peaks) - 1) + 1,
                            "positions": list(echoes)})
        i = j + 1
    return out


def detect_artifacts(trace: Dict[str, List[int]], peaks: List[int],
                     called_bases: str) -> List[Dict]:
    """三检测器汇总（T20）。伪迹区段列表，按 read 坐标先后排序。"""
    out: List[Dict] = []
    out.extend(detect_compression(trace, peaks))
    out.extend(detect_dye_blob(trace, peaks, called_bases))
    out.extend(detect_short_slippage(trace, peaks, called_bases))
    out.sort(key=lambda a: a.get("start") or 0)
    return out


def artifact_at(artifacts: List[Dict], read_pos: Optional[int]) -> Optional[str]:
    """read 位置落在哪个伪迹区段内（T15 特征）：返回 kind 或 None"""
    if not read_pos:
        return None
    for a in artifacts or []:
        if int(a.get("start") or 0) <= read_pos <= int(a.get("end") or 0):
            return a.get("kind")
    return None
