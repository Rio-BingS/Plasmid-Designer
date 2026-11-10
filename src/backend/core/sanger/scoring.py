"""校准的 P(真实) 与四档行动（goal T15；A 5.3 / B P1-9、P2-15）

变体判读的最终输出是一支校准概率 P(真实) 与由此导出的四档行动：

- accept        P > 0.99（确证，直接采信）
- exclude       P < 0.01 且有正面反证（互检定案，不进结论）
- review_trace  其余里峰图可核对者（有峰级证据——看一眼峰图即可裁决）
- resequence    其余（证据不足/无峰可看，重测比看峰图更省）

status 与 confidence 由 p_real 分档回填（T23 基准集拟合前的等价锚点，
与既有规则逐位等价、行为不倒退）：

- rejected：P < 0.01 且有反证（等价现 cross_read_resolved——定案必伴随
  低置信，见 _variant_confidence_pass 的定案条件）
- confirmed：P ≥ CONFIRMED_MIN=0.5（现 high/medium 的锚点分别在
  0.99+ 与 0.90+，均落入本档）
- unresolved：其余（现 low，无反证）
- confidence：P > 0.99 → high、P ≥ 0.5 → medium、否则 low

现规则（pipeline._variant_confidence 的 if 树）整体迁入 confidence_tier，
作为 T23 前的 hard tier；P(真实) = tier 锚点 z 值 + 层内连续修正
（logistic，纯 numpy 推理）。层内修正的权重手设且幅度受限——不跨层
边界，因此 status/confidence 与 T14 及之前完全一致；T23 用基准集经
scripts/fit_variant_score.py 拟合连续系数后替换 params/variant_score.json
的锚点与权重（并重校 confirmed_min）。

特征（T15 清单；暂缺项标注）：Q、mutant_pct、局部 SNR（T13 noise）、
mixed_z（T11）、峰距异常分、read 位置分位、可信区内外、支持 read 数、
正反链是否都支持、互检结果、poly/压缩/dye blob 区（T20 后）、
call_disagrees_with_trace（T24 后）。暂缺特征入 params 的缺省权重 0，
拟合脚本补齐。
"""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from core.sanger.homopolymer import POLY_COMPRESSION_RATIO
from core.sanger.reliable_zone import in_zone, zone_class  # noqa: F401

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy 为既有依赖，缺省环境不触发
    np = None

PARAMS_PATH = Path(__file__).resolve().parent / "params" / "variant_score.json"

# 行动与三态阈值（T23 拟合后由 params 覆盖）
ACCEPT_P = 0.99
EXCLUDE_P = 0.01
CONFIRMED_MIN = 0.5

_cache: Tuple[float, "ScoreParams"] | None = None


@dataclass
class ScoreParams:
    """logistic 锚点与层内权重（T23 前手设；来源见 params/variant_score.json）"""

    high_z: float = 5.0
    medium_z: float = 2.2
    low_z: float = -5.3
    # 层内修正权重：(q - 基准)/跨度、(mutant_pct - 80)/20、(snr - 10)/40
    w_q_high: float = 0.4
    w_mp_high: float = 0.4
    w_snr_high: float = 0.3
    w_q_medium: float = 0.4
    w_mp_medium: float = 0.4
    w_q_low: float = 0.15
    w_counter_low: float = 0.2
    accept_p: float = ACCEPT_P
    exclude_p: float = EXCLUDE_P
    confirmed_min: float = CONFIRMED_MIN


def load_params() -> ScoreParams:
    """读 params/variant_score.json（mtime 缓存）；缺文件用内置手设锚点"""
    global _cache
    try:
        mtime = PARAMS_PATH.stat().st_mtime
    except OSError:
        return ScoreParams()
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    try:
        raw = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
        p = ScoreParams(**{k: v for k, v in raw.items() if k in ScoreParams.__dataclass_fields__})
    except Exception:  # noqa: BLE001 参数文件损坏时回退内置锚点
        p = ScoreParams()
    _cache = (mtime, p)
    return p


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _sigmoid(z: float) -> float:
    if np is not None:
        return float(1.0 / (1.0 + np.exp(-z)))
    return 1.0 / (1.0 + math.exp(-z))


def confidence_tier(v: Dict, mixed_positions: set,
                    evidence: Optional[Dict] = None,
                    zone: Optional[Dict] = None) -> str:
    """变异置信度分级（Mutation Surveyor 评分要素：峰强比 + 信噪比 + Q 值 + 多 read 支持）

    自 pipeline._variant_confidence 原样迁入（goal T15：if 分支迁出管线），
    语义与边界逐位不变；本返回值即 T15 的 hard tier，p_real 在其锚点内
    连续修正。

    - 混合峰信号（次级峰占比 >30%，或峰级突变占比落在 30-70%）→ 低：疑似混合/杂合
    - 信噪比 < 5（突变峰接近本底）→ 低
    - 突变峰占比 ≥80% 且信号强，Q≥40（或多 read 支持 Q≥30）→ 高
    - 无峰证据（indel / trace 缺失）时退回 Q + 支持数口径；indel 假阳性率远高于
      替换（同聚物滑移、错配区比对补偿），单 read 低质量 indel 的门槛为 Q30
    - read 可信区之外 basecaller 错误率天然偏高（与修剪阈值无关），首端硬区
      （引物 3′ 端信号固有不可靠）的单 read 差异直接判低，多 read 支持才升级
    """
    support = v.get("support_reads") or 1
    rp = v.get("read_pos")
    # N/模糊调用本身就是未解析（basecaller 拿不准才给 N），无论 Q 值多高
    # 都不构成"与参考不同"的证据，恒低置信、不进共识与确证判定
    alt_up = (v.get("alt_base") or "").upper()
    if v.get("type") in ("substitution", "insertion") and any(
            c not in "ACGT" for c in alt_up):
        return "low"
    # 2026-11-10：单 read 差异只在核心区（首端不可信区之后、tail_gray 之前）
    # 按证据定置信；首端、尾端扩展区（信号达标但 >700bp）、信号区外一律
    # 低置信，多 read 支持才升级。尾端扩展区的差异在 verdict 进待核（不
    # 静默、不确证，T12 原意），信号区外的只列出备查
    if (zone and rp and support < 2 and zone_class(zone, rp) != "core"
            and not v.get("tail_clean")):
        return "low"
    ins_ratio = evidence.get("insertion_peak_ratio") if evidence else None
    if v.get("read_pos") and v["read_pos"] in mixed_positions:
        # 插入位点的峰级证据更具体：插入峰接近邻峰（≥0.6）时，GC 压缩区的
        # 通道拖尾（次级峰 30-40%）不按混合样品处理，交由插入峰规则分级
        if ins_ratio is None or ins_ratio < 0.6:
            return "low"
    q = v.get("read_q") or 0
    # poly（同聚物）区的单 read indel：滑移伪影高发，峰压缩时重复数计数
    # 本身不可靠——压缩明显（峰幅 < 一半）不给高置信，压缩轻微仍走常规口径
    hp = v.get("homopolymer")
    if (hp and v.get("type") in ("insertion", "deletion") and support < 2
            and hp.get("peak_amplitude_ratio") is not None
            and hp["peak_amplitude_ratio"] < POLY_COMPRESSION_RATIO):
        return "medium" if q >= 30 else "low"
    if evidence is not None:
        if ins_ratio is not None:
            # 插入峰强度比：峰接近邻峰（≥0.6）说明峰真实存在——Q 值在峰压缩区
            # 系统性偏低（basecaller 对 indel/压缩区的已知短板），不应一票否决
            if ins_ratio < 0.3:
                return "low"  # 几乎无独立峰：疑似 caller 伪影
            if ins_ratio >= 0.6 and (support >= 2 or q >= 25):
                return "high"
            return "medium" if (ins_ratio >= 0.6 or q >= 15) else "low"
        mp = evidence.get("mutant_pct")
        snr = evidence.get("snr")
        if mp is not None and mp < 70:
            return "low"  # 突变通道弱于参考通道（<30 疑似误读）或与参考通道相当（30-70 双峰混合）
        if snr is not None and snr < 5:
            return "low"  # 突变峰淹没在本底附近
        clean = mp is None or mp >= 80
        # T13：snr 不可估（None）视为不确定——不再当响亮（噪声淹没的弱峰
        # 不构成强证据）；snr 有值时按 ≥10 判响亮
        loud = snr is not None and snr >= 10
        if clean and loud and (q >= 40 or (support >= 2 and q >= 30)):
            return "high"
        if q >= 25:
            return "medium"
        return "low"
    floor = 30 if v.get("type") in ("insertion", "deletion") else 25
    if q < floor:
        return "medium" if support >= 2 else "low"
    if q >= 40:
        return "high"
    return "high" if support >= 2 else "medium"


def p_real_for(v: Dict, tier: str, evidence: Optional[Dict] = None) -> float:
    """hard tier 锚点 + 层内连续修正 → P(真实)。

    修正只用量纲清晰的富余量（Q 超出层门槛的幅度、峰强比超出 80 的幅度、
    信噪比超出 10 的幅度），权重手设且总幅度 < 层间距——不跨层边界。"""
    p = load_params()
    ev = evidence or {}
    q = v.get("read_q") or 0
    mp = ev.get("mutant_pct")
    snr = ev.get("snr")
    if tier == "high":
        z = (p.high_z
             + p.w_q_high * _clip((q - 40) / 10)
             + p.w_mp_high * _clip(((mp if mp is not None else 100) - 80) / 20)
             + p.w_snr_high * _clip(((snr if snr is not None else 30) - 10) / 40))
    elif tier == "medium":
        z = (p.medium_z
             + p.w_q_medium * _clip((q - 25) / 15)
             + p.w_mp_medium * _clip(((mp if mp is not None else 100) - 80) / 20))
    else:
        z = p.low_z + p.w_q_low * _clip(q / 40) \
            + (p.w_counter_low if v.get("cross_read_resolved") else 0.0)
    return round(_sigmoid(z), 4)


def _tier_of_p(p_real: float, p: ScoreParams) -> str:
    """confidence 由 p_real 分档回填（goal T15）"""
    if p_real > p.accept_p:
        return "high"
    if p_real >= p.confirmed_min:
        return "medium"
    return "low"


def _action_of(v: Dict, p_real: float, p: ScoreParams,
               evidence: Optional[Dict]) -> str:
    """四档行动（goal T15）：accept / exclude / review_trace / resequence"""
    if p_real < p.exclude_p and v.get("cross_read_resolved"):
        return "exclude"
    if p_real > p.accept_p:
        return "accept"
    # 待处理两档：有峰级证据可看 → review_trace；无峰可看 → resequence
    ev = evidence or {}
    if any(ev.get(k) is not None
           for k in ("mutant_pct", "snr", "insertion_peak_ratio")):
        return "review_trace"
    return "resequence"


def attach_score(v: Dict, evidence: Optional[Dict] = None,
                 zone: Optional[Dict] = None) -> None:
    """在置信度定级与全部降级之后调用（就地写 v）：

    - p_real：以 v["confidence"]（终局 tier）为锚计算；
    - action：四档行动；
    - confidence：由 p_real 分档回填（与终局 tier 构造性相等——降级路径
      写回的 low 同样落回 low）；
    - status：三态由 p_real + 反证导出（rejected 仅当 P<0.01 且有反证，
      confirmed = P≥confirmed_min，其余 unresolved）——与既有
      _variant_status 逐位等价。
    """
    p = load_params()
    tier = v.get("confidence") or "low"
    p_real = p_real_for(v, tier, evidence)
    v["p_real"] = p_real
    v["action"] = _action_of(v, p_real, p, evidence)
    v["confidence"] = _tier_of_p(p_real, p)
    if p_real < p.exclude_p and v.get("cross_read_resolved"):
        v["status"] = "rejected"
    elif p_real >= p.confirmed_min:
        v["status"] = "confirmed"
    else:
        v["status"] = "unresolved"
