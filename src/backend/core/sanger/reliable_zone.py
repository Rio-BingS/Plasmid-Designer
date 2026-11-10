"""read 可信区判定：信号驱动（goal T12；A 1.1）——配置载体与对外接口。

可信由 read 自身信号决定（20bp 滑窗 mean Q / 峰距 CV / 半高宽比 / 主次峰
比，实现本位见 zone_signal.compute_signal_zone）；本模块保留：

- ZoneParams：四参数配置载体。front_hard（首端 30bp 硬区）保留；tail_gray
  不再封顶（仅作 zone_reason 文案的灰区参照，goal「取消 700bp 硬顶」）；
  tail_hard 仅无峰图（旧记录重放）时兜底封顶；
- parse()/DEFAULT_ZONE：表单与默认配置（"30,60,700,900"）；
- compute_reliable_zone：委托 zone_signal，签名与返回结构不变；
- in_zone / zone_reason / zone_text：判属与文案。

旧三段式位置先验（front_gray 逐窗起点 / tail_gray 700 封顶 / tail_hard
900 硬界）随 T12 退役；位置对变体置信度的强制仅保留 front 硬区
（pipeline._variant_confidence），尾端由信号证据决定。
"""
from dataclasses import asdict, dataclass
from typing import Dict, Optional

from core.sanger.zone_signal import compute_signal_zone  # noqa: F401 (委托实现)


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


def compute_reliable_zone(raw_len: int, trim_start: int, trimmed_q,
                          trace=None, trimmed_peaks=None,
                          params: "ZoneParams" = DEFAULT_ZONE) -> Dict:
    """信号驱动可信区（实现迁 zone_signal.compute_signal_zone，T12）。

    参数与返回结构兼容旧版：{start, end}（修剪后 1-based 闭区间）、
    {raw_start, raw_end}（原始 read 坐标）、front_reason / tail_reason
    （边界依据，写进结论与悬停）以及所用参数。"""
    return compute_signal_zone(raw_len, trim_start, trimmed_q, trace,
                               trimmed_peaks, params)


def in_zone(zone: Optional[Dict], rp: Optional[int]) -> bool:
    """修剪后 read 坐标 rp（1-based）是否落在可信区内；无可信区信息视为在内"""
    if not zone or rp is None:
        return True
    return zone["start"] <= rp <= zone["end"]


def zone_core_end(zone: Optional[Dict]) -> Optional[int]:
    """核心区终点（修剪后坐标）；旧记录无 core_end 时等于可信区终点"""
    if not zone:
        return None
    ce = zone.get("core_end")
    return zone["end"] if ce is None else min(int(ce), zone["end"])


def core_zone(zone: Optional[Dict]) -> Optional[Dict]:
    """只含核心区的可信区副本（单 read 确证、双峰分级用）"""
    if not zone:
        return zone
    z = dict(zone)
    z["end"] = zone_core_end(zone)
    return z


def zone_class(zone: Optional[Dict], rp: Optional[int]) -> Optional[str]:
    """位点所在分区：front（首端不可信）/ core（核心区）/ tail_ext（尾端
    扩展区：tail_gray 之后但信号达标）/ outside（信号区之外）。无可信区
    信息返回 None"""
    if not zone or rp is None:
        return None
    if rp < zone["start"]:
        return "front"
    if rp <= zone_core_end(zone):
        return "core"
    if rp <= zone["end"]:
        return "tail_ext"
    return "outside"


TAIL_EXT_REASON = ("位于 read 尾端扩展区（{g}bp 之后、信号仍达标）——单 read "
                   "差异不确证，需其他引物覆盖确认")


def zone_reason(zone: Dict, rp: int) -> str:
    """rp 落在可信区外时的具体原因（tail_gray/tail_hard 仅为文案参照，
    区界本身由信号窗口决定，T12）"""
    p = zone["params"]
    raw = rp + (zone["raw_start"] - zone["start"])
    if zone_class(zone, rp) == "tail_ext":
        return TAIL_EXT_REASON.format(g=p["tail_gray"])
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
