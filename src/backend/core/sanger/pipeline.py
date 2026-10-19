"""Sanger 测序全自动分析管线

输入：一个或多个 .ab1 字节流 + 参考载体序列（与特征注释）
输出：每条 read 的比对与变体、合并突变表、共识序列（consensus）、自动结论

流程：ABIF 解析 → 质量修剪 → 双向比对（自动判向）→ 多 read 一致性投票
      → 共识序列生成 → 特征级注释 → 自动结论
双峰（疑似混合样品）：逐位次级峰检测（剔除 poly 滑移伪影与饱和峰拖影
pull-up）→ read 级分级（widespread 疑似混合 / scattered 个别双峰）→
结论显式提示；widespread 时批量一句话结论以「疑似混合」开头。
可选：检测到 tracy 可执行文件时，对疑似混合样品执行 tracy decompose 解卷积。
"""

import bisect
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from core.sanger.abif_reader import extract_read, AbiParseError, peak_window as _peak_window
from core.sanger.aligner import (
    align_read, align_read_circular, aln_segments, fold_pos, merge_coverage,
)
from core.sanger.annotator import annotate_variants, summarize_severity
from core.seq_utils import revcomp as _seq_revcomp
from core.sanger.signal import (
    baseline_correct, property_maps, estimate_run_length, peak_heights,
    detect_phase_shift, detect_post_poly_dropout, continuous_read_length,
    DROPOUT_WINDOW_BASES,
)
from core.sanger.reliable_zone import (
    DEFAULT_ZONE, ZoneParams, compute_reliable_zone, in_zone, zone_reason, zone_text,
)

TRACY_BIN = os.environ.get("TRACY_BIN", "tracy")
# tracy basecall -f json 的 primarySeq/secondarySeq 字段自 v0.3.10 起已稳定
# （gear-genomics/tracy src/json.h）；更早版本或无法解析的输出只用 primary
TRACY_JSON_MIN_VERSION = (0, 3, 10)
MIXED_PEAK_RATIO = 0.30  # 次级峰 / 主峰 高于此比例视为疑似混合
MIXED_WIDESPREAD_MIN = 5   # 双峰位点 ≥ 此数 → read 级判「疑似混合样品」
MIXED_SCATTERED_MIN = 2    # 2-4 个 → 「个别双峰位点」提示（不升级结论）
MIXED_STRETCH_MIN = 8      # 连续双峰 ≥ 此数（indel 介导的混合：交界下游逐位双峰）也算疑似混合
PULLUP_HEIGHT_FACTOR = 3.0  # 主峰高 ≥ 全 read 峰高中位 × 此倍数视为饱和峰
PULLUP_MAX_RATIO = 0.5      # 饱和峰位次级峰占比 ≤ 此值且与主峰同期 → 按染料拖影（pull-up）抑制
MIN_TRIM_Q = 20          # 默认末端修剪质量阈值
MIN_WINDOW = 50          # 修剪后最短保留长度
# poly 下游骤降判定的 read 末端豁免宽度（END_MARGIN×2）。read 首尾不可信区
# 已改为逐条 read 判定（reliable_zone：位置先验 + Q 值与峰型），不再固定扣 20bp
END_MARGIN = 20
HOMOPOLYMER_MIN = 20     # poly 结构判定：≥20bp 连续同一碱基视为 poly 结构（如 polyA）
POLY_OBSERVE_MIN = 8     # 观察级：8-19bp 同聚物入报告（不触发结论告警）
POLY_MIN_UNITS = 4       # 二/三核苷酸重复：≥4 个重复单元入报告
POLY_COMPRESSION_RATIO = 0.5  # poly 区峰幅 / 局部主峰幅 低于此值视为峰压缩
POLY_PEAK_HEIGHT_RATIO = 0.3  # 峰图计数：低于窗口中位峰高 30% 的波动视为噪声
CROSS_CHECK_MIN_Q = 20   # 跨 read 反证：junction 处 Q 低于此值时"未报"不构成反证
# 跨 read 反证看的是验证 read 在该位点周边一段区域的整体质量，不只是单个
# 碱基的 Q：单碱基 Q 高但周围信号塌陷（峰压缩、拖尾、信号衰减段）时，
# 这条 read 在此处同样不可靠。区域 = 位点两侧各 CROSS_REGION_HALF bp
# （±5bp 太窄，反映不出该引物这一段的测序质量；取 ±25bp 约 50bp 窗口，
# 与 Phred 质量窗口/修剪窗口同量级），截掉 read 首尾不可信区后至少要有
# CROSS_REGION_MIN_COLS 列。窗口宽了，单个低 Q 碱基不该一票否决，改用
# "≥Q20 的碱基占比"衡量整体；位点本身 ±CROSS_CORE_HALF 的核心区仍要求每个碱基 ≥Q20
CROSS_REGION_HALF = 25
CROSS_REGION_MIN_COLS = 30
CROSS_REGION_MEAN_Q = 30   # 区域平均 Q 下限
CROSS_REGION_HQ_FRAC = 0.9  # 区域内 ≥Q20 碱基占比下限
CROSS_CORE_HALF = 2
CROSS_CLEAN_HALF = 5       # 验证 read 自身在位点 ±5bp 内报了差异 → 不作反证



def _read_grade(trimmed: str, trimmed_q: List[int]) -> Tuple[str, float, int]:
    """Read 质量评级（ClinQC/Mutation Surveyor 类工具的 QC 口径）

    A：Q20 比例 ≥90%、修剪后 ≥400bp、无 N
    C：Q20 比例 <70% 或修剪后 <100bp
    B：其余
    返回 (等级, Q20 比例, CRL)；CRL = QV>20 最长连续段（Genewiz 官方定义，
    常规质粒/PCR 测序以 >500bp 为成功参考）。
    """
    q20 = sum(1 for q in trimmed_q if q >= 20)
    q20_ratio = round(q20 / len(trimmed_q), 3) if trimmed_q else 0.0
    n_count = trimmed.upper().count("N")
    if q20_ratio >= 0.9 and len(trimmed) >= 400 and n_count == 0:
        return "A", q20_ratio, continuous_read_length(trimmed_q)
    if q20_ratio < 0.7 or len(trimmed) < 100:
        return "C", q20_ratio, continuous_read_length(trimmed_q)
    return "B", q20_ratio, continuous_read_length(trimmed_q)


def _peak_snr(trace: Dict[str, List[int]], base: str, pk: int,
              half_window: int = 2) -> Optional[float]:
    """变体碱基通道的信噪比（Mutation Surveyor S/N 要素的工程近似）

    峰高取变体位点窗口内该通道最大值；噪声本底取该通道全轨迹中位数。
    """
    ch = trace.get(base) or []
    n = len(ch)
    if n == 0 or pk < 0 or pk >= n:
        return None
    lo, hi = max(0, pk - half_window), min(n, pk + half_window + 1)
    peak = max(ch[lo:hi])
    rest = ch[:lo] + ch[hi:]
    if not rest:
        return None
    noise = sorted(rest)[len(rest) // 2]
    return round(peak / noise, 1) if noise > 0 else None


def _insertion_peak_ratio(trace: Dict[str, List[int]], peak_indices: List[int],
                          read_pos: int, alt_base: str) -> Optional[float]:
    """插入峰强度比：插入碱基通道峰面积 / 相邻峰主峰面积中位数

    纯合质粒中真实存在的插入，其峰高接近邻峰（~1）；部分克隆带插入（混合
    样品）或峰压缩区峰高偏弱（~0.3-0.6）；caller 伪影几乎无独立峰（~0）。
    仅支持单碱基插入（多碱基插入的峰形判别复杂，返回 None 走 Q 口径）。
    """
    if len(alt_base) != 1 or alt_base not in "ACGT":
        return None
    if read_pos is None or read_pos < 1 or read_pos > len(peak_indices):
        return None
    i = read_pos - 1
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or peak_indices[i] is None or not (0 <= peak_indices[i] < n):
        return None
    lo, hi = _peak_window(peak_indices, i, n)
    alt_area = sum(trace[alt_base][lo:hi])
    neigh: List[int] = []
    for j in (i - 2, i - 1, i + 1, i + 2):
        if j == i or not (0 <= j < len(peak_indices)):
            continue
        if peak_indices[j] is None or not (0 <= peak_indices[j] < n):
            continue
        l2, h2 = _peak_window(peak_indices, j, n)
        neigh.append(max(sum(trace[b][l2:h2]) for b in "ACGT"))
    if not neigh:
        return None
    med = sorted(neigh)[len(neigh) // 2]
    return round(alt_area / med, 2) if med > 0 else None


def _variant_peak_evidence(trace: Dict[str, List[int]], peak_indices: List[int],
                           read_pos: Optional[int], ref_base: str,
                           alt_base: str, vtype: str = "substitution") -> Optional[Dict]:
    """变体的峰级证据：替换用峰强比（mutant_pct）+ 信噪比，插入用插入峰强度比"""
    if vtype == "insertion":
        # Sanger read 前导 ~20bp 峰形未稳定（信号爬升/压缩高发），峰证据不可靠
        if read_pos is None or read_pos < 20:
            return None
        ratio = _insertion_peak_ratio(trace, peak_indices, read_pos, alt_base)
        return {"insertion_peak_ratio": ratio} if ratio is not None else None
    if not ref_base or not alt_base or len(ref_base) != 1 or len(alt_base) != 1:
        return None
    if alt_base not in "ACGT" or ref_base not in "ACGT":
        return None
    if not trace or not peak_indices or read_pos is None or read_pos < 1 or read_pos > len(peak_indices):
        return None
    pk = peak_indices[read_pos - 1]
    n = min(len(v) for v in trace.values())
    if n == 0 or pk is None or pk < 0 or pk >= n:
        return None
    lo, hi = _peak_window(peak_indices, read_pos - 1, n)
    areas = {b: sum(trace[b][lo:hi]) for b in "ACGT"}
    alt_a, ref_a = areas.get(alt_base, 0), areas.get(ref_base, 0)
    mutant_pct = round(100 * alt_a / (alt_a + ref_a), 1) if alt_a + ref_a > 0 else None
    return {"mutant_pct": mutant_pct, "snr": _peak_snr(trace, alt_base, pk)}


def _variant_confidence(v: Dict, mixed_positions: set,
                        evidence: Optional[Dict] = None,
                        zone: Optional[Dict] = None) -> str:
    """变异置信度分级（Mutation Surveyor 评分要素：峰强比 + 信噪比 + Q 值 + 多 read 支持）

    - 混合峰信号（次级峰占比 >30%，或峰级突变占比落在 30-70%）→ 低：疑似混合/杂合
    - 信噪比 < 5（突变峰接近本底）→ 低
    - 突变峰占比 ≥80% 且信号强，Q≥40（或多 read 支持 Q≥30）→ 高
    - 无峰证据（indel / trace 缺失）时退回 Q + 支持数口径；indel 假阳性率远高于
      替换（同聚物滑移、错配区比对补偿），单 read 低质量 indel 的门槛为 Q30
    - read 可信区（reliable_zone：首端硬区/灰区判定、尾端灰区与硬区、中段
      提前衰减）之外 basecaller 错误率天然偏高（与修剪阈值无关），落在该区的
      单 read 差异直接判低，多 read 支持才升级
    """
    support = v.get("support_reads") or 1
    rp = v.get("read_pos")
    # N/模糊调用本身就是未解析（basecaller 拿不准才给 N），无论 Q 值多高
    # 都不构成"与参考不同"的证据，恒低置信、不进共识与确证判定
    alt_up = (v.get("alt_base") or "").upper()
    if v.get("type") in ("substitution", "insertion") and any(
            c not in "ACGT" for c in alt_up):
        return "low"
    if zone and rp and support < 2 and not in_zone(zone, rp):
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
        loud = snr is None or snr >= 10
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


def variant_type_text(v: Dict) -> str:
    """差异类型的明确文字（人工核对用）：替换 C>A / 插入 1bp（G）/ 缺失 2bp（AT）"""
    t = v.get("type")
    if t == "insertion":
        n = int(v.get("length") or len((v.get("alt_base") or "").strip("-")) or 1)
        seq = (v.get("alt_base") or "").strip("-")
        return f"插入 {n}bp" + (f"（{seq}）" if seq and len(seq) <= 6 else "")
    if t == "deletion":
        n = int(v.get("length") or 1)
        seq = (v.get("ref_base") or "").strip("-")
        return f"缺失 {n}bp" + (f"（{seq}）" if seq and len(seq) <= 6 else "")
    return f"替换 {v.get('ref_base', '')}>{v.get('alt_base', '')}"


def _low_confidence_reason(v: Dict, mixed_positions: set,
                           evidence: Optional[Dict] = None,
                           zone: Optional[Dict] = None) -> str:
    """低置信的具体原因（与 _variant_confidence 的判定分支一一对应），
    把"疑似测序噪声"这种笼统说法换成人工核对时能直接对照峰图的错误类型"""
    q = int(v.get("read_q") or 0)
    rp = v.get("read_pos")
    support = v.get("support_reads") or 1
    if v.get("cross_read_conflict") and not v.get("cross_read_resolved"):
        rd = "、".join(v["cross_read_conflict"].get("reads", [])[:2])
        return f"互检矛盾：{rd} 在同一位点读出设计碱基"
    if v.get("merged_zone_artifact"):
        return "落在同聚物峰合并压缩区，该 read 此处无可分辨峰"
    if v.get("slippage_artifact"):
        return "poly 下游滑移伪影（同聚物后信号错位）"
    alt_up = (v.get("alt_base") or "").upper()
    if v.get("type") in ("substitution", "insertion") and any(
            c not in "ACGT-" for c in alt_up):
        return "碱基判读为 N/模糊碱基（basecaller 未能解析）"
    if zone and rp and support < 2 and not in_zone(zone, rp):
        return zone_reason(zone, rp)
    ins_ratio = evidence.get("insertion_peak_ratio") if evidence else None
    if rp and rp in mixed_positions and (ins_ratio is None or ins_ratio < 0.6):
        return "该位点双峰（次级峰占比 >30%，疑似混合样品或杂峰）"
    hp = v.get("homopolymer")
    if (hp and v.get("type") in ("insertion", "deletion")
            and hp.get("peak_amplitude_ratio") is not None
            and hp["peak_amplitude_ratio"] < POLY_COMPRESSION_RATIO):
        return f"同聚物峰压缩（峰幅为局部主峰的 {round(hp['peak_amplitude_ratio'] * 100)}%），重复数计数不可靠"
    if evidence is not None:
        if ins_ratio is not None:
            pct = round(ins_ratio * 100)
            if ins_ratio < 0.3:
                return f"插入位置几乎无独立峰（插入峰为邻峰的 {pct}%），疑似 basecaller 伪影"
            if ins_ratio >= 0.6:
                return f"插入峰可见（为邻峰的 {pct}%），但仅单条 read 支持（Q{q}）"
            return f"插入峰偏弱（为邻峰的 {pct}%）且 Q 值低，Q{q}"
        mp, snr = evidence.get("mutant_pct"), evidence.get("snr")
        if mp is not None and mp < 30:
            return f"突变峰明显弱于参考峰（突变峰占比 {mp}%），疑似误读"
        if mp is not None and mp < 70:
            return f"突变峰与参考峰高度相当（突变峰占比 {mp}%），双峰/混合"
        if snr is not None and snr < 5:
            return f"突变峰信噪比低（SNR {snr}），接近本底"
    return f"Q 值偏低，Q{q}" + ("，仅单条 read 支持" if support < 2 else "")


def _coverage_gaps(covered_ranges: List[Tuple[int, int]], length: int,
                   min_len: int = 100, cap: int = 10,
                   circular: bool = False) -> List[Dict]:
    """覆盖缺口（未覆盖区间）按长度降序，供补测建议

    circular=True：环状参考的尾部缺口 [x, L] 与头部缺口 [1, y] 在原点处
    相连，先合并成一段跨原点缺口再按 min_len 过滤——分开过滤会把两侧
    各不足 min_len、合起来已够长的缺口整段漏掉。跨原点缺口与跨原点 read
    同口径：start/end 为折回坐标（start > end），wraps_origin=True，
    ref_segments 给出 [[x, L], [1, y]] 两段；length 为合并后总长。
    线性参考（及未跨原点的缺口）不带新字段，结果与此前完全一致。"""
    raw: List[Dict] = []
    prev = 0
    for s, e in covered_ranges:
        if s - prev - 1 > 0:
            raw.append({"start": prev + 1, "end": s - 1, "length": s - prev - 1})
        prev = max(prev, e)
    if length - prev > 0:
        raw.append({"start": prev + 1, "end": length, "length": length - prev})
    if (circular and len(raw) >= 2 and raw[0]["start"] == 1
            and raw[-1]["end"] == length):
        head, tail = raw[0], raw.pop()
        raw[0] = {
            "start": tail["start"], "end": head["end"],
            "length": tail["length"] + head["length"],
            "wraps_origin": True,
            "ref_segments": [[tail["start"], length], [1, head["end"]]],
        }
    gaps = [g for g in raw if g["length"] >= min_len]
    gaps.sort(key=lambda g: -g["length"])
    return gaps[:cap]


def _gap_span_text(gap: Dict, sep: str = "-") -> str:
    """缺口位置文本：线性「s-e」；跨原点「x-L、1-y」（与 read 区段同口径）"""
    segs = gap.get("ref_segments")
    if segs:
        return "、".join(f"{a}{sep}{b}" for a, b in segs)
    return f"{gap['start']}{sep}{gap['end']}"


def _coverage_ranges_text(covered_ranges: List[Tuple[int, int]]) -> str:
    """实测覆盖区间的紧凑文本（结论首行用）：部分测序是常规策略，
    结论围绕引物实际覆盖的区域组织，把"测的是哪段"直接摆出来。
    逗号前缀形态，可无缝嵌入既有括号短语（如「覆盖 5.0%，实测区间 21-110」）"""
    if not covered_ranges:
        return ""
    spans = "、".join(f"{s}-{e}" for s, e in covered_ranges[:3])
    if len(covered_ranges) > 3:
        spans += f" 等 {len(covered_ranges)} 段"
    return f"，实测区间 {spans}"


def _conclusion_summary(variants: List[Dict], cds_reports: List[Dict],
                        has_mixed: bool, partial_reads: int = 0,
                        resolved: int = 0) -> str:
    """结论头部的一句话总结：✓/✗/△ 一眼给出判读结果，详细段落空两行后展开。
    优先级：疑似混合 > 确认差异 > 低置信伪影 > 完全一致。
    partial_reads>0（有 read 大段未比对上参考：载体外序列、污染、参考
    有误，或线性参考上跨原点的 read）时不得给出干净的 ✓——未比对部分
    根本没有验证。环状参考的跨原点 read 已在主流程整条比对，不再计入。"""
    if partial_reads and not has_mixed:
        confirmed0 = [v for v in variants if v.get("confidence") != "low"]
        bad0 = [c for c in cds_reports if c.get("protein_identical") is False]
        if not confirmed0 and not bad0:
            return (f"△ 结论：已比对区域与设计一致，但 {partial_reads} 条 read "
                    "有大段未比对上参考，该部分未验证")
    confirmed = [v for v in variants if v.get("confidence") != "low"]
    bad = [c for c in cds_reports if c.get("protein_identical") is False]
    names = "、".join(c["name"] for c in bad[:2]) + (" 等" if len(bad) > 2 else "")
    if has_mixed:
        head = "△ 结论：疑似混合样品（部分位点双峰），主峰序列按多数碱基判读"
        if bad:
            head += f"，{names}蛋白与设计不一致"
        return head + "——建议重新挑单克隆复测"
    if confirmed or bad:
        bits = []
        if bad:
            bits.append(f"{names}蛋白与设计不一致")
        if confirmed:
            bits.append(f"检出 {len(confirmed)} 处确认差异")
        return "✗ 结论：" + "，".join(bits)
    if variants:
        return (f"✓ 结论：构建与设计一致"
                f"（{len(variants)} 处低置信差异疑似测序伪影，未计入判定）")
    if resolved:
        # variants 已剔除互检定案项：剩下的都排除了，结论是确定的
        return (f"✓ 结论：构建与设计一致（{resolved} 处单条 read 差异"
                "已被其他引物互检排除）")
    return "✓ 结论：构建与设计一致，未检出差异"


# 比对上的 read 长度低于修剪后长度的该比例即视为「部分比对」
PARTIAL_ALIGN_RATIO = 0.8


def alignment_span_text(aln: Dict, sep: str = "–") -> str:
    """read 比对区段的展示文本（报告用）：线性即「s–e」；环状跨原点按折回
    坐标分两段「s–L、1–e'」"""
    return "、".join(f"{a}{sep}{b}" for a, b in aln_segments(aln)) or \
        f"{aln.get('ref_start', 0)}{sep}{aln.get('ref_end', 0)}"


def _circ_len(aln: Dict) -> int:
    """环状比对的参考长度（线性比对返回 0：坐标不折回）"""
    return int(aln.get("ref_length") or 0) if aln.get("circular") else 0


def _run_offset(aln: Dict, start: int, end: int) -> int:
    """参考区间 [start,end]（1..L）在该 read 展开坐标中的平移量：环状比对
    跨原点时，原点后段的区间位于 L+1.. 处（返回 L），否则 0。
    线性比对恒为 0，与此前的逐位比较完全一致。"""
    L = _circ_len(aln)
    if (L and aln.get("ref_end", 0) > L and end < aln.get("ref_start", 0)
            and start + L <= aln["ref_end"]):
        return L
    return 0


def _check_partial_alignment(trimmed: str, aln: Dict,
                             circular: bool = False) -> Optional[Dict]:
    """read 大段未比对上参考时给出说明（未比对部分没有任何验证）。

    环状参考在主流程中已按「参考 + 参考头部」比对，跨原点的 read 整条
    比对上、坐标折回，不会落到这里；此处只剩其他原因的截断：载体外序列、
    污染、参考有误，或参考实为环状却按线性分析。"""
    n = len(trimmed)
    aligned = int(aln.get("aligned_read_len") or 0)
    if n == 0 or aligned >= PARTIAL_ALIGN_RATIO * n:
        return None
    return {
        "aligned_read_len": aligned,
        "trimmed_length": n,
        "circular": bool(circular),
    }


def _partial_alignment_note(filename: str, info: Dict) -> str:
    head = (f"注意：{filename} 修剪后 {info['trimmed_length']}bp，仅 "
            f"{info['aligned_read_len']}bp 比对上参考")
    txt = head + "——其余部分未验证（可能是载体外序列、污染或参考有误"
    if not info.get("circular"):
        txt += "；若参考实为环状质粒，请按环状参考分析"
    return txt + "），建议核对"


def _trim_by_quality(bases: str, quality: List[int], min_q: int) -> Tuple[int, int]:
    """返回保留区间 [start, end)（0-based）：Mott/Phred 式累积分数修剪

    逐碱基「剥掉 Q<阈值」碰到一个高质量碱基就会提前停手——真实 ab1 的
    首末端是低质量区里夹杂零星好碱基，逐碱基法几乎剪不动。这里改为
    Mott 算法：每碱基计分 q-threshold，取累积和最大的子区间（Kadane），
    整段坏末端（即使夹着好碱基）会被整体剪掉。
    """
    best_sum, cur_sum, best_start, cur_start, best_end = 0, 0, 0, 0, 0
    for i, q in enumerate(quality):
        if cur_sum <= 0:
            cur_start = i
            cur_sum = 0
        cur_sum += q - min_q
        if cur_sum > best_sum:
            best_sum, best_start, best_end = cur_sum, cur_start, i + 1
    return (best_start, best_end)


def _detect_mixed_detail(bases: str, trace: Dict[str, List[int]],
                         peak_indices: List[int]) -> List[Dict]:
    """逐位双峰细节（双峰检测的底层）：[{pos(1-based), ratio, secondary_base, pullup}]

    判定口径与旧版一致：次级通道峰面积占主峰比例 > MIXED_PEAK_RATIO、
    主峰面积高于全 read 主峰面积中位数 25%（called 碱基无信号的退化窗口
    四通道面积全相等，不设门槛会把每个位点都误判成混合）、四通道面积不接
    近相等（无主导通道 = 无真实信号差异）。

    pull-up（染料拖影）抑制：强饱和峰会在其他通道同期漏出小峰——与真实
    混合的双峰同期出现无法靠 timing 区分，改按「触发条件」区分：主峰高
    ≥ 全 read 峰高中位 × PULLUP_HEIGHT_FACTOR（接近饱和）且次级占比
    ≤ PULLUP_MAX_RATIO（拖影比例通常远低于真实混合）→ 标记 pullup。
    真实混合样品在 read 上有多个双峰位点，个别饱和位被抑制不影响分级。
    """
    detail: List[Dict] = []
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0:
        return detail
    windows: List[Optional[Dict[str, int]]] = []
    for i, base in enumerate(bases):
        if base not in "ACGT" or i >= len(peak_indices):
            windows.append(None)
            continue
        pk = peak_indices[i]
        if pk is None or pk < 0 or pk >= n:
            windows.append(None)
            continue
        lo, hi = _peak_window(peak_indices, i, n)
        windows.append({b: sum(trace[b][lo:hi]) for b in "ACGT"})
    tops = sorted(max(w.values()) for w in windows if w)
    pos_tops = [t for t in tops if t > 0]
    main_floor = 0.25 * pos_tops[len(pos_tops) // 2] if pos_tops else 0.0
    # 全 read 峰高中位（pull-up 触发参考）：每峰取四通道最大值
    all_heights = sorted(h for w in windows if w for h in [max(w.values())] if h > 0)
    med_height = all_heights[len(all_heights) // 2] if all_heights else 0.0
    for i, base in enumerate(bases):
        areas = windows[i]
        if areas is None:
            continue
        sorted_a = sorted(areas.values(), reverse=True)
        if sorted_a[0] < main_floor:
            continue
        # 退化窗口护栏：四通道面积接近相等（无主导通道）说明基线校正后
        # 该窗口无真实信号差异，按混合峰处理只会整段误报。
        # 终审 A-19：判据改为「第四名通道仍有显著信号」——真实 1:1 混合/
        # 杂合的双通道窗口里第三、四通道只有本底噪声（≪ 主峰），而基线
        # 校正失效的无信号窗口四通道同高。旧判据 sorted_a[1] >= 0.9 主峰
        # 恰好屏蔽了最典型的 1:1 双峰（实测 ratio 0.92/0.95/1.00 检出 0/5）
        if sorted_a[3] >= 0.8 * sorted_a[0]:
            continue
        if sorted_a[1] > 0 and sorted_a[0] > 0:
            ratio = sorted_a[1] / sorted_a[0]
            # 终审 A-19：去掉 areas[base]==sorted_a[0] 硬要求——真实混合中
            # basecaller 可能恰好把次要碱基作为 called（次要克隆占多数的位点），
            # 此时次要通道才是主峰，位点同样应计入双峰
            if ratio > MIXED_PEAK_RATIO:
                sec_base = next((b for b in "ACGT" if b != base
                                 and areas.get(b) == sorted_a[1]), None)
                pullup = False
                if med_height > 0 and sorted_a[0] >= PULLUP_HEIGHT_FACTOR * med_height \
                        and ratio <= PULLUP_MAX_RATIO:
                    pk = peak_indices[i] if i < len(peak_indices) else None
                    if pk is not None and 0 <= pk < n:
                        lo, hi = _peak_window(peak_indices, i, n)
                        main_apex = max(range(lo, hi), key=lambda x: trace[base][x])
                        sec_apex = max(range(lo, hi),
                                       key=lambda x: trace.get(sec_base, [0] * n)[x])
                        pullup = abs(sec_apex - main_apex) <= 1
                detail.append({"pos": i + 1, "ratio": round(ratio, 3),
                               "secondary_base": sec_base, "pullup": pullup})
    return detail


def _detect_mixed_positions(bases: str, trace: Dict[str, List[int]],
                            peak_indices: List[int]) -> List[int]:
    """检测疑似混合/杂合位点（兼容入口）：返回双峰位点的 1-based 位置列表

    已剔除 pull-up 拖影位（饱和峰在其他通道的同期小峰不是混合信号）；
    poly 下游滑移 echo 由管线在调用前剔除。
    """
    return [e["pos"] for e in _detect_mixed_detail(bases, trace, peak_indices)
            if not e["pullup"]]


def _tracy_mixed_corroboration(entries: List[Dict], peaks: List[int],
                               tracy_calls: Dict,
                               zone: Optional[Dict] = None) -> Dict:
    """tracy 次级 basecall 对院内逐位双峰的独立印证（审计 #8）

    tracy 与 ABI caller 的碱基序列不一一对应，按峰在 trace 上的采样坐标
    对位：取与院内峰最近的 tracy 调用，距离不超过半个峰间距才算同一位点。
    每个院内双峰位点（pull-up 已剔除的不计）标记：
    - agree：tracy 在该处也报次级峰，且院内主/次两个碱基都在 tracy 的
      primary + secondary（IUPAC 展开）内；
    - disagree：tracy 在该处单峰，或其次级碱基与院内不同；
    - unmatched：附近没有 tracy 调用（对位失败，不计入一致率）。
    tracy_only 为院内可判读区间内 tracy 报次级峰而院内未报的位点数。
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
        j = bisect.bisect_left(tpos, pk, 0, nt)
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
    # 院内可判读区间（read 可信区）内 tracy 独报的双峰
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


_IUPAC_EXPAND = {"A": "A", "C": "C", "G": "G", "T": "T", "R": "AG", "Y": "CT",
                 "S": "CG", "W": "AT", "K": "GT", "M": "AC", "B": "CGT",
                 "D": "AGT", "H": "ACT", "V": "ACG"}


def _classify_mixed(entries: List[Dict], read_len: int,
                    tracy_calls: Optional[Dict] = None,
                    peaks: Optional[List[int]] = None,
                    zone: Optional[Dict] = None) -> Dict:
    """把逐位双峰聚合成 read 级混合分级（双峰准确性的关键一层）

    - widespread（疑似混合样品）：位点数 ≥ MIXED_WIDESPREAD_MIN，或存在
      ≥ MIXED_STRETCH_MIN 的连续双峰段（两个克隆相差插入/缺失时，交界下游
      相位错开的逐位双峰是典型形态）；
    - scattered（个别双峰）：MIXED_SCATTERED_MIN..MIN-1 个位点——两个克隆
      仅差 1-2 个碱基的真实混合也会落在这里，按提示级处理、不升级结论；
    - none：无或仅 1 个位点（单点双峰与个别碱基噪声无法区分，交由该位点
      变体的峰级证据与置信度口径）。

    tracy_calls/peaks 给出时附加 tracy_corroboration（见
    _tracy_mixed_corroboration），不参与分级。

    minor_fraction：双峰占比中位数 r → 次要克隆占比 r/(1+r)
    （两克隆混合模型下 次峰/主峰 = f/(1-f)，f 为次要克隆的摩尔占比）。
    """
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
    if count >= MIXED_WIDESPREAD_MIN or best >= MIXED_STRETCH_MIN:
        profile["class"] = "widespread"
    elif count >= MIXED_SCATTERED_MIN:
        profile["class"] = "scattered"
    return profile


def _try_tracy_decompose(ab1_path: str, ref_fasta: str) -> Optional[List[Dict]]:
    """若 tracy 可用，对混合样品执行 decompose，返回两条等位基因序列"""
    tracy = shutil.which(TRACY_BIN) or (TRACY_BIN if os.path.isfile(TRACY_BIN) else None)
    if not tracy:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            out_prefix = os.path.join(td, "dec")
            proc = subprocess.run(
                [tracy, "decompose", "-r", ref_fasta, "-o", out_prefix, ab1_path],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                return None
            alleles = []
            for suffix in (".1.fasta", ".2.fasta", "_1.fasta", "_2.fasta"):
                p = out_prefix + suffix
                if os.path.isfile(p):
                    with open(p) as fh:
                        seq = "".join(line.strip() for line in fh if not line.startswith(">"))
                    alleles.append({"sequence": seq, "source": suffix})
            return alleles or None
    except (OSError, subprocess.SubprocessError):
        return None


def find_repeat_runs(seq: str, min_len: int = HOMOPOLYMER_MIN,
                     observe_min: int = POLY_OBSERVE_MIN,
                     min_units: int = POLY_MIN_UNITS) -> List[Dict]:
    """扫描重复结构（B3）：period=1 同聚物 + period=2/3 二/三核苷酸重复

    - period=1：≥min_len 为 poly 结构（tier="poly"），observe_min..min_len-1
      为"观察级"（tier="observed"，入报告但不触发结论告警——basecaller 的
      整段偏差风险主要出现在 ≥20bp，短 run 只作观察）；
    - period=2/3：≥min_units 个完整重复单元入报告；
    - TRF 类工具会把同一 span 以多个 period 冗余报出（如 AAAAA 也匹配
      period 2 的 AA）：只保留能解释该 span 的最小 period。

    返回 run 字典：{base(unit 首碱基；period>1 为 None), unit, period,
    start, end(1-based 闭区间), length(bp), repeat_count(单元数), lus, tier}。
    lus = 无中断重复数（本扫描只报最大纯 span，故等于 repeat_count；
    L31：LUS 比总长更能预测 stutter 水平）。
    """
    n = len(seq)
    raw: List[Dict] = []
    for period in (1, 2, 3):
        i = 0
        while i < n:
            unit = seq[i:i + period]
            if len(unit) < period or not all(c in "ACGT" for c in unit):
                i += 1
                continue
            j = i + period
            while j + period <= n and seq[j:j + period] == unit:
                j += period
            span = j - i
            units = span // period
            appended = False
            if period == 1:
                if span >= observe_min:
                    raw.append({"unit": unit, "period": 1, "start": i, "span": span,
                                "repeat_count": span})
                    appended = True
            elif units >= min_units:
                raw.append({"unit": unit, "period": period, "start": i, "span": span,
                            "repeat_count": units})
                appended = True
            # 命中 run 后跳到 run 末尾；未命中必须逐位前进（period 2/3 的
            # run 可从任意相位开始，跨步会漏掉错位起点）
            i = j if appended else i + 1
    # 去冗余：span 被更小 period 的 run 完全覆盖（且单元循环一致）时丢弃
    raw.sort(key=lambda r: (r["period"], r["start"]))
    runs: List[Dict] = []
    for r in raw:
        redundant = False
        for q in runs:
            if q["period"] < r["period"] and q["start"] <= r["start"] \
                    and r["start"] + r["span"] <= q["start"] + q["span"]:
                redundant = True
                break
        if not redundant:
            runs.append(r)
    out: List[Dict] = []
    for r in runs:
        length = r["span"]
        tier = "poly" if length >= min_len else "observed"
        out.append({
            "base": r["unit"][0] if r["period"] == 1 else None,
            "unit": r["unit"],
            "period": r["period"],
            "start": r["start"] + 1,          # 1-based 闭区间（沿用现有约定）
            "end": r["start"] + length,
            "length": length,
            "repeat_count": r["repeat_count"],
            "lus": r["repeat_count"],
            "tier": tier,
        })
    return out


def find_homopolymers(seq: str, min_len: int = HOMOPOLYMER_MIN) -> List[Dict]:
    """扫描同聚物（poly）结构：≥min_len 连续同一碱基，1-based 闭区间

    find_repeat_runs 的 period=1 视图（含 period/tier/repeat_count/lus 字段）。
    """
    return [r for r in find_repeat_runs(seq, min_len=min_len, observe_min=min_len)
            if r["period"] == 1]


def _overlapping_run(runs: List[Dict], pos: int, end: int, base: Optional[str] = None) -> Optional[Dict]:
    """返回与 [pos, end] 相交（可选要求碱基匹配）的第一个 poly 结构"""
    for r in runs:
        if r["start"] <= end and r["end"] >= pos and (base is None or r["base"] == base):
            return r
    return None


def _annotate_homopolymer(ref: str, variants: List[Dict],
                          runs: List[Dict]) -> None:
    """为变体标注所在/相邻重复结构，并计算参考与测得的重复数目

    - 替换落在 run 内：标注结构，重复数不变；
    - 插入：插入序列为相邻 run 的整倍单元（B3：poly 的 AAA 也好、(CAG)n
      的 CAG 也好，统一按"插入单元 = 重复单元"归属），测得重复数 =
      参考重复数 + 插入单元数；
    - 缺失：删除序列为所在 run 的整倍单元，测得重复数 =
      参考重复数 - 删除单元数；
    - 跨 run 的复杂 indel 不算重复结构变异。
    """
    for v in variants:
        pos = int(v["ref_pos"])
        if v["type"] == "substitution":
            run = _overlapping_run(runs, pos, pos)
            if run:
                v["homopolymer"] = {**run, "ref_repeat_count": run["repeat_count"],
                                    "observed_repeat_count": run["repeat_count"]}
            continue
        if v["type"] == "insertion":
            alt = (v.get("alt_base") or "").upper()
            if alt and all(c in "ACGT" for c in alt):
                run = _unit_run(runs, pos, pos + 1, alt)
                if run:
                    k = len(alt) // run["period"]
                    v["homopolymer"] = {**run, "ref_repeat_count": run["repeat_count"],
                                        "observed_repeat_count": run["repeat_count"] + k}
            continue
        # deletion
        vlen = int(v.get("length") or 1)
        deleted = ref[pos - 1:pos - 1 + vlen].upper()
        if deleted and all(c in "ACGT" for c in deleted):
            run = _unit_run(runs, pos, pos + vlen - 1, deleted)
            if run and run["length"] >= vlen:
                k = vlen // run["period"]
                v["homopolymer"] = {**run, "ref_repeat_count": run["repeat_count"],
                                    "observed_repeat_count": run["repeat_count"] - k}


def _unit_run(runs: List[Dict], pos: int, end: int, seq: str) -> Optional[Dict]:
    """返回与 [pos, end] 相交、且 seq 为其重复单元整倍数的第一个 run（B3）

    整倍判定：len(seq) 是 period 的整倍数且 seq == unit × k。period 小者
    优先（runs 按 period 升序传入），同 poly 插入优先归同聚物结构。
    """
    for r in runs:
        if r["start"] <= end and r["end"] >= pos:
            period = r["period"]
            if len(seq) % period == 0 and seq == r["unit"] * (len(seq) // period):
                return r
    return None


def _poly_peak_amplitude_ratio(trace: Dict[str, List[int]], peaks: List[int],
                               read_pos: int, run_len: int,
                               local: bool = False) -> Optional[float]:
    """poly 区峰幅 / 主峰幅 — 峰压缩程度（重复数计数可靠性）

    分子：run 各峰在峰窗内取 max（A1：PLOC 与真实峰顶常差 1-2 采样点，
    单点采样会系统性读低弱信号区峰高）后取中位数。
    分母：默认全 read 主峰幅中位数；local=True（B1）改取 run 中间峰附近
    ±4 个有效峰的滑动中位数 H(run)——信号沿 read 衰减时，全局分母会把
    read 后段的正常 poly 误判为压缩（前/后段同 poly 的比值有系统性位置
    差）；有效峰 < 5 个时退回全局口径。
    返回 None 表示无法评估（视为可靠）。
    """
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or not peaks or read_pos is None or read_pos < 1:
        return None
    heights = peak_heights(trace, peaks)
    i0 = read_pos - 1
    i1 = min(read_pos - 1 + max(run_len, 1), len(heights))
    if i0 >= len(heights):
        return None
    run_h = [h for h in heights[i0:i1] if h > 0]
    all_idx = [i for i, h in enumerate(heights) if h > 0]
    if not run_h or not all_idx:
        return None
    med_all = sorted(heights[i] for i in all_idx)[len(all_idx) // 2]
    denom = med_all
    if local and len(all_idx) >= 5:
        mid = (i0 + i1 - 1) // 2
        mi = min(range(len(all_idx)), key=lambda k: abs(all_idx[k] - mid))
        near = [heights[all_idx[k]] for k in range(max(0, mi - 4), min(len(all_idx), mi + 5))]
        denom = sorted(near)[len(near) // 2]
    if denom <= 0:
        return None
    return round(sorted(run_h)[len(run_h) // 2] / denom, 2)


def _poly_peak_count(trace: Dict[str, List[int]], peaks: List[int],
                     read_pos: int, run_len: int) -> Optional[int]:
    """poly 窗口内原始 trace 可分辨峰个数 — 独立于 basecaller 的重复数证据

    basecaller 在长同聚物区会整段多读/少读碱基（Thermo Fisher：>8-9 个连续
    同碱基即出现 n±1），只看调用碱基无法发现。本函数在 read_pos 起 run_len 个
    调用碱基对应的 trace 区间（两侧各延一个峰间距）内直接数局部极大峰：
    高度 ≥ 窗口中位峰高的 30%，间隔 ≥ 中位峰间距的一半。峰合并（压缩）使
    计数偏少，滑移肩峰使其偏多。采样密度不足（合成/退化数据）返回 None。
    """
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or not peaks or read_pos is None or read_pos < 1 or run_len < 1:
        return None
    i0 = read_pos - 1
    i1 = min(read_pos - 1 + run_len - 1, len(peaks) - 1)
    if i0 >= len(peaks) or i1 < i0:
        return None
    s0, s1 = peaks[i0], peaks[i1]
    if s0 is None or s1 is None or s1 < s0:
        return None
    spacing = (s1 - s0) / (i1 - i0) if i1 > i0 else 0.0
    if spacing < 2:
        return None  # 每碱基不足 2 个采样点，无法独立于调用结果数峰
    pad = max(1, int(spacing // 4))  # 覆盖峰顶抖动，不外扩到相邻碱基的峰

    def _h(p: int) -> int:
        return max((trace[b][p] for b in "ACGT" if b in trace and 0 <= p < len(trace[b])),
                   default=0)

    lo, hi = max(0, s0 - pad - 1), min(n - 1, s1 + pad + 1)
    hs = [_h(p) for p in range(lo, hi + 1)]
    pos_h = sorted(x for x in hs if x > 0)
    if not pos_h:
        return None
    thr = pos_h[len(pos_h) // 2] * POLY_PEAK_HEIGHT_RATIO
    min_dist = max(2, int(spacing / 2))
    count = 0
    last = -(10 ** 9)
    for k in range(1, len(hs) - 1):
        y = hs[k]
        if y < thr or y < hs[k - 1] or y <= hs[k + 1]:
            continue
        if k - last >= min_dist:
            count += 1
            last = k
    return count


_COMPLEMENT = {"A": "T", "T": "A", "G": "C", "C": "G"}


def _aligned_run_window(aln: Dict, cs: int, ce: int,
                        base: str) -> Optional[Tuple[int, int, int]]:
    """从逐列对齐提取参考区间 [cs,ce] 对应的 read 峰窗与段内调用数

    返回 (i0, i1, called)：i0/i1 为 read 原始方向 0-based 索引（峰窗两端），
    called 为窗口内与参考链 run 碱基一致的调用数（read_aligned 一律按参考
    方向展示，正反 read 通用）。线性偏移映射在 read 带上游 indel 时错位、
    且反向 read 会数错互补碱基（表现为调用 0），逐列对齐两种问题都避免。
    区间内无对齐列（对齐视图缺失/退化）时返回 None。
    """
    aligned = aln.get("aligned") or {}
    ref_s = (aligned.get("ref_aligned") or "").upper()
    read_s = (aligned.get("read_aligned") or "").upper()
    if not ref_s or len(ref_s) != len(read_s):
        return None
    ref_pos = int(aligned.get("ref_start") or aln["ref_start"])
    L = _circ_len(aln)  # 环状跨原点：展开坐标折回后再与 [cs,ce] 比较
    total = sum(1 for c in read_s if c != "-")
    first = last = None
    called = 0
    idx = 0  # 参考方向的 read 碱基序号（非 gap 才前进）
    for k, rch in enumerate(ref_s):
        b = read_s[k]
        if b != "-":
            if rch != "-":
                cur = fold_pos(ref_pos, L) if L else ref_pos
                ref_pos += 1
                if cs <= cur <= ce:
                    if first is None:
                        first = idx
                    last = idx
                    if b == base:
                        called += 1
            idx += 1
    if first is None:
        return None
    if aln.get("direction") == "-":
        # read_aligned 为参考方向，原始 read（trace/peaks）方向相反
        first, last = total - 1 - last, total - 1 - first
    return first, last, called


def _pos_ranges_str(positions: List[int], max_ranges: int = 6) -> str:
    """位点列表压缩成连续区段（如 81–140、201–260）：结论给范围概览，
    超出 max_ranges 段截断并注明总段数，逐位细节在峰图里核对"""
    if not positions:
        return ""
    spans: List[List[int]] = []
    for p in positions:
        if spans and p == spans[-1][1] + 1:
            spans[-1][1] = p
        else:
            spans.append([p, p])
    parts = [f"{a}–{b}" if b > a else str(a) for a, b in spans[:max_ranges]]
    out = "、".join(parts)
    if len(spans) > max_ranges:
        out += f" 等 {len(spans)} 段"
    return out


def _read_ref_maps(r: Dict, fold: bool = True) -> Tuple[Dict[int, int], Dict[int, Dict]]:
    """单条 read 的两张坐标映射（基于 alignment_view 的参考方向逐列视图）：

    - read2ref: 原始电泳 read 坐标(1-based, 修剪后) -> 参考坐标(1-based)；
      插入列无参考坐标，不入表
    - ref2call: 参考坐标 -> {base, q, read_pos}（base 为参考方向判读碱基，
      q 为该列 Q 值；同位点多次比对列取首列）

    反向 read 的 query 是 revcomp：query 列索引 qi ↔ 原始电泳坐标 n-qi
    （1-based，与 aligner 的 read_pos 镜像同式），mixed_detail 的 pos 即
    原始电泳坐标，经此表即可与参考坐标互换。

    终审 A-20：局部比对会把 read 端部的 junk/低质量段软剪切掉——对齐内
    相对列号不是 trimmed read 坐标，必须加上 query_start 偏移（aligner
    已输出对齐块在原始 query 内的 1-based 起止）。

    环状参考跨原点的 read：逐列视图沿展开坐标（可 > L）递增，fold=True
    时两张表的参考坐标折回 1..L；fold=False 保留展开坐标（单调，供
    线性内插用）。"""
    aligned = (r.get("alignment") or {}).get("aligned") or {}
    ref_s = (aligned.get("ref_aligned") or "").upper()
    read_s = (aligned.get("read_aligned") or "").upper()
    if not ref_s or len(ref_s) != len(read_s):
        return {}, {}
    aln = r.get("alignment") or {}
    L = _circ_len(aln) if fold else 0
    direction = aln.get("direction", "+")
    n = len(r.get("trimmed_bases") or "")
    q_aligned = aligned.get("q_aligned") or []
    ref_pos = int(aligned.get("ref_start") or aln.get("ref_start") or 1)
    # 对齐块在原始（修剪后）read 内的 1-based 起点：正向 = query_start；
    # 反向 read 的 query 是 revcomp，对齐块第 0 列对应原始电泳的 query_end 位置
    qs = aln.get("query_start")
    qe = aln.get("query_end")
    if qs is not None and qe is not None:
        block_first = int(qs) if direction == "+" else int(qe)
    else:
        block_first = 1  # 旧记录回退（无软剪切时恰为 1）
    read2ref: Dict[int, int] = {}
    ref2call: Dict[int, Dict] = {}
    qi = -1
    for i, rb in enumerate(ref_s):
        qb = read_s[i]
        if qb != "-":
            qi += 1
            # 原始电泳 1-based 坐标：正向从 block_first 递增；
            # 反向 read 对齐块第 0 列位于 block_first（=query_end），向左递减
            orig = (block_first - qi) if direction == "-" else (block_first + qi)
        else:
            orig = None
        if rb != "-":
            if qb != "-" and orig is not None:
                rp = fold_pos(ref_pos, L) if L else ref_pos
                read2ref[orig] = rp
                if rp not in ref2call:
                    ref2call[rp] = {
                        "base": qb,
                        "q": q_aligned[i] if i < len(q_aligned) else 0,
                        "read_pos": orig,
                    }
            ref_pos += 1
    return read2ref, ref2call


def _cross_region_quality(r2c: Dict[int, Dict], lo: int, hi: int, L: int,
                          zone: Optional[Dict]) -> Optional[Dict]:
    """验证 read 在参考区间 [lo-HALF, hi+HALF] 的区域质量。

    区域截掉验证 read 可信区之外的部分与未覆盖列后统计：平均 Q、
    最低 Q、≥Q20 占比、列数；核心区（位点 ±CROSS_CORE_HALF）任一列缺失或
    落进不可信区返回 None——位点本身都测不好，谈不上反证。"""
    def call(p: int) -> Optional[Dict]:
        c = r2c.get(fold_pos(p, L) if L else p)
        if not c or not in_zone(zone, c["read_pos"]):
            return None
        return c
    core: List[int] = []
    for p in range(lo - CROSS_CORE_HALF, hi + CROSS_CORE_HALF + 1):
        c = call(p)
        if c is None:
            return None
        core.append(int(c["q"] or 0))
    qs: List[int] = []
    for p in range(lo - CROSS_REGION_HALF, hi + CROSS_REGION_HALF + 1):
        c = call(p)
        if c is not None:
            qs.append(int(c["q"] or 0))
    return {
        "region_mean_q": round(sum(qs) / len(qs), 1),
        "region_min_q": min(qs),
        "region_hq_frac": round(sum(1 for q in qs if q >= CROSS_CHECK_MIN_Q) / len(qs), 3),
        "region_cols": len(qs),
        "core_min_q": min(core),
    }


def _region_ok(rq: Optional[Dict]) -> bool:
    return (rq is not None
            and rq["region_cols"] >= CROSS_REGION_MIN_COLS
            and rq["region_mean_q"] >= CROSS_REGION_MEAN_Q
            and rq["region_hq_frac"] >= CROSS_REGION_HQ_FRAC
            and rq["core_min_q"] >= CROSS_CHECK_MIN_Q)


def region_quality_text(ev: Dict) -> str:
    """验证 read 区域质量的文字（结论/悬停共用）"""
    return (f"该区 {ev.get('region_cols', '?')}bp 平均 Q{ev['region_mean_q']:g}、"
            f"≥Q20 占 {round(ev.get('region_hq_frac', 0) * 100)}%")


def _clean_cover_reads(v: Dict, read_results: List[Dict],
                       ref2call_by_read: Dict[str, Dict[int, Dict]],
                       keys_by_read: Dict[str, set],
                       weak: Optional[List[Dict]] = None) -> List[Dict]:
    """跨 read 干净覆盖反证：哪些其他 read 完整跨过该 indel 的 junction
    （且在质量可靠区内）却未报该 indel。

    clonal 样品中真实存在的变异会被每条覆盖该位的 read 独立报出；仅单
    read 报告、而另一条 read 干净跨过同一位点，是滑移/比对补偿伪影的
    高发形态（短同聚物边缘的 1bp 插入尤其常见，poly 机制只覆盖 ≥20bp
    的 run，更短的 run 落在空白区）。junction 落在验证 read 可信区之外
    或 Q 过低时，"未报"不构成反证；该 read 自己在此区域报了其他 indel
    时既不算支持也不算干净（它反对这个 call，但同样不支持参考序列）。
    """
    anchor = int(v["ref_pos"])
    vlen = max(1, int(v.get("length") or 1))
    if v.get("type") == "insertion":
        # 插入点在 anchor 与 anchor+1 之间：read 无此插入时两位恰好相邻
        junctions = [(anchor, anchor + 1)]
        win = (anchor - 1, anchor + 1)
    else:
        # 缺失 anchor..anchor+vlen-1：跨过即左翼 anchor-1 与右翼 anchor+vlen
        # 相邻；缺失紧贴 read 覆盖起点时右翼侧 junction 亦可
        junctions = [(anchor - 1, anchor + vlen),
                     (anchor + vlen, anchor + vlen + 1)]
        win = (anchor - 1, anchor + vlen + 1)
    by_name = {r["filename"]: r for r in read_results}
    src = by_name.get(v.get("read", ""))
    L = _circ_len(src["alignment"]) if src is not None else 0
    if L:
        # 环状参考：原点两侧（L 与 1）相邻，junction 坐标按环折回
        junctions = [(fold_pos(a, L), fold_pos(b, L)) for a, b in junctions]
    hits: List[Dict] = []
    for fname, r2c in ref2call_by_read.items():
        if fname == v.get("read"):
            continue
        # 验证 read 自己在该区域报了任何差异：区域不干净，不作反证
        if any(win[0] - CROSS_CLEAN_HALF <= k[0] <= win[1] + CROSS_CLEAN_HALF
               for k in keys_by_read.get(fname, ())):
            continue
        vzone = by_name[fname].get("reliable_zone")
        for a, b in junctions:
            c0, c1 = r2c.get(a), r2c.get(b)
            if not c0 or not c1 or abs(c0["read_pos"] - c1["read_pos"]) != 1:
                continue
            rq = _cross_region_quality(r2c, min(win), max(win), L, vzone)
            if not _region_ok(rq):
                if rq is not None and weak is not None:
                    weak.append({"read": fname, **rq})
                continue
            hits.append({"read": fname, **rq})
            break
    hits.sort(key=lambda h: -h["region_mean_q"])
    return hits


def _clean_cover_reads_sub(v: Dict, read_results: List[Dict],
                           ref2call_by_read: Dict[str, Dict[int, Dict]],
                           keys_by_read: Dict[str, set],
                           mixed_by_read: Dict[str, set],
                           weak: Optional[List[Dict]] = None) -> List[Dict]:
    """替换的跨 read 反证：哪些其他 read 在该位点（多碱基替换为整段）
    以可靠质量判读出参考碱基、峰形单一且自身未报差异。

    与 indel 反证同口径：克隆样品中真实的替换会被每条覆盖该位的 read
    独立读出；另一条引物在信号可靠区干净地读出参考碱基，说明这处差异
    是源 read 自己的判读误差。该 read 在此位点报了其他差异、落在其
    可信区之外、Q 不足或本身双峰时不构成反证。"""
    pos0 = int(v["ref_pos"])
    ref_b = (v.get("ref_base") or "").upper()
    if not ref_b or any(c not in "ACGT" for c in ref_b):
        return []
    by_name = {r["filename"]: r for r in read_results}
    src = by_name.get(v.get("read", ""))
    L = _circ_len(src["alignment"]) if src is not None else 0
    positions = [pos0 + k for k in range(len(ref_b))]
    if L:
        positions = [fold_pos(p, L) for p in positions]
    hits: List[Dict] = []
    lo, hi = pos0, pos0 + len(ref_b) - 1
    for fname, r2c in ref2call_by_read.items():
        if fname == v.get("read"):
            continue
        if any(lo - CROSS_CLEAN_HALF <= k[0] <= hi + CROSS_CLEAN_HALF
               for k in keys_by_read.get(fname, ())):
            continue
        vzone = by_name[fname].get("reliable_zone")
        mixed = mixed_by_read.get(fname, set())
        ok = True
        for p, rb in zip(positions, ref_b):
            c = r2c.get(p)
            if (not c or (c.get("base") or "").upper() != rb
                    or c["read_pos"] in mixed):
                ok = False
                break
        if not ok:
            continue
        rq = _cross_region_quality(r2c, lo, hi, L, vzone)
        if not _region_ok(rq) and rq is not None and weak is not None:
            weak.append({"read": fname, **rq})
        if _region_ok(rq):
            hits.append({"read": fname, **rq})
    hits.sort(key=lambda h: -h["region_mean_q"])
    return hits


def _corroborate_mixed(read_results: List[Dict]) -> Dict:
    """跨 read 双峰互检：一条引物的双峰位点映射到参考坐标后，看其他引物
    在同一位点的表现——用户视角的关键一层：另一条引物测过且峰形单一、
    判读碱基一致 → 多半是这条 read 自己的信号噪声（常见于引物首端）；
    其他引物同样报双峰 → 倾向真实混合；无其他引物覆盖 → 无法互检。

    返回 {by_read: {filename: {clean, multi, uncovered, multi_ref, clean_ref,
    uncovered_ref}},
          multi_sites: [(ref_pos, [filenames])], total, clean_total,
          multi_total, uncov_total}；单条 read 的分析互检无意义，字段照常
          返回（clean/multi 均为 0），由结论层判断是否展示。"""
    maps = [(r, *_read_ref_maps(r)) for r in read_results]
    # 环状跨原点 read 的折回坐标在原点处不单调，内插用展开坐标再折回
    unfolded = {r["filename"]: (_read_ref_maps(r, fold=False)[0], _circ_len(r["alignment"]))
                for r in read_results if _circ_len(r.get("alignment") or {})}

    def _approx(r: Dict, r2ref: Dict[int, int], pos: int) -> Optional[int]:
        u = unfolded.get(r["filename"])
        if u is None:
            return _approx_ref(r2ref, pos)
        rp = _approx_ref(u[0], pos)
        return fold_pos(rp, u[1]) if rp is not None else None

    def _approx_ref(r2ref: Dict[int, int], pos: int) -> Optional[int]:
        """位点在 read2ref 查不到时的近似定位。局部比对会在 read 端部留
        悬空（poly-A 同聚物区两端尤其常见），悬空段的双峰位点若直接归
        "待核"，会把实际被邻居 read 覆盖、可互检的位点误报成无法互检。
        以比对块两端（原始 read 坐标 ↔ 参考坐标）为锚点线性内插/外推
        ——Sanger read 与参考共线，锚点间斜率近似 ±1（反向 read 同式）"""
        if not r2ref:
            return None
        k_lo, k_hi = min(r2ref), max(r2ref)
        if k_hi == k_lo:
            return r2ref[k_lo]
        return round(r2ref[k_lo] + (pos - k_lo) * (r2ref[k_hi] - r2ref[k_lo])
                     / (k_hi - k_lo))

    # 各 read 报了双峰的参考坐标集合（互检时判"同报双峰"用）
    mixed_ref_by_read: Dict[str, set] = {}
    for r, r2ref, _c in maps:
        s = set()
        for e in r.get("mixed_detail") or []:
            rp = r2ref.get(e["pos"])
            if rp is None:
                rp = _approx(r, r2ref, e["pos"])
            if rp is not None:
                s.add(rp)
        mixed_ref_by_read[r["filename"]] = s

    by_read: Dict[str, Dict] = {}
    multi_sites: Dict[int, List[str]] = {}
    for r, r2ref, _c in maps:
        fname = r["filename"]
        clean: List[int] = []
        multi: List[int] = []
        uncovered: List[int] = []
        uncovered_ref: List[int] = []
        for e in r.get("mixed_detail") or []:
            rp = r2ref.get(e["pos"])
            if rp is None:
                rp = _approx(r, r2ref, e["pos"])
                if rp is None:
                    uncovered.append(e["pos"])
                    continue
            others_multi = [f for f, s in mixed_ref_by_read.items()
                            if f != fname and rp in s]
            if others_multi:
                multi.append(e["pos"])
                multi_sites.setdefault(rp, []).append(fname)
                continue
            # 其他引物在该位点峰形单一（自己没报双峰）且判读碱基一致、
            # Q≥20 → 判"被覆盖复核"
            ok = False
            for o, _o2ref, ocall in maps:
                if o["filename"] == fname:
                    continue
                call = ocall.get(rp)
                if call and call["base"] == _c.get(rp, {}).get("base") \
                        and call["q"] >= 20 \
                        and rp not in mixed_ref_by_read[o["filename"]]:
                    ok = True
                    break
            (clean if ok else uncovered).append(e["pos"])
            if not ok:
                uncovered_ref.append(rp)
        by_read[fname] = {
            "clean": len(clean), "multi": len(multi), "uncovered": len(uncovered),
            "clean_ref": sorted({r2ref[p] for p in clean if p in r2ref}),
            "multi_ref": sorted({r2ref[p] for p in multi if p in r2ref}),
            "uncovered_ref": sorted(set(uncovered_ref)),
        }
    total = sum(d["clean"] + d["multi"] + d["uncovered"] for d in by_read.values())
    return {
        "by_read": by_read,
        "multi_sites": sorted(multi_sites.items()),
        "total": total,
        "clean_total": sum(d["clean"] for d in by_read.values()),
        "multi_total": sum(d["multi"] for d in by_read.values()),
        "uncov_total": sum(d["uncovered"] for d in by_read.values()),
    }


def _mixed_check_phrase(chk: Dict) -> str:
    """单条 read 互检摘要短语（仅统计非零项，全零返回空串）"""
    parts = []
    if chk["multi"]:
        parts.append(f"{chk['multi']} 处其他引物同报双峰（倾向真实混合）")
    if chk["clean"]:
        parts.append(f"{chk['clean']} 处其他引物覆盖且峰形单一（倾向噪声）")
    if chk["uncovered"]:
        ref_txt = _pos_ranges_str(chk.get("uncovered_ref") or [])
        parts.append(f"{chk['uncovered']} 处无其他引物覆盖待核"
                     + (f"（参考位置 {ref_txt}）" if ref_txt else ""))
    return "互检：" + "、".join(parts) if parts else ""


def _run_label(run: Dict) -> str:
    """重复结构标签：period=1 为 poly(A) 同聚物；period>1 为 (CAG)n 型重复"""
    if run.get("period", 1) == 1 and run.get("base"):
        return f"poly({run['base']}) 同聚物"
    unit = run.get("unit") or ""
    if unit:
        return f"({unit}){run.get('ref_repeat_count', '?')} 重复"
    return "重复序列"


def _peak_verdict_phrase(e: Dict) -> str:
    """峰图主判读短语（以可分辨峰为准，basecaller 调用数只在逐 read 里出现）"""
    ref = e["ref_repeat_count"]
    m = e.get("peak_measured")
    if m is None:
        ci = e.get("length_ci")
        est = f"宽度法估计 {e.get('length_estimate')} 个"
        if ci:
            est += f"（区间 {ci[0]}–{ci[1]}）"
        return f"峰图计数不可用，{est}"
    if e.get("peak_missing"):
        return f"缺失 {e['peak_missing']} 个 {e['base']}：实测 {m} 个，参考 {ref} 个"
    if e.get("peak_inserted"):
        return f"插入 {e['peak_inserted']} 个 {e['base']}：实测 {m} 个，参考 {ref} 个"
    return f"poly({e['base']}) 碱基类型完整：实测 {m} 个，参考 {ref} 个"


# 锚定分级劣序（rank 越大越差）：取投票 read 的最差分级用
_ANCHOR_RANK = {"reliable": 0, "one-sided": 1, "marginal": 2, "unreliable": 3}


def _poly_anchor_notes(homopolymer_report: List[Dict]) -> List[str]:
    """accepted 但投票 read 的锚定分级不可靠的 run → 「计数确证但定位存疑」

    run_verdict=accepted 只由可分辨峰计数互证产生，确证的是「重复数」；
    run 起止定位依赖两侧路标碱基（锚），锚踩在信号异常区（边缘/低 Q/
    混合峰/压缩区）时位置仍可能整体偏移。此前该信息只藏在逐 read 的
    anchor_grade 字段里，结论不体现——用户容易误以为位置也确证了。
    """
    notes: List[str] = []
    for e in homopolymer_report:
        if e.get("run_verdict") != "accepted":
            continue
        g = e.get("vote_anchor_grade") or "reliable"
        if _ANCHOR_RANK.get(g, 1) < _ANCHOR_RANK["marginal"]:
            continue
        reason = ("路标落在信号异常区" if g == "unreliable"
                  else "路标处于信号边缘或单侧锚定")
        notes.append(
            f"注意：{_run_label(e)} {e['start']}-{e['end']} 重复数已由峰图"
            f"计数确证（{e.get('observed_repeat_count', '?')} 个），但该 run"
            f"的锚定路标不可靠（{reason}）——计数确证但定位存疑，run 起止"
            "可能整体偏移数 bp，建议对照图谱核对边界")
    return notes


def _anchor_quality(r: Dict, pos: int) -> Tuple[str, List[str]]:
    """单个路标碱基的信号可靠度分级（run 定位置信度 = 两侧路标中较差者）

    pos 为 1-based 原始 read 碱基序号（路标在 poly run 外侧紧邻处）。风险
    逐项检查（用户点出的关键：锚可靠性「借自」路标碱基质量，路标本身落在
    信号异常区时整段定位悄悄出错）：
    - read 可信区之外（首尾信号爬升/衰减区）→ 边缘
    - QV 修剪边界外（basecaller 低置信）→ 边缘
    - 路标落在另一 poly/重复结构内部（撞上第三种歧义）→ 边缘
    - poly 下游 DROPOUT_WINDOW_BASES 窗内且骤降未恢复 → 不可靠
    - 混合峰位点（次级通道可见的异常）→ 不可靠
    - 路标峰与相邻峰间距 < 0.6×中位峰距（同通道压缩合并）→ 不可靠
    返回 ("reliable"|"marginal"|"unreliable", 原因列表)。
    """
    issues: List[str] = []
    marginal: List[str] = []
    unreliable: List[str] = []
    off = pos - 1                      # 0-based 原始 read 坐标
    trim_start = r.get("trim_start") or 0
    n_trim = len(r["trimmed_bases"])
    ti = off - trim_start              # 0-based 修剪后坐标（与 mixed/dropout 同系）
    if r.get("reliable_zone") and not in_zone(r["reliable_zone"], ti + 1):
        marginal.append("read 可信区之外（首尾信号爬升/衰减区）")
    if ti < 0 or ti >= n_trim:
        marginal.append("QV 修剪边界外")
    if any(e["pos"] == pos for e in r.get("mixed_detail") or []):
        unreliable.append("混合峰位点")
    for d in r.get("post_poly_dropouts") or []:
        d0 = d.get("read_pos")
        if d0 is not None and d0 < ti <= d0 + DROPOUT_WINDOW_BASES \
                and not d.get("end_truncation"):
            unreliable.append("poly 下游信号骤降区")
            break
    # 路标峰与相邻峰合并（压缩）：峰距显著小于全 read 中位峰距时峰型不可
    # 分辨，路标本身可能被错位调用/挪位——整段定位会悄悄偏移。混合峰检查
    # 只覆盖"次级通道可见"的形态，这里补上"同通道压缩合并"的形态
    tp = r.get("trimmed_peaks") or []
    if 0 <= ti < len(tp) and tp[ti] is not None:
        _gaps = sorted(b - a for a, b in zip(tp, tp[1:])
                       if a is not None and b is not None and b > a)
        _med = _gaps[len(_gaps) // 2] if _gaps else 0
        if _med >= 2:
            _near = None
            if ti > 0 and tp[ti - 1] is not None and tp[ti] > tp[ti - 1]:
                _near = tp[ti] - tp[ti - 1]
            if ti + 1 < len(tp) and tp[ti + 1] is not None and tp[ti + 1] > tp[ti]:
                _near = min(_near, tp[ti + 1] - tp[ti]) if _near else tp[ti + 1] - tp[ti]
            if _near is not None and _near < 0.6 * _med:
                unreliable.append("路标峰与相邻峰合并（压缩区）")
    issues = marginal + unreliable
    if unreliable:
        return "unreliable", issues
    if marginal:
        return "marginal", issues
    return "reliable", issues


def _read_run_verdict(called: Optional[int],
                      pc: Optional[int], est: Optional[Dict],
                      anchor_grade: str,
                      anchor_issues: List[str]) -> Dict:
    """单条 read 对一个 poly run 的判读（Step A，先单条分析）

    五档 resolution：
    - resolvable：可分辨峰数 == 调用数（峰图证据完整，有投票权）；
    - deficit：0 < 峰数 < 调用数——可见缺失证据（B4 口径：峰只会被压缩
      合并、不会凭空多出，段内可分辨峰即实测），主判读以峰图为准；
    - merged：峰完全合并（pc==0）或宽度法救回（峰数 < 调用数且 B2 判定
      合并形态）——该 read 对此 run「不可判定」，峰窗内逐位双峰不作证据；
    - noisy：峰数 > 调用数（疑似滑移肩峰），计数不可作证据；
    - no_evidence：峰计数不可用（采样密度不足，如稀疏合成数据）——无矛盾
      证据，维持调用数（caller_only），不做任何翻判。
    定位置信度 = 锚分级：锚不可靠时 position_confidence 降为 marginal。
    """
    if called is None:
        resolution, why = "unavailable", "覆盖段无法提取"
    elif pc is None:
        resolution, why = "no_evidence", "峰图计数不可用（采样密度不足）"
    elif pc == 0:
        resolution, why = "merged", "峰完全合并，无可分辨峰"
    elif pc == called:
        resolution, why = "resolvable", ""
    elif pc < called and est is not None \
            and est.get("method") in ("width", "second_derivative"):
        resolution, why = "merged", f"峰合并（宽度法救回，可见 {pc} < 调用 {called}）"
    elif pc < called:
        resolution, why = "deficit", f"可见峰 {pc} < 调用 {called}（段内缺失/部分合并）"
    else:
        resolution, why = "noisy", f"峰数 {pc} > 调用 {called}（疑似滑移肩峰）"
    verdict: Dict = {
        "resolution": resolution,
        "reason": why,
        "called_count": called,
        "peak_count": pc,
        "length_estimate": est["n"] if est else None,
        "length_ci": ([est["ci_low"], est["ci_high"]] if est else None),
        "anchor_grade": anchor_grade,
        "anchor_issues": anchor_issues,
        "position_confidence": (
            "marginal" if anchor_grade != "reliable" else "reliable"),
    }
    if est is not None and resolution == "merged":
        # 宽度法是模型估计而非可见证据：保留数值供参考，但明确它不是证据
        verdict["width_estimate_only"] = True
    return verdict


def _anchor_verdict_for_read(r: Dict, run: Dict, aln: Dict) -> Tuple[str, List[str]]:
    """run 两侧路标碱基的可靠度综合（定位可靠度 = 两侧路标中最差者）

    路标 = 原始 read 上紧邻 run 连续同碱基段外侧的第一位非重复碱基——
    它钉死 run 的起止坐标，把「可滑动」变「可计数」；路标自身踩在信号
    异常区（边缘/低 Q/混合峰/骤降）时，整段定位会悄悄出错，必须降级。
    返回 (grade, issues)：grade ∈ reliable / marginal / unreliable /
    one-sided（单侧锚定或两侧均无路标）。"""
    b_read = run["base"] if aln["direction"] == "+" else _COMPLEMENT[run["base"]]
    win = _aligned_run_window(aln, run["start"], run["end"], run["base"])
    if win is None:
        return "unreliable", ["run 窗口无法定位"]
    i0, i1, _ = win
    s_off = r.get("trim_start") or 0
    raw = (r.get("raw_bases") or "").upper()
    if not raw:
        return "unreliable", ["原始碱基串缺失"]
    r0, r1 = s_off + i0, s_off + i1
    while r0 > 0 and raw[r0 - 1] == b_read:
        r0 -= 1
    while r1 < len(raw) - 1 and raw[r1 + 1] == b_read:
        r1 += 1
    grades: List[str] = []
    issues: List[str] = []
    if r0 > 0 and raw[r0 - 1] not in (b_read, "N"):
        g, iss = _anchor_quality(r, r0)          # 左路标：raw 0-based r0-1 → 1-based r0
        grades.append(g)
        issues += [f"左路标：{x}" for x in iss]
    if r1 < len(raw) - 1 and raw[r1 + 1] not in (b_read, "N"):
        g, iss = _anchor_quality(r, r1 + 2)      # 右路标：raw 0-based r1+1 → 1-based r1+2
        grades.append(g)
        issues += [f"右路标：{x}" for x in iss]
    if not grades:
        return "one-sided", ["两侧均无路标（read 截断在 run 内）"]
    order = {"reliable": 0, "marginal": 1, "unreliable": 2}
    worst = max(grades, key=lambda g: order[g])
    if len(grades) == 1 and worst == "reliable":
        return "one-sided", issues + ["单侧锚定"]
    return worst, issues


def _normalize_indel(ref: str, v: Dict) -> Dict:
    """indel 归一化为最左最简表示（Tan 2015，bcftools norm/GATK 同款算法）

    同一 indel 在重复/同聚物区可被比对到多个等价位置：不同 read（或不同
    basecaller）报告的 anchor 常差 1-3bp，不归一化则无法按 key 合并印证，
    支持数被人为分裂。左移不改变其序列效果（数学等价），替换变体原样返回。
    """
    if v.get("type") == "insertion":
        seq = v["alt_base"].upper()
        pos = int(v["ref_pos"])  # anchor：插入点左侧参考位置（1-based）
        # 等价左移：插入点右侧第一位参考碱基 == 插入序列末位 → 整体左移一位
        while pos >= 1 and pos < len(ref) and ref[pos - 1] == seq[-1]:
            seq = seq[-1] + seq[:-1]
            pos -= 1
        return {**v, "ref_pos": pos, "alt_base": seq}
    if v.get("type") == "deletion":
        pos = int(v["ref_pos"])
        length = int(v.get("length") or 1)
        seq = ref[pos - 1:pos - 1 + length]
        # 等价左移：删除区间的上一位参考碱基 == 删除序列末位 → 整体左移一位
        while pos >= 2 and ref[pos - 2] == seq[-1]:
            seq = ref[pos - 2] + seq[:-1]
            pos -= 1
        return {**v, "ref_pos": pos}
    return v


def _normalize_indel_all(ref: str, variants: List[Dict]) -> List[Dict]:
    return [_normalize_indel(ref, v) for v in variants]


def _variant_key(v: Dict) -> Tuple[int, str, str, int]:
    """归一化后的合并 key：同一 indel 无论被哪个 caller/read 报在哪一等价位都聚合。

    长度必须入 key：同一锚点上长度不同的删除（如 polyA 少 2 个 vs 少 3 个）
    是不同的构建体，不能合并——否则 support_reads 虚增、长度取决于 read 顺序。
    """
    return (int(v["ref_pos"]), v.get("type", ""), v.get("alt_base", "").upper(),
            int(v.get("length") or 1))


_TRACY_VERSION_CACHE: Dict[str, Optional[Tuple[int, ...]]] = {}


def _tracy_version(tracy: str) -> Optional[Tuple[int, ...]]:
    """`tracy --version` → (major, minor, patch)；无法判定返回 None（按进程缓存）"""
    if tracy in _TRACY_VERSION_CACHE:
        return _TRACY_VERSION_CACHE[tracy]
    ver = None
    try:
        proc = subprocess.run([tracy, "--version"], capture_output=True,
                              text=True, timeout=10)
        m = re.search(r"version:?\s*v?(\d+)\.(\d+)\.(\d+)",
                      (proc.stdout or "") + (proc.stderr or ""), re.I)
        if m:
            ver = tuple(int(x) for x in m.groups())
    except (OSError, subprocess.SubprocessError):
        ver = None
    _TRACY_VERSION_CACHE[tracy] = ver
    return ver


def _parse_tracy_json(text: str) -> Optional[Tuple[str, List[int], Dict]]:
    """解析 tracy basecall -f json；结构不符（版本漂移）返回 None

    字段（tracy v0.3.10+，src/json.h）：primarySeq / secondarySeq 等长，
    secondary 与 primary 相同为单峰，否则为其余过阈值碱基的 IUPAC 码；
    basecallQual 为逐碱基 Q，basecallPos 为峰在 trace 上的 1-based 采样坐标。"""
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    prim, sec = d.get("primarySeq"), d.get("secondarySeq")
    qual, pos = d.get("basecallQual"), d.get("basecallPos")
    if not (isinstance(prim, str) and isinstance(sec, str)
            and isinstance(qual, list) and isinstance(pos, list)):
        return None
    n = len(prim)
    if n == 0 or len(sec) != n or len(qual) < n or len(pos) < n:
        return None
    try:
        q = [int(x) for x in qual[:n]]
        tp = [int(x) - 1 for x in pos[:n]]   # → 0-based，与 PLOC 峰坐标同系
    except (TypeError, ValueError):
        return None
    if any(x < 0 for x in q):
        return None
    prim, sec = prim.upper(), sec.upper()
    return prim, q, {"primary": prim, "secondary": sec, "positions": tp}


def _try_tracy_basecall(ab1_bytes: bytes):
    """tracy basecall 重新 basecall（现代 caller，对峰压缩区/indel 更强）

    tracy 是正式发表的 Sanger 分析工具（BMC Bioinformatics 2020），其 basecall
    与 ABI 内嵌 caller 相互独立——一致即为交叉印证，给出"独立第二意见"。
    以 -f json 运行，返回 (bases, quality, calls)：bases/quality 为 primary
    序列（与旧版 -f fastq 输出逐字相同），calls 带 secondary 次级调用与峰的
    trace 坐标，供混合峰印证。版本低于 TRACY_JSON_MIN_VERSION 或 JSON 结构
    不符时退回 -f fastq，返回 (bases, quality)。tracy 不可用或失败返回 None。
    """
    tracy = shutil.which(TRACY_BIN) or (TRACY_BIN if os.path.isfile(TRACY_BIN) else None)
    if not tracy:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "read.ab1")
            with open(src, "wb") as fh:
                fh.write(ab1_bytes)
            ver = _tracy_version(tracy)
            if ver is None or ver >= TRACY_JSON_MIN_VERSION:
                out = os.path.join(td, "bc.json")
                proc = subprocess.run(
                    [tracy, "basecall", "-f", "json", "-o", out, src],
                    capture_output=True, text=True, timeout=60,
                )
                if proc.returncode == 0 and os.path.isfile(out):
                    with open(out) as fh:
                        parsed = _parse_tracy_json(fh.read())
                    if parsed is not None:
                        bases, qual, calls = parsed
                        calls["version"] = ".".join(map(str, ver)) if ver else None
                        return bases, qual, calls
                    logger.info("tracy JSON 结构无法识别（版本 %s），退回 FASTQ", ver)
            return _tracy_basecall_fastq(tracy, src, td)
    except (OSError, subprocess.SubprocessError):
        return None


def _tracy_basecall_fastq(tracy: str, src: str, td: str) -> Optional[Tuple[str, List[int]]]:
    """primary-only 路径（旧版行为）：返回 (bases, quality)，失败 None"""
    out = os.path.join(td, "bc.fastq")
    proc = subprocess.run(
        [tracy, "basecall", "-f", "fastq", "-o", out, src],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0 or not os.path.isfile(out):
        return None
    with open(out) as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    # FASTQ 4 行：@primary / seq / + / qual（tracy 输出 primary 序列）
    if len(lines) < 4:
        return None
    bases = lines[1].upper()
    qual = [ord(c) - 33 for c in lines[3]]
    if not bases or len(qual) < len(bases) or not all(q >= 0 for q in qual[: len(bases)]):
        return None
    return bases, qual[: len(bases)]


def _phred_weight(q: float) -> float:
    """Phred Q → 投票权重：log10(3p/e)，e = 10^(-Q/10)、p = 1 - e

    即「该列为 read 所报碱基」相对「为另外三种之一」的对数似然比，独立
    read 的票直接相加就是各候选碱基的后验比较（phred/phrap 的口径）；
    Q 截在 [2, 60]，保证权重为正、极端 Q 不至于一票压死其余全部 read。"""
    q = min(60.0, max(2.0, float(q)))
    e = 10.0 ** (-q / 10.0)
    return math.log10(3.0 * (1.0 - e) / e)


def _column_quality(aln: Dict, L: int, circular: bool
                    ) -> Tuple[Dict[int, int], Dict[int, int]]:
    """由逐列对齐视图得到 read 在每个参考位点（0-based 投票下标）的 Q

    返回 (base_q, ins_q)：base_q 为参考列上的 read Q（read 缺失列取左右
    相邻 read 碱基 Q 的较小者）；ins_q 为插入碱基 Q 的均值，键为插入点
    右侧参考位点。视图缺失或与比对长度不符时返回空表（调用方用 mean_q）。"""
    av = aln.get("aligned") or {}
    ref_al = av.get("ref_aligned") or ""
    read_al = av.get("read_aligned") or ""
    qs = av.get("q_aligned") or []
    if not ref_al or len(qs) != len(ref_al) or len(read_al) != len(ref_al):
        return {}, {}
    n = len(ref_al)
    # read 缺失列没有自己的 Q：取左右最近 read 碱基 Q 的较小者
    left = [0] * n
    last = None
    for i in range(n):
        if read_al[i] != "-":
            last = qs[i]
        left[i] = last
    right = [0] * n
    last = None
    for i in range(n - 1, -1, -1):
        if read_al[i] != "-":
            last = qs[i]
        right[i] = last

    def idx(p: int) -> int:
        return (fold_pos(p, L) - 1) if circular else p - 1

    base_q: Dict[int, int] = {}
    ins_q: Dict[int, int] = {}
    pos = int(av.get("ref_start") or aln.get("ref_start") or 1)
    ins_buf: List[int] = []
    for i in range(n):
        if ref_al[i] == "-":
            ins_buf.append(qs[i])
            continue
        if ins_buf:
            ins_q[idx(pos)] = int(round(sum(ins_buf) / len(ins_buf)))
            ins_buf = []
        if read_al[i] == "-":
            flank = [x for x in (left[i], right[i]) if x is not None]
            base_q[idx(pos)] = min(flank) if flank else 0
        else:
            base_q[idx(pos)] = qs[i]
        pos += 1
    if ins_buf:
        ins_q[idx(pos)] = int(round(sum(ins_buf) / len(ins_buf)))
    return base_q, ins_q


# 模糊调用（N 与 IUPAC 简并码）：它们可以代表"该 read 在这里没读清"，
# 但不能压过另一条 read 干净读出的具体碱基（见 _pick_base_vote）
_AMBIGUOUS_BASES = frozenset("NRYSWKMBDHVU")


def _pick_base_vote(cell: Dict[str, float]) -> str:
    """多位点投票的胜出键：明确碱基优先于 N/IUPAC 模糊调用。

    单 read 覆盖时调用方根本不走这里（以该 read 为准，N 如实保留）。
    多个 read 打平时，只要有一条 read 给出具体碱基（ACGT 或插入/缺失），
    就选它在权重最高的那个；只有全场都是模糊调用时才让 N 胜出——
    否则"一条 read 读 N、另一条干净读出参考碱基"会让共识序列出现 N。
    插入（"+A"）与缺失（"-"）不是模糊调用，照常参与。"""
    definite = {k: w for k, w in cell.items() if k not in _AMBIGUOUS_BASES}
    return max((definite or cell).items(), key=lambda kv: kv[1])[0]


def _merged_zone_artifact(v: Dict, src: Optional[Dict]) -> bool:
    """变异是否落在源 read 的合并压缩窗内、且只靠单条 read 支撑。

    read 在那段同聚物区没有可分辨证据（逐位判定含 indel 锚点都不可信），
    所以单 read 报告一律降级；但跨 read 互证一致（support_reads >= 2）的
    变异不受影响——各 read 的证据是独立的，合并压缩只削弱其中一条。"""
    if src is None or v.get("confidence") == "low":
        return False
    if (v.get("support_reads") or 1) >= 2:
        return False
    positions = set()
    for zone in src.get("poly_merged_zones") or []:
        positions.update(zone.get("positions") or ())
    return v.get("read_pos") in positions


def _build_consensus(reference: str, read_results: List[Dict],
                     skip_keys: Optional[set] = None,
                     circular: bool = False) -> Dict:
    """按参考坐标逐位质量加权投票生成共识序列

    每个 read 在其覆盖的每个参考位点投一票，票投给它在该位点的观测：
    参考碱基，或该处变体（替换碱基 / 缺失 '-' / 插入 '+ALT'，插入记在插入点
    右侧位点）。票的权重取该列自身的 Q（aligned.q_aligned，经 _phred_weight
    由错误概率换算成对数似然比）——Sanger read 沿读长单调退化，后段低 Q
    误判不应与前段高 Q 正确调用同权；无逐列 Q 时退回 read 的 mean_q。
    单 read 覆盖的位点显式以该 read 为准（模拟人工核对）；多 read 覆盖时
    按列 Q 加权多数，孤立低质量差异被压制。

    skip_keys：{(ref_pos, type, alt_base)} 低置信变体集合。这些调用不写入
    共识——该 read 在此处按参考碱基投票：共识序列代表当前证据下的最佳猜测
    构建体，疑似测序噪声只在变体清单中列出供人工核对，不应固化进共识。

    circular=True：环状参考，跨原点 read 的展开坐标、跨原点的缺失与
    原点处（L 与 1 之间）的插入都按环折回，不再有「参考末端之后」的插入。
    """
    L = len(reference)

    def _idx(p: int) -> int:  # 1-based 参考坐标 → 0-based 投票下标（环状折回）
        return (fold_pos(p, L) - 1) if circular else p - 1

    votes: List[Dict[str, float]] = [{} for _ in range(L)]
    voters: List[List[str]] = [[] for _ in range(L)]   # 每位点各 read 的观测
    end_insert_votes: Dict[str, float] = {}  # 参考末端之后的插入（无右侧锚定位）

    for r in read_results:
        mean_q = float(r.get("mean_q") or 0)
        aln = r["alignment"]
        base_q, ins_q = _column_quality(aln, L, circular)
        observed: Dict[int, str] = {}      # 投票下标 → 该 read 的非参考观测
        obs_q: Dict[int, float] = {}       # 插入票的 Q（插入碱基自身的 Q）
        for v in aln["variants"]:
            if skip_keys and _variant_key(v) in skip_keys:
                continue
            if v["type"] == "substitution":
                idx = _idx(v["ref_pos"])
                if 0 <= idx < L:
                    observed[idx] = v["alt_base"].upper()
            elif v["type"] == "deletion":
                for k in range(v["length"]):
                    idx = _idx(v["ref_pos"] + k)
                    if 0 <= idx < L:
                        observed[idx] = "-"
            elif v["type"] == "insertion":
                idx = _idx(v["ref_pos"] + 1)  # 插入点右侧参考位置
                key = f"+{v['alt_base'].upper()}"
                q = ins_q.get(idx, v.get("quality", mean_q))
                if 0 <= idx < L:
                    observed[idx] = key
                    obs_q[idx] = q
                elif idx == L:
                    end_insert_votes[key] = end_insert_votes.get(key, 0.0) + _phred_weight(q)
        # read 在参考列上调用为 N/IUPAC 的位点：投它自己的调用，而不是默认投参考碱基——
        # 否则只有 N 覆盖的位点会被参考碱基悄悄填上，拼接结果看起来就是参考序列
        ambig = _ambiguous_columns(aln, L, circular)
        for pos in range(aln["ref_start"], aln["ref_end"] + 1):
            if circular:
                pos = fold_pos(pos, L)
            if 1 <= pos <= L:
                i = pos - 1
                key = observed.get(i) or ambig.get(i) or reference[i].upper()
                q = obs_q.get(i, base_q.get(i, mean_q))
                votes[i][key] = votes[i].get(key, 0.0) + _phred_weight(q)
                voters[i].append(key)

    consensus_chars: List[str] = []
    char_ref: List[int] = []      # 每个共识字符对应的参考位点（插入碱基记插入点右侧位点）
    char_cov: List[bool] = []     # 该字符是否来自 read 实测（False = 未测位置的参考填充）
    diffs: List[Dict] = []  # 共识与参考的差异位（供前端高亮，人工核对最终构建体）
    covered: List[bool] = []

    def _emit(chars, pos: int, cov: bool) -> None:
        for ch in chars:
            consensus_chars.append(ch)
            char_ref.append(pos)
            char_cov.append(cov)

    for pos in range(1, L + 1):
        cell = votes[pos - 1]
        if not cell:
            _emit(reference[pos - 1].upper(), pos, False)
            covered.append(False)
            continue
        covered.append(True)
        if len(voters[pos - 1]) == 1:
            best_key = voters[pos - 1][0]   # 单 read 覆盖：以该 read 为准
        else:
            best_key = _pick_base_vote(cell)
        if best_key.startswith("+"):
            alt = best_key[1:]
            cons_index = len(consensus_chars)
            _emit(alt, pos, True)
            # 插入只新增碱基，紧跟其后的参考碱基必须保留，
            # 否则共识序列会悄悄吞掉插入位点右侧的参考碱基
            _emit(reference[pos - 1].upper(), pos, True)
            diffs.append({
                "ref_pos": pos, "ref_base": "-", "cons_base": alt, "cons_index": cons_index,
            })
        elif best_key == "-":
            diffs.append({
                "ref_pos": pos, "ref_base": reference[pos - 1].upper(), "cons_base": "-",
            })
        else:
            _emit(best_key, pos, True)
            if best_key != reference[pos - 1].upper():
                diffs.append({
                    "ref_pos": pos, "ref_base": reference[pos - 1].upper(),
                    "cons_base": best_key, "cons_index": len(consensus_chars) - 1,
                })

    if end_insert_votes:
        best_key = max(end_insert_votes.items(), key=lambda kv: kv[1])[0]
        alt = best_key[1:]
        cons_index = len(consensus_chars)
        _emit(alt, L, True)
        diffs.append({"ref_pos": L, "ref_base": "-", "cons_base": alt, "cons_index": cons_index})

    covered_ranges = merge_coverage(
        [(i + 1, i + 1) for i, c in enumerate(covered) if c], L
    )
    coverage = sum(1 for c in covered if c) / L if L else 0.0
    sequence = "".join(consensus_chars)
    segments = _read_segments(sequence, char_ref, char_cov)
    return {
        "sequence": sequence,
        "covered_ranges": covered_ranges,
        "coverage_percent": round(coverage * 100, 2),
        "diffs": diffs,
        # 由 read 实测拼接出的连续片段（不含未测位置的参考填充）；
        # cons_start/cons_end 为 sequence 内 1 起始闭区间，ref_start/ref_end 为参考坐标
        "segments": segments,
        "assembled_length": sum(len(g["sequence"]) for g in segments),
    }


def _read_segments(sequence: str, char_ref: List[int], char_cov: List[bool]) -> List[Dict]:
    """把共识序列中 read 实测的字符切成连续片段。

    共识序列为了坐标对齐用参考碱基填充了未测位置；用户要的「拼接结果」是
    测序结果本身拼出来的序列，因此单独给出实测片段。片段按共识下标切分，
    有插入/缺失时与参考坐标不再一一对应，下游一律用 cons_start/cons_end 切。"""
    out: List[Dict] = []
    i, n = 0, len(sequence)
    while i < n:
        if not char_cov[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and char_cov[j + 1]:
            j += 1
        out.append({
            "cons_start": i + 1, "cons_end": j + 1,
            "ref_start": char_ref[i], "ref_end": char_ref[j],
            "sequence": sequence[i:j + 1],
        })
        i = j + 1
    return out


def _ambiguous_columns(aln: Dict, L: int, circular: bool) -> Dict[int, str]:
    """read 在参考列上的 N/IUPAC 调用：{0-based 投票下标: 调用字符}"""
    av = aln.get("aligned") or {}
    ref_al = av.get("ref_aligned") or ""
    read_al = av.get("read_aligned") or ""
    if not ref_al or len(read_al) != len(ref_al):
        return {}
    out: Dict[int, str] = {}
    pos = int(av.get("ref_start") or aln.get("ref_start") or 1)
    for rc, qc in zip(ref_al, read_al):
        if rc == "-":
            continue
        q = qc.upper()
        if q not in ("A", "C", "G", "T", "-"):
            out[(fold_pos(pos, L) - 1) if circular else pos - 1] = q
        pos += 1
    return out


_ALT_STARTS = ("ATG", "GTG", "TTG", "ATT", "CTG", "ATC", "ATA")  # 细菌起始密码子
_STOP_CODONS = {"TAA", "TAG", "TGA"}


def _find_orf(ref: str, start: int, end: int, strand: str) -> Optional[Dict]:
    """在特征区间内寻找最佳开放阅读框（ORF），返回参考坐标 {orf_start, orf_end}

    手动标注/导入的 CDS 特征边界常有 1-3bp 偏差（如起点多含上游碱基），
    直接按特征起点取框翻译会整体错位、连起始密码子判定都失效。
    枚举区间内三个阅读框：优先“起始密码子→终止密码子”完整的 ORF
    （ATG 优先于替代起始），其次起始→区间末端的开放框，取最长者。
    """
    win = ref[start - 1:end]
    if strand == "-":
        win = _seq_revcomp(win)
    N = len(win)
    best: Optional[Tuple[tuple, int, int]] = None  # (score, a, b) win 内 0-based 半开 [a,b)

    def consider(a: int, b: int, has_stop: bool):
        nonlocal best
        atg = win[a:a + 3] == "ATG"
        # 长度主导：真实 CDS 通常是区间内最长开放框，防止随机短完整 ORF 反超
        score = (b - a, atg, has_stop)
        if best is None or score > best[0]:
            best = (score, a, b)

    for frame in range(3):
        i = frame
        while i + 3 <= N:
            if win[i:i + 3] in _ALT_STARTS:
                j = i
                stop_found = False
                while j + 3 <= N:
                    if win[j:j + 3] in _STOP_CODONS:
                        consider(i, j + 3, True)
                        stop_found = True
                        break
                    j += 3
                if not stop_found:
                    consider(i, N, False)  # 有起始但区间内无终止：开放框
            i += 3
    if best is None:
        return None
    _, a, b = best
    if strand == "-":
        # 反向互补空间 [a,b) → 参考坐标：位置 i（0-based）对应参考 end-i（1-based）
        return {"orf_start": end - b + 1, "orf_end": end - a}
    return {"orf_start": start + a, "orf_end": start + b - 1}


def _build_cds_reports(
    ref: str, features: List[Dict], variants: List[Dict], consensus: Dict,
    _ref_pos_of=None,
) -> List[Dict]:
    """CDS 级别测序结论：覆盖完整性 + 共识重建 CDS 的翻译产物与参考逐位比对

    回答“整段 CDS 测序结果有没有问题”：
    - 覆盖：CDS 区间与共识覆盖区间求交，未覆盖部分按参考填充、不参与判定；
    - 编码区校正：特征边界偏差（1-3bp 很常见）时自动对齐区间内最佳 ORF，
      以真实编码区翻译，避免阅读框整体错位；
    - 嵌套去重：被更大 CDS 完全包含的小特征（如 6xHis 标签）不单独出结论；
    - 蛋白：从共识差异重建 CDS 序列（按链方向翻译），与参考翻译逐位比对，
      给出一致/不一致、移码数、无义提前终止位置与氨基酸替换清单。
    - 多段 CDS（GenBank join，含跨原点）：各段按编码方向拼成虚拟参考后
      走同一套判读，见 _joined_cds_report。

    _ref_pos_of：内部参数，把本次调用的坐标映射回原参考坐标（仅用于
    结论文案里的位置；多段 CDS 递归时传入）。
    """
    from Bio.Seq import Seq
    from core.sanger.annotator import group_feature_segments

    _rp = _ref_pos_of or (lambda pos: pos)

    ALT_START_CODONS = set(_ALT_STARTS)

    def _translate(seq: str, strand: str) -> str:
        s = _seq_revcomp(seq) if strand == "-" else seq
        s = s[:len(s) - len(s) % 3]  # 移码可能留下不足整密码子的尾部
        prot = str(Seq(s).translate(table=11))
        if prot and s[:3] in ALT_START_CODONS:
            prot = "M" + prot[1:]  # 细菌替代起始密码子按惯例显示为 M
        return prot[:-1] if prot.endswith("*") else prot  # 去掉末尾终止密码子

    covered_ranges = consensus.get("covered_ranges", [])

    def _span_coverage(start: int, end: int) -> float:
        cov = 0
        for s, e in covered_ranges:
            lo, hi = max(s, start), min(e, end)
            if hi >= lo:
                cov += hi - lo + 1
        length = end - start + 1
        return round(cov / length * 100, 1) if length else 0.0

    L_ref = len(ref)

    def _del_positions(v: Dict) -> List[int]:
        """缺失覆盖的参考坐标。环状参考上跨原点的缺失（ref_pos+len-1 > L）
        折回 1..L（与 _joined_cds_report 的折回口径一致）；线性参考上缺失
        不会越过 L，结果与原线性区间相同"""
        a, n = int(v["ref_pos"]), int(v.get("length") or 1)
        return [(p - 1) % L_ref + 1 for p in range(a, a + n)] if L_ref else []

    def _rebuild_from_variants(start: int, end: int, vs: List[Dict]) -> str:
        """按变体列表重建区间核酸（anchor 语义：插入位于 ref_pos 与 ref_pos+1 之间）"""
        subs = {v["ref_pos"]: v["alt_base"] for v in vs if v["type"] == "substitution"}
        dels: set = set()
        for v in vs:
            if v["type"] == "deletion":
                dels.update(_del_positions(v))
        ins_at: Dict[int, List[str]] = {}
        for v in vs:
            if v["type"] == "insertion" and start <= v["ref_pos"] < end:
                ins_at.setdefault(v["ref_pos"], []).append(v["alt_base"])
        out: List[str] = []
        for pos in range(start, end + 1):
            if pos not in dels:
                out.append(subs.get(pos) or ref[pos - 1])
            for alt in ins_at.get(pos, []):
                out.append(alt)
        return "".join(out)

    def _protein_alignment(ref_prot: str, alt_prot: str) -> Dict:
        """蛋白层面全局比对（BLOSUM62，blastp 同款 gap 罚分；HGVS 要求的对比方式）

        返回一致残基数、首个分歧参考位置（0-based，含 gap 事件）、
        错义替换清单（仅对齐块内）与残基水平的插入/缺失总数。
        """
        from Bio import Align

        # 终审 C-08：任一侧为空（如起始密码子突变为终止 → alt 截断为空串）
        # 时 Biopython 抛 ValueError: sequence has zero length，整份分析 500。
        # 空比对 = 全部残基为 gap 事件的退化对齐，直接构造结果。
        if not ref_prot or not alt_prot:
            return {
                "identical": 0,
                "aligned_ref": 0,
                "aligned_alt": 0,
                "first_diff": 0,
                "subs": [],
                "gap_residues": len(ref_prot) + len(alt_prot),
            }
        aligner = Align.PairwiseAligner()
        try:
            from Bio.Align import substitution_matrices
            aligner.substitution_matrix = substitution_matrices.load("BLOSUM62")
        except Exception:
            aligner.match_score = 2
            aligner.mismatch_score = -1
        aligner.open_gap_score = -11
        aligner.extend_gap_score = -1
        best = aligner.align(ref_prot, alt_prot)[0]

        blocks = best.aligned
        identical = 0
        aligned_ref = 0
        aligned_alt = 0
        first_diff: Optional[int] = None
        prev_ref_end = 0
        subs: List[str] = []
        for (rs, re_), (qs, qe_) in zip(blocks[0], blocks[1]):
            if rs > prev_ref_end and first_diff is None:
                first_diff = prev_ref_end  # 比对 gap：参考缺失或 alt 插入残基
            for k in range(re_ - rs):
                a, b = ref_prot[rs + k], alt_prot[qs + k]
                if a == b:
                    identical += 1
                else:
                    if first_diff is None:
                        first_diff = rs + k
                    subs.append(f"{a}{rs + k + 1}{b}")
            aligned_ref += re_ - rs
            aligned_alt += qe_ - qs
            prev_ref_end = re_
        if first_diff is None and aligned_ref < len(ref_prot):
            first_diff = aligned_ref  # 尾部参考残基在 alt 中缺失
        return {
            "identical": identical,
            "aligned_ref": aligned_ref,
            "aligned_alt": aligned_alt,
            "first_diff": first_diff if first_diff is not None else len(ref_prot),
            "subs": subs,
            "gap_residues": (len(ref_prot) - aligned_ref) + (len(alt_prot) - aligned_alt),
        }

    reports: List[Dict] = []
    # 嵌套去重：被更大 CDS 完全包含的小特征（如 6xHis 标签）不单独出结论
    cds_feats = [f for f in (features or []) if f.get("type") == "CDS"]
    cds_groups = group_feature_segments(cds_feats)
    nested_ids = {
        id(f)
        for f in cds_feats
        for g in cds_feats
        if g is not f
        and int(g["start"]) <= int(f["start"]) and int(f["end"]) <= int(g["end"])
        and (int(g["end"]) - int(g["start"])) > (int(f["end"]) - int(f["start"]))
    }
    for group in cds_groups:
        if len(group) > 1:
            # 多段 CDS 的各段不参与嵌套去重（段本身不是独立编码区）
            reports.extend(_joined_cds_report(ref, group, variants, consensus))
            continue
        f = group[0]
        if id(f) in nested_ids:
            continue
        feat_start, feat_end = int(f["start"]), int(f["end"])
        if feat_start < 1 or feat_end > len(ref) or feat_start > feat_end:
            continue
        strand = f.get("strand") or "+"
        # 编码区校正：特征边界常有 1-3bp 偏差（如起点多含上游碱基），
        # 对齐区间内最佳 ORF 后再翻译，此后 start/end 均指真实编码区
        orf = _find_orf(ref, feat_start, feat_end, strand)
        start, end = (orf["orf_start"], orf["orf_end"]) if orf else (feat_start, feat_end)
        if orf and (start, end) != (feat_start, feat_end):
            shift = f"起点相差 {abs(start - feat_start)} bp" if start != feat_start \
                else f"终点相差 {abs(end - feat_end)} bp"
            frame_note = (
                f"（特征标注 {_rp(feat_start)}-{_rp(feat_end)} 与实际编码区 "
                f"{_rp(start)}-{_rp(end)} {shift}，已按编码区翻译）"
            )
        elif (end - start + 1) % 3:
            frame_note = f"（注意：该特征长度 {end - start + 1} bp 不是 3 的倍数，翻译按参考阅读框截断）"
        else:
            frame_note = ""
        cov_pct = _span_coverage(start, end)
        base = {
            "name": f.get("name") or "CDS",
            "start": start,
            "end": end,
            "strand": strand,
            "covered_percent": cov_pct,
            "coverage_status": "full" if cov_pct >= 99 else ("uncovered" if cov_pct <= 0 else "partial"),
        }
        if cov_pct <= 0:
            reports.append({
                **base,
                "ref_protein_length": None,
                "alt_protein_length": None,
                "protein_identical": None,
                "premature_stop_aa": None,
                "frameshift_count": 0,
                "aa_changes": [],
                "consequences": [],
                "synonymous_count": 0,
                "pending_low_confidence": 0,
                "verdict": "未测：不参与判读（覆盖区外按设计序列对待——部分测序是常规策略，无需测全）",
            })
            continue

        ref_prot = _translate(ref[start - 1:end], strand)

        # 变体是否影响该 CDS：替换/缺失按碱基区间与 CDS 相交判定；
        # 插入发生在 ref_pos 与 ref_pos+1 之间，插入点严格落在 CDS 内部
        #（anchor ∈ [start, end-1]）才影响该 CDS，紧贴边界的插入不改变 CDS 自身序列
        def _affects(v: Dict) -> bool:
            if v.get("type") == "insertion":
                return start <= v["ref_pos"] < end
            if v.get("type") == "deletion":
                return any(start <= p <= end for p in _del_positions(v))
            return start <= v["ref_pos"] <= end

        in_cds = [v for v in variants if _affects(v)]
        # 置信度分层：低置信变异（峰级证据/Q 值不支持，疑似测序噪声）不计入
        # 确证判定，单独提示待复核——避免噪声推翻整段 CDS 结论
        confirmed = [v for v in in_cds if v.get("confidence", "high") != "low"]
        pending = [v for v in in_cds if v.get("confidence", "high") == "low"]
        alt_nt_full = _rebuild_from_variants(start, end, confirmed)
        alt_prot_raw = _translate(alt_nt_full, strand)
        stop_idx = alt_prot_raw.find("*")  # 内部终止（-1 为无）
        alt_prot = alt_prot_raw[:stop_idx] if stop_idx >= 0 else alt_prot_raw
        indels = [v for v in confirmed if v.get("type") in ("insertion", "deletion")]
        # 移码自判定：删除按其实际落入 CDS 的碱基数取模——跨边界删除只有
        # 部分落在 CDS 内时，按变体全长取模会误判 inframe/frameshift
        #（不依赖全局注释——注释器只看变体自身所在特征，会漏掉边界插入）
        def _indel_len_in_cds(v: Dict) -> int:
            vlen = int(v.get("length") or 1)
            if v["type"] == "insertion":
                return vlen  # anchor 落在 CDS 内部（_affects 已保证）
            return sum(1 for p in _del_positions(v) if start <= p <= end)
        frameshifts = [v for v in indels if _indel_len_in_cds(v) % 3 != 0]
        inframe_ins = sum(1 for v in indels if v["type"] == "insertion" and int(v.get("length") or 1) % 3 == 0)
        inframe_del = sum(1 for v in indels if v["type"] == "deletion" and int(v.get("length") or 1) % 3 == 0)
        protein_identical = ref_prot == alt_prot_raw

        # 起始/终止密码子状态（参考方向核酸层面判定，参照 VEP/snpEff 的 start_lost/stop_lost）
        from Bio.SeqUtils import seq3

        def _orient(s: str) -> str:
            return _seq_revcomp(s) if strand == "-" else s

        start_codons = {"ATG"} | ALT_START_CODONS
        stop_codons = {"TAA", "TAG", "TGA"}
        ref_o = _orient(ref[start - 1:end])
        alt_o = _orient(alt_nt_full)
        alt_o_codons = alt_o[:len(alt_o) - len(alt_o) % 3]
        start_lost = ref_o[:3] in start_codons and alt_o[:3] not in start_codons
        # 移码下整个下游阅读框已改变，参考终止密码子无从谈起，不报 stop_lost
        stop_lost = (
            not frameshifts
            and len(ref_o) >= 3 and ref_o[-3:] in stop_codons
            and (len(alt_o_codons) < 3 or alt_o_codons[-3:] not in stop_codons)
        )

        # CDS 区间内的 DNA 替换数（确证变体，用于同义突变统计）
        dna_subs = sum(
            1 for v in confirmed
            if v.get("type") == "substitution" and start <= v["ref_pos"] <= end
        )

        aln = _protein_alignment(ref_prot, alt_prot)
        first_diff = aln["first_diff"]  # 0-based；一致前缀长度
        aa_diffs = [] if frameshifts else aln["subs"]  # 移码区的“替换”是移码噪声，不列
        # 同义计数仅在无移码/无内部终止时可靠（错义数来自蛋白比对）
        synonymous = max(0, dna_subs - len(aa_diffs)) if not frameshifts and stop_idx < 0 else 0

        # Sequence Ontology 标准后果词表（与 VEP/snpEff/bcftools csq 对齐，按影响从高到低）
        consequences: List[str] = []
        if stop_idx >= 0:
            consequences.append("stop_gained")
        if stop_lost:
            consequences.append("stop_lost")
        if start_lost:
            consequences.append("start_lost")
        if frameshifts:
            consequences.append("frameshift_variant")
        if inframe_ins:
            consequences.append("inframe_insertion")
        if inframe_del:
            consequences.append("inframe_deletion")
        if aa_diffs:
            consequences.append("missense_variant")
        if synonymous:
            consequences.append("synonymous_variant")

        # 结论主句：一句话因果链（哪个位置的什么变异 → 什么后果），面向实验员可直接行动
        name = base["name"]
        if protein_identical:
            main = (
                f"已测区域蛋白与设计一致（{len(ref_prot)} aa）" if cov_pct < 99
                else f"{name} 蛋白与设计一致（{len(ref_prot)} aa）"
            )
            if synonymous:
                main += f"；另有 {synonymous} 处同义突变（DNA 变、蛋白不变）"
        else:
            fs0 = frameshifts[0] if frameshifts else None
            if fs0 is not None:
                kind = "插入" if fs0.get("type") == "insertion" else "缺失"
                vlen = int(fs0.get("length") or 1)
                main = f"{_rp(fs0['ref_pos'])} 处的{kind}{'' if vlen == 1 else f' {vlen}bp'}使阅读框自第 {first_diff + 1} 位起移码"
                if stop_idx >= 0:
                    main += f"，翻译在第 {stop_idx + 1} 位提前终止（产物 {len(alt_prot)} aa，设计为 {len(ref_prot)} aa）"
                else:
                    main += "，其后氨基酸序列与设计不再对应"
                if len(frameshifts) > 1:
                    main += f"（共 {len(frameshifts)} 处移码）"
            elif stop_idx >= 0:
                main = f"翻译在第 {stop_idx + 1} 位提前终止（产物 {len(alt_prot)} aa，设计为 {len(ref_prot)} aa）"
            elif start_lost:
                main = f"起始密码子改变（{ref_o[:3]} → {alt_o[:3]}），翻译可能无法正常起始"
            elif stop_lost:
                main = "终止密码子丢失，翻译将读穿至下游"
            elif inframe_ins or inframe_del:
                n_bp = sum(int(v.get("length") or 1) for v in indels
                           if int(v.get("length") or 1) % 3 == 0)
                main = f"阅读框内{'插入' if inframe_ins else '缺失'} {n_bp} bp，自第 {first_diff + 1} 位起氨基酸序列改变"
            elif aa_diffs:
                preview = "、".join(aa_diffs[:5]) + ("等" if len(aa_diffs) > 5 else "")
                main = f"{len(aa_diffs)} 处氨基酸替换（{preview}）"
            else:
                main = f"翻译产物与设计不同（长度 {len(ref_prot)} → {len(alt_prot)} aa）"
            if synonymous and (aa_diffs or inframe_ins or inframe_del):
                main += f"；另有 {synonymous} 处同义突变（蛋白不变）"
            # 移码/提前终止下蛋白层面的比对不再可靠，但确证的碱基替换仍需列出——
            # 它们是真实存在的序列差异（如重新挑克隆时需要一并确认）
            if frameshifts or stop_idx >= 0:
                confirmed_subs = [
                    v for v in confirmed
                    if v.get("type") == "substitution" and start <= v["ref_pos"] <= end
                ]
                if confirmed_subs:
                    preview = "、".join(
                        f"{_rp(v['ref_pos'])} {v.get('ref_base', '')}>{v.get('alt_base', '')}"
                        for v in confirmed_subs[:4]
                    ) + ("等" if len(confirmed_subs) > 4 else "")
                    main += f"；另有 {len(confirmed_subs)} 处确证的碱基替换（{preview}）"
            main = f"{name} 蛋白与设计不一致：{main}"
        # 待复核提示：低置信变异未计入以上判定，告知其潜在影响。
        # 互检矛盾（单 read 报告 indel、其他 read 干净跨过同一位点）单独
        # 点名——它与"疑似测序噪声"不同：另一条 read 已给出反向证据，
        # 更可能是伪影而非漏检，人工核对有明确抓手
        if pending:
            conflicted = [v for v in pending if v.get("cross_read_conflict")]
            plain = [v for v in pending if not v.get("cross_read_conflict")]
            would_change = _translate(_rebuild_from_variants(start, end, in_cds), strand) != alt_prot_raw
            if plain:
                detail = "；".join(
                    f"{_rp(v['ref_pos'])} {variant_type_text(v)}"
                    + (f"（{v['low_reason']}）" if v.get("low_reason") else "")
                    for v in plain[:3]) + ("等" if len(plain) > 3 else "")
                if would_change:
                    main += (
                        f"。另有 {len(plain)} 处低置信差异未计入判定：{detail}"
                        "——若为真蛋白还会改变，建议核对峰图"
                    )
                else:
                    main += f"。另有 {len(plain)} 处低置信差异，不影响以上判定：{detail}"
            if conflicted:
                parts = []
                for v in conflicted[:2]:
                    kind = {"insertion": "插入", "deletion": "缺失"}.get(
                        v.get("type"), "替换")
                    rd = (v.get("cross_read_conflict") or {}).get("reads") or []
                    reads = "、".join(rd[:2]) + ("等" if len(rd) > 2 else "")
                    parts.append(f"{_rp(v['ref_pos'])} 处的{kind}（{reads} 跨过同一位点未见差异）")
                head = (f"等 {len(conflicted)} 处" if len(conflicted) > 2
                        else f"{len(conflicted)} 处")
                main += (
                    f"。另有 {head}互检矛盾差异未计入判定：{'；'.join(parts)}"
                    "——真实变异应被覆盖该位的每条 read 独立报出，如需确认请核对峰图或换引物复测"
                )
        verdict = (
            f"CDS 覆盖 {cov_pct}%（未覆盖部分按参考填充、未验证）。{main}"
            if cov_pct < 99
            else f"CDS 已完整覆盖。{main}"
        )
        verdict += frame_note
        reports.append({
            **base,
            "coverage_status": "full" if cov_pct >= 99 else "partial",
            "ref_protein_length": len(ref_prot),
            "alt_protein_length": len(alt_prot),
            "protein_identical": protein_identical,
            "premature_stop_aa": stop_idx + 1 if stop_idx >= 0 else None,
            "frameshift_count": len(frameshifts),
            "aa_changes": aa_diffs[:10],
            "consequences": consequences,
            "synonymous_count": synonymous,
            "pending_low_confidence": len(pending),
            "verdict": verdict,
        })
    return reports


def _joined_cds_report(
    ref: str, parts: List[Dict], variants: List[Dict], consensus: Dict
) -> List[Dict]:
    """多段 CDS（GenBank join，含环状质粒跨原点的 join(2701..3000,1..300)）

    各段按编码方向拼接成虚拟参考（正链即段顺序；负链段逆序拼正链序列，
    其反向互补即编码序列），把变体与覆盖区间映射到虚拟坐标后复用单段
    判读；报告的 start/end 映射回原参考坐标（跨原点时 start > end），
    并附 segments 列出编码区实际落在参考上的各段（编码方向顺序）。
    段间链方向不一致（反式剪接）时无法拼接，不出报告。
    """
    strands = {p.get("strand") or "+" for p in parts}
    if len(strands) != 1:
        return []
    strand = strands.pop()
    L = len(ref)
    fwd = list(reversed(parts)) if strand == "-" else list(parts)
    vmap: List[int] = []  # 虚拟坐标（0-based 下标）→ 原参考 1-based 坐标
    part_vranges: List[Tuple[int, int]] = []  # 各段（fwd 顺序）的虚拟 1-based 区间
    for p in fwd:
        s, e = int(p["start"]), int(p["end"])
        if s < 1 or e > L or s > e:
            return []
        part_vranges.append((len(vmap) + 1, len(vmap) + e - s + 1))
        vmap.extend(range(s, e + 1))
    inv: Dict[int, List[int]] = {}
    for k, pos in enumerate(vmap, 1):
        inv.setdefault(pos, []).append(k)
    vref = "".join(ref[pos - 1] for pos in vmap)

    vvars: List[Dict] = []
    for v in variants:
        t, a = v.get("type"), int(v["ref_pos"])
        if t == "substitution":
            vvars.extend({**v, "ref_pos": k} for k in inv.get(a, []))
        elif t == "insertion":
            # 插入位于 a 与 a+1 之间：两者在虚拟序列中也相邻才算落在 CDS 内
            nxt = a % L + 1 if L else a + 1
            vvars.extend({**v, "ref_pos": k} for k in inv.get(a, [])
                         if k < len(vmap) and vmap[k] == nxt)
        elif t == "deletion":
            n = int(v.get("length") or 1)
            ks = sorted(k for i in range(n) for k in inv.get((a - 1 + i) % L + 1, []))
            run_start = prev = None
            for k in ks + [None]:
                if k is not None and prev is not None and k == prev + 1:
                    prev = k
                    continue
                if run_start is not None:
                    vvars.append({**v, "ref_pos": run_start, "length": prev - run_start + 1})
                run_start = prev = k

    covered = bytearray(L + 2)
    for s, e in consensus.get("covered_ranges", []):
        for pos in range(max(1, int(s)), min(L, int(e)) + 1):
            covered[pos] = 1
    vcov: List[Tuple[int, int]] = []
    for k, pos in enumerate(vmap, 1):
        if covered[pos]:
            if vcov and vcov[-1][1] == k - 1:
                vcov[-1] = (vcov[-1][0], k)
            else:
                vcov.append((k, k))

    head = parts[0]
    vfeat = {"name": head.get("name") or "CDS", "type": "CDS",
             "start": 1, "end": len(vref), "strand": strand}
    reports = _build_cds_reports(vref, [vfeat], vvars, {"covered_ranges": vcov},
                                 _ref_pos_of=lambda k: vmap[k - 1])
    for r in reports:
        vs, ve = int(r["start"]), int(r["end"])
        segs = []
        for ps, pe in part_vranges:
            lo, hi = max(ps, vs), min(pe, ve)
            if lo <= hi:
                segs.append([vmap[lo - 1], vmap[hi - 1]])
        if strand == "-":
            segs.reverse()
        r["start"], r["end"] = vmap[vs - 1], vmap[ve - 1]
        r["segments"] = segs
    return reports


def _signal_maps(r: Dict) -> Optional[Dict]:
    """read 级 H(x)/W(x) property map（B1；惰性计算并缓存，_sigmaps 不进输出）"""
    if "_sigmaps" not in r:
        r["_sigmaps"] = property_maps(r["trace"], r["trimmed_peaks"])
    return r["_sigmaps"]


def analyze(
    ab1_files: List[Tuple[str, bytes]],
    reference: str,
    features: Optional[List[Dict]] = None,
    min_q: int = MIN_TRIM_Q,
    allow_decompose: bool = True,
    signal_correct: bool = True,
    poly_local_ratio: bool = True,
    circular: bool = False,
    zone_params: Optional[ZoneParams] = None,
) -> Dict:
    """全自动分析入口

    ab1_files: [(filename, bytes), ...]
    reference: 参考载体序列（1-based 坐标）
    circular: 参考为环状（质粒）时，每条 read 对「参考 + 参考[:k]」比对
    （k≈read 长），跨越原点的 read 整条比对上；变体、覆盖区间（跨原点
    拆为两段）、共识、read↔参考坐标映射全部按 ((p-1) % L) + 1 折回。
    False（默认）按线性参考比对，行为与此前一致。
    signal_correct: A2 基线校正（逐通道扣除滑动低分位本底后再做峰级定量，
    显示 trace 亦为校正后信号）；False 回退原始信号（约束 2 的前后对比开关）。
    poly_local_ratio: B1 poly 压缩比分母用局部峰幅 H(run)（False 回退全 read
    中位数——信号沿 read 衰减时后段正常 poly 会被误判压缩）。
    zone_params: read 可信区边界（首端硬区/灰区、尾端灰区/硬区，read 坐标 bp），
    缺省 30/60/700/900，见 reliable_zone。
    """
    features = features or []
    zone_params = zone_params or DEFAULT_ZONE
    ref = reference.upper().replace("U", "T")
    if not ref:
        raise ValueError("参考序列为空，无法进行比对分析")

    read_results: List[Dict] = []
    errors: List[Dict] = []
    L_ref = len(ref)
    _align = align_read_circular if circular else align_read
    # indel 最左归一化：环状参考上可越过原点左移——在三倍参考的中间一份
    # 上归一化（坐标 +L 抬升），结果折回；线性参考维持原口径
    ref3 = ref * 3 if circular else ""

    def _norm_all(vs: List[Dict]) -> List[Dict]:
        if not circular:
            return _normalize_indel_all(ref, vs)
        out = []
        for v in vs:
            nv = _normalize_indel(ref3, {**v, "ref_pos": int(v["ref_pos"]) + L_ref})
            out.append({**nv, "ref_pos": fold_pos(nv["ref_pos"], L_ref)})
        return out

    for filename, blob in ab1_files:
        try:
            read = extract_read(blob)
        except (AbiParseError, Exception) as e:  # noqa: BLE001 单文件失败不阻断整体
            errors.append({"filename": filename, "error": f"解析失败: {e}"})
            continue

        bases = read["bases"]
        quality = read["quality"]
        if len(bases) < MIN_WINDOW:
            errors.append({"filename": filename, "error": "碱基数过少（<50bp）"})
            continue

        # A2 基线校正：峰级定量前扣除各通道加性本底（通道映射不动）；
        # 显示与定量统一使用校正后信号（关闭开关回退原始信号）
        if signal_correct:
            read["trace"] = baseline_correct(read["trace"])

        s, e = _trim_by_quality(bases, quality, min_q)
        trimmed = bases[s:e]
        trimmed_q = quality[s:e]
        # 阈值高于全部碱基 Q 值时修剪结果为空：记为错误条目，
        # 不能送进 PairwiseAligner（空序列会抛 ValueError → 500）
        if len(trimmed) < MIN_WINDOW:
            errors.append({
                "filename": filename,
                "error": f"Q≥{min_q} 的碱基不足 {MIN_WINDOW}bp（修剪后仅 {len(trimmed)}bp），无法比对",
            })
            continue
        mean_q = sum(trimmed_q) / len(trimmed_q) if trimmed_q else 0
        grade, q20_ratio, crl = _read_grade(trimmed, trimmed_q)

        aln = _align(trimmed, ref, trimmed_q)
        # 截断提示：环状参考的跨原点 read 已整条比对，仍截断即另有原因
        partial = _check_partial_alignment(trimmed, aln, circular)

        # 变体附加质量值，并归一化为最左表示（重复/同聚物区 anchor 漂移时
        # 不同 read / 不同 caller 报告的等价 indel 才能合并印证）
        aln["variants"] = _norm_all(aln["variants"])
        for v in aln["variants"]:
            rp = v.get("read_pos") or 1
            qi = min(max(rp - 1, 0), len(trimmed_q) - 1)
            v["quality"] = trimmed_q[qi]

        # tracy 交叉 basecall：独立 caller 的第二意见（生产镜像内置，缺失时降级）
        # 终审 C-09：tracy 全流程降级隔离——一条低质量 ab1 的交叉 basecall
        # 异常不得拖垮整份多 read 分析
        cross = _try_tracy_basecall(blob)
        used_tracy = cross is not None
        cross_variants: List[Dict] = []
        tracy_calls: Optional[Dict] = None
        if used_tracy:
            cb, cq = cross[0], cross[1]
            # 第三项为 JSON 路径的次级调用（FASTQ 退化路径无此项）
            tracy_calls = cross[2] if len(cross) > 2 else None
            cs, ce = _trim_by_quality(cb, cq, min_q)
            # 终审 C-09：守卫必须看修剪后长度——修剪后为空时
            # align_read("") 抛 ValueError，一条低质量 read 拖垮整份分析
            if ce - cs >= MIN_WINDOW:
                caln = _align(cb[cs:ce], ref, cq[cs:ce])
                for v in caln["variants"]:
                    rp = v.get("read_pos") or 1
                    qi = min(max(rp - 1, 0), len(cq[cs:ce]) - 1)
                    v["quality"] = cq[cs:ce][qi]
                cross_variants = _norm_all(caln["variants"])

        # 混合峰检测移至 poly 结构识别之后（B4：滑移 echo 先于 mixed 判定剔除）
        # 变体 read_pos 基于 trimmed 碱基（1-based）；峰坐标保持 trace 数据点坐标系，
        # 与 PLOC 一致，供混合检测与峰级证据按相邻峰窗口取样
        trimmed_peaks = [
            read["peak_indices"][i] for i in range(s, min(e, len(read["peak_indices"])))
        ]

        read_results.append({
            "filename": filename,
            "sample_name": read["sample_name"],
            "raw_length": len(bases),
            "trimmed_length": len(trimmed),
            "mean_q": round(mean_q, 1),
            "grade": grade,
            "q20_ratio": q20_ratio,
            "crl": crl,
            "trimmed_bases": trimmed,
            "trimmed_quality": trimmed_q,
            "alignment": aln,
            "trace": read["trace"],
            "peak_indices": read["peak_indices"],
            "trimmed_peaks": trimmed_peaks,
            # poly 计数用：质量修剪常剪掉 poly 末端低 Q 压缩区，峰窗需沿
            # 原始 read 外扩（B4），保留原始碱基串与修剪偏移
            "raw_bases": bases,
            "trim_start": s,
            "cross_variants": cross_variants if used_tracy else None,
            "tracy_calls": tracy_calls,
            "partial_alignment": partial,
            # read 可信区：位置先验（首端硬区/灰区、尾端灰区/硬区）+ Q 值与峰型
            "reliable_zone": compute_reliable_zone(
                len(bases), s, trimmed_q, read["trace"], trimmed_peaks, zone_params),
        })

    # 可信区折算到参考坐标（展开坐标，与 alignment.ref_start/ref_end 同系）：
    # 报告「read 概况」按参考坐标列出每条 read 的不可信区段
    for r in read_results:
        zone = r.get("reliable_zone")
        if not zone:
            continue
        r2ref, _ = _read_ref_maps(r, fold=False)
        inside = [rf for rp, rf in r2ref.items() if in_zone(zone, rp)]
        zone["ref_trusted"] = [min(inside), max(inside)] if inside else None

    # 合并全部变体 → 注释 → 汇总（变体已按最左表示归一化，key 聚合等价 indel）
    all_variants: List[Dict] = []
    for r in read_results:
        for v in r["alignment"]["variants"]:
            vv = {**v, "read": r["filename"], "read_q": v.get("quality", 0)}
            all_variants.append(vv)
    # 按 (ref_pos, type, alt) 去重合并（多 read 支持计数）
    merged: Dict[Tuple, Dict] = {}
    for v in all_variants:
        key = _variant_key(v)
        if key in merged:
            merged[key]["support_reads"] += 1
            merged[key]["read_q"] = max(merged[key]["read_q"], v["read_q"])
        else:
            merged[key] = {**v, "support_reads": 1}
    variants = list(merged.values())
    variants.sort(key=lambda x: (x["ref_pos"], x["type"]))
    variants = annotate_variants(variants, features, ref)

    # poly 重复结构：参考序列扫描（B3：period 1-3 + 观察级）+ 变体归属
    poly_runs = find_repeat_runs(ref)
    _annotate_homopolymer(ref, variants, poly_runs)

    # 比对端点落在同聚物区内部 → 落点歧义标注（不改变判定）：
    # local 比对在同聚物内的起止随打分滑移，read 端部悬空也源于此——
    # 覆盖边界与跨引物互检的参考定位在该端点附近可能有数 bp 偏差
    poly_p1 = [run for run in poly_runs
               if run["period"] == 1 and run.get("tier") == "poly"]
    edge_ambiguous_reads: List[Dict] = []
    for r in read_results:
        aln = r["alignment"]
        if aln.get("ref_end", 0) <= 0:
            continue
        hits = []
        for edge_key, edge_label in (("ref_start", "起点"), ("ref_end", "终点")):
            pos = aln[edge_key]
            if _circ_len(aln):
                pos = fold_pos(pos, L_ref)
            run = next((rn for rn in poly_p1
                        if rn["start"] <= pos <= rn["end"]), None)
            if run:
                hits.append({"edge": edge_label, "ref_pos": pos,
                             "base": run.get("base") or "",
                             "start": run["start"], "end": run["end"]})
        if hits:
            r["homopolymer_edge_hits"] = hits
            edge_ambiguous_reads.append({
                "filename": r["filename"], "hits": hits})

    # 每 read 的信号层分析（B4/B5）：滑移 echo 位置 → 从 mixed 中剔除；
    # poly 末端下游骤降记录。mixed 判定在此进行（P17 顺序：stutter 先于 merge）。
    # Step A：run 窗口对该 read 呈"合并压缩"（无可分辨峰/强压缩）时记入
    # merged_zones——该 read 在 run 内的逐位双峰不可分辨，属伪差异证据源
    poly_runs_p1 = [r for r in poly_runs if r["period"] == 1]
    for r in read_results:
        aln = r["alignment"]
        slip: set = set()
        dropouts: List[Dict] = []
        merged_zones: List[Dict] = []
        for run in poly_runs_p1:
            # 环状跨原点 read：原点后段的 run 在展开坐标中平移 +L
            off = _run_offset(aln, run["start"], run["end"])
            if aln["direction"] == "-":
                i0 = aln["ref_end"] - (run["end"] + off)
                i1 = i0 + run["length"] - 1
            else:
                i0 = (run["start"] + off) - aln["ref_start"]
                i1 = i0 + run["length"] - 1
            if i1 < 0 or i0 > len(r["trimmed_peaks"]) - 1:
                continue
            w0, w1 = max(0, i0), min(i1, len(r["trimmed_peaks"]) - 1)
            slip.update(detect_phase_shift(
                r["trace"], r["trimmed_peaks"], w0, w1,
                # 反向 read 的 trace 通道按读向（互补链）记录，必须取互补碱基
                run["base"] if aln["direction"] == "+" else _COMPLEMENT[run["base"]],
            ))
            drop = detect_post_poly_dropout(
                r["trace"], r["trimmed_peaks"], w0, w1,
                bases=r["trimmed_bases"], end_margin=2 * END_MARGIN,
            )
            if drop:
                dropouts.append({"base": run["base"], "ref_start": run["start"],
                                 "ref_end": run["end"], "read_pos": i1, **drop})
            # 压缩判定（与 report 循环同口径）：峰计数为 0 或峰幅比过半压缩
            wpc = _poly_peak_count(r["trace"], r["trimmed_peaks"], w0 + 1, w1 - w0 + 1)
            wamp = _poly_peak_amplitude_ratio(
                r["trace"], r["trimmed_peaks"], w0 + 1, w1 - w0 + 1,
                local=poly_local_ratio)
            if wpc == 0 or (wamp is not None and wamp < POLY_COMPRESSION_RATIO):
                # run 窗内（含 echo 边界 ±2）逐位双峰均不可作证据
                merged_zones.append({
                    "base": run["base"], "ref_start": run["start"],
                    "ref_end": run["end"],
                    "positions": list(range(max(1, w0 - 1), w1 + 3)),
                })
        r["slippage_positions"] = sorted(slip)
        r["post_poly_dropouts"] = dropouts
        r["poly_merged_zones"] = merged_zones
        merged_span: set = set()
        for z in merged_zones:
            merged_span.update(z["positions"])
        n_trim = len(r["trimmed_bases"])
        mixed_detail = [
            e for e in _detect_mixed_detail(
                r["trimmed_bases"], r["trace"], r["trimmed_peaks"])
            if e["pos"] not in slip
            # read 可信区之外是信号爬升/衰减区，次级峰不可信——
            # 与变体置信度同一条边界（区外差异本就需多 read 支持才升级），
            # 否则末端拖尾会凭空触发"连续双峰段"误报
            and in_zone(r.get("reliable_zone"), e["pos"])
            # Step A 抑制：合并压缩的 run 窗内次级峰结构不可分辨，
            # 强行判读会把压缩段切成一串伪双峰（反向压缩山丘难例）
            and e["pos"] not in merged_span
        ]
        r["mixed_positions"] = [e["pos"] for e in mixed_detail if not e["pullup"]]
        # 逐位证据（次级通道与占比）随结果下发，供前端在峰图上标注双峰
        r["mixed_detail"] = [
            {"pos": e["pos"], "ratio": round(e["ratio"], 3),
             "secondary_base": e["secondary_base"]}
            for e in mixed_detail if not e["pullup"]
        ]
        # read 级混合分级：把逐位双峰聚合成 可行动 的判定（widespread=疑似
        # 混合样品 / scattered=个别双峰），pull-up 拖影已在 detail 内标记剔除
        # tracy 次级 basecall 作独立印证（审计 #8）：只附加 tracy_corroboration
        # 与逐位 tracy 标记，分级仍按院内口径
        for e in mixed_detail:
            i = e["pos"] - 1
            e["called_base"] = r["trimmed_bases"][i] if i < n_trim else None
        r["mixed_profile"] = _classify_mixed(
            mixed_detail, len(r["trimmed_bases"]),
            tracy_calls=r.get("tracy_calls"), peaks=r["trimmed_peaks"],
            zone=r.get("reliable_zone"))
        if r.get("tracy_calls") is not None:
            tags = {e["pos"]: e.get("tracy") for e in mixed_detail if not e["pullup"]}
            for d in r["mixed_detail"]:
                d["tracy"] = tags.get(d["pos"])

    # 跨 read 双峰互检：一条引物的双峰位点若被其他引物测过且峰形单一、判读
    # 一致，多半是这条 read 自己的信号噪声（常见于引物首端信号爬升区）；
    # 其他引物同报双峰才倾向真实混合。结论与建议均按互检结果措辞
    mixed_corroboration = _corroborate_mixed(read_results)

    # 变异置信度（Mutation Surveyor 式：峰强比 + 信噪比 + Q 值 + 多 read 支持）
    by_read = {r["filename"]: r for r in read_results}
    mixed_by_read = {r["filename"]: set(r["mixed_positions"]) for r in read_results}
    # 跨 read 反证预计算：每 read 的 参考坐标→比对列 映射与自身变体 key 集
    ref2call_by_read = {r["filename"]: _read_ref_maps(r)[1] for r in read_results}
    keys_by_read = {
        r["filename"]: {_variant_key(vv) for vv in r["alignment"]["variants"]}
        for r in read_results
    }
    for v in variants:
        src = by_read.get(v.get("read", ""))
        evidence = None
        if src is not None:
            # 反向 read 的 alt/ref 碱基是参考链方向，trace 通道记录的却是原始
            # 电泳链（互补链）信号：峰证据取样前必须按链向互补换算
            if src["alignment"].get("direction") == "-":
                comp = str.maketrans("ACGTN", "TGCAN")
                ev_ref = (v.get("ref_base", "") or "").translate(comp)
                ev_alt = (v.get("alt_base", "") or "").translate(comp)[::-1]
            else:
                ev_ref, ev_alt = v.get("ref_base", ""), v.get("alt_base", "")
            evidence = _variant_peak_evidence(
                src["trace"], src["trimmed_peaks"], v.get("read_pos"),
                ev_ref, ev_alt, v.get("type", "substitution"),
            )
            if evidence is not None:
                v["peak_evidence"] = evidence
        # poly 区 indel 的峰压缩评估：决定重复数计数是否可靠（参与置信度分级）
        hp = v.get("homopolymer")
        if hp and v.get("type") in ("insertion", "deletion") and src is not None:
            # run 窗长以碱基计：period>1 的重复（如 (CAG)n）按单元数×period 折算
            period = hp.get("period") or 1
            run_len_read = hp["ref_repeat_count"] * period
            if v["type"] == "insertion":
                run_len_read += int(v.get("length") or 1)
            hp["peak_amplitude_ratio"] = _poly_peak_amplitude_ratio(
                src["trace"], src["trimmed_peaks"], v.get("read_pos"), run_len_read,
                local=poly_local_ratio,
            )
            hp["count_reliable"] = (
                hp["peak_amplitude_ratio"] is None
                or hp["peak_amplitude_ratio"] >= POLY_COMPRESSION_RATIO
            )
            if period == 1:
                # 峰图独立计数交叉验证（仅同聚物）：可分辨峰与该 read 的调用数
                # 有任何出入即判计数不可靠——人工核对以可见峰为准，不设容差；
                # 二/三核苷酸重复只做结构标注，不做峰图计数
                _pc = _poly_peak_count(
                    src["trace"], src["trimmed_peaks"], v.get("read_pos"),
                    hp["observed_repeat_count"],
                )
                hp["peak_count"] = _pc
                if _pc is not None and _pc != hp["observed_repeat_count"]:
                    hp["count_reliable"] = False
            else:
                hp["peak_count"] = None
        if src is not None and v.get("read_pos") in set(src.get("slippage_positions") or []):
            v["slippage_artifact"] = True
        # 跨 read 干净覆盖反证（Step B 推广到非 poly 区）：indel 仅单 read
        # 支持、另一条 read 完整跨过同一 junction 却未报 → 互检矛盾。
        # 真实变异（clonal）必然被覆盖该位的每条 read 独立报出，单 read
        # 独有的 indel 是伪影高发形态，不能单独驱动"蛋白与设计不一致"
        contradicts: List[Dict] = []
        weak_cover: List[Dict] = []
        if (v.get("support_reads") or 1) == 1:
            if v.get("type") in ("insertion", "deletion"):
                contradicts = _clean_cover_reads(
                    v, read_results, ref2call_by_read, keys_by_read, weak_cover)
            elif v.get("type") == "substitution":
                contradicts = _clean_cover_reads_sub(
                    v, read_results, ref2call_by_read, keys_by_read,
                    mixed_by_read, weak_cover)
            if weak_cover and not contradicts:
                # 有其他引物读出设计碱基但该区质量不够：明确告诉用户为什么没能互检
                v["cross_read_weak"] = sorted(
                    weak_cover, key=lambda h: -h["region_mean_q"])
            if contradicts:
                # 按验证区域平均 Q 从高到低排序，首条即质量最高的验证 read
                v["cross_read_conflict"] = {
                    "reads": [h["read"] for h in contradicts],
                    "evidence": contradicts,
                }
        v["confidence"] = _variant_confidence(
            v, mixed_by_read.get(v.get("read", ""), set()), evidence,
            zone=src.get("reliable_zone") if src is not None else None,
        )
        # 互检定案：单 read 差异被其他引物在可靠区干净读出参考碱基——
        # 判为源 read 的测序误差，直接排除，不再作为"不确定"写进结论。
        # indel 是伪影高发形态，一律定案；替换只有源 read 自身证据不强
        # （非高置信）时定案——强峰对强峰才是真正需要人工看的互检矛盾
        if contradicts and (v.get("type") != "substitution"
                            or v["confidence"] != "high"):
            v["cross_read_resolved"] = True
        # Step A 抑制：变异位点落在源 read 的合并压缩 run 窗内 → 该 read 在
        # 此处根本没有可分辨证据，逐位判定（含 indel 锚点）不可信，强制降级
        # （跨 read 互证一致的变异不受单 read 降级影响——support_reads 聚合
        # 来自各 read 独立证据，merged read 的伪差异已被 mixed 抑制挡在门外）
        if _merged_zone_artifact(v, src):
            v["confidence"] = "low"
            v["merged_zone_artifact"] = True
        # 互检矛盾：另一条 read 的干净覆盖压过单 read 的 indel 报告——
        # 与 poly 合并区抑制同口径，矛盾证据不进确证判定与共识
        if v.get("cross_read_conflict") and v.get("confidence") != "low":
            v["confidence"] = "low"
        # 单 read indel 遇到"读出设计序列但区域质量不够定案"的验证 read：
        # 不足以排除，但也不能让单条 read 的 indel 独自改写蛋白结论（与此前
        # junction 反证同口径），降为低置信、写明原因待人工核对
        if (v.get("cross_read_weak") and v.get("type") in ("insertion", "deletion")
                and v.get("confidence") != "low"
                and v["cross_read_weak"][0].get("core_min_q", 0) >= CROSS_CHECK_MIN_Q):
            v["confidence"] = "low"
        if v.get("confidence") == "low" and not v.get("cross_read_resolved"):
            v["low_reason"] = _low_confidence_reason(
                v, mixed_by_read.get(v.get("read", ""), set()), evidence,
                zone=src.get("reliable_zone") if src is not None else None)
            wk = (v.get("cross_read_weak") or [None])[0]
            if wk:
                v["low_reason"] += (
                    f"；{wk['read']} 读出设计碱基，但{region_quality_text(wk)}，"
                    f"质量不足以互检")

    # tracy 交叉印证仅作标记、不改置信度：两个 caller 读的是同一条 read 的
    # 同一个信号，信号弱点（峰压缩/低 Q 区）的错误高度相关，call 一致并不
    # 构成独立证据。是否真实仍由峰级证据、多 read 支持与 Q 值决定。
    corrob_keys: set = set()
    for r in read_results:
        for cv in r.get("cross_variants") or []:
            corrob_keys.add(_variant_key(cv))
    if corrob_keys:
        for v in variants:
            if _variant_key(v) in corrob_keys:
                v["corroborated_by_basecall"] = True

    # 低置信调用不写入共识：共识序列是当前证据下的最佳猜测构建体，
    # 疑似测序噪声仅在变体清单中列出供人工核对（与 CDS 结论的 confirmed 口径一致）
    low_keys = {_variant_key(v) for v in variants if v.get("confidence") == "low"}
    consensus = _build_consensus(ref, read_results, skip_keys=low_keys,
                                 circular=circular)

    # 覆盖区间按折回坐标合并：环状跨原点 read 拆成 [s, L] + [1, e-L] 两段
    # （线性比对即 (ref_start, ref_end)，与此前一致）
    coverage_ranges = merge_coverage(
        [sp for r in read_results for sp in aln_segments(r["alignment"])],
        len(ref),
    )
    coverage_gaps = _coverage_gaps(coverage_ranges, len(ref), circular=circular)

    # poly 重复结构清单：仅列出落在测序覆盖范围内的结构，回填测得重复数。
    # 除调用碱基外，对每条覆盖该结构的 read 做峰图独立计数并交叉验证：
    # 正反 read 的滑移位置不同，计数互差或与调用数不符 → 重复数不可靠。
    # B2：每条覆盖 read 同时输出宽度法长度估计（峰完全合并时峰数法失效的
    # 救援）；B3：period>1 重复只做结构标注（重复数以单元计），不做峰图计数
    homopolymer_report: List[Dict] = []
    for run in poly_runs:
        if not any(max(run["start"], s) <= min(run["end"], e) for s, e in coverage_ranges):
            continue
        entry = {
            "base": run["base"], "unit": run["unit"], "period": run["period"],
            "tier": run["tier"], "lus": run["lus"],
            "start": run["start"], "end": run["end"], "length": run["length"],
            "ref_repeat_count": run["repeat_count"],
            "observed_repeat_count": run["repeat_count"],
            "count_reliable": True,
            "variant": None,
        }
        for v in variants:
            hp = v.get("homopolymer")
            if (hp and hp["base"] == run["base"] and hp["unit"] == run["unit"]
                    and hp["start"] == run["start"]
                    and v["type"] in ("insertion", "deletion")):
                entry["observed_repeat_count"] = hp["observed_repeat_count"]
                entry["count_reliable"] = hp.get("count_reliable", True)
                entry["variant"] = {
                    "ref_pos": v["ref_pos"], "type": v["type"],
                    "length": int(v.get("length") or 1),
                    "confidence": v.get("confidence"),
                }
                break
        run_reads: List[Dict] = []
        if run["period"] == 1:
            for r in read_results:
                aln = r["alignment"]
                # 环状跨原点 read：run 位于原点后段时在展开坐标中平移 +L，
                # 覆盖段 cs/ce 折回 run 自身坐标（线性 off 恒为 0）
                off = _run_offset(aln, run["start"], run["end"])
                rs_, re_ = run["start"] + off, run["end"] + off
                if aln["ref_end"] < rs_ or aln["ref_start"] > re_:
                    continue
                # 覆盖判读（B4）：完整跨过才给整段证据；截断/起始落在结构内的
                # read 只对覆盖段负责——人工核对会综合两条 read 而不是整体舍弃，
                # 部分覆盖的 read 照常数峰、只与其覆盖段比较
                full_aln = aln["ref_start"] <= rs_ and aln["ref_end"] >= re_
                cs = run["start"] if full_aln else max(rs_, aln["ref_start"]) - off
                ce = run["end"] if full_aln else min(re_, aln["ref_end"]) - off
                win = _aligned_run_window(aln, cs, ce, run["base"])
                called = None
                pc = None
                est = None
                full = full_aln
                if win is not None:
                    i0, i1, aln_called = win
                    # B4：质量修剪常把 poly 末端低 Q 压缩区剪掉，而那里正是
                    # 计数证据所在（SnapGene 显示原始 read，所以人工看到的
                    # 覆盖比修剪后长）——峰窗沿原始 read 的连续同碱基外扩、
                    # 越出修剪边界。两侧都见到非 run 碱基侧翼时，该 read 的
                    # run 完整对应参考整段（人类"数两个侧翼之间的 A 串"口径）
                    s_off = r.get("trim_start") or 0
                    raw_bases = (r.get("raw_bases") or "").upper()
                    b_up = run["base"]
                    # 反向 read 的原始碱基串是读向的：run 在读向为互补碱基
                    b_read = b_up if aln["direction"] == "+" else _COMPLEMENT[b_up]
                    r0, r1 = s_off + i0, s_off + i1
                    if raw_bases:
                        while r0 > 0 and raw_bases[r0 - 1] == b_read:
                            r0 -= 1
                        while r1 < len(raw_bases) - 1 and raw_bases[r1 + 1] == b_read:
                            r1 += 1
                        # 对齐列窗口可能比连续 run 段宽（缺失放 run 前端时
                        # 末端多包非 run 碱基）：向内收紧到连续 run 碱基，
                        # 峰窗即"两个侧翼之间的 A 串"——非 run 碱基自己的
                        # 通道峰会经跨通道 max 混进 _poly_peak_count 计数
                        while r0 <= r1 and raw_bases[r0] != b_read:
                            r0 += 1
                        while r1 >= r0 and raw_bases[r1] != b_read:
                            r1 -= 1
                        flank_l = r0 > 0 and raw_bases[r0 - 1] not in (b_read, "N")
                        flank_r = (r1 < len(raw_bases) - 1
                                   and raw_bases[r1 + 1] not in (b_read, "N"))
                        full = full_aln or (flank_l and flank_r)
                        if full:
                            cs, ce = run["start"], run["end"]
                        called = sum(1 for c in raw_bases[r0:r1 + 1] if c == b_read)
                        pc = _poly_peak_count(r["trace"], r["peak_indices"],
                                              r0 + 1, r1 - r0 + 1)
                        if not full:
                            ext_low, ext_high = (s_off + i0) - r0, r1 - (s_off + i1)
                            if aln["direction"] == "-":
                                ext_low, ext_high = ext_high, ext_low
                            cs = max(run["start"], cs - ext_low)
                            ce = min(run["end"], ce + ext_high)
                        est = estimate_run_length(
                            r["trace"], r["peak_indices"], r0, r1,
                            maps=_signal_maps(r))
                    else:
                        called = aln_called
                        pc = _poly_peak_count(r["trace"], r["trimmed_peaks"],
                                              i0 + 1, i1 - i0 + 1)
                        est = estimate_run_length(
                            r["trace"], r["trimmed_peaks"], max(0, i0),
                            min(i1, len(r["trimmed_peaks"]) - 1),
                            maps=_signal_maps(r))
                run_reads.append({
                    "filename": r["filename"], "direction": aln["direction"],
                    "coverage": "full" if full else "partial",
                    "covered_span": [cs, ce],
                    "called_count": called,
                    "peak_count": pc,
                    "length_estimate": est["n"] if est else None,
                    "length_method": est["method"] if est else None,
                    "length_ci": ([est["ci_low"], est["ci_high"]] if est else None),
                    # Step A 单 read 判读（先单条分析）：resolvable 峰图证据
                    # 完整 / merged 峰合并不可判定 / conflicting 证据内部矛盾
                    **_read_run_verdict(
                        called, pc, est,
                        *_anchor_verdict_for_read(r, run, aln)),
                })
        entry["read_counts"] = run_reads
        # 整段重复数的峰图估计只聚合完整覆盖 read：部分覆盖 read 的峰数
        # 只对应覆盖段，混入会把"段内计数"误当"整段计数"；合并 read 的
        # peak_count=0 是"无可分辨峰"而非真实计数，混进中位数会把极端
        # [30,0,0] 压成 0——只取 >0 的可分辨计数（全合并则无证据 → None）
        full_pcs = [x["peak_count"] for x in run_reads
                    if x["coverage"] == "full" and x["peak_count"]]
        entry["peak_count_estimate"] = (sorted(full_pcs)[len(full_pcs) // 2]
                                        if full_pcs else None)
        est_pool = [x for x in run_reads if x["length_estimate"] is not None]
        use = [x for x in est_pool if x["coverage"] == "full"] or est_pool
        ests = [x["length_estimate"] for x in use]
        cis = [x["length_ci"] for x in use if x["length_ci"] is not None]
        methods = [x["length_method"] for x in use if x["length_method"]]
        entry["length_estimate"] = sorted(ests)[len(ests) // 2] if ests else None
        entry["length_method"] = max(set(methods), key=methods.count) if methods else None
        entry["length_ci"] = ([min(c[0] for c in cis), max(c[1] for c in cis)]
                              if cis else None)
        # Step B：跨 read / 跨链综合评判（后综合评判）
        # 只有 resolvable read（峰数==调用数）拥有完整的可见计数证据，才有
        # 投票权；deficit（可见缺失）与 merged（峰合并不可分辨）的 read 不
        # 投票。旧口径"任一 read 峰数≠调用数即不可信"会把真实难例（正向
        # 可分辨且互证一致 + 反向合并成压缩山丘）整体否决——反向合并是该
        # read 的信号极限，不是对正向证据的反驳
        votes = [x for x in run_reads
                 if x.get("resolution") == "resolvable"
                 and (x["coverage"] == "full"
                      or (x.get("covered_span") and x["called_count"] is not None
                          and x["peak_count"] == x["called_count"]
                          and x["covered_span"][0] <= run["start"]
                          and x["covered_span"][1] >= run["end"]))]
        deficit = [x for x in run_reads
                   if x.get("resolution") == "deficit"
                   and x["coverage"] == "full"]
        merged_any = [x for x in run_reads
                      if x.get("resolution") == "merged"]
        _vote_rec = lambda x: {"filename": x["filename"],
                               "direction": x["direction"],
                               "peak_count": x["peak_count"],
                               "anchor": x.get("anchor_grade")}
        # 联合覆盖投票（Step B 扩展）：无完整覆盖 read 时，多条部分覆盖且
        # 段内自洽（resolvable）的 read 拼满整段 run 也可联合确证——
        # 段间须恰好衔接（重叠段峰计数无法按位分解，保守跳过）、并集盖满
        # run 起止，联合观测数 = 各段调用数之和（各段 pc==called）
        joint_votes: List[Dict] = []
        joint_total: Optional[int] = None
        _partials = sorted(
            (x for x in run_reads
             if x.get("resolution") == "resolvable" and x["coverage"] == "partial"
             and x.get("covered_span") and x.get("called_count") is not None),
            key=lambda x: x["covered_span"][0])
        _cursor: Optional[int] = None
        for x in _partials:
            cs, ce = x["covered_span"]
            if _cursor is None:
                if cs != run["start"]:
                    break                      # 首段未从 run 起点开始 → 有缺口
                _cursor = ce
                joint_total = int(x["called_count"])
                joint_votes.append(x)
            elif cs <= _cursor:
                continue                       # 与已覆盖段重叠：冗余，跳过
            elif cs == _cursor + 1:
                _cursor = ce                   # 恰好衔接，继续拼接
                joint_total += int(x["called_count"])
                joint_votes.append(x)
            else:
                break                          # 段间缺口
        joint_ok = bool(joint_votes) and _cursor == run["end"]
        # 投票 read 的锚定分级最差者（accepted 只确证「重复数」，不确证
        # 「位置」——路标踩在信号异常区时 run 起止定位仍可能偏移，结论
        # 需显式写明，见 _poly_anchor_notes）
        if votes or joint_votes:
            entry["vote_anchor_grade"] = max(
                (x.get("anchor_grade") or "reliable" for x in (votes or joint_votes)),
                key=lambda g: _ANCHOR_RANK.get(g, 1))
        if votes:
            pcs = sorted({x["peak_count"] for x in votes})
            if len(pcs) == 1 and (not joint_ok or joint_total == pcs[0]):
                # 可分辨 read 互证一致（含单条）：重复数采信峰图实测；
                # 联合覆盖读数与完整覆盖一致时互为印证
                entry["run_verdict"] = "accepted"
                entry["count_reliable"] = True
                entry["observed_repeat_count"] = pcs[0]
                entry["verdict_votes"] = [_vote_rec(x) for x in votes]
            elif len(pcs) == 1 and joint_ok and joint_total != pcs[0]:
                # 完整覆盖 read 与联合覆盖读数冲突（两段拼接各自自洽但互相
                # 矛盾——两克隆混合的典型形态）：不可信
                entry["run_verdict"] = "contradictory"
                entry["count_reliable"] = False
                entry["verdict_votes"] = [_vote_rec(x) for x in votes]
                entry["verdict_votes"].append({
                    "joint": True, "observed_total": joint_total,
                    "reads": [_vote_rec(x) for x in joint_votes]})
            else:
                # 可分辨 read 之间互证矛盾：不可信，需人工核对或换引物
                entry["run_verdict"] = "contradictory"
                entry["count_reliable"] = False
                entry["verdict_votes"] = [_vote_rec(x) for x in votes]
        elif joint_ok:
            # 联合覆盖确证：各段内部自洽且拼满整段——等价于一条完整覆盖
            # read 的证据（各 read 独立信号，段间无重叠无缺口）
            entry["run_verdict"] = "accepted"
            entry["count_reliable"] = True
            entry["observed_repeat_count"] = joint_total
            entry["joint_coverage"] = True
            entry["verdict_votes"] = [_vote_rec(x) for x in joint_votes]
        elif deficit:
            # 无完整互证，但有可见缺失证据（B4 口径）：主判读以峰图为准，
            # 计数不确证（部分合并可能掩盖更多缺失）
            entry["run_verdict"] = "deficit_observed"
            entry["count_reliable"] = False
        elif merged_any:
            # 整段合并、无可分辨证据：不可判定。
            # 宽度法估计照常输出但仅为模型参考，不作为判定
            entry["run_verdict"] = "undetermined"
            entry["count_reliable"] = False
        else:
            # 无任何峰图计数证据（采样不足/仅部分覆盖且一致）：无矛盾证据，
            # 维持调用数（旧口径兼容，spare 合成稀疏数据场景）
            entry["run_verdict"] = "caller_only"
            entry["count_reliable"] = True
        # 主判读以峰图为准（B4）：每条 read 覆盖段内，参考段长 − 可分辨峰 =
        # 该段缺失数（峰只会被压缩合并、不会凭空多出，段内峰数即实测）；
        # 整段缺失取各 read 覆盖段缺失的最大值（缺失只能被覆盖它的 read 看见），
        # 实测 = 参考 − 缺失 + 插入。峰数法整体失效（None/0）时不编造实测数，
        # 交宽度法口径与人工判读
        usable = [x for x in run_reads if x["peak_count"] and x.get("covered_span")]
        if usable:
            seg_len = lambda x: x["covered_span"][1] - x["covered_span"][0] + 1
            entry["peak_missing"] = max(0, max(seg_len(x) - x["peak_count"]
                                               for x in usable))
            entry["peak_inserted"] = max(0, max(x["peak_count"] - seg_len(x)
                                                for x in usable))
            entry["peak_measured"] = (run["repeat_count"] - entry["peak_missing"]
                                      + entry["peak_inserted"])
        cov_pos: set = set()
        for x in run_reads:
            if x.get("covered_span"):
                cov_pos.update(range(x["covered_span"][0], x["covered_span"][1] + 1))
        entry["run_covered"] = len(cov_pos)
        homopolymer_report.append(entry)

    # 互检定案的差异（单 read 报告、其他引物可靠区干净读出参考）不再进入
    # 任何判读文本：变体清单保留并标注，结论只用一句话交代排除了几处
    shown_variants = [v for v in variants if not v.get("cross_read_resolved")]
    resolved_variants = [v for v in variants if v.get("cross_read_resolved")]

    # 终审 C-08：CDS 判读的意外异常降级为单条失败报告，不得让一条
    # 畸形特征（空蛋白比对、非法翻译边界等）拖垮整份多 read 分析
    try:
        cds_reports = _build_cds_reports(ref, features, shown_variants, consensus)
    except Exception as e:
        logger.exception("CDS 判读整体失败，降级为占位报告")
        cds_reports = [{
            "name": "CDS",
            "start": 0,
            "end": 0,
            "strand": "+",
            "covered_percent": 0.0,
            "coverage_status": "partial",
            "ref_protein_length": None,
            "alt_protein_length": None,
            "protein_identical": None,
            "premature_stop_aa": None,
            "frameshift_count": 0,
            "aa_changes": [],
            "consequences": [],
            "synonymous_count": 0,
            "pending_low_confidence": 0,
            "verdict": f"CDS 判读失败（内部异常：{type(e).__name__}），变体明细见突变表，可核对峰图或反馈日志",
        }]

    # B5：poly 下游信号骤降注记（end_truncation 属 read 末端正常下降，不算）。
    # 与"未覆盖"明确区分：覆盖区内的骤降段碱基判读不可信，不是没测到
    dropout_notes: List[str] = []
    seen_drop: set = set()
    for r in read_results:
        for d in r.get("post_poly_dropouts") or []:
            if d.get("end_truncation"):
                continue
            key = (d["base"], d["ref_start"], d["ref_end"])
            if key in seen_drop:
                continue
            seen_drop.add(key)
            ratio_txt = f"{round((d['ratio'] or 0) * 100)}%" if d.get("ratio") is not None else "?"
            rec = "" if d.get("recovered") else "且未见恢复"
            dropout_notes.append(
                f"  ↳ poly({d['base']}) 同聚物 {d['ref_start']}-{d['ref_end']} 下游检测到信号骤降"
                f"（降至约 {ratio_txt}{rec}）：该段碱基判读不可信（区别于未覆盖），"
                "建议人工核对峰图或换引物复测"
            )

    # 双峰（疑似混合样品）结论行：read 级分级后给可行动的提示。
    # 混合样品影响整份判读（主峰序列只是多数克隆），必须显式写出——此前
    # mixed_positions 只进结构化字段，结论与报告完全不可见，混合样品会被
    # 报成"与设计一致"。多条 read 全部 widespread 时聚合成一条综合判读
    # （逐 read 明细在报告双峰章节），否则 N 条近似长句把关键建议淹没
    mixed_profiles = {r["filename"]: r["mixed_profile"] for r in read_results
                      if r["mixed_profile"]["count"] > 0}
    mixed_lines: List[str] = []

    def _frac_range(ps: List[Dict]) -> str:
        fr = sorted(round(p["minor_fraction"] * 100) for p in ps
                    if p.get("minor_fraction"))
        if not fr:
            return ""
        lo, hi = fr[0], fr[-1]
        return f"约 {lo}%" if lo == hi else f"约 {lo}–{hi}%"

    wide = [(r, r["mixed_profile"]) for r in read_results
            if r["mixed_profile"]["class"] == "widespread"]
    crossable = len(read_results) >= 2   # 单条 read 无从互检
    if len(wide) == 1:
        r, p = wide[0]
        chk = mixed_corroboration["by_read"].get(r["filename"]) or {}
        frac_txt = _frac_range([p])
        # 互检主导措辞：多数双峰位点被其他引物覆盖且峰形单一、无同报 →
        # 倾向该 read 自身信号问题（常见于引物首端），而非真实混合
        noise_like = (crossable and chk.get("multi", 0) == 0
                      and chk.get("clean", 0) >= max(1, p["count"] // 2))
        if noise_like:
            line = (f"⚠ {r['filename']} 检出 {p['count']} 处双峰位点"
                    + (f"（估计次要克隆占比{frac_txt}）" if frac_txt else "")
                    + "——跨引物互检：多数双峰位点被其他引物测过且峰形单一、判读一致，"
                    "更倾向该 read 自身信号问题（信号不稳区以引物首端为主）"
                    "而非真实混合，建议核对峰图与互检位置后复测该段")
            if p["longest_stretch"] >= MIXED_STRETCH_MIN and p.get("span"):
                line += (f"；自位置 {p['span'][0]} 起存在连续双峰段"
                         "（若互检未覆盖该段，需警惕两个克隆相差插入/缺失）")
        else:
            line = (f"⚠ 疑似混合样品：{r['filename']} 检出 {p['count']} 处双峰位点"
                    + (f"（估计次要克隆占比{frac_txt}）" if frac_txt else "")
                    + "，主峰序列按多数碱基判读——建议重新挑单克隆划线培养后复测")
            if p["longest_stretch"] >= MIXED_STRETCH_MIN:
                line += (f"；自位置 {p['span'][0]} 起存在连续双峰段"
                         "（两个克隆相差插入/缺失时的典型形态）")
            note = _mixed_check_phrase(chk) if crossable else ""
            if note:
                line += f"；{note}"
        mixed_lines.append(line)
    elif wide:
        counts = sorted(p["count"] for _r, p in wide)
        cr = (str(counts[0]) if counts[0] == counts[-1]
              else f"{counts[0]}–{counts[-1]}")
        frac_txt = _frac_range([p for _r, p in wide])
        line = (f"⚠ {len(wide)} 条 read 均疑似混合样品（每条双峰位点 {cr} 处"
                + (f"，估计次要克隆占比{frac_txt}" if frac_txt else "")
                + "）——样品为两种质粒的混合，主峰序列按多数碱基判读，"
                "建议重新挑单克隆划线培养后复测")
        stretchy = [r["filename"] for r, p in wide
                    if p["longest_stretch"] >= MIXED_STRETCH_MIN and p.get("span")]
        if stretchy:
            names = "、".join(stretchy[:2])
            line += f"；{names} 存在连续双峰段（两个克隆相差插入/缺失时的典型形态）"
        mixed_lines.append(line)
    # 逐 read 位点范围子行（↳ 详情行，前端默认折叠）：核对"双峰是否落在
    # 引物首尾"需要具体范围——read 坐标 + 互检结果
    for r, p in wide:
        chk = mixed_corroboration["by_read"].get(r["filename"]) or {}
        note = _mixed_check_phrase(chk) if crossable else ""
        # 双峰位点是修剪后 read 坐标，而 zone_text（read 概况/结论尾部）报原始坐标；
        # 两者不同时把原始区间一并写出，避免同一份结论里两处"可信区"看起来打架
        z = r["reliable_zone"]
        zone_txt = f"{z['start']}–{z['end']}"
        if (z["raw_start"], z["raw_end"]) != (z["start"], z["end"]):
            zone_txt += f"（原始 {z['raw_start']}–{z['raw_end']}）"
        base = (f"  ↳ {r['filename']}：双峰 {p['count']} 处，位于 read "
                f"{_pos_ranges_str(p['positions'])}"
                f"（read 坐标；可信区 {zone_txt} 以外未计入）")
        mixed_lines.append(base + (f"；{note}" if note else ""))
    scat = [(r, r["mixed_profile"]) for r in read_results
            if r["mixed_profile"]["class"] == "scattered"]
    if len(scat) == 1:
        r, p = scat[0]
        pos_preview = "、".join(str(x) for x in p["positions"][:6])
        chk = mixed_corroboration["by_read"].get(r["filename"]) or {}
        noise_like = (crossable and chk.get("multi", 0) == 0
                      and chk.get("clean", 0) >= max(1, p["count"] // 2))
        advice = ("多数位点被其他引物测过且峰形单一，更倾向测序噪声"
                  if noise_like else "可能为个别碱基噪声或低比例混合，建议核对峰图")
        mixed_lines.append(
            f"⚠ {r['filename']} 有 {p['count']} 个双峰位点（位置 {pos_preview}）："
            f"{advice}")
        note = _mixed_check_phrase(chk) if crossable else ""
        if note:
            mixed_lines.append(f"  ↳ {r['filename']}（{p['count']} 处双峰位点）：{note}")
    elif scat:
        n_pos = sum(p["count"] for _r, p in scat)
        names = "、".join(r["filename"] for r, _p in scat[:2])
        more = f" 等 {len(scat)} 条 read" if len(scat) > 2 else ""
        mixed_lines.append(
            f"⚠ {names}{more} 共有 {n_pos} 个双峰位点："
            "可能为个别碱基噪声或低比例混合，建议核对峰图")
        for r, p in scat:
            chk = mixed_corroboration["by_read"].get(r["filename"]) or {}
            note = _mixed_check_phrase(chk) if crossable else ""
            base = (f"  ↳ {r['filename']}：{p['count']} 处双峰位于 read "
                    f"{_pos_ranges_str(p['positions'])}（read 坐标）")
            mixed_lines.append(base + (f"；{note}" if note else ""))
    # 跨引物互检汇总（↳ 行随逐 read 明细折叠）：多引物同报双峰的参考位置
    # 是真实混合的最强信号；其余按 复核一致/无覆盖 归类
    if crossable and mixed_corroboration["total"] > 0:
        mc = mixed_corroboration
        parts = []
        if mc["multi_sites"]:
            pos_txt = "、".join(str(rp) for rp, _fs in mc["multi_sites"][:4])
            more = " 等" if len(mc["multi_sites"]) > 4 else ""
            parts.append(f"{len(mc['multi_sites'])} 处参考位点被多条引物同报双峰"
                         f"（参考位置 {pos_txt}{more}，倾向真实混合）")
        if mc["clean_total"]:
            parts.append(f"{mc['clean_total']} 处其他引物覆盖且峰形单一（倾向噪声）")
        if mc["uncov_total"]:
            uncov_refs = sorted({rp
                                 for d in mc["by_read"].values()
                                 for rp in d.get("uncovered_ref") or []})
            ref_txt = _pos_ranges_str(uncov_refs)
            parts.append(f"{mc['uncov_total']} 处仅单条引物覆盖，无法互检"
                         + (f"（参考位置 {ref_txt}）" if ref_txt else ""))
        if parts:
            mixed_lines.append("  ↳ 双峰位点跨引物互检：" + "；".join(parts))

    # read 可信区显式提示：逐条 read 按位置先验 + Q 值与峰型判定，区外差异
    # 按低置信处理；有其他引物覆盖的位点已自动互检定案，不再给用户布置
    # "自己去看另一条引物"的核对作业。逐 read 可信区写成 ↳ 详情行
    zp = ((read_results[0].get("reliable_zone") or {}).get("params")
          if read_results else None) or DEFAULT_ZONE.as_dict()
    end_note = (
        f"注意：read 首 {zp['front_hard']}bp 与 {zp['tail_gray']}bp 之后为不可信区"
        f"（{zp['front_hard'] + 1}–{zp['front_gray']}bp 按 Q 值与峰型逐条判定，"
        f"{zp['tail_hard']}bp 之后一律不可信），该区差异按低置信处理"
        f"{'，有其他引物覆盖的位点已自动互检' if len(read_results) >= 2 else ''}"
    )
    zone_rows = [f"  ↳ {r['filename']}：{zone_text(r['reliable_zone'])}"
                 for r in read_results if r.get("reliable_zone")]
    if zone_rows:
        end_note = "\n".join([end_note] + zone_rows)

    # 互检定案一句话：多引物交叉验证已给出确定答案，不再逐条罗列为
    # "低置信/待核对"（逐条明细仍在变体表，标注为互检排除）
    resolved_line = ""
    if resolved_variants:
        rows = []
        for v in resolved_variants[:8]:
            ev = ((v.get("cross_read_conflict") or {}).get("evidence") or [{}])[0]
            q = v.get("read_q")
            rows.append(
                f"  ↳ 位置 {v['ref_pos']} {variant_type_text(v)}：{v.get('read', '')}"
                + (f" 该位 Q{q}" if q is not None else "")
                + (f"；{ev['read']} 读出设计碱基（{region_quality_text(ev)}）"
                   if ev.get("read") else ""))
        if len(resolved_variants) > 8:
            rows.append(f"  ↳ 另有 {len(resolved_variants) - 8} 处，见差异明细表")
        resolved_line = "\n".join(
            [f"互检排除 {len(resolved_variants)} 处单条 read 差异：其他引物在该区域"
             f"高质量覆盖（位点 ±{CROSS_REGION_HALF}bp 平均 Q≥{CROSS_REGION_MEAN_Q}、"
             f"≥Q20 占 {round(CROSS_REGION_HQ_FRAC * 100)}% 以上）并读出设计碱基，"
             "判为该 read 测序误差，不计入判定"] + rows)

    # 自动结论（编码区结论放最前，直接回答“整段 CDS 有没有问题”）
    cds_lines = [
        f"【{cr['name']} CDS】{cr['verdict']}"
        for cr in cds_reports if cr["coverage_status"] != "uncovered"
    ]
    if not read_results:
        conclusion = "没有可分析的测序文件"
    elif not shown_variants:
        conclusion = (
            f"构建序列与设计一致：{len(read_results)} 条 read "
            f"{'交叉验证无差异' if resolved_variants else '全部匹配'}，"
            f"覆盖参考序列的 {consensus['coverage_percent']:.1f}%"
            f"{_coverage_ranges_text(coverage_ranges)}"
        )
        # poly 重复数峰图计数存疑时不能只报“一致”（观察级 run 不告警）；
        # 三态 verdict 分措辞（Step B）：矛盾→不可信，不可判定→需换引物
        _verdict_warn = {
            "contradictory": "可分辨 read 之间计数互证矛盾，不可信",
            "undetermined": "无可分辨峰证据，重复数不可判定（建议换引物或人工核对峰图）",
            "deficit_observed": "峰图缺失证据，以峰图可分辨峰为准，建议人工复核",
        }
        poly_warnings = [
            f"注意：poly({e['base']}) 同聚物 {e['start']}-{e['end']} 峰图判读："
            f"{_peak_verdict_phrase(e)}——{_verdict_warn.get(e.get('run_verdict'), '以峰图可分辨峰为准，建议人工复核')}"
            for e in homopolymer_report
            if e["tier"] == "poly" and not e["count_reliable"]
        ]
        # 端点歧义按 verdict 豁免（与变体分支同口径）：run 已 accepted 时
        # 端点滑动不影响判定，不再提示
        _verdict_ok0 = {(e["base"], e["start"]) for e in homopolymer_report
                        if e.get("run_verdict") == "accepted"}
        edge_notes0 = [x for x in edge_ambiguous_reads
                       if not any((h["base"], h["start"]) in _verdict_ok0
                                  for h in x["hits"])]
        if edge_notes0:
            names = "、".join(x["filename"] for x in edge_notes0[:3])
            more = f" 等 {len(edge_notes0)} 条" if len(edge_notes0) > 3 else ""
            first = edge_notes0[0]["hits"][0]
            run_txt = (f"poly({first['base']}) {first['start']}-{first['end']}"
                       if first["base"] else f"{first['start']}-{first['end']}")
            poly_warnings.append(
                f"注意：{names}{more} 的比对端点落在同聚物区内部"
                f"（如 {run_txt}）——同聚物内的比对落点存在歧义，覆盖边界与"
                "互检定位可能有数 bp 偏差，建议对照图谱核对")
        # accepted 但锚不可靠的 run：计数确证 ≠ 定位确证，须显式写明
        poly_warnings.extend(_poly_anchor_notes(homopolymer_report))
        # 双峰提示紧跟首行：混合样品即使主克隆与设计一致也必须显式提示
        all_lines = ([conclusion, resolved_line] + mixed_lines + cds_lines + poly_warnings
                     + dropout_notes + [end_note])
        conclusion = "\n".join(x for x in all_lines if x)
    else:
        lines = [f"共检出 {len(shown_variants)} 处差异（覆盖 {consensus['coverage_percent']:.1f}%"
                 f"{_coverage_ranges_text(coverage_ranges)}）："]
        lines.extend(mixed_lines)  # 疑似混合样品影响整份判读，紧跟首行
        lines.extend(summarize_severity(shown_variants))
        if resolved_line:
            lines.append(resolved_line)
        # CDS 结论紧随差异摘要，poly 判读放后面（整段 CDS 有没有问题是第一信息）
        lines.extend(cds_lines)
        # poly 判读（合并口径，C2）：峰图计数与调用不一致的 poly 结构只出一条
        # 线——变体锚点 + 峰图实测 + 逐 read 调用/峰数。此前变体行与峰图判读
        # 行两条 ↳ 各说一遍，同一事件在结论里出现两次
        emitted_runs = {(e["base"], e["unit"], e["start"])
                        for e in homopolymer_report
                        if e["tier"] == "poly" and not e["count_reliable"]
                        and e["read_counts"]}

        def _rc_label(x: Dict) -> str:
            d = "反向" if x["direction"] == "-" else "正向"
            n = x["peak_count"] if x["peak_count"] is not None else "?"
            if x.get("coverage") == "partial":
                s, t = x["covered_span"]
                return (f"{x['filename']}{d}覆盖段{s}-{t}"
                        f"调用{x['called_count']}/峰{n}个")
            return f"{x['filename']}{d}调用{x['called_count']}/峰{n}个"

        # poly 判读：计数可靠的重复区 indel 单列重复数变化（实验员关心的
        # “多了/少了几个”）；峰图矛盾的结构由下面的峰图判读行统一覆盖
        for v in shown_variants:
            hp = v.get("homopolymer")
            if not (hp and v.get("type") in ("insertion", "deletion")):
                continue
            if (hp.get("base"), hp.get("unit"), hp.get("start")) in emitted_runs:
                continue
            kind = "插入" if v["type"] == "insertion" else "缺失"
            rel = ("重复数计数可靠" if hp.get("count_reliable", True)
                   else "峰压缩区，重复数计数可能不准，建议人工核对峰图")
            if hp.get("peak_count") is not None:
                rel += f"；峰图独立计数 {hp['peak_count']} 个"
            lines.append(
                f"  ↳ {_run_label(hp)} {hp['start']}-{hp['end']}："
                f"参考 {hp['ref_repeat_count']} 个，测得 {hp['observed_repeat_count']} 个"
                f"（位置 {v['ref_pos']} {kind} {int(v.get('length') or 1)}bp，{rel}）"
            )
        # 峰图计数与调用不一致（即使无 indel 变体）：长同聚物的碱基调用可能
        # 整段偏差——一条线给全 变体锚点/实测数/逐 read 证据
        for e in homopolymer_report:
            if e["tier"] != "poly" or e["count_reliable"] or not e["read_counts"]:
                continue
        # 峰图判读行按三态 verdict 分措辞（Step B）：
        # contradictory 互证矛盾 → 不可信；undetermined 无可分辨证据 → 不可
        # 判定（宽度法仅供参考）；accepted 但整体计数不可靠的（如仅 partial
        # read 覆盖）仍走旧提示。同一 run 只出一条线，逐 read 证据随行
        _verdict_phrase = {
            "contradictory": "可分辨 read 之间计数互证矛盾，不可信",
            "undetermined": "无可分辨峰证据，长度不可判定（宽度法估计仅供参考）",
            "deficit_observed": "峰图缺失证据，以峰图可分辨峰为准",
        }
        for e in homopolymer_report:
            if e["tier"] != "poly" or e["count_reliable"] or not e["read_counts"]:
                continue
            var = e.get("variant")
            anchor = ""
            if var:
                kind = "插入" if var["type"] == "insertion" else "缺失"
                anchor = f"位置 {var['ref_pos']} {kind} {var['length']}bp；"
            detail = " / ".join(_rc_label(x) for x in e["read_counts"])
            vt = _verdict_phrase.get(
                e.get("run_verdict"), "峰压缩区，以峰图可分辨峰为准")
            lines.append(
                f"  ↳ {_run_label(e)} {e['start']}-{e['end']} 峰图判读："
                f"{_peak_verdict_phrase(e)}（{anchor}{detail}）"
                f"——{vt}，建议人工核对峰图"
            )
        lines.extend(dropout_notes)
        # 端点歧义行按 verdict 豁免（Step B）：run 已有可分辨 read 互证出
        # 确定重复数（accepted）时，端点滑动不再影响判定——只有无判决或
        # 判决存疑的 run 才提示定位偏差
        _verdict_ok = {(e["base"], e["start"]) for e in homopolymer_report
                       if e.get("run_verdict") == "accepted"}
        edge_notes = [x for x in edge_ambiguous_reads
                      if not any((h["base"], h["start"]) in _verdict_ok
                                 for h in x["hits"])]
        if edge_notes:
            names = "、".join(x["filename"] for x in edge_notes[:3])
            more = f" 等 {len(edge_notes)} 条" if len(edge_notes) > 3 else ""
            first = edge_notes[0]["hits"][0]
            run_txt = f"poly({first['base']}) {first['start']}-{first['end']}" if first["base"] else f"{first['start']}-{first['end']}"
            lines.append(
                f"注意：{names}{more} 的比对端点落在同聚物区内部"
                f"（如 {run_txt}）——同聚物内的比对落点存在歧义，覆盖边界与"
                "互检定位可能有数 bp 偏差，建议对照图谱核对")
        # accepted 但锚不可靠的 run：计数确证 ≠ 定位确证，须显式写明
        lines.extend(_poly_anchor_notes(homopolymer_report))
        lines.append(end_note)
        if consensus["coverage_percent"] < 95:
            gap_hint = ""
            if coverage_gaps:
                g = coverage_gaps[0]
                gap_hint = (
                    f"；共 {len(coverage_gaps)} 段未覆盖缺口"
                    f"（最大 {_gap_span_text(g)}，{g['length']}bp）"
                )
            lines.append(
                f"注意：本次实测覆盖 {consensus['coverage_percent']:.1f}%，"
                f"未测区域按设计序列对待（部分测序是常规策略，无需测全）{gap_hint}"
            )
        conclusion = "\n".join(lines)

    # 结论头部一句话总结：详细段落（差异明细/CDS 结论/注意事项）信息密度高，
    # ✓/✗/△ 先给判读结果，空两行后再展开——回答“到底行不行”只用一眼
    # （scattered 双峰只提示不升级，不算混合）
    partial_reads = [r for r in read_results if r.get("partial_alignment")]
    if partial_reads:
        conclusion = "\n".join(
            [conclusion] + [_partial_alignment_note(r["filename"], r["partial_alignment"])
                            for r in partial_reads])
    if read_results:
        has_mixed = any("疑似混合样品" in x for x in mixed_lines)
        conclusion = (_conclusion_summary(shown_variants, cds_reports, has_mixed,
                                          partial_reads=len(partial_reads),
                                          resolved=len(resolved_variants))
                      + "\n\n\n" + conclusion)

    # 混合样品提示：检出疑似混合位点时建议人工复核或使用 tracy decompose 解卷积
    mixed_reads = [r for r in read_results if r["mixed_positions"]]
    any_tracy = any(r.get("cross_variants") is not None for r in read_results)  # tracy 实际运行过

    return {
        "reads": [
            {k: v for k, v in r.items()
             if k not in ("trace", "peak_indices", "cross_variants", "tracy_calls", "_sigmaps",
                          "raw_bases", "trim_start")}
            for r in read_results
        ],
        # 峰图原始数据（与 reads 同序）：四通道 + 碱基 + 质量 + 峰位置。
        # peak_indices 按原始 read 碱基索引，前端换算采样窗需要修剪偏移
        "traces": [
            {
                "filename": r["filename"],
                "bases": r["trimmed_bases"],
                "quality": r["trimmed_quality"],
                "channels": r["trace"],
                "peak_indices": r["peak_indices"],
                "trim_start": r.get("trim_start", 0),
            }
            for r in read_results
        ],
        "variants": variants,
        "consensus": consensus,
        "coverage_ranges": coverage_ranges,
        "coverage_gaps": coverage_gaps,
        "circular": bool(circular),
        "cds_reports": cds_reports,
        "homopolymers": homopolymer_report,
        "conclusion": conclusion,
        "mixed_detected": {r["filename"]: r["mixed_positions"] for r in mixed_reads},
        "mixed_profiles": mixed_profiles,
        "mixed_corroboration": mixed_corroboration,
        "errors": errors,
        "engine": "internal+biopython+tracy-basecall" if any_tracy else "internal+biopython",
    }
