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

from core.sanger.abif_reader import (  # noqa: F401
    AbiParseError, extract_read, estimate_quality_from_trace,
    peak_window as _peak_window,
)
from core.sanger.noise import peak_snr
from core.sanger.orf_guard import find_guarded_orf, suspect_site
from core.sanger.primer_check import check_reads
from core.sanger.scoring import attach_score, confidence_tier as _variant_confidence
from core.sanger.split_read import classify_segment
from core.sanger.artifacts import detect_artifacts, artifact_at as _artifact_at
from core.sanger.overlap import (
    find_discordances, downgrade_overlap_conflicts,
    double_strand_fraction as _double_strand_fraction,
)
from core.sanger.aligner import (
    align_read, align_read_circular, aln_segments, fold_pos, merge_coverage,
    check_unaligned_hq_segment,
)
from core.sanger.annotator import annotate_variants, summarize_severity
from core.seq_utils import revcomp as _seq_revcomp
from core.sanger.signal import (
    baseline_correct, property_maps, estimate_run_length,
    detect_phase_shift, detect_post_poly_dropout, continuous_read_length,
)
from core.sanger.reliable_zone import (
    DEFAULT_ZONE, ZoneParams, compute_reliable_zone, in_zone, zone_reason, zone_text,
)

# 同聚物判读簇已迁至 core/sanger/homopolymer.py（纯搬运）；此处 re-export
# 维持既有导入路径（tests / app.sequencing_report 均从 pipeline 取名）
from core.sanger.homopolymer import (  # noqa: F401
    CROSS_CHECK_MIN_Q, CROSS_CLEAN_HALF, CROSS_CORE_HALF, CROSS_REGION_HALF,
    CROSS_REGION_HQ_FRAC, CROSS_REGION_MEAN_Q, CROSS_REGION_MIN_COLS,
    HOMOPOLYMER_MIN, POLY_COMPRESSION_RATIO, POLY_MIN_UNITS, POLY_OBSERVE_MIN,
    POLY_PEAK_HEIGHT_RATIO,
    _circ_len, _ANCHOR_RANK, _COMPLEMENT, _aligned_run_window,
    _anchor_quality, _anchor_verdict_for_read, _annotate_homopolymer,
    _clean_cover_reads, _clean_cover_reads_sub, _corroborate_mixed,
    _mixed_check_phrase, _poly_anchor_notes, _poly_peak_amplitude_ratio,
    _poly_peak_count, _read_ref_maps, _read_run_verdict, _region_ok,
    _unit_run, _overlapping_run, find_homopolymers, find_repeat_runs,
    mixed_sample_confirmed,
    peak_verdict_phrase, pos_ranges_str, region_quality_text, run_label,
)
from core.sanger.resolved_regions import (
    build_resolved_regions, scattered_pending_lines,
)
from core.sanger.verdict import build_verdict
from core.sanger.scope import coerce_scope  # goal v3 W1：scope 入参归一
from core.sanger.readmap import ReadRefMap
from core.sanger.mixed_call import collect_mixed_variants
from core.sanger.coverage import compute_coverage
# 覆盖缺口与区间文本已迁至 core/sanger/coverage.py（T3 拆分，尺寸预算）；
# 此处 re-export 维持既有导入路径
from core.sanger.coverage import (  # noqa: F401
    _coverage_gaps, _coverage_ranges_text, _gap_span_text,
    expected_ranges,
)

# 证据三态与结论资格门已迁至 core/sanger/evidence.py（T1 拆分，尺寸预算）；
# 此处 re-export 维持既有导入路径（tests / app 层仍从 pipeline 取名）
from core.sanger.evidence import (  # noqa: F401
    _IUPAC_MERGE, _collect_unresolved_sites, _conclusion_summary,
    _insertion_peak_ratio, _iupac_merge, _low_confidence_reason,
    _variant_peak_evidence, _variant_reason_codes, _variant_status,
    attach_evidence_by_read, covering_not_supporting, variant_type_text,
)

TRACY_BIN = os.environ.get("TRACY_BIN", "tracy")
# 结果口径版本（goal_sanger_full_fix_v2 §2）：写入每次分析 result["algo_version"]，
# 任何改变结论口径的提交都要递增并在 HANDOVER §2 登记
SANGER_ALGO_VERSION = "v14"  # v14: goal v3 W10——scope 来源推断 infer_scope（design_insert/filename_match）、parse-reference 预设、Form scope、批量「验证范围」列；v13: W9——resolve_auto 参考名×read 名歧义判别、显式选择未去重（嵌套可选/重名报错）、变体 coords 双坐标（质粒+c./p.）
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


def _peak_snr(trace: Dict[str, List[int]], peak_indices: List[Optional[int]],
              read_pos: int, alt_base: str, called_bases: str = ""):
    """变体碱基通道信噪比（T13 起委托 noise.peak_snr：噪声本底改为
    局部非调用通道峰窗面积的 max(1, 1.4826×MAD)——旧「全轨迹中位数」
    在大面积静默通道（如 3730.ab1 的 T 通道）上恒 0 → None → 被误当
    响亮；新口径全零通道也给出确定 SNR）"""
    return peak_snr(trace, peak_indices, read_pos, alt_base, called_bases)




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
    if zone and rp and support < 2 and not in_zone(zone, rp) \
            and rp < zone.get("start", 0):
        # T12：位置对置信度的强制仅保留首端（front_hard 硬区 + 首端未稳定
        # 段——引物 3′ 端信号固有不可靠）；尾端由信号证据决定（S1 确证口径）
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


# 比对上的 read 长度低于修剪后长度的该比例即视为「部分比对」
PARTIAL_ALIGN_RATIO = 0.8


def alignment_span_text(aln: Dict, sep: str = "–") -> str:
    """read 比对区段的展示文本（报告用）：线性即「s–e」；环状跨原点按折回
    坐标分两段「s–L、1–e'」"""
    return "、".join(f"{a}{sep}{b}" for a, b in aln_segments(aln)) or \
        f"{aln.get('ref_start', 0)}{sep}{aln.get('ref_end', 0)}"




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


def _trim_span_mask(quality: List[int], min_q: int) -> Tuple[int, int]:
    """估 Q 路径的修剪口径（goal T24.5）：首末好碱基张成区间，凹陷掩蔽

    峰型估的 Q 对中段凹陷系统性偏悲观（峰高受染料团/压缩影响，不纯代表
    碱基错误率），Kadane 会在凹陷处把 read 截断丢掉一整侧。改为取首个与
    最后一个 Q≥min_q 的碱基张成区间：区间内的低 Q 凹陷**保留不截**，由
    调用方在 low_q_ranges 里掩蔽标注（Q 低 → 置信口径自然保守）。"""
    good = [i for i, q in enumerate(quality) if q >= min_q]
    if not good:
        return (0, 0)
    return (good[0], good[-1] + 1)


# 双峰检测与 read 级混合分级迁 core/sanger/mixed_model.py（goal T11；A 3.1；
# B P2-14 自适应背景模型）。此处 re-export 维持既有导入路径（tests/app 从
# pipeline 取名）；MIXED_* 绝对计数常量退役——判定改走证据形态学（详见
# mixed_model 模块注），常量仅作历史兼容保留。
from core.sanger.mixed_model import (  # noqa: F401
    MixedParams, classify_mixed as _classify_mixed,
    detect_and_classify, detect_mixed_detail as _detect_mixed_detail,
    detect_mixed_positions as _detect_mixed_positions,
    load_params as load_mixed_params,
    _tracy_mixed_corroboration,
)


# tracy decompose 解析与等位判读迁 core/sanger/decompose.py（goal T10；
# A 3.6：产物是 json/align1/align2，旧 fasta 后缀恒空）。此处 re-export
# 维持既有导入路径（app.routes 从 pipeline 取名）
from core.sanger.decompose import (  # noqa: F401
    try_tracy_decompose, decompose_and_annotate,
)
_try_tracy_decompose = try_tracy_decompose  # 公开化前的私有名（历史引用兼容）
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
    basecallQual 为逐碱基 Q，basecallPos 为峰在 trace 采样点上的坐标。
    T24.1 起与 PLOC 统一取 0 基原值（tracy 内部读同一 ABIF 的 PLOC；
    本地无 tracy 无法实测核对，基准疑义挂 KNOWN_ISSUES「tracy 基准」。"""
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
        tp = [int(x) for x in pos[:n]]   # 0 基采样点，与 PLOC 原值同口径（T24.1）
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
                     unresolved_keys: Optional[set] = None,
                     circular: bool = False) -> Dict:
    """按参考坐标逐位质量加权投票生成共识序列

    每个 read 在其覆盖的每个参考位点投一票，票投给它在该位点的观测：
    参考碱基，或该处变体（替换碱基 / 缺失 '-' / 插入 '+ALT'，插入记在插入点
    右侧位点）。票的权重取该列自身的 Q（aligned.q_aligned，经 _phred_weight
    由错误概率换算成对数似然比）——Sanger read 沿读长单调退化，后段低 Q
    误判不应与前段高 Q 正确调用同权；无逐列 Q 时退回 read 的 mean_q。
    单 read 覆盖的位点显式以该 read 为准（模拟人工核对）；多 read 覆盖时
    按列 Q 加权多数，孤立低质量差异被压制。

    skip_keys：rejected（有正面反证被互检定案）变体的 key 集合。这些调用
    不写入共识——该 read 在此处按参考碱基投票（T1 三态：只有 rejected
    可以从判定中剔除）。

    unresolved_keys：证据不足（低置信）变体的 key 集合。这些位点不再回填
    参考碱基：unresolved 替换输出 IUPAC 合并码（如 K=R? K=G/T），unresolved
    indel 照实写入共识并在 diffs 标 unresolved: true——「没读清」不冒充
    「与参考一致」（goal T1；A 3.3）。

    circular=True：环状参考，跨原点 read 的展开坐标、跨原点的缺失与
    原点处（L 与 1 之间）的插入都按环折回，不再有「参考末端之后」的插入。
    """
    L = len(reference)

    def _idx(p: int) -> int:  # 1-based 参考坐标 → 0-based 投票下标（环状折回）
        return (fold_pos(p, L) - 1) if circular else p - 1

    votes: List[Dict[str, float]] = [{} for _ in range(L)]
    voters: List[List[str]] = [[] for _ in range(L)]   # 每位点各 read 的观测
    end_insert_votes: Dict[str, float] = {}  # 参考末端之后的插入（无右侧锚定位）
    end_insert_voters: Dict[str, int] = {}   # 各候选插入的票数（单票 → unresolved）
    # 重叠区分歧明细（goal T21；A 4.4）：非参考票全记，热位点（已出现非参
    # 考票）上的参考票也记——分歧判定与各 read 调用/Q 下发都用它
    voter_info: Dict[int, List[Dict]] = {}
    hot_pos: set = set()

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
                    # 参考末端之后的插入（无右侧锚定位）。T25.3 与 skip_keys
                    # 同口径：rejected（skip_keys）与 unresolved（低置信）票
                    # 不参与定插——单 low 票不把插入写进共识
                    if skip_keys and _variant_key(v) in skip_keys:
                        continue
                    if unresolved_keys and \
                            (L, "insertion", v.get("alt_base", "").upper(),
                             int(v.get("length") or 1)) in unresolved_keys:
                        continue
                    end_insert_votes[key] = end_insert_votes.get(key, 0.0) + _phred_weight(q)
                    end_insert_voters[key] = end_insert_voters.get(key, 0) + 1
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
                w = _phred_weight(q)
                votes[i][key] = votes[i].get(key, 0.0) + w
                voters[i].append(key)
                if key != reference[i].upper():
                    hot_pos.add(i)
                if i in hot_pos:
                    voter_info.setdefault(i, []).append(
                        {"read": r.get("filename") or "?", "call": key,
                         "q": q, "w": w})

    consensus_chars: List[str] = []
    char_ref: List[int] = []      # 每个共识字符对应的参考位点（插入碱基记插入点右侧位点）
    char_cov: List[bool] = []     # 该字符是否来自 read 实测（False = 未测位置的参考填充）
    diffs: List[Dict] = []  # 共识与参考的差异位（供前端高亮，人工核对最终构建体）
    covered: List[bool] = []
    # 近平局分歧位点（goal T21；A 4.4）：共识输出 IUPAC 合并码，不再静默
    # 按投票顺序取一边；分歧记录随共识下发
    discordances = find_discordances(voter_info, reference)
    discordance_map = {_idx(d["ref_pos"]): d for d in discordances}

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
        d = discordance_map.get(_idx(pos))
        if d is not None:
            _emit(d["consensus"], pos, True)
            diffs.append({
                "ref_pos": pos, "ref_base": reference[pos - 1].upper(),
                "cons_base": d["consensus"],
                "cons_index": len(consensus_chars) - 1,
                "unresolved": True, "overlap_discordance": True,
            })
            continue
        if len(voters[pos - 1]) == 1:
            best_key = voters[pos - 1][0]   # 单 read 覆盖：以该 read 为准
        else:
            best_key = _pick_base_vote(cell)

        def _is_unresolved(key: str) -> bool:
            if not unresolved_keys:
                return False
            if key.startswith("+"):
                # 插入票挂在锚点右侧参考位（ref_pos+1），变体 key 用左侧锚位
                # ——查表前折回锚位（环状 pos=1 时锚为 L）
                anchor = ((pos - 2) % L) + 1
                return (anchor, "insertion", key[1:], len(key) - 1) in unresolved_keys
            if key == "-":
                return (pos, "deletion", "-", 1) in unresolved_keys or \
                    any(k[0] == pos and k[1] == "deletion" for k in unresolved_keys)
            return (pos, "substitution", key, 1) in unresolved_keys

        if best_key.startswith("+"):
            alt = best_key[1:]
            cons_index = len(consensus_chars)
            _emit(alt, pos, True)
            # 插入只新增碱基，紧跟其后的参考碱基必须保留，
            # 否则共识序列会悄悄吞掉插入位点右侧的参考碱基
            _emit(reference[pos - 1].upper(), pos, True)
            diffs.append({
                "ref_pos": pos, "ref_base": "-", "cons_base": alt,
                "cons_index": cons_index,
                **({"unresolved": True} if _is_unresolved(best_key) else {}),
            })
        elif best_key == "-":
            diffs.append({
                "ref_pos": pos, "ref_base": reference[pos - 1].upper(), "cons_base": "-",
                **({"unresolved": True} if _is_unresolved(best_key) else {}),
            })
        else:
            unresolved_sub = (best_key != reference[pos - 1].upper()
                              and _is_unresolved(best_key))
            # unresolved 替换：共识输出 IUPAC 合并码（ref 与 alt 的并集），
            # 不再回填参考碱基冒充「与参考一致」
            _emit(_iupac_merge(reference[pos - 1], best_key)
                  if unresolved_sub else best_key, pos, True)
            if best_key != reference[pos - 1].upper():
                diffs.append({
                    "ref_pos": pos, "ref_base": reference[pos - 1].upper(),
                    "cons_base": _iupac_merge(reference[pos - 1], best_key)
                    if unresolved_sub else best_key,
                    "cons_index": len(consensus_chars) - 1,
                    **({"unresolved": True} if unresolved_sub else {}),
                })

    if end_insert_votes:
        best_key = max(end_insert_votes.items(), key=lambda kv: kv[1])[0]
        alt = best_key[1:]
        cons_index = len(consensus_chars)
        _emit(alt, L, True)
        # 单票（仅一条 read 报告该末端插入）：照实写入但标 unresolved，
        # 不冒充已验证序列（与主循环插入位点的 unresolved 口径一致）
        diffs.append({"ref_pos": L, "ref_base": "-", "cons_base": alt,
                      "cons_index": cons_index,
                      **({"unresolved": True}
                         if end_insert_voters.get(best_key, 0) <= 1 else {})})

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
        # 重叠区近平局分歧记录（goal T21）：ref_pos/calls/vote_gap/consensus
        "overlap_discordances": discordances,
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


def _find_orf(ref: str, start: int, end: int, strand: str,
              transl_table=None, codon_start=None) -> Optional[Dict]:
    """特征区间内最佳 ORF（T16 起委托 orf_guard.find_guarded_orf：校正
    只允许同读框 ±3bp 标注偏差，超出保留原标注并给 suspect）"""
    return find_guarded_orf(ref, start, end, strand, transl_table, codon_start)


def _build_cds_reports(
    ref: str, features: List[Dict], variants: List[Dict], consensus: Dict,
    _ref_pos_of=None, unresolved_sites: Optional[List[Dict]] = None,
) -> List[Dict]:
    """CDS 级别测序结论：覆盖完整性 + 共识重建 CDS 的翻译产物与参考逐位比对

    回答“整段 CDS 测序结果有没有问题”：
    - 覆盖：CDS 区间与共识覆盖区间求交，未覆盖部分按参考填充、不参与判定；
    - 编码区校正：特征边界偏差（1-3bp 很常见）时自动对齐区间内最佳 ORF，
      以真实编码区翻译，避免阅读框整体错位；
    - 嵌套去重：被更大 CDS 完全包含的小特征（如 6xHis 标签）不单独出结论；
    - 蛋白：从共识差异重建 CDS 序列（按链方向翻译），与参考翻译逐位比对，
      给出一致/不一致、移码数、无义提前终止位置与氨基酸替换清单；
      CDS 内有 N/模糊调用位点时蛋白判定降级为待定（protein_identical=None，
      goal T1；B P0-2——N 参与翻译只会产出 X，「一致」无从谈起）。
    - 多段 CDS（GenBank join，含跨原点）：各段按编码方向拼成虚拟参考后
      走同一套判读，见 _joined_cds_report。

    _ref_pos_of：内部参数，把本次调用的坐标映射回原参考坐标（仅用于
    结论文案里的位置；多段 CDS 递归时传入）。
    unresolved_sites：N/模糊调用等未决位点清单（analyze 下发，用于 CDS 待定判定）。
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
    verified_ranges = consensus.get("verified_ranges") or covered_ranges
    trusted_ranges = consensus.get("trusted_ranges") or covered_ranges

    def _span_coverage(start: int, end: int, ranges=None) -> float:
        cov = 0
        for s, e in (ranges or covered_ranges):
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
            reports.extend(_joined_cds_report(ref, group, variants, consensus,
                                              unresolved_sites=unresolved_sites))
            continue
        f = group[0]
        if id(f) in nested_ids:
            continue
        feat_start, feat_end = int(f["start"]), int(f["end"])
        if feat_start < 1 or feat_end > len(ref) or feat_start > feat_end:
            continue
        strand = f.get("strand") or "+"
        # 编码区校正（T16 护栏）：特征边界常有 1-3bp 偏差（如起点多含上游
        # 碱基），对齐区间内最佳 ORF 后再翻译，此后 start/end 均指真实编码
        # 区；偏差超出 ±3bp 同读框护栏 → 保留原标注并记 orf_annotation_suspect
        # （标注疑点交人工确认，不被静默改写）
        orf = _find_orf(ref, feat_start, feat_end, strand,
                        transl_table=f.get("transl_table"),
                        codon_start=f.get("codon_start"))
        start, end = (orf["orf_start"], orf["orf_end"]) if orf else (feat_start, feat_end)
        if orf and orf.get("suspect") and unresolved_sites is not None:
            # 注意不能写 (unresolved_sites or [])：空列表为假值会拿到新
            # 抛弃列表，位点丢失
            unresolved_sites.append(
                suspect_site(feat_start, feat_end, f.get("name") or "CDS"))
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
        # verified 口径（goal T3；B P0-4）：verified/trusted 覆盖随报告下发
        # （verified_percent 字段 + coverage 区块 + 导出小写标注），full 判定
        # 与状态语义的切换随 T5 ROI / T12 可信区变宽落地——T3 单独切换会让
        # 短 CDS（首端硬区占比高）的对照组误降级（约束：对照组不得变红）
        cov_verified = _span_coverage(start, end, verified_ranges)
        cov_trusted = _span_coverage(start, end, trusted_ranges)
        cov_ok = cov_pct >= 99
        # CDS 内的 N/模糊调用位点（goal T1；B P0-2）：重建时置 N（翻译产出
        # X），蛋白判定降级待定——「N 读成参考碱基」的老口径会把错义位点的
        # 蛋白误报成与设计一致
        n_sites = [u for u in (unresolved_sites or [])
                   if u.get("kind") in ("n_call", "iupac")
                   and start <= int(u.get("ref_pos") or 0) <= end]
        n_in_cds = len(n_sites)
        base = {
            "name": f.get("name") or "CDS",
            "start": start,
            "end": end,
            "strand": strand,
            "covered_percent": cov_pct,
            "verified_percent": cov_verified,
            "trusted_percent": cov_trusted,
            "coverage_status": "full" if cov_ok else ("uncovered" if cov_pct <= 0 else "partial"),
            # 目的基因模式的分析目标标记（cds_extract 伪特征）；结论头据此做
            # 覆盖资格判定（_conclusion_summary）。全质粒模式的 CDS 无此键
            **({"analysis_target": True} if f.get("analysis_target") else {}),
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
        # 确证判定，单独提示待复核——避免噪声推翻整段 CDS 结论。
        # 显式 status 优先（T21 重叠分歧降级写回 unresolved），旧记录按置信推导
        def _cstat(v: Dict) -> str:
            st = v.get("status")
            if st:
                return st
            return "confirmed" if v.get("confidence", "high") != "low" else "unresolved"
        confirmed = [v for v in in_cds if _cstat(v) == "confirmed"]
        pending = [v for v in in_cds if _cstat(v) != "confirmed"]
        # 重建不掺 N：N 位点按参考填充参与翻译，N 的影响只体现为「判定待定」
        # （protein_identical=None）——若把 N 强制掺进产物，X 残基会把
        # 「没读清」误算成「与设计不一致」（未决 ≠ 反证）
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
        # ✓ 资格门（CDS 层，goal T1；B P0-2）：CDS 内有 N/模糊调用时蛋白
        # 「一致」无从判定（该位点可能是任何错义/无义），降级待定（None）；
        # 确证损伤（False）保留——真差异证据优先于未决提示
        if n_in_cds and protein_identical:
            protein_identical = None

        # 起始/终止密码子状态（参考方向核酸层面判定，参照 VEP/snpEff 的 start_lost/stop_lost）

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
        if protein_identical is None:
            pos_txt = pos_ranges_str(sorted(int(u["ref_pos"]) for u in n_sites))
            main = (f"{name} 蛋白判定待定：CDS 内 {n_in_cds} 处 N/模糊调用"
                    f"（参考位置 {pos_txt}）未读清——需核对峰图或换引物补测后再判")
            if aa_diffs:
                preview = "、".join(aa_diffs[:5]) + ("等" if len(aa_diffs) > 5 else "")
                main += f"；已确证 {len(aa_diffs)} 处氨基酸替换（{preview}）"
        elif protein_identical:
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
        pending_impact = None
        if pending:
            conflicted = [v for v in pending if v.get("cross_read_conflict")]
            plain = [v for v in pending if not v.get("cross_read_conflict")]
            would_change = _translate(_rebuild_from_variants(start, end, in_cds), strand) != alt_prot_raw
            pending_impact = "would_change" if would_change else "no_effect"
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
            "coverage_status": "full" if cov_ok else "partial",
            "ref_protein_length": len(ref_prot),
            "alt_protein_length": len(alt_prot),
            "protein_identical": protein_identical,
            "premature_stop_aa": stop_idx + 1 if stop_idx >= 0 else None,
            "frameshift_count": len(frameshifts),
            "aa_changes": aa_diffs[:10],
            "consequences": consequences,
            "synonymous_count": synonymous,
            "pending_low_confidence": len(pending),
            # N/模糊调用位点的机器字段（goal T1；B P0-2）：待定判定与前端渲染用
            "n_in_cds": n_in_cds,
            "pending_impact": pending_impact,
            "verdict": verdict,
        })
    return reports


def _joined_cds_report(
    ref: str, parts: List[Dict], variants: List[Dict], consensus: Dict,
    unresolved_sites: Optional[List[Dict]] = None,
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
    # N/模糊调用位点同样映射进虚拟坐标（蛋白判定待定口径与单段一致）
    vns: List[Dict] = []
    for u in unresolved_sites or []:
        if u.get("kind") in ("n_call", "iupac"):
            vns.extend({**u, "ref_pos": k} for k in inv.get(int(u["ref_pos"]), []))
    reports = _build_cds_reports(vref, [vfeat], vvars, {"covered_ranges": vcov},
                                 _ref_pos_of=lambda k: vmap[k - 1],
                                 unresolved_sites=vns)
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


def _analyze_one_read(
    filename: str, blob: bytes, *, ref: str, align_fn, norm_variants,
    zone_params, min_q: int, signal_correct: bool, circular: bool,
    full_ref: Optional[str] = None, full_circular: bool = False,
) -> Tuple[Optional[Dict], Optional[Dict]]:
    """analyze 的逐 read 阶段：解析→质量修剪→比对→tracy 交叉 basecall。

    返回 (read 结果字典, None) 或 (None, errors 条目)：单文件失败不阻断整体。
    """
    try:
        read = extract_read(blob)
    except (AbiParseError, Exception) as e:  # noqa: BLE001 单文件失败不阻断整体
        return None, {"filename": filename, "error": f"解析失败: {e}"}

    bases = read["bases"]
    quality = read["quality"]
    if len(bases) < MIN_WINDOW:
        return None, {"filename": filename, "error": "碱基数过少（<50bp）"}

    # tracy 交叉 basecall（提前到此，无 PCON 时先借它的 Q；生产镜像内置，
    # 缺失时降级。终审 C-09：交叉 basecall 异常不得拖垮整份多 read 分析）
    cross = _try_tracy_basecall(blob)

    # 质量值来源（goal T24.5）：PBAS 内嵌 Q 为常规来源；PCON 缺失/全 0
    # （个别老机型/导出工具）先试 tracy 重新 basecall 的 Q（长度须与
    # PBAS 对齐），无 tracy 环境按峰型估 Q——绝不让「无 Q」等同「读不了」
    quality_source = "pbas"
    if not any((q or 0) > 0 for q in quality):
        cq = cross[1] if cross else None
        if cq and len(cq) == len(bases):
            quality = list(cq)
            quality_source = "tracy_basecall"
        else:
            quality = estimate_quality_from_trace(
                read["trace"], read["peak_indices"], len(bases))
            quality_source = "peak_estimate"
    read["quality_source"] = quality_source

    # A2 基线校正：峰级定量前扣除各通道加性本底（通道映射不动）；
    # 显示与定量统一使用校正后信号（关闭开关回退原始信号）
    if signal_correct:
        read["trace"] = baseline_correct(read["trace"])

    # 估 Q 路径用 span+mask 口径（凹陷掩蔽而非截断），常规路径保持 Mott
    if quality_source == "peak_estimate":
        s, e = _trim_span_mask(quality, min_q)
    else:
        s, e = _trim_by_quality(bases, quality, min_q)
    trimmed = bases[s:e]
    trimmed_q = quality[s:e]
    # 阈值高于全部碱基 Q 值时修剪结果为空：记为错误条目，
    # 不能送进 PairwiseAligner（空序列会抛 ValueError → 500）
    if len(trimmed) < MIN_WINDOW:
        return None, {
            "filename": filename,
            "error": f"Q≥{min_q} 的碱基不足 {MIN_WINDOW}bp（修剪后仅 {len(trimmed)}bp），无法比对",
        }
    # 估 Q 路径的凹陷掩蔽标注（goal T24.5）：区间内 Q<min_q 的连续段记入
    # 结果（0 基半开区间，与 trim_start 同系），供前端/证据包标注
    # 「测到但峰型不可信」——掩蔽而非截断
    low_q_ranges: List[List[int]] = []
    if quality_source == "peak_estimate" and e > s:
        run: Optional[List[int]] = None
        for i in range(s, e):
            if quality[i] < min_q:
                if run and run[1] == i:
                    run[1] = i + 1
                else:
                    if run:
                        low_q_ranges.append(run)
                    run = [i, i + 1]
        if run:
            low_q_ranges.append(run)
    mean_q = sum(trimmed_q) / len(trimmed_q) if trimmed_q else 0
    grade, q20_ratio, crl = _read_grade(trimmed, trimmed_q)

    aln = align_fn(trimmed, ref, trimmed_q)
    # 截断提示：环状参考的跨原点 read 已整条比对，仍截断即另有原因
    partial = _check_partial_alignment(trimmed, aln, circular)
    # 端部软剪切高质量段（goal T6；A 2.1）：连接/结构异常，不再静默成"没测到"；
    # split-read 二次定位（goal T19）：命中→结构断点坐标，未命中→未知序列
    hq_segments = check_unaligned_hq_segment(aln, trimmed_q)
    for seg in hq_segments:
        sr = classify_segment(trimmed[seg["start"] - 1:seg["end"]], ref, circular)
        if sr.get("status") != "breakpoint" and full_ref:
            # W8 裁剪窗口路径：段在窗口上无落点时按完整质粒再定位——
            # 能连续定位 = 读取越过窗口边缘的正常延续，记 located_outside
            # 不算异常（goal W8「能连续定位到窗口外的段不算异常」）
            sr_full = classify_segment(trimmed[seg["start"] - 1:seg["end"]],
                                       full_ref, full_circular)
            if sr_full.get("status") == "breakpoint":
                sr = {**sr_full, "status": "located_outside"}
        seg["split_read"] = sr
    # 未比对部分全部能在完整质粒上连续定位（窗口外已定位）→ 不判
    # partial_alignment（W8 裁剪路径；仍有未定位段时照旧判）
    if partial and full_ref and hq_segments and all(
            (s.get("split_read") or {}).get("status") == "located_outside"
            for s in hq_segments):
        partial = None

    # 变体附加质量值，并归一化为最左表示（重复/同聚物区 anchor 漂移时
    # 不同 read / 不同 caller 报告的等价 indel 才能合并印证）
    aln["variants"] = norm_variants(aln["variants"])
    for v in aln["variants"]:
        rp = v.get("read_pos") or 1
        qi = min(max(rp - 1, 0), len(trimmed_q) - 1)
        v["quality"] = trimmed_q[qi]

    # tracy 交叉 basecall 结果复用（已在修剪前调用，见上）：独立 caller 的
    # 第二意见，全流程降级隔离（终审 C-09）
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
            caln = align_fn(cb[cs:ce], ref, cq[cs:ce])
            for v in caln["variants"]:
                rp = v.get("read_pos") or 1
                qi = min(max(rp - 1, 0), len(cq[cs:ce]) - 1)
                v["quality"] = cq[cs:ce][qi]
            cross_variants = norm_variants(caln["variants"])

    # 混合峰检测移至 poly 结构识别之后（B4：滑移 echo 先于 mixed 判定剔除）
    # 变体 read_pos 基于 trimmed 碱基（1-based）；峰坐标保持 trace 数据点坐标系，
    # 与 PLOC 一致，供混合检测与峰级证据按相邻峰窗口取样
    trimmed_peaks = [
        read["peak_indices"][i] for i in range(s, min(e, len(read["peak_indices"])))
    ]

    return ({
        "filename": filename,
        "sample_name": read["sample_name"],
        "raw_length": len(bases),
        "trimmed_length": len(trimmed),
        "mean_q": round(mean_q, 1),
        "grade": grade,
        "q20_ratio": q20_ratio,
        "crl": crl,
        # 质量值来源（goal T24.5）：pbas / tracy_basecall / peak_estimate
        "quality_source": quality_source,
        # PLOC 比 PBAS 短的条数（goal T24.2/M12；0=正常），管线汇总告警
        "ploc_shortfall": read.get("ploc_shortfall") or 0,
        **({"low_q_ranges": low_q_ranges} if low_q_ranges else {}),
        "trimmed_bases": trimmed,
        "trimmed_quality": trimmed_q,
        "alignment": aln,
        "trace": read["trace"],
        "peak_indices": read["peak_indices"],
        "trimmed_peaks": trimmed_peaks,
        # 通用伪迹区段（goal T20；A 3.5）：压缩/dye blob/短同聚物滑移，
        # 只作 T15 特征与 verdict info，不进任何判定分支
        "artifacts": detect_artifacts(read["trace"], trimmed_peaks, trimmed),
        # poly 计数用：质量修剪常剪掉 poly 末端低 Q 压缩区，峰窗需沿
        # 原始 read 外扩（B4），保留原始碱基串与修剪偏移
        "raw_bases": bases,
        "trim_start": s,
        "cross_variants": cross_variants if used_tracy else None,
        "tracy_calls": tracy_calls,
        "partial_alignment": partial,
        "unaligned_hq_segments": hq_segments,  # 端部软剪切高质量段（T6）；空=无
        # read 可信区：位置先验（首端硬区/灰区、尾端灰区/硬区）+ Q 值与峰型
        "reliable_zone": compute_reliable_zone(
            len(bases), s, trimmed_q, read["trace"], trimmed_peaks, zone_params),
    }, None)

def _signal_analysis_pass(read_results: List[Dict], poly_runs: List[Dict],
                          poly_local_ratio: bool) -> None:
    """B4/B5 每 read 的信号层分析（就地修改 read_results）：滑移 echo、
    poly 下游骤降、合并压缩区、mixed 逐位判定与分级（P17：stutter 先于 merge）。
    """
    # 每 read 的信号层分析（B4/B5）：滑移 echo 位置 → 从 mixed 中剔除；
    # poly 末端下游骤降记录。mixed 判定在此进行（P17 顺序：stutter 先于 merge）。
    # Step A：run 窗口对该 read 呈"合并压缩"（无可分辨峰/强压缩）时记入
    # merged_zones——该 read 在 run 内的逐位双峰不可分辨，属伪差异证据源
    poly_runs_p1 = [r for r in poly_runs if r["period"] == 1]
    for r in read_results:
        aln = r["alignment"]
        rmap = ReadRefMap.from_alignment(r)
        slip: set = set()
        dropouts: List[Dict] = []
        merged_zones: List[Dict] = []
        for run in poly_runs_p1:
            # run 窗口走统一坐标映射（goal T9）：折回键直查，软剪切/indel/
            # 反向镜像都在映射内处理（线性偏移式已删）
            win = rmap.run_window(run["start"], run["end"])
            if win is None:
                continue
            i0, i1 = win
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
        # 双峰检出 + read 级分级走 mixed_model 自适应背景模型（goal T11；
        # A 3.1）：滑移 echo / 合并压缩区剔除、可信区外剔除（末端拖尾不凭空
        # 触发「连续双峰段」误报），通道对比值表一次构图，站点检测与
        # floor 兜底路径共用
        mixed_detail, r["mixed_profile"] = detect_and_classify(
            r["trimmed_bases"], r["trace"], r["trimmed_peaks"],
            quality=r.get("trimmed_quality"), zone=r.get("reliable_zone"),
            tracy_calls=r.get("tracy_calls"),
            params=load_mixed_params(r.get("instrument")),
            exclude=slip | merged_span,
        )
        # mixed_positions 只收硬检出位点（soft 带不进置信度/互检闸，
        # goal v3 W4：软带仅供 verdict 在声明范围×功能区出 pending 复核项）
        r["mixed_positions"] = [e["pos"] for e in mixed_detail
                                if not e["pullup"] and not e.get("soft")]
        # 逐位证据（次级通道/占比/自适应 z 值）随结果下发，供前端峰图标注；
        # call_disagrees_with_trace（T24.4）：调用碱基不在峰面积前两通道。
        # 软检出带（W4）单列 mixed_soft——所有既有消费方（互检分母、poly
        # 判读、报告双峰计数）口径不变，verdict 侧合并读取
        _md = [e for e in mixed_detail if not e["pullup"] and not e.get("soft")]
        r["mixed_detail"] = [
            {"pos": e["pos"], "ratio": round(e["ratio"], 3),
             "secondary_base": e["secondary_base"], "mixed_z": e.get("mixed_z"),
             **({"call_disagrees_with_trace": True}
                if e.get("call_disagrees_with_trace") else {})}
            for e in _md
        ]
        r["mixed_soft"] = [
            {"pos": e["pos"], "ratio": round(e["ratio"], 3),
             "secondary_base": e["secondary_base"]}
            for e in mixed_detail if not e["pullup"] and e.get("soft")
        ]
        if r.get("tracy_calls") is not None:
            tags = {e["pos"]: e.get("tracy") for e in mixed_detail if not e["pullup"]}
            for d in r["mixed_detail"]:
                d["tracy"] = tags.get(d["pos"])

def _variant_confidence_pass(variants: List[Dict], read_results: List[Dict],
                             poly_local_ratio: bool) -> None:
    """变异置信度（Mutation Surveyor 式：峰强比 + 信噪比 + Q 值 + 多 read 支持），
    含跨 read 干净覆盖反证与互检定案、合并压缩区降级（就地修改 variants）。
    """
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
        # T14 多 read 证据聚合（A 3.4 / B P1-6）：evidence_by_read + 代表
        # read 择强（重绑 read/read_pos/read_q——后续置信度分级、混合背景、
        # zone 强制、互检全部基于代表 read，与上传顺序无关，M2）；
        # peak_evidence 取代表 read 的峰证据
        attach_evidence_by_read(v, by_read, mixed_by_read)
        evidence = v.get("peak_evidence")
        src = by_read.get(v.get("read", ""))
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
        # T14：互检信息扩展到所有变体（A 3.4）——覆盖该位点但未支持该变体
        # 的其他 read 一律记录（只记覆盖事实，含 N 列/质量不足/邻近有差异的
        # read）；反证推断仍走下面的干净覆盖口径，判定语义不变
        v["covering_reads_not_supporting"] = covering_not_supporting(
            v, ref2call_by_read, keys_by_read, by_read)
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
        # 证据三态 + 机器可读原因码（goal T1；A 3.3 / B P0-1）：在定案与
        # 全部降级之后计算——rejected 仅当互检定案，confirmed=中高置信，
        # 其余（低置信）=unresolved。放前面会拿到滞后的 status
        # T15 校准评分（A 5.3）：p_real + 四档 action + 三态/confidence 由
        # p_real 分档回填（T23 前与既有规则逐位等价，行为不倒退）
        attach_score(v, evidence, src.get("reliable_zone") if src is not None else None)
        v["reason_codes"] = _variant_reason_codes(
            v, evidence,
            src.get("reliable_zone") if src is not None else None,
            mixed_by_read.get(v.get("read", ""), set()),
        )

def _homopolymer_report_pass(poly_runs: List[Dict], variants: List[Dict],
                             read_results: List[Dict],
                             coverage_ranges: List[Tuple[int, int]]) -> List[Dict]:
    """poly 重复结构清单：仅列出落在测序覆盖范围内的结构，回填测得重复数。
    对每条覆盖该结构的 read 做峰图独立计数并交叉验证（B2/B3/B4 口径），
    并给出整段重复数的 Step B 综合评判。"""
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

    return homopolymer_report
def _cds_reports_safe(ref: str, features: List[Dict], variants: List[Dict],
                      consensus: Dict,
                      unresolved_sites: Optional[List[Dict]] = None) -> List[Dict]:
    """CDS 判读（终审 C-08）：意外异常降级为单条失败报告，不得让一条
    畸形特征（空蛋白比对、非法翻译边界等）拖垮整份多 read 分析。
    独立成函数供 analyze 先于结论文本调用（verdict 需要它）。"""
    try:
        return _build_cds_reports(ref, features, variants, consensus,
                                  unresolved_sites=unresolved_sites)
    except Exception as e:
        logger.exception("CDS 判读整体失败，降级为占位报告")
        return [{
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


def _build_conclusion(
    read_results: List[Dict], variants: List[Dict], mixed_corroboration: Dict,
    edge_ambiguous_reads: List[Dict], consensus: Dict,
    coverage_ranges: List[Tuple[int, int]], coverage_gaps: List[Dict],
    homopolymer_report: List[Dict], ref: str, features: List[Dict],
    unresolved_sites: Optional[List[Dict]] = None,
    errors: Optional[List[Dict]] = None,
    cds_reports: Optional[List[Dict]] = None,
    mixed_profiles: Optional[Dict] = None,
    verdict: Optional[Dict] = None,
) -> Tuple[str, List[Dict], Dict]:
    """结论文本组装：互检定案行、CDS 判读、dropout/mixed/可信区提示与
    自动结论（编码区结论放最前）。返回 (conclusion, cds_reports,
    mixed_profiles, resolved_regions)——resolved_regions 为区间裁决行的
    结构化形态（前端高亮用）。cds_reports/mixed_profiles/verdict 可由
    analyze 预计算传入（T5：结构化判定先行，结论头由 verdict.status 驱动）；
    缺省时内部自算（兼容直调）。
    """
    # 互检定案的差异（单 read 报告、其他引物可靠区干净读出参考）不再进入
    # 任何判读文本：变体清单保留并标注，结论只用一句话交代排除了几处
    shown_variants = [v for v in variants if not v.get("cross_read_resolved")]
    resolved_variants = [v for v in variants if v.get("cross_read_resolved")]

    if cds_reports is None:
        cds_reports = _cds_reports_safe(ref, features, shown_variants, consensus,
                                        unresolved_sites=unresolved_sites)

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
    if mixed_profiles is None:
        mixed_profiles = {r["filename"]: r["mixed_profile"] for r in read_results
                          if r["mixed_profile"]["count"] > 0}
    mixed_lines: List[str] = []  # 待折叠组（R6：↳ 逐 read 双峰行 + scattered 待核行）
    mixed_signal_lines: List[str] = []

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
    # 逐 read 位点范围子行（2026-11-05 用户定稿：收敛）——双峰位点能被互检
    # 归为噪声的 read 不再单独成行（read 质量细节，与构建判读无关）；只有
    # 含「无覆盖待核位点」的 read 保留一行，直接带待核参考位置（用户可对照
    # 图谱核对），read 坐标细节在报告双峰章节（前端折叠组）。
    # T11：local（局部信号异常）/noise（信号质量）read 的位点同样待核，
    # 与 widespread 同等保留子行（scattered 走 resolved_regions 的差异区
    # 间款，不在此列避免双行）
    for r, p in [(r, r["mixed_profile"]) for r in read_results
                 if r["mixed_profile"]["class"] in ("widespread", "local", "noise")]:
        chk = mixed_corroboration["by_read"].get(r["filename"]) or {}
        uncov_refs = sorted(chk.get("uncovered_ref") or [])
        if crossable and not uncov_refs:
            continue
        if crossable:
            mixed_lines.append(
                f"  ↳ {r['filename']}：双峰 {p['count']} 处中 "
                f"{len(uncov_refs)} 处无其他引物覆盖待核"
                f"（参考位置 {pos_ranges_str(uncov_refs)}）")
            continue
        # 单条 read 无从互检：保留原坐标行（此时坐标是唯一的核对线索）
        z = r["reliable_zone"]
        zone_txt = f"{z['start']}–{z['end']}"
        if (z["raw_start"], z["raw_end"]) != (z["start"], z["end"]):
            zone_txt += f"（原始 {z['raw_start']}–{z['raw_end']}）"
        mixed_lines.append(
            f"  ↳ {r['filename']}：双峰 {p['count']} 处，位于 read "
            f"{pos_ranges_str(p['positions'])}"
            f"（read 坐标；可信区 {zone_txt} 以外未计入）")
    # scattered（个别双峰位点）只提示不升级结论：待核行与区间裁决行同款
    # 表述（2026-11-06 用户定稿），构建在 resolved_regions 模块（T0 拆分）
    mixed_lines.extend(
        scattered_pending_lines(read_results, mixed_corroboration, crossable,
                                features))
    # 跨引物互检汇总：只保留「多引物同报双峰」（真实混合的最强信号）——
    # 倾向噪声/无法互检的计数是 read 质量细节（2026-11-05 用户定稿：结论不列）
    if crossable and mixed_corroboration["total"] > 0:
        mc = mixed_corroboration
        if mc["multi_sites"]:
            pos_txt = "、".join(str(rp) for rp, _fs in mc["multi_sites"][:4])
            more = " 等" if len(mc["multi_sites"]) > 4 else ""
            # R6：⚠ 信号行不进折叠组（前端正文常驻），mixed_lines 只留
            # 待折叠的 ↳ 逐 read/scattered 行
            mixed_signal_lines.append(
                f"⚠ {len(mc['multi_sites'])} 处参考位点被多条引物同报双峰"
                f"（参考位置 {pos_txt}{more}，倾向真实混合）——建议重新挑单克隆复测")

    # read 可信区提示（2026-11-06 用户定稿）：默认边界的「首尾不可信区按低置信
    # 处理」教育行删——差异该标低置信的已在变体表标注，结论里这句话是噪声；
    # 只有用户自定义了边界时才说明边界（此时 Read 表数字与直觉可能不符）
    _zparams = ((read_results[0].get("reliable_zone") or {}).get("params")
                if read_results else None)
    zp = _zparams or DEFAULT_ZONE.as_dict()
    end_note = ""
    if _zparams and (_zparams != DEFAULT_ZONE.as_dict()):
        end_note = (
            f"read 可信区为自定义边界（首端 {zp['front_hard']}bp 硬区、"
            f"{zp['front_hard'] + 1}–{zp['front_gray']}bp 按 Q 值与峰型判定，"
            f"{zp['tail_gray']}bp 之后不可信），区外差异按低置信处理"
        )

    # 互检定案 → 区间裁决行（2026-11-05 用户定稿结构）：文本行与结构化
    # resolved_regions 同源产出，构建在 resolved_regions 模块（T0 拆分）
    resolved_lines, resolved_regions = build_resolved_regions(
        resolved_variants, read_results, features)

    # 自动结论（编码区结论放最前，直接回答“整段 CDS 有没有问题”）
    cds_lines = [
        f"【{cr['name']} CDS】{cr['verdict']}"
        for cr in cds_reports if cr["coverage_status"] != "uncovered"
    ]
    if not read_results:
        conclusion = "没有可分析的测序文件"
    elif not shown_variants:
        # 用户定稿结构（2026-11-05）：正文 = 区间裁决行 → CDS 行。裁决头
        # 由 _conclusion_summary 统一生成（✓/✗/△ + 强度），正文不再重复
        # 覆盖数字与「构建序列与设计一致」复述
        body_lines = list(resolved_lines)
        # scattered 双峰提示：互检能归为噪声的不再打扰；仅剩「无覆盖待核」的
        # 位点才保留一行（双峰是源 read 自身属性，与构建判读无关）
        body_lines.extend(mixed_lines + mixed_signal_lines)
        body_lines.extend(cds_lines)
        # poly 重复数峰图计数存疑时不能只报“一致”（观察级 run 不告警）；
        # 三态 verdict 分措辞（Step B）：矛盾→不可信，不可判定→需换引物
        _verdict_warn = {
            "contradictory": "可分辨 read 之间计数互证矛盾，不可信",
            "undetermined": "无可分辨峰证据，重复数不可判定（建议换引物或人工核对峰图）",
            "deficit_observed": "峰图缺失证据，以峰图可分辨峰为准，建议人工复核",
        }
        poly_warnings = [
            f"注意：poly({e['base']}) 同聚物 {e['start']}-{e['end']} 峰图判读："
            f"{peak_verdict_phrase(e)}——{_verdict_warn.get(e.get('run_verdict'), '以峰图可分辨峰为准，建议人工复核')}"
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
        body_lines.extend(poly_warnings)
        body_lines.extend(dropout_notes)
        body_lines.append(end_note)
        conclusion = "\n".join(x for x in body_lines if x)
    else:
        # 有确认差异：区间裁决行 → 差异摘要 → CDS 行（确认差异是主要事件，
        # 但被排除的区间行依然靠前——它们是 read 质量信息，不是待办）
        lines = list(resolved_lines)  # 区间裁决行紧跟头行（2026-11-05 定稿结构）
        lines.append(f"共检出 {len(shown_variants)} 处差异（覆盖 {consensus['coverage_percent']:.1f}%"
                     f"{_coverage_ranges_text(coverage_ranges)}）：")
        lines.extend(mixed_lines)  # 疑似混合样品影响整份判读，紧跟首行
        lines.extend(mixed_signal_lines)
        severity_tagged = summarize_severity(shown_variants)
        lines.extend(text for text, _kind in severity_tagged)
        # CDS 结论紧随其后（整段编码区有没有问题是第一信息，此前重构时遗漏）
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
                f"  ↳ {run_label(hp)} {hp['start']}-{hp['end']}："
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
                f"  ↳ {run_label(e)} {e['start']}-{e['end']} 峰图判读："
                f"{peak_verdict_phrase(e)}（{anchor}{detail}）"
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
        # 覆盖 % 首行已有；「部分测序是常规策略」安抚话术删（2026-11-05 用户
        # 定稿）——只保留未覆盖缺口的位置提示（判断是否补引物用得上）
        if consensus["coverage_percent"] < 95 and coverage_gaps:
            g = coverage_gaps[0]
            lines.append(
                f"注意：共 {len(coverage_gaps)} 段未覆盖缺口"
                f"（最大 {_gap_span_text(g)}，{g['length']}bp），未测区域按设计序列对待"
            )
        conclusion = "\n".join(lines)

    # 结论头部一句话总结：✓/✗/△ 先给判读结果，区间裁决行紧跟其后
    # （2026-11-05 定稿结构，头行与正文间不再空行）
    # （scattered 双峰只提示不升级，不算混合）
    partial_reads = [r for r in read_results if r.get("partial_alignment")]
    if partial_reads:
        conclusion = "\n".join(
            [conclusion] + [_partial_alignment_note(r["filename"], r["partial_alignment"])
                            for r in partial_reads])
    # R6 结构化分组（goal §2 判定不依赖文案）：前端折叠组由构建端显式给出，
    # 不再靠「低置信度（/↳+双峰/差异区间」文案匹配（旧记录无该键走前端回退）
    conclusion_groups = {
        "low": [text for text, kind in (severity_tagged if shown_variants else [])
                if kind == "low"],
        "mixed_detail": list(mixed_lines),
        "resolved": list(resolved_lines),
    }
    if read_results:
        # has_mixed 用机器判定（A 5.2：不靠文案匹配）：widespread 分级 +
        # 跨引物互检降级例外，与 mixed_lines 的分叉同源（mixed_sample_confirmed）
        has_mixed = mixed_sample_confirmed(read_results, mixed_corroboration)
        conclusion = (_conclusion_summary(shown_variants, cds_reports, has_mixed,
                                          partial_reads=len(partial_reads),
                                          resolved=len(resolved_variants),
                                          unresolved_sites=len(unresolved_sites or []),
                                          failed_reads=len(errors or []),
                                          failed_names="、".join(
                                              e.get("filename", "?") for e in (errors or [])[:2])
                                          + ("等" if len(errors or []) > 2 else ""),
                                          verdict=verdict)
                      + "\n" + conclusion)

    return conclusion, cds_reports, mixed_profiles, resolved_regions, conclusion_groups


def analyze(
    ab1_files: List[Tuple[str, bytes]],
    reference: str,
    features: Optional[List[Dict]] = None,
    min_q: int = MIN_TRIM_Q,
    allow_decompose: bool = True,
    signal_correct: bool = True,
    poly_local_ratio: bool = True,
    *,
    circular: bool,
    zone_params: Optional[ZoneParams] = None,
    primer: Optional[Dict] = None,
    scope=None,
    synonymous_action: str = "ok",
    full_ref: Optional[str] = None,
    full_circular: bool = False,
) -> Dict:
    """全自动分析入口

    ab1_files: [(filename, bytes), ...]
    reference: 参考载体序列（1-based 坐标）
    circular: 参考是否环状，**必填关键字参数（goal T25.2/M9）**——拓扑是
    判定语义的一部分，核心层不猜测：由调用方从参考对象读取后显式传入
    （app 层走载体/图谱记录的真实 topology，见 reference_circular）。
    True 时每条 read 对「参考 + 参考[:k]」比对（k≈read 长），跨越原点的
    read 整条比对上；变体、覆盖区间（跨原点拆为两段）、共识、read↔参考
    坐标映射全部按 ((p-1) % L) + 1 折回。False 按线性参考比对。
    signal_correct: A2 基线校正（逐通道扣除滑动低分位本底后再做峰级定量，
    显示 trace 亦为校正后信号）；False 回退原始信号（约束 2 的前后对比开关）。
    poly_local_ratio: B1 poly 压缩比分母用局部峰幅 H(run)（False 回退全 read
    中位数——信号沿 read 衰减时后段正常 poly 会被误判压缩）。
    zone_params: read 可信区边界（首端硬区/灰区、尾端灰区/硬区，read 坐标 bp），
    缺省 30/60/700/900，见 reliable_zone。
    scope: 需测范围（goal v3 W1，可选关键字，只增不改）：None / Scope /
    dict / str（元件名、「301-900」、多段、关键字，见 core.sanger.scope）。
    analyze 只把它透传给 build_verdict 并写入 result["scope"]——per-read
    分析与覆盖计算不随 scope 变化（换范围不重跑分析，§0.6）；范围语义
    （缺口归因/未声明口径）在 build_verdict 侧（W2 起）。
    full_ref: 完整质粒序列（goal v3 W8，可选关键字，只增不改）：参考被
    裁剪取窗时（cds_mode 超 MAX_REF_BP 路径）传入完整质粒——端部未比对
    高质量段在窗口上无落点时按完整质粒再定位，能连续定位 = 读取越过窗口
    边缘的正常延续（记 located_outside，不算异常）；未比对部分全部定位
    成功时不判 partial_alignment。full_circular 为完整质粒的拓扑。
    """
    features = features or []
    zone_params = zone_params or DEFAULT_ZONE
    ref = reference.upper().replace("U", "T")
    if not ref:
        raise ValueError("参考序列为空，无法进行比对分析")
    scope_obj = coerce_scope(scope, features, len(ref))

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
        rd, err = _analyze_one_read(
            filename, blob, ref=ref, align_fn=_align, norm_variants=_norm_all,
            zone_params=zone_params, min_q=min_q, signal_correct=signal_correct,
            circular=circular, full_ref=(full_ref or None),
            full_circular=full_circular)
        if err is not None:
            errors.append(err)
        elif rd is not None:
            read_results.append(rd)
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
        # T14：逐 read 证据聚合的原料——每条支持 read 的 read_pos/Q 记入
        # _per_read（attach_evidence_by_read 消费后弹出，不外发）
        pr = {"read": v["read"], "read_pos": v.get("read_pos"),
              "read_q": v["read_q"]}
        if key in merged:
            merged[key]["support_reads"] += 1
            merged[key]["read_q"] = max(merged[key]["read_q"], v["read_q"])
            merged[key]["_per_read"].append(pr)
        else:
            merged[key] = {**v, "support_reads": 1, "_per_read": [pr]}
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

    _signal_analysis_pass(read_results, poly_runs, poly_local_ratio)
    # 跨 read 双峰互检：一条引物的双峰位点若被其他引物测过且峰形单一、判读
    # 一致，多半是这条 read 自己的信号噪声（常见于引物首端信号爬升区）；
    # 其他引物同报双峰才倾向真实混合。结论与建议均按互检结果措辞
    mixed_corroboration = _corroborate_mixed(read_results)

    _variant_confidence_pass(variants, read_results, poly_local_ratio)
    # 单点双峰次等位（goal T7）：映射到参考，verdict 按 AF/判定范围分流
    mixed_variants = collect_mixed_variants(read_results, ref)
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

    # 共识口径（goal T1 三态）：只有 rejected（互检定案）不写入共识按参考投票；
    # unresolved 不再回填参考——替换输出 IUPAC 合并码、indel 照实写入并标
    # unresolved（「没读清」不冒充「与参考一致」）
    rejected_keys = {_variant_key(v) for v in variants
                     if v.get("status") == "rejected"}
    unresolved_keys = {_variant_key(v) for v in variants
                       if v.get("status") == "unresolved"}
    consensus = _build_consensus(ref, read_results, skip_keys=rejected_keys,
                                 unresolved_keys=unresolved_keys,
                                 circular=circular)
    # 重叠区分歧（goal T21；A 4.4）：近平局的非参考碱基冲突 → 冲突变体双方
    # 降 unresolved（共识已在 _build_consensus 内按 IUPAC 合并）
    discordances = consensus.get("overlap_discordances") or []
    if discordances:
        downgrade_overlap_conflicts(variants, discordances)

    # 覆盖区间按折回坐标合并：环状跨原点 read 拆成 [s, L] + [1, e-L] 两段
    # （线性比对即 (ref_start, ref_end)，与此前一致）
    coverage_ranges = merge_coverage(
        [sp for r in read_results for sp in aln_segments(r["alignment"])],
        len(ref),
    )
    # 理论覆盖 E 与缺口归因（goal v3 W6，PL B1/B2）：expected_ranges 以
    # 比对起点为默认依据（引物结合位点随 W7）；缺口只对声明范围判 cause，
    # 未声明范围整参考缺口照旧（报告用），cause 同步给出
    _expected = expected_ranges(read_results)
    # W8：缺口按需测并集（含接合区窗口）归因——cds_target 的接合区属于
    # 需测范围，其缺口同样要给 cause
    coverage_gaps = _coverage_gaps(
        coverage_ranges, len(ref), circular=circular, expected=_expected,
        scope_ranges=(scope_obj.merged_ranges() if (scope_obj is not None
                                                    and scope_obj.declared)
                      else None))

    homopolymer_report = _homopolymer_report_pass(
        poly_runs, variants, read_results, coverage_ranges)
    # measured/verified 双覆盖口径（goal T3；B P0-4）：verified = measured ∩
    # 可信区 − N/模糊调用列；注入共识供 CDS 判定切 verified 口径
    coverage = compute_coverage(read_results, len(ref), coverage_ranges,
                                circular=circular)
    # E 与带 cause 的缺口随 coverage 下发（goal v3 W6）：verdict 据此把
    # roi_uncovered 拆 scope_beyond_primer / scope_read_shortfall /
    # scope_uncovered；前端 W13 用 expected_ranges 画理论窗
    coverage["expected_ranges"] = _expected
    coverage["gaps"] = coverage_gaps
    consensus["verified_ranges"] = coverage["verified_ranges"]
    consensus["trusted_ranges"] = coverage["trusted_ranges"]
    # 未决位点清单（goal T1；B P0-2）：N/模糊调用、poly 不可判定、dropout、
    # 无法互检的双峰——它们不进已验证口径，也是 ✓ 的资格门输入
    unresolved_sites = _collect_unresolved_sites(
        read_results, homopolymer_report, mixed_corroboration)
    for d in discordances:
        unresolved_sites.append({
            "ref_pos": d["ref_pos"], "kind": "overlap_discordance",
            "reads": [c["read"] for c in d["calls"]],
            "detail": d["detail"],
            "consensus_base": d["consensus"],
        })
    # 结构化判定先于结论文本（goal T2/T5）：verdict.status 驱动结论头符号
    mixed_profiles = {r["filename"]: r["mixed_profile"] for r in read_results if r["mixed_profile"]["count"] > 0}
    cds_reports = _cds_reports_safe(
        ref, features, [v for v in variants if not v.get("cross_read_resolved")],
        consensus, unresolved_sites=unresolved_sites)
    # 引物校验（goal T17；A 1.5）：方向/结合位点/3′ 端距离逐 read 校验，
    # mismatch 由 verdict 判 fail（默认），无法确定引物只记 info
    primer_checks = check_reads(primer, read_results, ref)
    verdict = build_verdict({
        "variants": variants, "unresolved_sites": unresolved_sites,
        "primer_checks": primer_checks,
        "cds_reports": cds_reports, "errors": errors, "reads": read_results,
        "mixed_profiles": mixed_profiles, "mixed_variants": mixed_variants,
        "mixed_corroboration": mixed_corroboration, "consensus": consensus,
        "coverage": coverage, "features": features or [],
        "algo_version": SANGER_ALGO_VERSION,
    }, len(ref), scope=scope_obj,
    synonymous_action=synonymous_action)
    conclusion, cds_reports, mixed_profiles, resolved_regions, conclusion_groups = _build_conclusion(
        read_results, variants, mixed_corroboration, edge_ambiguous_reads,
        consensus, coverage_ranges, coverage_gaps, homopolymer_report,
        ref, features, unresolved_sites=unresolved_sites, errors=errors,
        cds_reports=cds_reports, mixed_profiles=mixed_profiles, verdict=verdict)
    # 混合样品提示：检出疑似混合位点时建议人工复核或使用 tracy decompose 解卷积
    mixed_reads = [r for r in read_results if r["mixed_positions"]]
    any_tracy = any(r.get("cross_variants") is not None for r in read_results)  # tracy 实际运行过

    # 解析层告警（goal T24.2/T24.5）：PLOC 截短与质量值来源降级，不静默
    warnings: List[str] = []
    for r in read_results:
        k = r.get("ploc_shortfall") or 0
        if k:
            warnings.append(
                f"PLOC 比 PBAS 短 {k} 条（{r['filename']}，read 尾端 {k}bp）："
                "缺失区段峰位置不可用，峰级证据按缺失处理")
        if r.get("quality_source") == "peak_estimate":
            warnings.append(
                f"{r['filename']}：PCON 质量值缺失/全 0，已按峰型估 Q"
                "（quality_source=peak_estimate），置信口径相应保守")

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
        "conclusion_groups": conclusion_groups,  # R6 结构化折叠组（low/mixed_detail/resolved）
        # 未决位点（goal T1 三态）：判定区域内「没读清」的地方，✓ 资格门输入
        "unresolved_sites": unresolved_sites,
        "primer_checks": primer_checks,
        # measured/verified 双覆盖（goal T3；B P0-4）：verified_percent/
        # verified_bp 供 CDS/闸门/结论切口径，coverage_percent 保留实测语义
        "coverage": coverage,
        # 算法版本（goal §2 约束 6）：口径变更时递增并在 HANDOVER §2 登记
        "algo_version": SANGER_ALGO_VERSION,
        # 区间裁决行（互检排除差异的 read×区段 聚合，前端高亮展示）
        "resolved_regions": resolved_regions,
        # 结构化判定（goal T2；A 5.1/5.5）：status/blocking/roi/reads，
        # 网页/Excel/归档/API 出口统一由它驱动（先于结论文本构建，见上）
        "verdict": verdict,
        # 需测范围（goal v3 §3.1；None=未声明，W2 起合成 measured 口径）
        "scope": scope_obj.to_dict() if scope_obj is not None else None,
        # 参考注释（goal v3 W9/SC9）：随结果下发，build_verdict 直接以
        # result 重算 scope（元件名/区间解析）与 element_verdicts 不再
        # 依赖调用方另行传 features
        "features": features or [],
        "mixed_detected": {r["filename"]: r["mixed_positions"] for r in mixed_reads},
        "mixed_profiles": mixed_profiles,
        "mixed_variants": mixed_variants,   # 单点双峰次等位（goal T7；AF=次等位丰度）
        "mixed_corroboration": mixed_corroboration,
        "errors": errors,
        # 解析层告警（goal T24.2/T24.5；旧结果无此键，API 向后兼容）
        "warnings": warnings,
        "engine": "internal+biopython+tracy-basecall" if any_tracy else "internal+biopython",
    }


# ==================== 公开化别名（跨层契约原为私有名）====================
_try_tracy_decompose = try_tracy_decompose  # 公开化前的私有名（历史引用兼容）
_pos_ranges_str = pos_ranges_str  # 公开化前的私有名（历史引用兼容）
_run_label = run_label  # 公开化前的私有名（历史引用兼容）
_peak_verdict_phrase = peak_verdict_phrase  # 公开化前的私有名（历史引用兼容）
