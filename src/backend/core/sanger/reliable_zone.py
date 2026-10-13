"""read 可信区判定：位置先验 + 信号（Q 值与峰型）

Sanger 测序的可靠性首先由位置决定：引物 3′ 端之后紧邻一段信号未稳定
（染料团、峰距不稳），读到 ~700bp 以后峰展宽、分辨率下降，900bp 以后
基本不可用。固定扣首尾 N bp 跟每条 read 的实际信号无关——好 read 白白
丢掉一段、差 read 又扣不够。这里按三段式划区（坐标为原始 read 碱基序号，
1-based，从引物 3′ 端之后第一个碱基起算）：

    1 .. front_hard              首端硬不可信区：一律不可信
    front_hard+1 .. front_gray   首端灰区：逐条 read 按 Q 值 + 峰型判定起点
    front_gray+1 .. tail_gray    中段：默认可信；信号提前衰减时提前截断
    tail_gray+1 .. tail_hard     尾端灰区：默认不可信，靠其他引物覆盖定案
    > tail_hard                  尾端硬不可信区：一律不可信

硬边界不被信号推翻；信号只能把可信区往里收（首端灰区内找到稳定起点时
才往外放到该点）。可信区之外的差异按低置信处理，有其他引物覆盖时由互检定案。
"""
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Sequence

from .signal import peak_widths

# 首端灰区判定窗口与门槛：窗口内 Q 与峰型都达标才算信号已稳定
FRONT_WIN = 10
FRONT_MEAN_Q = 25
FRONT_HQ_FRAC = 0.9          # 窗口内 ≥Q20 占比
# 峰型：峰宽（半高宽）相对中段中位数、峰距变异系数
WIDTH_RATIO_MAX = 1.5
SPACING_CV_MAX = 0.3
# 中段衰减判定：窗口平均 Q 跌破下限，或 Q 偏低且峰明显展宽
DECAY_WIN = 20
DECAY_MEAN_Q = 20
DECAY_SOFT_Q = 25


@dataclass(frozen=True)
class ZoneParams:
    front_hard: int = 30
    front_gray: int = 60
    tail_gray: int = 700
    tail_hard: int = 900

    def __post_init__(self) -> None:
        vals = (self.front_hard, self.front_gray, self.tail_gray, self.tail_hard)
        if any(not isinstance(x, int) or x < 0 for x in vals):
            raise ValueError("可信区边界须为非负整数")
        if not (self.front_hard <= self.front_gray < self.tail_gray <= self.tail_hard):
            raise ValueError("可信区边界须满足 首端硬区 ≤ 首端灰区 < 尾端灰区 ≤ 尾端硬区")
        if self.tail_hard > 5000:
            raise ValueError("尾端硬区边界不应超过 5000bp")

    @classmethod
    def parse(cls, text: Optional[str]) -> "ZoneParams":
        """"30,60,700,900" → ZoneParams；空串/None 用默认"""
        if text is None or not str(text).strip():
            return cls()
        parts = [p.strip() for p in str(text).replace("，", ",").split(",")]
        if len(parts) != 4:
            raise ValueError("可信区参数须为 4 个数：首端硬区,首端灰区,尾端灰区,尾端硬区")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            raise ValueError("可信区参数须为整数") from None
        return cls(*nums)

    def as_dict(self) -> Dict[str, int]:
        return asdict(self)


DEFAULT_ZONE = ZoneParams()


def _median(vals: Sequence[float]) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _spacing_cv(peaks: Sequence[Optional[int]]) -> Optional[float]:
    pk = [p for p in peaks if p is not None]
    gaps = [b - a for a, b in zip(pk, pk[1:]) if b > a]
    if len(gaps) < 3:
        return None
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return None
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    return (var ** 0.5) / mean


def compute_reliable_zone(raw_len: int, trim_start: int, trimmed_q: List[int],
                          trace: Optional[Dict[str, List[int]]] = None,
                          trimmed_peaks: Optional[List[Optional[int]]] = None,
                          params: ZoneParams = DEFAULT_ZONE) -> Dict:
    """逐条 read 的可信区。

    返回 {start, end}（修剪后 read 坐标，1-based 闭区间；空区间 start>end）、
    {raw_start, raw_end}（原始 read 坐标）、front_reason / tail_reason（边界依据，
    写进结论与悬停）以及所用参数。"""
    n = len(trimmed_q)
    s = trim_start
    widths: List[float] = []
    if trace and trimmed_peaks and len(trimmed_peaks) == n:
        try:
            widths = peak_widths(trace, [p if p is not None else -1 for p in trimmed_peaks])
        except Exception:  # noqa: BLE001 峰型缺失时退回只看 Q
            widths = []
    # 中段峰宽基准：中段可信区内有效峰宽的中位数（读太短时用全 read）
    def t_idx(raw_pos: int) -> int:          # 原始 1-based → 修剪后 0-based
        return raw_pos - 1 - s
    mid = [widths[i] for i in range(max(0, t_idx(params.front_gray + 1)),
                                    min(n, t_idx(params.tail_gray) + 1))
           if widths and widths[i] > 0]
    if len(mid) < 10 and widths:
        mid = [w for w in widths if w > 0]
    base_w = _median(mid) if mid else 0.0

    def q_stats(i0: int, i1: int):
        qs = trimmed_q[max(0, i0):min(n, i1 + 1)]
        if not qs:
            return 0.0, 0.0
        return sum(qs) / len(qs), sum(1 for q in qs if q >= 20) / len(qs)

    def width_ratio(i0: int, i1: int) -> Optional[float]:
        if not base_w:
            return None
        ws = [widths[i] for i in range(max(0, i0), min(n, i1 + 1)) if widths[i] > 0]
        return _median(ws) / base_w if ws else None

    def cv(i0: int, i1: int) -> Optional[float]:
        if not trimmed_peaks:
            return None
        return _spacing_cv(trimmed_peaks[max(0, i0):min(n, i1 + 1)])

    # ---- 首端 ----
    first_raw = s + 1                        # 修剪后第一个碱基的原始坐标
    lo_raw = params.front_hard + 1
    front_reason = f"首端 1–{params.front_hard}bp 为硬不可信区" if params.front_hard else ""
    if params.front_gray > params.front_hard:
        found = None
        why = ""
        for p in range(max(lo_raw, first_raw), params.front_gray + 1):
            i0, i1 = t_idx(p), t_idx(p) + FRONT_WIN - 1
            if i1 >= n:
                break
            mq, hq = q_stats(i0, i1)
            wr, c = width_ratio(i0, i1), cv(i0, i1)
            # 起点碱基本身也要达标：窗口均值掩盖不了起点就是坏碱基
            w0 = width_ratio(i0, i0)
            if trimmed_q[i0] < 20 or (w0 is not None and w0 > WIDTH_RATIO_MAX):
                why = (f"该位 Q{trimmed_q[i0]}" if trimmed_q[i0] < 20
                       else f"峰宽为中段 {w0:.1f} 倍")
                continue
            if mq < FRONT_MEAN_Q or hq < FRONT_HQ_FRAC:
                why = f"窗口平均 Q{mq:.0f}、≥Q20 占 {round(hq * 100)}%"
                continue
            if wr is not None and wr > WIDTH_RATIO_MAX:
                why = f"峰宽为中段 {wr:.1f} 倍"
                continue
            if c is not None and c > SPACING_CV_MAX:
                why = f"峰距不稳（变异系数 {c:.2f}）"
                continue
            found = p
            break
        if found is None:
            lo_raw = params.front_gray + 1
            front_reason = (f"首端灰区 {params.front_hard + 1}–{params.front_gray}bp "
                            f"信号未稳定" + (f"（{why}）" if why else ""))
        else:
            lo_raw = found
            if found > params.front_hard + 1:
                front_reason = (f"首端至 {found - 1}bp 信号未稳定"
                                + (f"（{why}）" if why else ""))
    if first_raw > lo_raw:
        lo_raw = first_raw
        front_reason = f"质量修剪至 {first_raw - 1}bp"

    # ---- 尾端 ----
    last_raw = s + n
    hi_raw = params.tail_gray
    tail_reason = f"{params.tail_gray}bp 之后为尾端不可信区（{params.tail_gray + 1}–{params.tail_hard}bp 灰区需其他引物覆盖）"
    # 中段信号提前衰减：自中段起滑窗，Q 跌破下限或 Q 偏低且峰明显展宽即截断
    for p in range(max(lo_raw, params.front_gray + 1), min(hi_raw, last_raw) - DECAY_WIN + 2):
        i0, i1 = t_idx(p), t_idx(p) + DECAY_WIN - 1
        mq, _hq = q_stats(i0, i1)
        wr = width_ratio(i0, i1)
        if mq < DECAY_MEAN_Q or (mq < DECAY_SOFT_Q and wr is not None and wr > WIDTH_RATIO_MAX):
            # 截在窗口内第一个低 Q 碱基之前（窗口均值滞后于真实衰减起点）
            first_bad = next((k for k in range(i0, min(n, i1 + 1)) if trimmed_q[k] < 20), i0)
            p = first_bad + 1 + s
            hi_raw = p - 1
            tail_reason = (f"{p}bp 起信号衰减（窗口平均 Q{mq:.0f}"
                           + (f"、峰宽为中段 {wr:.1f} 倍" if wr is not None else "") + "）")
            break
    if last_raw < hi_raw:
        hi_raw = last_raw
        tail_reason = f"质量修剪止于 {last_raw}bp"
    hi_raw = min(hi_raw, raw_len)

    return {
        "start": lo_raw - s,
        "end": hi_raw - s,
        "raw_start": lo_raw,
        "raw_end": hi_raw,
        "front_reason": front_reason,
        "tail_reason": tail_reason,
        "params": params.as_dict(),
    }


def in_zone(zone: Optional[Dict], rp: Optional[int]) -> bool:
    """修剪后 read 坐标 rp（1-based）是否落在可信区内；无可信区信息视为在内"""
    if not zone or rp is None:
        return True
    return zone["start"] <= rp <= zone["end"]


def zone_reason(zone: Dict, rp: int) -> str:
    """rp 落在可信区外时的具体原因"""
    p = zone["params"]
    raw = rp + (zone["raw_start"] - zone["start"])
    if rp < zone["start"]:
        if raw <= p["front_hard"]:
            return f"位于 read 首端 1–{p['front_hard']}bp 硬不可信区"
        return f"位于 read 首端不可信区（{zone['front_reason']}）"
    if raw > p["tail_hard"]:
        return f"位于 read {p['tail_hard']}bp 之后，一律不可信"
    if raw > p["tail_gray"]:
        return (f"位于 read 尾端灰区 {p['tail_gray'] + 1}–{p['tail_hard']}bp"
                f"（默认不可信，需其他引物覆盖）")
    return f"位于 read 尾端不可信区（{zone['tail_reason']}）"


def zone_text(zone: Dict) -> str:
    """read 概况/结论用：可信区 x–y（原始 read 坐标）及边界依据"""
    if zone["start"] > zone["end"]:
        return "无可信区"
    why = "；".join(x for x in (zone.get("front_reason"), zone.get("tail_reason")) if x)
    return f"可信区 {zone['raw_start']}–{zone['raw_end']}bp" + (f"（{why}）" if why else "")
