"""统一设计服务：单任务与批量共用同一流水线。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.cache import cache
from app.config import settings  # 测试经 design_service.settings 打补丁（patch 面）
from core.genbank_format import (
    format_feature_line, format_locus_line, format_qualifier, render_origin_block,
)
from core.enzyme_sites import backbone_recut_warnings as _backbone_recut_warnings
from core.primer_designer import screen_gg_overhang_pair
from app.schemas import (
    MAP_MAX_LENGTH,
    CloningMethod,
    DesignRequest,
    DesignResult,
    DesignStatus,
    PrimerInfo,
    SequenceType,
)


# Golden Gate 默认 overhang（当载体无法推导时的回退）
_DEFAULT_GG_OVERHANGS = ("AATG", "GCTT")


@dataclass
class ConstructAssembly:
    """完整质粒组装结果（1-based 注释坐标）。"""

    sequence: str
    insert_start: int
    insert_end: int
    features: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # 切点/切除区间的可读说明（如「BamHI 切点 198 / EcoRI 切点 5000，切除 4802 bp」）
    junction: str = ""
    # 是否按真实切点方案（plan）组装。False = 回退 MCS 区间替换：构建体连接处
    # 没有位点、与引物带入的位点不对应，酶切-连接复现不出该构建体（调用方
    # 据此追加显式告警，不静默）
    plan_used: bool = True


# 载体库缓存迁至 app.vector_library_cache；此处 re-export 维持旧导入路径
from app.vector_library_cache import (  # noqa: F401
    get_vector_library, invalidate_vector_library_cache,
)

logger = logging.getLogger(__name__)


def ensure_vector_backbone(vector) -> str:
    """确保载体有可用骨架序列；YAML 无 sequence 时用特征拼出占位骨架。"""
    if vector.sequence and len(vector.sequence) >= 50 and set(vector.sequence.upper()) & set("ATGC"):
        return vector.sequence.upper().replace(" ", "").replace("\n", "")

    max_end = 5000
    if vector.elements:
        max_end = max(max_end, max(e.end for e in vector.elements))
    if vector.mcs:
        max_end = max(max_end, vector.mcs.end + 50)

    backbone = ["N"] * max_end
    for elem in vector.elements or []:
        seq = (elem.sequence or "").upper()
        if seq and all(b in "ATGCN" for b in seq):
            for i, nt in enumerate(seq):
                pos = elem.start - 1 + i
                if 0 <= pos < len(backbone):
                    backbone[pos] = nt

    # MCS 区填入酶识别位点，便于限制性克隆同源臂设计
    if vector.mcs and vector.mcs.sites:
        for site in vector.mcs.sites:
            rec = (site.recognition_seq or "").upper()
            if not rec:
                continue
            start = max(0, site.cut_position_5 - 1)
            for i, nt in enumerate(rec):
                pos = start + i
                if 0 <= pos < len(backbone) and nt in "ATGC":
                    backbone[pos] = nt

    return "".join(backbone)


def _vector_element_dicts(vector) -> List[Dict]:
    """载体元件 → 组装用的普通字典（core 层不依赖 Vector 类型）"""
    return [
        {"name": el.name, "type": el.element_type.value, "start": el.start,
         "end": el.end, "strand": el.strand, "description": el.description}
        for el in (vector.elements or [])
    ]


def _vector_is_circular(vector) -> bool:
    """载体拓扑是否环状（缺省按环状处理）"""
    return str(getattr(vector, "topology", "circular")
               or "circular").lower() != "linear"


def _circular_backbone_window(backbone: str, start: int, length: int,
                              circular: bool) -> str:
    """载体上 [start, start+length) 的连续窗口；环状时越过首尾自动绕回
    （跨原点的连接点碱基是真实存在的质粒序列），线性时负起点/越界截空"""
    n = len(backbone)
    if length <= 0 or n == 0:
        return ""
    if not circular:
        if start < 0:
            return ""
        return backbone[start:start + length]
    start %= n
    if start + length <= n:
        return backbone[start:start + length]
    return backbone[start:] + backbone[:start + length - n]


def _fusion_frame_warnings(fusion: Dict, pos: int) -> List[str]:
    """融合克隆的读码框 / 内部 Met 告警（Gibson 单点与自定义臂共用口径）"""
    name = fusion.get("name") or fusion.get("type") or "CDS"
    cnt = pos - int(fusion["start"]) + 1   # 融合元件在插入点前的碱基数
    if cnt % 3 == 0:
        return [
            "Gibson 融合克隆提示："
            f"线性化点紧邻 {name}（CDS）末端且同框，插入序列开头的 ATG 会作为"
            "内部甲硫氨酸进入融合蛋白；如需纯融合请去掉序列开头的 ATG，"
            "保留与否请按实验意图确认"
        ]
    return [
        "Gibson 融合克隆读码框不匹配："
        f"线性化点位于 {name}（CDS）内，插入点前该 CDS 已有 {cnt} bp"
        f"（非 3 的倍数），直接拼接会使下游读码框移位 {3 - cnt % 3} bp；"
        "请调整线性化位置或同源臂设计"
    ]


def _mcs_window(vector) -> Optional[Tuple[int, int]]:
    """MCS 区间（1-based 闭区间），供「切点是否落在多克隆位点附近」判定

    一次性载体的推测 MCS 是「点插入」写法（start=point, end=point-1），
    这里折成退化区间 (point, point)，否则会被当成「跨原点的整圈」。
    """
    mcs = getattr(vector, "mcs", None)
    if not mcs or not mcs.start:
        return None
    start = int(mcs.start)
    end = int(mcs.end or 0)
    if end < start:
        return (start, start)
    return (start, end)


@dataclass
class RestrictionEnds:
    """双酶切两个连接端的最终指派（方向已解析）

    enzyme_5 / enzyme_3 是**接基因 5′ / 3′ 端**的酶，由载体表达盒决定，
    不是用户填的那两个框的顺序。strand 是基因最终落在构建体的哪条链上。
    """

    enzyme_5: str
    enzyme_3: str
    strand: str = "+"
    source: str = "default"          # annotation / auto_detected / ambiguous / default / user
    hint: Dict = field(default_factory=dict)
    removed_bp: int = 0
    warnings: List[str] = field(default_factory=list)
    # 自动识别出来、载体本身没有注释的元件（预览要用它们画启动子）
    detected: List[Dict] = field(default_factory=list)
    # 方向解析时跑过的切点对规划（plan_restriction 结果），校验复用免二次扫描
    plan: Optional[object] = None


_ANNOTATION_CACHE: Dict[str, List[Dict]] = {}


def _auto_annotate(backbone: str, circular: bool) -> List[Dict]:
    """对骨架跑一次元件库识别（载体没有注释时用来找启动子）

    5 kb 质粒约 130–230 ms，按序列摘要 + 拓扑缓存，同一载体只有第一次付这个代价。
    """
    import hashlib

    if not backbone:
        return []
    key = hashlib.sha256(backbone.encode()).hexdigest() + ("c" if circular else "l")
    hit = _ANNOTATION_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        from core.plasmid_map.annotate import annotate

        feats = annotate(backbone, circular) or []
    except Exception:  # 注释失败不影响设计，按「没有注释」处理
        feats = []
    converted = []
    for feat in feats:
        strand = feat.get("strand")
        converted.append({
            "name": feat.get("name") or "",
            "type": feat.get("type") or "",
            "start": feat.get("start"),
            "end": feat.get("end"),
            "strand": "-" if strand in (-1, "-") else "+",
        })
    if len(_ANNOTATION_CACHE) > 16:
        _ANNOTATION_CACHE.clear()
    _ANNOTATION_CACHE[key] = converted
    return converted


def _cassette_elements(vector, backbone: str, circular: bool) -> Tuple[List[Dict], str]:
    """载体的方向性元件：先用自己的注释，没有启动子时自动识别

    返回 (元件列表, 来源)。来源 = annotation / auto_detected / none。
    """
    from core.construct_assembly import is_promoter

    elements = _vector_element_dicts(vector)
    if any(is_promoter(e) for e in elements):
        return elements, "annotation"
    detected = _auto_annotate(backbone, circular)
    if any(is_promoter(e) for e in detected):
        # 自动识别的元件补进来；和已有注释重叠的丢掉（注释优先）
        extra = [e for e in detected if not _overlaps_any(e, elements)]
        return elements + extra, "auto_detected"
    return elements, "none"


def _overlaps_any(element: Dict, others: List[Dict]) -> bool:
    """元件与已有注释是否重叠（同位置重复的自动识别结果不再重复画）"""
    start = int(element.get("start") or 0)
    end = int(element.get("end") or 0)
    for other in others:
        os_ = int(other.get("start") or 0)
        oe = int(other.get("end") or 0)
        if start <= oe and os_ <= end:
            return True
    return False


def resolve_restriction_ends(
    vector,
    backbone: str,
    enzyme_a: Optional[str],
    enzyme_b: Optional[str],
    *,
    direction: str = "auto",
    circular: bool = True,
    window: Optional[Tuple[int, int]] = None,
    join: str = "sticky", cut_pair: Optional[Tuple[int, int]] = None,
) -> RestrictionEnds:
    """把「两把酶」解析成「接基因 5′ 端 / 3′ 端」的指派。

    方向按载体表达盒判定（cassette_hint）；direction=auto 听载体的，
    plus/minus/reverse 是用户显式指定（覆盖载体的判定）。
    """
    from core.construct_assembly import cassette_hint, plan_restriction

    ends = RestrictionEnds(enzyme_5=enzyme_a or "", enzyme_3=enzyme_b or "")
    if not enzyme_a or not enzyme_b or enzyme_a == enzyme_b:
        return ends
    probe = plan_restriction(backbone, enzyme_a, enzyme_b, window=window,
                             circular=circular, warnings=[], join=join, cut_pair=cut_pair)
    if probe is None or probe.left_cut == probe.right_cut:
        return ends
    left_owner, right_owner = (
        (enzyme_b, enzyme_a) if probe.insert_reverse else (enzyme_a, enzyme_b)
    )
    elements, element_source = _cassette_elements(vector, backbone, circular)
    if element_source == "auto_detected":
        annotated = _vector_element_dicts(vector)
        ends.detected = [e for e in elements if e not in annotated]
    hint = cassette_hint(elements, probe.left_cut, probe.right_cut)
    base = hint.get("strand")
    if direction == "plus":
        desired = "+"
    elif direction == "minus":
        desired = "-"
    elif direction == "reverse":
        # 相对「载体判定」反向：反义构建。载体判不出时按默认（正链）反向
        desired = "-" if (base or "+") == "+" else "+"
    else:
        desired = base or "+"
    if desired == "+":
        ends.enzyme_5, ends.enzyme_3 = left_owner, right_owner
    else:
        ends.enzyme_5, ends.enzyme_3 = right_owner, left_owner
    ends.strand, ends.hint, ends.plan = desired, hint, probe
    ends.removed_bp = probe.removed
    if direction in ("plus", "minus", "reverse"):
        ends.source = "user"
        ends.warnings.append(
            f"按你的选择做{'负链' if desired == '-' else '正链'}插入：基因 5′ 端接 "
            f"{ends.enzyme_5}、3′ 端接 {ends.enzyme_3}"
            + ("（与载体表达方向相反，转录产物为反义 RNA）" if desired != (base or "+") else "")
        )
    elif base:
        ends.source = "auto_detected" if element_source == "auto_detected" else "annotation"
    elif hint.get("ambiguous"):
        ends.source = "ambiguous"
        ends.warnings.append(
            f"插入区间两侧都有启动子/RBS 证据，无法自动判定方向：已按默认处理"
            f"（5′ 端接上游切点 {left_owner}）。若与预期不符，请在表单上勾选「反向」重新设计"
        )
    else:
        ends.source = "default"
        ends.warnings.append(
            f"载体上没有可判定的启动子/RBS，插入方向按默认处理（5′ 端接上游切点 "
            f"{left_owner}）。若与预期不符，请勾选「反向」重新设计"
        )
    return ends


def build_restriction_preview(request) -> Dict:
    """限制性克隆实时预览：占位基因跑真实组装，坐标与正式设计完全一致。"""
    from core.construct_assembly import physical_sites

    inline = getattr(request, "vector", None)
    warnings: List[str] = []
    if inline is not None:
        from app.adhoc_vector import vector_from_inline

        vector, vec_meta = vector_from_inline(inline, max_length=MAP_MAX_LENGTH)
        warnings.extend(vec_meta.get("warnings") or [])
    elif request.vector_id:
        vector = get_vector_library().get_vector(request.vector_id)
        if vector is None:
            raise ValueError(f"未找到载体 {request.vector_id}")
    else:
        raise ValueError("预览需要 vector_id 或内联载体")

    enzymes = [e for e in (getattr(request, "enzymes", None) or []) if e]
    backbone = ensure_vector_backbone(vector)
    circular = str(getattr(vector, "topology", "circular") or "circular").lower() != "linear"
    gene_len = int(getattr(request, "insert_length", 0) or 600)
    estimated = not getattr(request, "insert_length", None)
    # Gibson 双酶切线性化与限制性克隆几何相同：连接语义不同（join/保留位点）
    is_gibson = getattr(request, "cloning_method", "restriction") == "gibson"
    arms_mode = getattr(request, "cloning_method", "restriction") == "custom_arms"
    custom_plan = None
    backbone_risk, pair, cut_options = False, None, []
    ends = None

    if arms_mode:
        # 自定义同源臂：臂序列即定位。plan_custom_arms 的 ValueError（无匹配/
        # 多处匹配/两臂相对位置围不出区间）直接抛给前端——这就是拼接顺序的
        # 「判断」：定位不成立用户立刻看到原因，不用提交设计再失败
        from core.construct_assembly import plan_custom_arms
        arm5 = str(getattr(request, "homology_arm_5", "") or "")
        arm3 = str(getattr(request, "homology_arm_3", "") or "")
        if not arm5 or not arm3:
            raise ValueError("同源臂预览需要 5′/3′ 两条重组臂序列")
        custom_plan = plan_custom_arms(
            backbone, arm5, arm3, circular=circular,
            direction=getattr(request, "direction", "auto"),
            elements=_vector_element_dicts(vector))
        warnings.extend(custom_plan.warnings)
        strand = "-" if custom_plan.insert_reverse else "+"
        source = "user"
        ambiguous = False
        evidence: List[Dict] = []
        label5 = f"臂5 {len(arm5)}bp"
        label3 = f"臂3 {len(arm3)}bp"
        sites: List[Dict] = []
        removed = max(0, custom_plan.removed)
    else:
        if len(enzymes) < 2 or enzymes[0] == enzymes[1]:
            raise ValueError("预览需要两个不同的限制酶")
        keep_sites = not is_gibson or getattr(request, "keep_restriction_sites", True)
        pair = tuple(map(int, _rp)) if (_rp := getattr(request, "cut_pair", None)) else None
        ends = resolve_restriction_ends(
            vector, backbone, enzymes[0], enzymes[1],
            direction=getattr(request, "direction", "auto"),
            circular=circular, window=_mcs_window(vector),
            join="gibson" if is_gibson else "sticky", cut_pair=pair)
        warnings.extend(ends.warnings)
        from core.cut_pairs import enumerate_cut_pairs
        cut_options = enumerate_cut_pairs(backbone, enzymes[0], enzymes[1],
                                          window=_mcs_window(vector), circular=circular)
        backbone_risk = False
        if not is_gibson and ends.plan is not None:
            # 骨架重切不阻断（决定权在用户）：预览展示警告，设计需知情确认
            recut = _backbone_recut_warnings(
                backbone, ends.plan.left_cut, ends.plan.right_cut,
                [ends.enzyme_5, ends.enzyme_3], circular=circular)
            backbone_risk, _ = bool(recut), warnings.extend(recut)
        strand = ends.strand
        source = ends.source
        ambiguous = bool(ends.hint.get("ambiguous"))
        evidence = ends.hint.get("evidence") or []
        label5, label3 = ends.enzyme_5, ends.enzyme_3

    asm = assemble_construct(
        vector, "N" * gene_len, "insert",
        method="gibson" if arms_mode else "restriction",
        enzyme_5=(ends.enzyme_5 if not arms_mode else None),
        enzyme_3=(ends.enzyme_3 if not arms_mode else None),
        check_cassette=True,
        extra_elements=(ends.detected if not arms_mode else None),
        join="gibson" if (is_gibson or arms_mode) else "sticky",
        keep_sites=keep_sites if not arms_mode else True, custom_plan=custom_plan,
        allow_backbone_cuts=True, cut_pair=pair)
    warnings.extend(asm.warnings)
    construct = asm.sequence

    if arms_mode:
        removed = max(0, custom_plan.removed)
    else:
        # 切除长度取规划值：构建体两端补了位点残留，长度差反推会算错；
        # 无缝（Gibson 保留位点关）切点外扩到位点边界，按无缝 plan 重取
        removed = max(0, ends.removed_bp)
        if is_gibson and not keep_sites:   # 无缝：按外扩后的无缝 plan 重取
            from core.construct_assembly import plan_restriction as _plan_seamless
            seamless_plan = _plan_seamless(
                backbone, ends.enzyme_5, ends.enzyme_3, window=_mcs_window(vector),
                circular=circular, warnings=[], join="gibson", keep_sites=False)
            if seamless_plan is not None:
                removed = max(0, seamless_plan.removed)
    sites: List[Dict] = []
    if not arms_mode:  # custom_arms 无位点可画
        for role, enzyme, boundary in (("five", ends.enzyme_5, asm.insert_start),
                                       ("three", ends.enzyme_3, asm.insert_end)):
            hits = physical_sites(construct, enzyme, circular=circular) if enzyme else []
            if not hits:
                continue
            best = min(hits, key=lambda h: abs(int(h["cut_fwd"]) - boundary))
            sites.append({"role": role, "enzyme": enzyme, "junction": True,
                          "position": int(best["position"]), "cut": int(best["cut_fwd"]),
                          "end": int(best["position"]) + len(best.get("recognition") or "") - 1})
    # 徽标口径=原始载体的位点数（多切点=多条带）；cut_marks=原载体全部候选
    # 切点（用户选片段时可见位置）；arms/gibson 无废片段概念，不画
    from core.cut_pairs import cut_marks_for_preview, vector_enzyme_counts
    _e5, _e3 = (ends.enzyme_5, ends.enzyme_3) if ends else (label5, label3)
    enzyme_counts = vector_enzyme_counts(backbone, (_e5, _e3), circular=circular)
    cut_marks = ([] if (arms_mode or is_gibson or ends is None) else
                 cut_marks_for_preview(backbone, _e5, _e3, pair, ends.plan, circular=circular))

    lo, hi, features = asm.insert_start - 3000, asm.insert_end + 3000, []
    for feat in asm.features:
        start = int(feat.get("start") or 0)
        end = int(feat.get("end") or 0)
        # 占位基因不是元件：起止都命中才跳过（只看 start 会吞掉恰好同起点的元件）
        if ((start, end) == (asm.insert_start, asm.insert_end)
                and str(feat.get("type") or "").upper() == "CDS"):
            continue
        if end < lo or start > hi:
            continue
        features.append({"name": feat.get("name") or "feature",
                         "type": feat.get("type") or "other", "start": start,
                         "end": end, "strand": feat.get("strand") or "+"})
    features.sort(key=lambda item: item["start"])
    features = features[:16]

    five_at, three_at = max(0, asm.insert_start - 7), max(0, asm.insert_end - 6)
    return {
        "vector": {"name": vector.name or "vector", "length": len(backbone),
                   "topology": "linear" if not circular else "circular"},
        "sites": sites,
        "gene": {"start": asm.insert_start, "end": asm.insert_end,
                 "length": gene_len, "estimated": estimated},
        "removed_bp": removed,
        "construct_length": len(construct),
        "strand": strand,
        "source": source,
        "ambiguous": ambiguous,
        "gene_5prime_enzyme": label5,
        "gene_3prime_enzyme": label3,
        "enzyme_counts": enzyme_counts, "backbone_cut_risk": backbone_risk,
        "cut_options": cut_options, "cut_pair": list(pair) if pair else None,
        "cut_marks": cut_marks,
        "evidence": evidence,
        "features": features,
        "junctions": {
            "five": construct[five_at:five_at + 16],
            "three": construct[three_at:three_at + 16],
        },
        "warnings": warnings,
    }


def _assemble_by_mcs(backbone: str, vector, insert: str, insert_name: str,
                     warnings: List[str]) -> ConstructAssembly:
    """回退路径：把 MCS 注释区间整段换成插入片段（旧行为）

    只在「按真实切点建模」做不了时使用（载体上没有所选酶的位点、切点跨原点等），
    调用方已在 warnings 里说明原因——绝不静默退回。
    """
    if vector.mcs and vector.mcs.start > 0:
        # YAML 坐标为 1-based inclusive
        cut_start = max(0, vector.mcs.start - 1)
        cut_end = min(len(backbone), vector.mcs.end)
    else:
        cut_start = len(backbone) // 2
        cut_end = cut_start
        warnings.append("载体缺少 MCS 定义，已在骨架中部插入")

    # 替换 MCS 区间为 insert
    full = backbone[:cut_start] + insert + backbone[cut_end:]
    insert_start = cut_start + 1  # 1-based
    insert_end = cut_start + len(insert)

    features: List[Dict] = []
    # 插入片段
    features.append(
        {
            "name": insert_name or "Insert",
            "type": "CDS",
            "start": insert_start,
            "end": insert_end,
            "strand": "+",
            "description": "Designed insert",
        }
    )

    # 平移载体原有元件（MCS 之后的元件右移 insert_len - mcs_len）
    mcs_len = max(0, cut_end - cut_start)
    delta = len(insert) - mcs_len
    for elem in vector.elements or []:
        if elem.element_type.value == "multiple_cloning_site":
            continue
        start = elem.start
        end = elem.end
        if end <= cut_start:
            pass
        elif start > cut_end:
            start += delta
            end += delta
        else:
            # 与 MCS 重叠的元件跳过或截断
            continue
        features.append(
            {
                "name": elem.name,
                "type": elem.element_type.value,
                "start": start,
                "end": end,
                "strand": elem.strand or "+",
                "description": elem.description or "",
            }
        )

    return ConstructAssembly(sequence=full, insert_start=insert_start,
                             insert_end=insert_end, features=features,
                             warnings=warnings, plan_used=False)


def assemble_construct(
    vector,
    insert_dna: str,
    insert_name: str = "insert",
    *,
    method: str = "mcs",
    insert_pos: Optional[int] = None,
    enzyme: Optional[str] = None,
    enzyme_5: Optional[str] = None,
    enzyme_3: Optional[str] = None,
    overhang_5: Optional[str] = None,
    overhang_3: Optional[str] = None,
    anchor: Optional[int] = None,
    check_cassette: bool = True,
    extra_elements: Optional[List[Dict]] = None,
    join: str = "sticky",
    keep_sites: bool = True,
    custom_plan=None,
    allow_backbone_cuts: bool = False, cut_pair: Optional[Tuple[int, int]] = None,
) -> ConstructAssembly:
    """按克隆方法把插入片段拼进载体，生成完整构建体序列与注释。

    gibson / restriction / golden_gate 按**真实切点**建模（见
    core/construct_assembly.py：Gibson 一位点线性化、双酶切切走切点间片段、
    GG 切走 Type IIS 切点间片段，两端补位点残留/overhang）；mcs（或缺省）
    沿用「MCS 区间整段替换」——只在按切点建模做不了时回退并说明原因。
    """
    from core.construct_assembly import (
        apply_plan, cassette_direction, cassette_hint, plan_gibson,
        plan_golden_gate, plan_restriction,
    )

    backbone = ensure_vector_backbone(vector)
    insert = insert_dna.upper().replace(" ", "").replace("\n", "")
    warnings: List[str] = []
    if not backbone or set(backbone) == {"N"}:
        warnings.append("载体骨架序列不完整，使用占位骨架组装")

    plan = None
    plan_warnings: List[str] = []   # 单独收集，避免与 apply_plan 的返回重复
    if method in ("gibson", "restriction", "golden_gate") and backbone:
        circular = str(getattr(vector, "topology", "circular") or "circular").lower() != "linear"
        window = (anchor, anchor) if anchor is not None else _mcs_window(vector)
        try:
            if method == "gibson" and custom_plan is not None:
                # 自定义同源臂：边界由臂序列定位（plan 已在调用方算好）
                plan = custom_plan
            elif method == "gibson":
                pos = insert_pos
                if pos is None:
                    pos = (int(vector.mcs.start) - 1) if vector.mcs and vector.mcs.start \
                        else len(backbone) // 2
                plan = plan_gibson(backbone, pos)
            elif method == "restriction":
                plan = plan_restriction(backbone, enzyme_5 or "", enzyme_3 or "", window=window,
                                        circular=circular, warnings=plan_warnings, join=join,
                                        keep_sites=keep_sites, cut_pair=cut_pair)
                # 骨架重切风险：警告始终给出（知情记录/建议改酶）；
                # 自动选择下有风险且未确认 allow_backbone_cuts 时回退 MCS
                # 替换——用户显式传 cut_pair 选片段本身即知情，不拦截
                if plan is not None and join == "sticky":
                    recut = _backbone_recut_warnings(
                        backbone, plan.left_cut, plan.right_cut,
                        [enzyme_5 or "", enzyme_3 or ""], circular=circular)
                    plan_warnings.extend(recut)
                    if recut and not allow_backbone_cuts and cut_pair is None:
                        plan = None
            else:
                plan = plan_golden_gate(backbone, enzyme or "", overhang_5 or "",
                                        overhang_3 or "", window=window,
                                        circular=circular, warnings=plan_warnings)
        except ValueError as exc:
            warnings.extend(plan_warnings)
            warnings.append(f"{exc}：已按 MCS 区间替换构建体")
            plan = None

    if plan is not None and method == "restriction" and check_cassette:
        # 表达方向自检：5'/3' 两端酶填反时粘端照样配得上、构建体照样拼得出来，
        # 只是基因整个反着进质粒（转录不出目标蛋白），而图谱上看不出对错。
        # 载体自己带启动子注释时用它判定基因该往哪条链读，判定结果与本次装配
        # 相反就明确告警并给出「把两端酶对调」的具体写法——不静默改动用户填写。
        required, promoter = cassette_direction(
            _vector_element_dicts(vector) + list(extra_elements or []),
            plan.left_cut, plan.right_cut)
        if required and (required == "-") != bool(plan.insert_reverse):
            # 写进 plan.warnings（apply_plan 会把它复制进返回的告警列表）
            plan.warnings.append(
                f"插入方向和载体启动子相反：{promoter.get('name') or '启动子'}"
                f"（{promoter.get('start')}..{promoter.get('end')}，"
                f"{'负' if str(promoter.get('strand') or '+') == '-' else '正'}链）"
                f"紧邻插入片段的{'下游' if required == '-' else '上游'}一侧，"
                f"按现在的填写（5′ 端 {enzyme_5 or '-'}、3′ 端 {enzyme_3 or '-'}）"
                f"基因会反着插进去、转录不出目标蛋白。把两端酶对调后重新设计"
                f"（5′ 端填 {enzyme_3 or '-'}、3′ 端填 {enzyme_5 or '-'}）。")

    if plan is not None and method == "gibson" and check_cassette:
        # Gibson 方向自检（对称）：基因取向由臂/同源臂设计决定，与载体
        # 表达盒（启动子/RBS）判出的方向相反时，组装出的是反义构建体——
        # 必须在结果层说清；一致时（同源臂模式）也明确说「方向正确」
        right_hint = (plan.right_cut if plan.right_cut > plan.left_cut
                      else plan.left_cut + 1)
        hint = cassette_hint(
            _vector_element_dicts(vector) + list(extra_elements or []),
            plan.left_cut, right_hint)
        hint_strand = hint.get("strand")
        assembly_strand = "-" if plan.insert_reverse else "+"
        if hint_strand and hint_strand != assembly_strand:
            promoter = hint.get("promoter") or {}
            advice = ("请交换两条重组臂的来源位置（取载体另一侧的同源区）重新填写"
                      if custom_plan is not None else
                      "请确认引物同源臂取向与此一致，或改用双酶切克隆（自动判向）")
            where = (f"{promoter.get('name') or '启动子'}"
                     f"（{promoter.get('start')}..{promoter.get('end')}，"
                     f"{'负' if str(promoter.get('strand') or '+') == '-' else '正'}链）"
                     if promoter else "载体表达盒")
            plan.warnings.append(
                f"CDS 方向与启动子相反：{where}决定的转录方向是"
                f"{'负链' if hint_strand == '-' else '正链'}，而按当前臂/同源臂的"
                f"锚定位置，基因落在{'负链' if assembly_strand == '-' else '正链'}"
                f"上，转录产物为反义 RNA。{advice}"
                + ("；把两条臂改为其反向互补序列可翻转装配方向"
                   if custom_plan is not None else ""))
        elif hint_strand and custom_plan is not None:
            # 同源臂模式：明确说「拼对了」，用户不用自己去图上核对
            plan.warnings.append(
                f"CDS 方向与启动子一致（{'负链' if hint_strand == '-' else '正链'}）："
                "按当前两条臂的锚定位置，基因与载体表达方向相同")

    if plan is not None:
        try:
            seq, feats, istart, iend, plan_warnings = apply_plan(
                backbone, _vector_element_dicts(vector) + list(extra_elements or []), plan,
                insert=insert, insert_name=insert_name,
                circular=circular)
        except ValueError:
            warnings.extend(plan_warnings)
            warnings.append("切点跨越载体原点，已按 MCS 区间替换构建体")
        else:
            warnings.extend(plan_warnings)
            _extend_insert_cds_for_site_start(seq, feats, istart, iend, plan,
                                              enzyme_5, vector)
            return ConstructAssembly(
                sequence=seq, insert_start=istart, insert_end=iend,
                features=feats, warnings=warnings, junction=plan.junction)

    # plan 被打掉（骨架重切未确认等）：plan_warnings 里的原因说明一并带出，
    # 否则用户只看到「无法按双酶切建模」却不知道为什么
    warnings.extend(plan_warnings)
    return _assemble_by_mcs(backbone, vector, insert, insert_name, warnings)


def _extend_insert_cds_for_site_start(seq: str, feats: List[Dict],
                                      istart: int, iend: int,
                                      plan, enzyme_5: Optional[str],
                                      vector=None) -> None:
    """CDS 完整性：连接处紧贴基因 5′ 端的载体自带 ATG 就是实际起始密码子
    （restriction 的位点残留 ATG / Gibson 线性化点前 3bp）——把它包进 CDS
    注释。

    此前 CDS 注释从基因第一个碱基开始（接头/残留归为克隆接头），当载体
    提供起始密码子时注释的 ORF 与真实读码框差几 bp，图谱 ORF 推断与
    测序 CDS 变异分析都会偏。只改 CDS 特征的起止，insert_start/end 克隆
    坐标不动；连接处实际碱基与预期不符（意外排布）时保持原注释不动。
    """
    if plan is None:
        return
    offset = 0
    if plan.method == "restriction":
        if not enzyme_5:
            return
        from core.construct_assembly import restriction_site
        site = restriction_site(enzyme_5)
        if not site:
            return
        if plan.insert_reverse:
            from core.seq_utils import revcomp as _revcomp
            j = _revcomp(site).rfind("ATG")
            if j < 0:
                return
            offset = len(site) - j
        else:
            i = site.rfind("ATG")
            if i < 0:
                return
            offset = len(site) - i
    elif plan.method == "gibson":
        from core.construct_assembly import gibson_junction_fusion
        # 融合克隆（连接点贴着/落在正链 CDS 内）：载体 ATG 是融合元件的
        # 碱基而非起始密码子，CDS 不扩展。负链装配的基因 5′ 端在右连接点，
        # 融合检测第一版不覆盖（负链 CDS 语义）
        if (vector is not None and not plan.insert_reverse
                and gibson_junction_fusion(
                    _vector_element_dicts(vector), plan.left_cut) is not None):
            return
        offset = 3
    else:
        return

    feat = feats[0] if feats and feats[0].get("type") == "CDS" else None
    if feat is None:
        return
    if plan.method == "restriction" and getattr(plan, "seamless", False):
        return  # 无缝连接（位点去除）：CDS 起点就是基因自身起点
    if plan.insert_reverse:
        if iend + offset > len(seq):
            return
        from core.seq_utils import revcomp as _revcomp
        if offset != 3 or _revcomp(seq[iend:iend + 3]) != "ATG":
            return
        feat["end"] = iend + offset
    else:
        start = istart - offset
        if start < 1 or seq[start - 1:start + 2] != "ATG":
            return
        feat["start"] = start
    origin = (f"{enzyme_5} 位点" if plan.method == "restriction"
              else "线性化点前的载体碱基")
    feat["description"] = (feat.get("description") or "") + \
        f"；起始密码子来自 {origin}（CDS 已包含）"


def _apply_flanks(request, optimized_dna: str, warnings: List[str]) -> str:
    """把 5′ 前置 / 3′ 尾随固定 DNA 接头按基因方向拼进插入序列。

    接头是既定序列（Kozak、信号肽编码区、终止密码子后标签等）：不参与
    密码子优化与排除位点避让（process_sequence 已在拼接前跑完），拼进后
    随整段走组装——负链插入时 _rc 整段反向互补，接头自然落在基因的
    5′/3′ 端，不需要单独的方向逻辑。

    做两件不修改序列的事：
    - 前置接头末尾自带 ATG 且序列以 M 开头 → 提示冗余起始（与酶切位点
      自带 ATG 同类，但接头是用户给的既定序列，只提示不自动去除）；
    - 接头里命中本设计的克隆酶或排除清单里的酶识别序列 → 信息性提示
      （既定序列不该被改动，但克隆前值得知道）。
    """
    up = getattr(request, "upstream_flank", None) or ""
    down = getattr(request, "downstream_flank", None) or ""
    if not up and not down:
        return optimized_dna
    if up:
        if optimized_dna.startswith("ATG") and up.endswith("ATG"):
            warnings.append(
                "5′ 前置序列末尾自带 ATG，插入序列又以 ATG 开头，蛋白 N 端会"
                "出现连续两个 Met（Met-Met）；请去掉输入序列开头的 M（或前置"
                "序列末尾的 ATG）后重新设计")
    from core.sequence_analysis import RESTRICTION_ENZYMES
    from core.enzyme_sites import canonical_enzyme_name

    cloned = [e for e in (getattr(request, "enzyme_5", None),
                          getattr(request, "enzyme_3", None),
                          getattr(request, "enzyme", None)) if e]
    watch = set(cloned) | set(getattr(request, "exclude_enzymes", None) or [])
    for name in sorted(watch):
        entry = RESTRICTION_ENZYMES.get(canonical_enzyme_name(name))
        site = (entry[0] or "").upper() if entry else ""
        if not site:
            continue
        span = "5′ 前置" if site in up else (
            "3′ 尾随" if site in down else None)
        if span:
            warnings.append(
                f"{span}接头序列内含 {name}（{site}）识别位点：接头是既定"
                f"序列不会改动，但{f'它与克隆酶相同，' if name in cloned else ''}"
                "会破坏克隆/切出接头，请确认位点不干扰本次设计")
    return up + optimized_dna + down


def _resolve_redundant_start_codon(
    request, vector, backbone: str, optimized_dna: str,
    resolved_ends: Optional[RestrictionEnds],
    gibson_single_site: bool = False,
    gibson_arms_plan=None,
) -> Tuple[str, List[str]]:
    """限制性克隆的起始密码子一致性检查（NdeI CATATG 是典型场景）。

    位点在连接处复原后自带 ATG 且紧贴基因 5′ 端——常规 pET 用法里它就是
    起始密码子；插入序列再以 ATG 开头会让蛋白 N 端出现连续两个 Met。

    - trim_redundant_start=True（默认）：去掉插入序列开头的 ATG。蛋白仍与
      输入氨基酸一致——第一个甲硫氨酸由位点 ATG 提供；正反链通用，因为
      基因读法上自己的起始密码子都在插入序列头部。
    - False：只告警不动序列。

    防护：位点方案落空（载体缺位点回退 MCS 整段替换）时位点根本不在
    构建体里，绝不能动插入序列——用与组装完全相同的 plan_restriction
    探测一次，None 即回退路径。Golden Gate overhang（MoClo 惯例）不在此列。
    """
    if (request.cloning_method == CloningMethod.GIBSON
            and gibson_arms_plan is not None):
        # 自定义同源臂：按基因 5′ 连接点的载体碱基判定（正反链）
        return _resolve_custom_arms_start_codon(
            request, vector, backbone, gibson_arms_plan, optimized_dna)
    if (request.cloning_method == CloningMethod.GIBSON
            and (gibson_single_site
                 or getattr(request, "gibson_linearization", "double_digest")
                 != "double_digest")):
        # Gibson 单位点线性化：无位点残留，按连接处实际碱基判定
        return _resolve_gibson_start_codon(request, vector, backbone, optimized_dna)
    if request.cloning_method not in (CloningMethod.RESTRICTION,
                                      CloningMethod.GIBSON) or not optimized_dna:
        return optimized_dna, []
    if not str(optimized_dna).upper().startswith("ATG"):
        return optimized_dna, []

    enzyme_5_name = (resolved_ends.enzyme_5 if resolved_ends and resolved_ends.enzyme_5
                     else (request.enzyme_5 or request.enzyme))
    if not enzyme_5_name:
        return optimized_dna, []
    from core.construct_assembly import plan_restriction, restriction_site
    site = restriction_site(enzyme_5_name)
    if not site:
        return optimized_dna, []

    strand = (resolved_ends.strand if resolved_ends and resolved_ends.strand else "+")
    # 位点 ATG 是否紧贴基因 5′ 端：正链看位点末尾，负链（反向互补读基因）
    # 看位点开头（其反向互补的末尾是 ATG）。无缝连接（位点去除）时位点
    # 根本不在构建体里，起始密码子语义归基因自身
    gibson_double = (request.cloning_method == CloningMethod.GIBSON
                     and not gibson_single_site)
    if gibson_double and not getattr(request, "keep_restriction_sites", True):
        return optimized_dna, []
    supplies = site.endswith("ATG") if strand != "-" else site.startswith("CAT")
    if not supplies:
        return optimized_dna, []

    circular = str(getattr(vector, "topology", "circular")
                   or "circular").lower() != "linear"
    probe = plan_restriction(
        backbone, request.enzyme_5 or request.enzyme,
        request.enzyme_3 or request.enzyme,
        window=_mcs_window(vector), circular=circular, warnings=[])
    if probe is None:
        # 位点方案落空 → 走 MCS 整段替换，位点不在构建体里：
        # 此时插入序列的 ATG 是唯一的起始密码子，不能去
        return optimized_dna, []

    if getattr(request, "trim_redundant_start", True) and len(optimized_dna) > 6:
        trimmed = optimized_dna[3:]
        return trimmed, [
            "已自动去除冗余起始密码子："
            f"{enzyme_5_name} 位点自带 ATG 并充当起始密码子，插入序列再以 ATG 开头"
            "会使蛋白 N 端出现连续两个 Met；已去掉插入序列开头的 ATG"
            "（第一个甲硫氨酸由位点提供，蛋白与你输入的氨基酸序列一致）。"
            "不希望自动去除可在设计表单取消勾选「位点自带起始密码子时去除冗余 ATG」"
        ]
    return optimized_dna, [
        "检测到冗余起始密码子："
        f"{enzyme_5_name} 位点自带 ATG 并充当起始密码子，插入序列又以 ATG 开头，"
        "蛋白 N 端会出现连续两个 Met（Met-Met）；建议去掉输入序列开头的 ATG/M "
        "重新设计，或勾选「位点自带起始密码子时去除冗余 ATG」"
    ]


def _resolve_custom_arms_start_codon(
    request, vector, backbone: str, plan, optimized_dna: str,
) -> Tuple[str, List[str]]:
    """自定义同源臂：起始密码子按基因 5′ 连接点的载体碱基判定。

    正链装配看左连接点前 3bp，负链装配（臂角色互换）看右连接点后 3bp 的
    反向互补；去除的是插入序列自己开头的 ATG（基因读法），蛋白仍与输入
    氨基酸一致。正链装配先做融合克隆检查（连接点落在正链 CDS/tag 内或
    紧贴其末端时与 Gibson 单点同口径：只告警读码框/内部 Met，不自动去除）；
    负链臂落进 minus CDS 的融合检测仍挂账。环状载体的连接点碱基跨原点时
    按绕回窗口取（不再漏检）。
    """
    if not backbone or not optimized_dna:
        return optimized_dna, []
    if not str(optimized_dna).upper().startswith("ATG"):
        return optimized_dna, []
    circular = _vector_is_circular(vector) if vector is not None else True
    if not plan.insert_reverse and vector is not None:
        from core.construct_assembly import gibson_junction_fusion
        fusion = gibson_junction_fusion(
            _vector_element_dicts(vector), plan.left_cut)
        if fusion is not None:
            return optimized_dna, _fusion_frame_warnings(fusion, plan.left_cut)
    if plan.insert_reverse:
        from core.seq_utils import revcomp as _rc
        leading_ok = _rc(_circular_backbone_window(
            backbone, plan.right_cut, 3, circular)) == "ATG"
    else:
        leading_ok = _circular_backbone_window(
            backbone, plan.left_cut - 3, 3, circular).upper() == "ATG"
    if not leading_ok:
        return optimized_dna, []
    where = "线性化点前" if not plan.insert_reverse else "线性化点后（负链读法）"
    if getattr(request, "trim_redundant_start", True) and len(optimized_dna) > 6:
        return optimized_dna[3:], [
            "已自动去除冗余起始密码子："
            f"{where}的载体碱基以 ATG 结尾并充当起始密码子，插入序列再以 ATG 开头"
            "会使蛋白 N 端出现连续两个 Met；已去掉插入序列开头的 ATG"
            "（第一个甲硫氨酸由载体提供，蛋白与你输入的氨基酸序列一致）。"
            "不希望自动去除可在设计表单取消勾选「位点自带起始密码子时去除冗余 ATG」"
        ]
    return optimized_dna, [
        "检测到冗余起始密码子："
        f"{where}的载体碱基以 ATG 结尾并充当起始密码子，插入序列又以 ATG 开头，"
        "蛋白 N 端会出现连续两个 Met（Met-Met）；建议去掉输入序列开头的 ATG/M "
        "重新设计，或勾选「位点自带起始密码子时去除冗余 ATG」"
    ]


def _resolve_gibson_start_codon(
    request, vector, backbone: str, optimized_dna: str,
) -> Tuple[str, List[str]]:
    """Gibson 的起始密码子一致性（比 restriction 复杂，见各分支）。

    Gibson 定位通常按酶切位点（gibson_site / MCS 起点，见
    resolve_gibson_insert_pos），但没有位点残留——连接处语义由**实际碱基**
    决定，分三种情形：

    - 融合克隆：线性化点落在正链 CDS/tag 内或紧贴其末端 → 基因与元件融合，
      起始密码子语义不成立；只告警读码框（碱基数非 3 的倍数 = 移位）与
      内部 Met，不自动去除（保留与否取决于实验意图，如肠激酶位点）；
    - 独立表达：线性化点前 3bp 为 ATG 且插入序列以 ATG 开头 → 与 restriction
      同一套去除/告警，CDS 注释同样向前扩 3bp；
    - 方向：Gibson 当前恒按正链组装（模型限制），表达盒判为负链时告警
      （方向告警在 assemble_construct 内做，随组装告警返回）。
    """
    if not backbone or not optimized_dna or not vector:
        return optimized_dna, []
    if not str(optimized_dna).upper().startswith("ATG"):
        return optimized_dna, []

    from core.construct_assembly import gibson_junction_fusion
    pos = resolve_gibson_insert_pos(request, vector)          # 0-based
    if pos >= len(backbone):
        return optimized_dna, []
    circular = _vector_is_circular(vector)
    # 连接点前 3bp：环状时跨原点按绕回窗口取（载体末尾 + 开头）
    leading = _circular_backbone_window(backbone, pos - 3, 3, circular).upper()
    elements = _vector_element_dicts(vector)
    fusion = gibson_junction_fusion(elements, pos)
    if fusion is not None:
        # 融合克隆：读码框与内部 Met 告警与插入序列首密码子无关
        return optimized_dna, _fusion_frame_warnings(fusion, pos)

    if leading != "ATG":
        return optimized_dna, []
    where = request.gibson_site or "MCS 起点"
    if getattr(request, "trim_redundant_start", True) and len(optimized_dna) > 6:
        return optimized_dna[3:], [
            "已自动去除冗余起始密码子："
            f"线性化点（{where}处）前的载体碱基以 ATG 结尾并充当起始密码子，"
            "插入序列再以 ATG 开头会使蛋白 N 端出现连续两个 Met；已去掉插入序列"
            "开头的 ATG（第一个甲硫氨酸由载体提供，蛋白与你输入的氨基酸序列一致）。"
            "不希望自动去除可在设计表单取消勾选「位点自带起始密码子时去除冗余 ATG」"
        ]
    return optimized_dna, [
        "检测到冗余起始密码子："
        f"线性化点（{where}处）前的载体碱基以 ATG 结尾并充当起始密码子，"
        "插入序列又以 ATG 开头，蛋白 N 端会出现连续两个 Met（Met-Met）；"
        "建议去掉输入序列开头的 ATG/M 重新设计，或勾选"
        "「位点自带起始密码子时去除冗余 ATG」"
    ]


def derive_gg_overhangs(vector, enzyme_name: str) -> Tuple[str, str, bool]:
    """从载体 MCS 推导 Golden Gate 4bp overhang。

    返回 (5' overhang, 3' overhang, 是否成功从载体推导)。
    在载体可用的 overhang 组合里优先选通过连接保真度筛查
    （非回文、互差 ≥2 碱基、GC 不为 0/100%，见
    core.primer_designer.screen_gg_overhang_pair）的一对；
    一对都挑不出来时保持原行为（取首尾两个唯一位点），由调用方告警——
    严格拒绝会让某些载体彻底无法设计。
    无法推导时回退默认值 AATG/GCTT 并置 derived=False（调用方应告警，
    用户需自行确认 overhang 与阅读框/载体衔接）。
    """
    if vector and vector.mcs and vector.mcs.sites:
        unique = [s for s in vector.mcs.sites if s.is_unique and s.overhang]
        pads = [s.overhang[:4].upper().ljust(4, "A")[:4] for s in unique]
        if len(pads) >= 2:
            for i in range(len(pads)):
                for j in range(len(pads) - 1, i, -1):
                    if not screen_gg_overhang_pair(pads[i], pads[j]):
                        return pads[i], pads[j], True
            # 没有合格组合：保持原行为（首尾两个唯一位点）
            return pads[0], pads[-1], True
        if len(pads) == 1:
            return pads[0], _DEFAULT_GG_OVERHANGS[1], False
    return _DEFAULT_GG_OVERHANGS[0], _DEFAULT_GG_OVERHANGS[1], False


def _primer_to_info(p, include_overhang: bool = True) -> PrimerInfo:
    notes = p.notes or ""
    quality_notes = []
    # 质量标记
    if hasattr(p, "sequence"):
        from core.primer_designer import PrimerDesigner

        designer = PrimerDesigner()
        self_comp = designer._max_self_complementarity(p.sequence)
        if self_comp >= 6:
            quality_notes.append(f"self-comp={self_comp}")
        if p.tm < 55 or p.tm > 65:
            quality_notes.append(f"Tm={p.tm:.1f}")
        if p.gc_content < 35 or p.gc_content > 65:
            quality_notes.append(f"GC={p.gc_content:.1f}%")
    if quality_notes:
        notes = (notes + "; " if notes else "") + "QC: " + ", ".join(quality_notes)

    return PrimerInfo(
        name=p.name,
        sequence=p.sequence,
        full_sequence=p.full_sequence if include_overhang else p.sequence,
        tm=p.tm,
        gc_content=p.gc_content,
        length=p.length,
        overhang=getattr(p, "overhang", None) or None,
        target_start=getattr(p, "target_start", None),
        target_end=getattr(p, "target_end", None),
        notes=notes or None,
    )


def process_sequence(
    sequence: str,
    sequence_type: SequenceType,
    optimize_codons: bool,
    target_species: str,
    gc_min: float,
    gc_max: float,
    exclude_enzymes: Optional[List[str]] = None,
) -> Tuple[str, Optional[float], Optional[float], List[str]]:
    """处理输入序列 → DNA；返回 (dna, cai, gc, warnings)。

    exclude_enzymes: 需要从优化序列中排除的限制酶名称。**只对「氨基酸输入且
    启用密码子优化」生效**：DNA 输入、未启用密码子优化的反翻译都原样返回，
    不校验、不告警、也不做避让。该列表同样从不作用于载体骨架或组装过程
    （载体序列只参与组装，排除位点只在插入片段的反翻译/优化里使用）。
    """
    from core.codon_optimizer import CodonOptimizer
    from core.sequence_analysis import RESTRICTION_ENZYMES

    # 字符归一（全角/异体星号/各类空白）：粘贴来的序列常带全角＊、全角空格、
    # NBSP 与零宽字符，散在各处 .replace(" ", "") 处理不干净
    from core.seq_input import normalize_dna_text, normalize_sequence_text

    seq = (normalize_dna_text(sequence) if sequence_type == SequenceType.DNA
           else normalize_sequence_text(sequence))
    warnings: List[str] = []
    # 排除位点只能靠同义密码子实现：只有「氨基酸输入 + 启用密码子优化」这条
    # 路径能真正做到。DNA 输入无法沉默突变，未优化路径只是反翻译，都不该让
    # 用户以为排除已生效——因此直接忽略该参数，连未知酶/位点告警也不产生。
    applies_exclusion = sequence_type == SequenceType.AMINO_ACID and optimize_codons
    exclude_enzymes = (
        [e for e in (exclude_enzymes or []) if e] if applies_exclusion else []
    )

    # 酶名合法性检查，取识别序列作为避让 motif（仅适用时）。
    # 酶名先归一到规范拼写（ndei → NdeI）：Excel 排除酶列常是小写
    from core.enzyme_sites import canonical_enzyme_name

    motifs: List[str] = []
    unknown: List[str] = []
    for enzyme in exclude_enzymes:
        canonical = canonical_enzyme_name(enzyme)
        entry = RESTRICTION_ENZYMES.get(canonical)
        if entry and entry[0]:
            motifs.append(entry[0].upper())
        else:
            unknown.append(enzyme)
    if unknown:
        warnings.append(f"忽略未知限制酶: {', '.join(unknown)}")

    if sequence_type == SequenceType.DNA:
        # DNA 输入不做任何排除：序列已是指定碱基，沉默突变不可能，排除参数
        # 在这里没有可执行的语义（不是「尝试后失败」，而是根本不适用）。
        return seq, None, None, warnings

    # 终审 A-05：未知物种在此显式失败（设计结果 FAILED + 错误明细），
    # 不再静默回退大肠杆菌表——此前 pichia/insect/bacillus/martian 全部
    # 静默返回 COMPLETED。CodonOptimizer 构造抛 ValueError。
    optimizer = CodonOptimizer(species=target_species)
    if optimize_codons:
        # 密码子优化结果缓存（24h TTL）：同 (序列, 物种, GC 区间, 排除酶) 直接复用
        cache_key_enzymes = sorted(exclude_enzymes)
        try:
            cached_opt = cache.get_codon_optimization(
                sequence=seq, species=target_species, gc_min=gc_min, gc_max=gc_max,
                exclude_enzymes=cache_key_enzymes,
            )
        except Exception:
            cached_opt = None
        if cached_opt is not None:
            return (
                cached_opt["dna_sequence"],
                cached_opt["cai"],
                cached_opt["gc_content"],
                list(cached_opt["warnings"]),
            )

        result = optimizer.optimize(
            seq,
            gc_target=(gc_min / 100, gc_max / 100),
            avoid_motifs=motifs,
        )
        # 终审 D-04：未知限制酶、经优化仍无法排除的位点这两条局部告警
        # 此前被整体丢弃（exclude_enzymes=["NotAnEnzyme"] 返回 warnings=[]，
        # 用户以为序列干净直接下单合成），必须合并进返回值
        warnings.extend(result.warnings)
        if motifs:
            kept = [m for m in motifs if m in result.dna_sequence]
            if kept:
                warnings.append(
                    f"警告：经密码子优化仍无法完全排除的位点: {', '.join(kept)}"
                )
        try:
            cache.cache_codon_optimization(
                sequence=seq,
                species=target_species,
                gc_min=gc_min,
                gc_max=gc_max,
                exclude_enzymes=cache_key_enzymes,
                result={
                    "dna_sequence": result.dna_sequence,
                    "cai": result.cai,
                    "gc_content": result.gc_content * 100,
                    # 缓存键含 exclude_enzymes，告警与该输入绑定，可整体缓存
                    "warnings": list(warnings),
                },
            )
        except Exception:
            pass
        return result.dna_sequence, result.cai, result.gc_content * 100, list(warnings)

    # 不优化：按物种频率忠实反翻译（最高频密码子，不做 GC 迭代）。
    # 排除位点参数在上方已按 apply 条件清空，这里不再有任何避让分支。
    dna = optimizer.back_translate(seq)
    warnings.append("未启用密码子优化，使用物种最优密码子反翻译")
    cai = optimizer._calculate_cai(dna, seq)
    gc = optimizer._calculate_gc_content(dna) * 100
    return dna, cai, gc, warnings


def resolve_gibson_insert_pos(request: DesignRequest, vector) -> int:
    """解析 Gibson 同源重组位置（0-based）。

    优先级：指定的酶切位点（MCS 内 → 该位点切点；一次性载体的唯一位点表 → 该位点切点）
    > MCS 起点 > 骨架中点。
    最后一级此前固定 100：对没有 MCS 的载体（上传/在线导入的载体都没有 YAML 的
    mcs 块）第 100 bp 是与实际线性化点无关的任意位置，同源臂必然错；骨架中点至少
    与 assemble_construct 的插入点一致。
    """
    if request.gibson_site and vector:
        if vector.mcs:
            for site in vector.mcs.sites:
                if site.enzyme_name == request.gibson_site and site.cut_position_5:
                    return max(0, site.cut_position_5 - 1)  # 1-based → 0-based
        # 一次性载体：唯一位点可能落在推测出来的 MCS 区间之外（用户明确指定了
        # 那把酶），此时按扫描出的唯一切点定位
        cuts = getattr(vector, "unique_site_cuts", None) or {}
        hit = cuts.get(request.gibson_site) or []
        if len(hit) == 1:
            return max(0, hit[0] - 1)
    if vector and vector.mcs and vector.mcs.start:
        return vector.mcs.start - 1
    seq_len = len(getattr(vector, "sequence", "") or "")
    return seq_len // 2 if seq_len >= 2 else 100


def _construct_segment_molecule(assembly, plan, n_left: int, n_right: int,
                                circular: bool) -> Optional[str]:
    """合成分子 = 组装构建体上「片段（含接头残留）± 两侧侧翼」的真实段落。

    构建体是组装的既成结果、坐标自洽——分子直接取它的段落，oligo 与图谱
    的对应关系由构造保证，不再依赖任何臂/接头取向簿记（基因取向 vs 构建
    体取向正是 v2.26.2 首/尾 oligo 装反的根源）。片段边界用 insert_start/
    insert_end（基因本体）± 接头残留长度推出；跨原点（环状）取环绕段落。
    返回 None 表示取不到（回退路径 / 边界越界 / 含非 ATGC 碱基）。
    """
    if assembly is None or not getattr(assembly, "plan_used", True):
        return None
    cons = (assembly.sequence or "").upper()
    i_s, i_e = assembly.insert_start, assembly.insert_end
    if not cons or not i_s or not i_e or not (1 <= i_s <= i_e <= len(cons)):
        return None
    head_len = len(getattr(plan, "head", "") or "") if plan is not None else 0
    tail_len = len(getattr(plan, "tail", "") or "") if plan is not None else 0
    frag_start = i_s - head_len          # 1-based：接头残留首碱基
    frag_end = i_e + tail_len            # 1-based：接头残留末碱基
    start1, end1 = frag_start - n_left, frag_end + n_right
    length = len(cons)
    if start1 < 1 or end1 > length:
        if not circular or end1 - start1 + 1 > length:
            return None
        # 跨原点环绕：只可能一侧越界，两侧各取模。end1 不取模时切片
        # cons[:end1] 会被钳制成整条序列，长度校验必然失败、白走回退
        seg = cons[(start1 - 1) % length:] + cons[:end1 % length]
    else:
        seg = cons[start1 - 1:end1]
    if len(seg) != n_left + (frag_end - frag_start + 1) + n_right:
        return None
    if not set(seg) <= set("ATGC"):
        return None
    return seg


def _restriction_construct_molecule(assembly, enzyme_5: Optional[str],
                                    enzyme_3: Optional[str],
                                    n: int, circular: bool) -> Optional[str]:
    """限制性合成分子 = 构建体上「连接点外侧各 n nt + 位点残留 + 基因」段落。

    与 _construct_segment_molecule 同思路，但限制性的接头残留（位点复原）
    长度不等于识别序列长度的情形无法从 plan 直接拿——按位点残留在构建体
    上紧贴基因两端的实际位置定位。定位失败（回退路径 / 侧翼不足 / 侧翼里
    又出现所选酶位点——串联位点会多切一刀 / 线性载体侧翼越出末端）返回
    None，调用方回退固定保护碱基。
    """
    if assembly is None or not getattr(assembly, "plan_used", True):
        return None
    from core.construct_assembly import restriction_site
    site5 = restriction_site(enzyme_5 or "")
    site3 = restriction_site(enzyme_3 or "")
    if not site5 or not site3:
        return None
    cons = (assembly.sequence or "").upper()
    i_s, i_e = assembly.insert_start, assembly.insert_end
    if not cons or not i_s or not i_e or not (1 <= i_s <= i_e <= len(cons)):
        return None
    g5 = i_s - 1              # 0-based 基因首碱基
    g3 = i_e                  # 0-based 切片边界：基因末碱基后一位
    l5, l3 = len(site5), len(site3)
    if cons[g5 - l5:g5] == site5 and cons[g3:g3 + l3] == site3:
        head_len, tail_len = l5, l3
    elif cons[g5 - l3:g5] == site3 and cons[g3:g3 + l5] == site5:
        head_len, tail_len = l3, l5
    else:
        return None
    a = g5 - head_len - n                 # 0-based：分子起点（含侧翼）
    b = g3 + tail_len + n                 # 0-based：分子终点（不含）
    length = len(cons)
    if a >= 0 and b <= length:
        molecule = cons[a:b]
    elif circular and b - a <= length:     # 环状载体跨原点取环绕段落
        molecule = cons[a % length:] + cons[:b % length]
    else:
        return None
    if len(molecule) != n + head_len + (g3 - g5) + tail_len + n:
        return None
    for site in (site5, site3):
        for end in (molecule[:n], molecule[-n:]):
            if not end or site in end:
                return None
    return molecule


def _oligo_hits_construct(s: str, cons_up: str, cons_rc: str) -> bool:
    """oligo 序列是否命中构建体（顶链或其反向互补）。

    环状构建体上的合成分子可能是跨原点的环绕段落（切除区间跨原点被
    apply_plan 旋转后，右连接点就在字符串末端，侧翼必然绕过原点）——
    线性子串判断会漏掉环绕命中，把合法 oligo 误报成「无法定位」，用
    双倍序列补上环绕语义。
    """
    return (s in cons_up or s in cons_rc
            or s in cons_up + cons_up or s in cons_rc + cons_rc)


def _synthesis_molecule(
    request, optimized_dna: str, vector, backbone: str, *, assembly=None,
    enzyme_5: Optional[str], enzyme_3: Optional[str],
    gibson_double_digest: bool, gibson_plan, gibson_insert_pos: Optional[int],
    gibson_arms_plan, designer,
) -> Tuple[str, Optional[str], bool]:
    """全基因合成的目标分子：把克隆末端直接合进 oligo（能合就合）。

    返回 (分子序列, 说明, 末端是否取自构建体)。说明为 None 表示末端未
    包含（Golden Gate 等连接语义不同的方法）——调用方回退旧行为（合成
    基因 + 组装后扩增加末端）。

    分子优先直接取**组装构建体上「片段±侧翼」的现成段落**：构建体坐标
    自洽，oligo 与图谱的对应关系由构造保证，不需要任何取向换算；取不到
    （载体回退路径等）才退回按 plan 字段重新拼接，末端用固定保护碱基：

    - 限制性：两端各 6nt + 连接处位点残留 + 基因。侧翼/保护碱基保证酶切
      效率，酶切后随小片段去除；
    - Gibson（双酶切/单位点/自定义同源臂）：两端同源臂 + 基因（负链装配
      时基因取反向互补），合成产物退火后直接与线性化载体组装。
    """
    from core.construct_assembly import restriction_site
    from core.seq_utils import revcomp as _rc

    method = request.cloning_method
    pad = designer.PROTECTIVE_SPACER
    if method == CloningMethod.RESTRICTION:
        e5 = enzyme_5 or request.enzyme_5 or request.enzyme
        e3 = enzyme_3 or request.enzyme_3 or request.enzyme
        site5 = restriction_site(e5 or "")
        site3 = restriction_site(e3 or "")
        if site5 and site3:
            molecule = _restriction_construct_molecule(
                assembly, e5, e3, len(pad), _vector_is_circular(vector))
            if molecule:
                return (molecule,
                        f"合成 oligo 已包含克隆末端（{e5}/{e3} 位点与两端各 "
                        f"{len(pad)}nt 载体连接点侧翼）：合成产物酶切后可直接"
                        "连接载体，无需 PCR 扩增加末端；整条分子取自构建体"
                        "段落，oligo 全序列可在构建体图谱上核对",
                        True)
            return (pad + site5 + optimized_dna + _rc(site3) + pad,
                    f"合成 oligo 已包含克隆末端（{e5}/{e3} 位点与两端 "
                    f"{len(pad)}nt 保护碱基）：合成产物酶切后可直接连接载体，"
                    "无需 PCR 扩增加末端",
                    False)
    elif method == CloningMethod.GIBSON:
        lin = getattr(request, "gibson_linearization", "double_digest")
        arm = int(getattr(request, "homology_arm", 20) or 20)
        circular = _vector_is_circular(vector)
        if lin == "custom_arms" and gibson_arms_plan is not None:
            body = (_rc(optimized_dna) if gibson_arms_plan.insert_reverse
                    else optimized_dna)
            # 侧翼长度按解析后的左右臂取（用户两条臂长度可能不同）
            n_left = len(getattr(gibson_arms_plan, "arm_left_seq", "") or "")
            n_right = len(getattr(gibson_arms_plan, "arm_right_seq", "") or "")
            molecule = _construct_segment_molecule(
                assembly, gibson_arms_plan, n_left or arm, n_right or arm,
                circular)
            if molecule:
                return (molecule,
                        "合成 oligo 已包含两条同源臂（载体同源区，与构建体"
                        "序列一致）：合成产物可直接 Gibson 组装，无需 PCR "
                        "扩增加臂；整条分子取自构建体段落",
                        True)
            # 回退：按 plan 的构建体取向臂字段拼接（无构建体可用时）
            left_arm = (getattr(gibson_arms_plan, "arm_left_seq", "")
                        or gibson_arms_plan.arm_5_seq)
            right_arm = (getattr(gibson_arms_plan, "arm_right_seq", "")
                         or gibson_arms_plan.arm_3_seq)
            return (left_arm + body + right_arm,
                    "合成 oligo 已包含两条同源臂（载体同源区，与构建体序列"
                    "一致）：合成产物可直接 Gibson 组装，无需 PCR 扩增加臂",
                    True)
        if gibson_double_digest and gibson_plan is not None:
            body = _rc(optimized_dna) if gibson_plan.insert_reverse else optimized_dna
            molecule = _construct_segment_molecule(
                assembly, gibson_plan, arm, arm, circular)
            if molecule:
                return (molecule,
                        "合成 oligo 已包含两端同源臂（载体同源区+位点残留）："
                        "合成产物可直接 Gibson 组装，无需 PCR 扩增加臂；"
                        "整条分子取自构建体段落",
                        True)
            left_arm = _circular_backbone_window(
                backbone, gibson_plan.left_cut - arm, arm, circular)
            right_arm = _circular_backbone_window(
                backbone, gibson_plan.right_cut, arm, circular)
            return (left_arm + gibson_plan.head + body + gibson_plan.tail
                    + right_arm,
                    "合成 oligo 已包含两端同源臂（载体同源区+位点残留）："
                    "合成产物可直接 Gibson 组装，无需 PCR 扩增加臂",
                    True)
        insert_pos = (gibson_insert_pos if gibson_insert_pos is not None
                      else resolve_gibson_insert_pos(request, vector))
        insert_pos = max(0, min(insert_pos, max(0, len(backbone) - 1)))
        molecule = _construct_segment_molecule(
            assembly, None, arm, arm, circular)
        if molecule:
            return (molecule,
                    "合成 oligo 已包含两端同源臂：合成产物可直接 Gibson 组装，"
                    "无需 PCR 扩增加臂；整条分子取自构建体段落",
                    True)
        left_arm = _circular_backbone_window(
            backbone, insert_pos - arm, arm, circular)
        right_arm = _circular_backbone_window(
            backbone, insert_pos, arm, circular)
        return (left_arm + optimized_dna + right_arm,
                "合成 oligo 已包含两端同源臂：合成产物可直接 Gibson 组装，"
                "无需 PCR 扩增加臂",
                True)
    return optimized_dna, None, False


def _annotate_synthesis_oligo_ends(infos, molecule: str, ends_note: Optional[str],
                                   request, *, ends_from_construct: bool = False,
                                   enzyme_5, enzyme_3, designer) -> None:
    """给覆盖合成分子两端的首/尾 oligo 在 notes 里写明末端成分。

    限制性分子两端 = 载体侧翼/保护碱基 + 位点：末端取自构建体时 oligo
    全序列都在图谱里（首/尾 oligo 横跨连接点，正好用来核对拼接），位点
    在哪一端随装配链向而定，notes 不指名方向；回退固定保护碱基时酶切后
    随小片段去除、不进构建体（此时分子是基因取向，e5 贴 5′ 端）——差在
    哪、为什么差写进 notes，不留给用户猜。Gibson 分子的两端是载体同源臂，
    组装后与载体序列一致（就在图谱里）。
    """
    if ends_note is None:
        return
    pad = designer.PROTECTIVE_SPACER
    pad_len = len(pad)
    mol_len = len(molecule)
    method = request.cloning_method
    e5 = enzyme_5 or request.enzyme_5 or request.enzyme
    e3 = enzyme_3 or request.enzyme_3 or request.enzyme
    for info in infos:
        ts, te = info.target_start, info.target_end
        if ts is None or te is None:
            continue
        parts: List[str] = []
        if method == CloningMethod.RESTRICTION:
            if ts == 0:
                if ends_from_construct:
                    parts.append(
                        f"分子5′末端={pad_len}nt载体连接点侧翼+连接处位点："
                        "均为构建体序列，oligo 全序列可在图谱核对")
                elif molecule.startswith(pad):
                    parts.append(
                        f"分子5′末端={pad_len}nt保护碱基+{e5}位点："
                        "保护碱基保障酶切效率，酶切后随小片段去除，不在构建体中")
            if te == mol_len:
                if ends_from_construct:
                    parts.append(
                        f"分子3′末端=连接处位点+{pad_len}nt载体连接点侧翼："
                        "均为构建体序列，oligo 全序列可在图谱核对")
                elif molecule.endswith(pad):
                    parts.append(
                        f"分子3′末端={e3}位点+{pad_len}nt保护碱基："
                        "酶切后去除，不在构建体中")
        elif method == CloningMethod.GIBSON:
            if ts == 0:
                parts.append("分子5′末端为载体同源臂（组装后与载体序列一致）")
            if te == mol_len:
                parts.append("分子3′末端为载体同源臂（组装后与载体序列一致）")
        if parts:
            info.notes = ((info.notes + "；") if info.notes else "") + "；".join(parts)


def design_primers_for_method(
    request: DesignRequest,
    optimized_dna: str,
    vector,
    backbone: str,
    *,
    assembly=None,
    enzyme_5: Optional[str] = None,
    enzyme_3: Optional[str] = None,
    gibson_double_digest: bool = False,
    gibson_plan=None,
    gibson_insert_pos: Optional[int] = None,
    gibson_arms_plan=None,
) -> List[PrimerInfo]:
    """按「插入片段来源 × 克隆方法」设计引物/寡核苷酸。

    enzyme_5 / enzyme_3 是**已解析**的指派（接基因 5′/3′ 端的酶）：由
    resolve_restriction_ends 按载体表达盒定下来，这里不再读 request 里的原值，
    否则引物和构建体会各说各话。gibson_insert_pos 是组装用的单位点线性化
    位置（同酶双填回退时已被覆盖为该酶自己的切点），不传才就地重算。

    - 来源=PCR：产出带克隆末端的扩增引物对（末端形态由克隆方法决定）
    - 来源=全基因合成：克隆末端（位点+保护碱基/同源臂）直接合进重叠
      组装 oligo 组，合成产物即可组装/酶切（Golden Gate 末端语义不同，
      保留「合成基因 + 组装后扩增加末端」旧路径）
    """
    from core.primer_designer import PrimerDesigner

    designer = PrimerDesigner()
    name = request.sequence_name or "insert"
    primers: List[PrimerInfo] = []

    def _cloning_pair() -> List[PrimerInfo]:
        """按克隆方法设计带对应末端的引物对"""
        if request.cloning_method == CloningMethod.GIBSON:
            if (getattr(request, "gibson_linearization", "double_digest")
                    == "custom_arms"):
                # 自定义同源臂：插入分子 = 臂5 + 基因 + 臂3。臂序列用
                # plan 的解析结果（栏位顺序/粘贴链向可能已被自动纠正），
                # 不再直接用 request 里的原始填写
                arms = (gibson_arms_plan if gibson_arms_plan is not None else None)
                arm5_seq = getattr(arms, "arm_5_seq", "") or request.homology_arm_5
                arm3_seq = getattr(arms, "arm_3_seq", "") or request.homology_arm_3
                pair = designer.design_custom_arm_primers(
                    optimized_dna, arm5_seq, arm3_seq, primer_name=name)
                return [_primer_to_info(pair.forward), _primer_to_info(pair.reverse)]
            if gibson_double_digest and gibson_plan is not None:
                # 双酶切线性化：插入分子 = 位点残留 + 基因 + 位点残留，
                # 同源臂越过切点锚定线性化载体两端。负链装配时基因取
                # 反向互补进片段（构建体正链上是 revcomp(基因)），臂的
                # 左右只由连接点决定、不随取向对调
                body = optimized_dna
                if gibson_plan.insert_reverse:
                    from core.seq_utils import revcomp as _gene_rc
                    body = _gene_rc(optimized_dna)
                fragment = gibson_plan.head + body + gibson_plan.tail
                pair = designer.design_gibson_primers(
                    fragment,
                    backbone if backbone else "N" * 5000,
                    gibson_plan.left_cut,
                    gibson_plan.right_cut,
                    homology_arm=request.homology_arm,
                    primer_name=name,
                    circular=_vector_is_circular(vector),
                )
                return [_primer_to_info(pair.forward), _primer_to_info(pair.reverse)]
            insert_pos = (gibson_insert_pos
                          if gibson_insert_pos is not None
                          else resolve_gibson_insert_pos(request, vector))
            insert_pos = max(0, min(insert_pos, max(0, len(backbone) - 1)))
            pair = designer.design_gibson_primers(
                optimized_dna,
                backbone if backbone else "N" * 5000,
                insert_pos,
                homology_arm=request.homology_arm,
                primer_name=name,
                circular=_vector_is_circular(vector),
            )
            return [_primer_to_info(pair.forward), _primer_to_info(pair.reverse)]

        if request.cloning_method == CloningMethod.GOLDEN_GATE:
            oh5, oh3, _derived = derive_gg_overhangs(vector, request.enzyme)
            pair = designer.design_golden_gate_primers(
                optimized_dna,
                enzyme_name=request.enzyme,
                overhang_seq_5=oh5,
                overhang_seq_3=oh3,
                primer_name=name,
            )
            return [_primer_to_info(pair.forward), _primer_to_info(pair.reverse)]

        # restriction：双酶切（5'/3' 端可用不同酶；未提供时回落到单酶 enzyme）
        e5 = enzyme_5 or request.enzyme_5 or request.enzyme
        e3 = enzyme_3 or request.enzyme_3 or request.enzyme
        if not e5 or not e3:
            raise ValueError(
                "限制性克隆需指定限制酶：enzyme_5 与 enzyme_3（或单酶 enzyme）"
            )
        pair = designer.design_restriction_primers(
            optimized_dna, e5, e3, primer_name=name
        )
        return [_primer_to_info(pair.forward), _primer_to_info(pair.reverse)]

    if request.insert_source == "gene_synthesis":
        # 全基因合成：克隆末端（同源臂/位点+保护碱基）直接合进 oligo——
        # 合成产物即可组装/酶切，省掉「合成基因 → PCR 加末端」一轮；
        # 末端语义不同的方法（Golden Gate）保留合成后扩增加末端的旧路径
        molecule, ends_note, ends_from_construct = _synthesis_molecule(
            request, optimized_dna, vector, backbone, assembly=assembly,
            enzyme_5=enzyme_5, enzyme_3=enzyme_3,
            gibson_double_digest=gibson_double_digest, gibson_plan=gibson_plan,
            gibson_insert_pos=gibson_insert_pos,
            gibson_arms_plan=gibson_arms_plan, designer=designer)
        oligos = designer.design_synthesis_oligos(
            molecule,
            oligo_length_min=request.oligo_length_min or request.oligo_length,
            oligo_length_max=request.oligo_length_max or request.oligo_length,
            overlap_length=request.overlap_length,
            primer_name=name,
        )
        infos = [_primer_to_info(o) for o in oligos]
        _annotate_synthesis_oligo_ends(
            infos, molecule, ends_note, request,
            ends_from_construct=ends_from_construct,
            enzyme_5=enzyme_5, enzyme_3=enzyme_3, designer=designer)
        primers.extend(infos)
        if ends_note is None:
            # 组装完成后用带克隆末端的引物扩增出可克隆的插入片段
            primers.extend(_cloning_pair())
    else:
        primers = _cloning_pair()

    return primers


def _golden_gate_site_warning(sequence: str, enzyme_name: str) -> Optional[str]:
    """返回插入序列内 Type IIS 位点警告（含双链方向）。

    酶参数走 REBASE（get_enzyme），任何 Golden Gate 候选酶都能检查，
    不再只认默认菜单里的 BsaI/BsmBI/BbsI。"""
    from core.enzyme_sites import get_enzyme
    from core.primer_designer import gg_site_count

    spec = get_enzyme(enzyme_name)
    if spec is None:
        return f"未知 Golden Gate 酶 {enzyme_name}，无法检查内部识别位点"
    # 按识别序列计数：靠近插入片段末端、切点落在片段外的位点 find_enzyme_sites
    # 会丢掉，但接上引物尾后切点就在扩增子内，照样会切
    n = gg_site_count(sequence, enzyme_name)
    if not n:
        return None
    return (
        f"插入序列内部含 {n} 个 {enzyme_name} 识别位点 {spec['site']}，"
        "Golden Gate 可能切碎"
    )


def _golden_gate_junction_warning(primers: List[PrimerInfo], insert: str,
                                  enzyme_name: str) -> Optional[str]:
    """克隆引物对（最后一对带尾的 _F/_R）组成的扩增子里，拼接处额外形成的位点"""
    from core.primer_designer import gg_junction_extra_sites

    fwd = [p for p in primers if p.overhang and p.name.endswith("_F")]
    rev = [p for p in primers if p.overhang and p.name.endswith("_R")]
    if not fwd or not rev:
        return None
    extra = gg_junction_extra_sites(insert, enzyme_name, fwd[-1].overhang, rev[-1].overhang)
    if not extra:
        return None
    return (f"警告：Golden Gate 引物尾部与插入片段拼接处另形成 {extra} 个 {enzyme_name} "
            "识别位点，酶切会多切；建议更换 overhang 或酶")


def _direction_summary(ends) -> str:
    """把 resolve_restriction_ends 的自动方向判定写成一句用户能看见的话。

    判定成功时 resolve_restriction_ends 不产生任何告警——用户（尤其走
    Gibson 双酶切线性化时）看不出方向识别是否生效，这里补一条结果层说明。
    """
    promoter = ends.hint.get("promoter") if ends.hint else None
    if promoter:
        basis = (f"启动子 {promoter.get('name') or 'promoter'}"
                 f"（{promoter.get('start')}..{promoter.get('end')}，"
                 f"{'负' if str(promoter.get('strand') or '+') == '-' else '正'}链）")
    else:
        evidence = (ends.hint.get("evidence") or []) if ends.hint else []
        names = "、".join(str(e.get("name") or "") for e in evidence[:2] if e.get("name"))
        basis = f"载体表达盒证据（{names}）" if names else "载体表达盒位置"
    strand_text = "负链（-）" if ends.strand == "-" else "正链（+）"
    return (f"方向判定：基因 5′ 端接 {ends.enzyme_5}、3′ 端接 {ends.enzyme_3}，"
            f"按{strand_text}插入（依据：{basis}）")


def _assembly_feasibility_warnings(
    request, assembly, resolved_ends: Optional[RestrictionEnds],
    primers: List[PrimerInfo],
) -> List[str]:
    """双酶切组装可行性自检（限制性克隆 / Gibson 双酶切线性化共用）。

    切点几何由 plan_restriction/apply_plan 保证；这里检查的是「按返回的
    引物做酶切-连接能否真的得到这条构建体」：

    1. 组装走了 MCS 区间回退 → 构建体连接处没有位点，与引物带入的位点
       不对应，酶切-连接复现不出该构建体（此前只有笼统的回退告警）；
    2. 两把酶粘端相同（如 XhoI/SalI 同为 TCGA）或都是平末端 → 连接方向
       不唯一，载体判定的方向没有物理约束，必须测序确认；
    3. 保留位点模式下构建体必须能找到两个完整识别序列、无缝模式下必须
       找不到（位点复原自检）；引物必须带对应的位点（限制性克隆）。

    发现任何一条都写成显式告警，不静默。
    """
    from core.construct_assembly import restriction_site, sticky_overhang
    from core.seq_utils import revcomp as _rc

    warnings: List[str] = []
    e5 = (resolved_ends.enzyme_5 if resolved_ends and resolved_ends.enzyme_5
          else (request.enzyme_5 or request.enzyme or ""))
    e3 = (resolved_ends.enzyme_3 if resolved_ends and resolved_ends.enzyme_3
          else (request.enzyme_3 or request.enzyme or ""))
    if not e5 or not e3 or e5 == e3:
        return warnings

    if not getattr(assembly, "plan_used", True):
        warnings.append(
            f"警告：{e5}/{e3} 无法按双酶切建模（位点缺失或区间无法围出），"
            "构建体是按 MCS 区间替换生成的——连接处没有这两个位点，"
            "按引物引入位点酶切-连接无法复现该构建体；"
            "请改用载体序列上真实存在的酶切位点，或核对导入的载体序列")
        return warnings

    oh5, oh3 = sticky_overhang(e5), sticky_overhang(e3)
    if oh5 and oh5 == oh3:
        warnings.append(
            f"警告：{e5} 与 {e3} 的粘端相同（{oh5}），插入片段两种方向都能连接——"
            "载体表达盒判定的方向不构成物理约束，构建完成后请测序确认方向；"
            "如需方向强制，换用粘端不同的酶组合")
    elif not oh5 and not oh3:
        warnings.append(
            f"警告：{e5} 与 {e3} 均为平末端，连接无方向性且效率低——"
            "插入方向完全随机，请测序确认；建议优先选择带粘端的酶组合")

    keep_sites = (True if request.cloning_method == CloningMethod.RESTRICTION
                  else getattr(request, "keep_restriction_sites", True))
    construct = assembly.sequence or ""
    seamless = not keep_sites   # 无缝仅对 Gibson 双酶切线性化有意义；restriction 恒保留位点
    if construct:
        sites_in = [name for name, site in ((e5, restriction_site(e5)),
                                            (e3, restriction_site(e3)))
                    if site and site in construct]
        if keep_sites and not seamless and len(sites_in) < 2:
            warnings.append(
                "警告：构建体中未找到完整的 "
                + "、".join(n for n in (e5, e3) if n not in sites_in)
                + " 识别序列——位点没有正确复原，酶切-连接无法复现该构建体，"
                "请核对载体序列与酶选择")
        elif seamless and sites_in:
            warnings.append(
                f"警告：无缝连接的构建体中仍存在 {'、'.join(sites_in)} 位点序列——"
                "与无缝连接语义矛盾，请核对组装结果")

    if (request.cloning_method == CloningMethod.RESTRICTION and primers
            and keep_sites):
        site5, site3 = restriction_site(e5), restriction_site(e3)
        fwd = next((p for p in primers if p.name.endswith("_F")), None)
        rev = next((p for p in primers if p.name.endswith("_R")), None)
        if (site5 and fwd and site5 not in fwd.sequence.upper()) or \
           (site3 and rev and site3 not in rev.sequence.upper()):
            warnings.append(
                f"警告：克隆引物 5′ 端未携带完整的 {e5}/{e3} 位点——"
                "PCR 产物酶切后无法与载体两端配对连接，请检查引物")
    return warnings


def run_design(
    design_id: str,
    request: DesignRequest,
    *,
    store_result: bool = True,
) -> DesignResult:
    """执行完整设计流水线，返回 DesignResult。"""
    from core.clone_strategy import CloningMethod as CM
    from core.clone_strategy import generate_cloning_strategy
    from core.sequence_validator import SequenceValidator

    result = DesignResult(
        design_id=design_id,
        status=DesignStatus.RUNNING,
        input_sequence=request.sequence,
        vector_id=request.vector_id,
        cloning_method=request.cloning_method,
        created_at=datetime.now(),
    )

    try:
        # 1. 序列处理（含排除限制酶位点避让）
        optimized_dna, cai, gc, seq_warnings = process_sequence(
            request.sequence,
            request.sequence_type,
            request.optimize_codons,
            request.target_species,
            request.gc_min,
            request.gc_max,
            exclude_enzymes=request.exclude_enzymes,
        )
        result.optimized_sequence = optimized_dna
        result.cai = cai
        result.gc_content = gc
        result.warnings.extend(seq_warnings)

        # 1b. 固定 DNA 接头（5′ 前置 / 3′ 尾随，如 Kozak）：既定序列不参与
        # 优化，在验证与组装前拼进克隆工作序列；optimized_sequence 保持
        # 纯 CDS 优化产物（与输入氨基酸一一对应），接头只进组装侧
        optimized_dna = _apply_flanks(request, optimized_dna, result.warnings)

        # 2. 验证
        validator = SequenceValidator()
        val = validator.validate(optimized_dna, sequence_type="dna")
        result.validation_passed = val.is_valid
        result.errors.extend(val.errors)
        result.warnings.extend(val.warnings)
        if not val.is_valid:
            result.status = DesignStatus.FAILED
            result.completed_at = datetime.now()
            return result

        # 3. 载体
        # 一次性载体（设计页本地上传/在线导入）优先：它不属于载体库，
        # 序列与元件都在请求里，不落盘、也不依赖管理员导入权限
        inline = getattr(request, "vector", None)
        if inline is not None:
            from app.adhoc_vector import vector_from_inline

            vector, vec_meta = vector_from_inline(inline, max_length=MAP_MAX_LENGTH)
            result.vector_id = vector.id
            result.warnings.extend(vec_meta["warnings"])
        else:
            vector = get_vector_library().get_vector(request.vector_id)

        # 组装与方案文本共用的方法参数：切点/overhang 先算一次
        # （vector 为 None 时 derive_gg_overhangs 返回默认值）
        gibson_pos: Optional[int] = None
        gibson_arms_plan = None
        gg_oh5: Optional[str] = None
        gg_oh3: Optional[str] = None
        gg_derived = False
        # Gibson 双酶切线性化（主流实践）：载体用两把酶切开、区间被切走，
        # 组装几何与 restriction 完全一致——方向判定、位点残留起始密码子、
        # CDS 完整性逻辑全套复用；区别仅在引物（同源臂）与方案文本。
        # 位点不全时回退单位点线性化（旧模型）并明确告警
        gibson_single = False
        gibson_arms_mode = False
        gibson_same_enzyme = False
        if request.cloning_method == CloningMethod.GIBSON:
            if getattr(request, "gibson_linearization", "double_digest") == "custom_arms":
                # 自定义同源臂：臂序列即定位（plan 在 else 分支拿到 backbone
                # 后计算），不走酶切位点
                gibson_arms_mode = True
            elif getattr(request, "gibson_linearization", "double_digest") == "single_site":
                gibson_single = True
            elif ((request.enzyme_5 or request.enzyme)
                  and (request.enzyme_3 or request.enzyme)
                  and (request.enzyme_5 or request.enzyme)
                  != (request.enzyme_3 or request.enzyme)):
                gibson_single = False
            else:
                gibson_single = True
                gibson_same_enzyme = bool(
                    (request.enzyme_5 or request.enzyme)
                    and (request.enzyme_5 or request.enzyme)
                    == (request.enzyme_3 or request.enzyme))
                gibson_pos = resolve_gibson_insert_pos(request, vector)
                result.warnings.append(
                    "Gibson 双酶切线性化需要两个不同的酶切位点："
                    + (f"两把酶相同（{request.enzyme_5 or request.enzyme}）。"
                       if gibson_same_enzyme else "未提供完整位点，")
                    + "已按单位点线性化处理")
        elif request.cloning_method == CloningMethod.GOLDEN_GATE:
            gg_oh5, gg_oh3, gg_derived = derive_gg_overhangs(vector, request.enzyme)

        backbone = "N" * 5000
        resolved_ends: Optional[RestrictionEnds] = None
        if not vector:
            result.warnings.append(f"未找到载体 {request.vector_id}，使用空骨架回退")
            result.vector_name = request.vector_id
            assembly = ConstructAssembly(
                sequence=optimized_dna,
                insert_start=1,
                insert_end=len(optimized_dna),
                features=[
                    {
                        "name": request.sequence_name or "Insert",
                        "type": "CDS",
                        "start": 1,
                        "end": len(optimized_dna),
                        "strand": "+",
                        "description": "Insert only (no vector)",
                    }
                ],
            )
        else:
            result.vector_name = vector.name
            backbone = ensure_vector_backbone(vector)
            # 成品质粒名：默认为序列名；开启前缀后为「载体名-序列名」。
            # GenBank LOCUS 与下载文件名都以它为准
            _base_name = request.sequence_name or "insert"
            if request.vector_name_prefix and result.vector_name:
                result.plasmid_name = f"{result.vector_name}-{_base_name}"
            else:
                result.plasmid_name = _base_name

            # 自定义同源臂：由臂序列反推线性化边界（定位失败/歧义直接抛
            # ValueError，设计以失败态给出原因，不做静默回退）
            if gibson_arms_mode:
                from core.construct_assembly import plan_custom_arms
                # 顺序/链向无关：栏位贴反自动对调、装配链向按启动子自动判定
                gibson_arms_plan = plan_custom_arms(
                    backbone, request.homology_arm_5, request.homology_arm_3,
                    circular=str(getattr(vector, "topology", "circular")
                                 or "circular").lower() != "linear",
                    direction=getattr(request, "insert_direction", "auto"),
                    elements=_vector_element_dicts(vector))
                # 拼接顺序的「判断」写成结果层说明：两臂锚定在哪、替换了
                # 哪段、基因按哪条链装配（定位失败/歧义在上方已直接抛错）
                result.warnings.append(
                    "拼接定位："
                    + gibson_arms_plan.junction
                    + "；两臂在载体上均唯一精确匹配")
            elif gibson_same_enzyme:
                # 同酶双填回退的单点线性化：优先落在这把酶自己的切点上
                # （MCS 窗口内优先），与用户填这把酶的意图一致，而不是
                # 笼统落到 MCS 起点
                from core.construct_assembly import physical_sites
                _enz = request.enzyme_5 or request.enzyme
                _hits = physical_sites(backbone, _enz,
                                       circular=_vector_is_circular(vector))
                if _hits:
                    _lo, _hi = _mcs_window(vector) or (0, len(backbone))
                    _pick = next(
                        (s for s in _hits if _lo <= int(s["position"]) <= _hi),
                        _hits[0])
                    gibson_pos = int(_pick["cut_fwd"])

            if (request.cloning_method == CloningMethod.RESTRICTION
                    or (request.cloning_method == CloningMethod.GIBSON
                        and not gibson_single and not gibson_arms_mode)):
                # 方向由载体表达盒决定：两把酶填成什么顺序都不影响结果，
                # 只有用户显式选「正向/反向」时才覆盖（反义构建）
                resolved_ends = resolve_restriction_ends(
                    vector, backbone,
                    request.enzyme_5 or request.enzyme,
                    request.enzyme_3 or request.enzyme,
                    direction=getattr(request, "insert_direction", "auto"),
                    circular=str(getattr(vector, "topology", "circular")
                                 or "circular").lower() != "linear",
                    window=_mcs_window(vector),
                    join="gibson" if request.cloning_method == CloningMethod.GIBSON
                    else "sticky",
                )
                result.warnings.extend(resolved_ends.warnings)
                if (resolved_ends.strand and resolved_ends.source
                        in ("annotation", "auto_detected")):
                    # 自动判定成功时 ends 本身不产生告警；方向对用户不可见
                    # （尤其 Gibson 双酶切线性化），这里补一条结果层说明
                    result.warnings.append(_direction_summary(resolved_ends))

            if request.cloning_method in (CloningMethod.RESTRICTION,
                                          CloningMethod.GIBSON):
                # 起始密码子一致性（restriction 位点残留 / gibson 线性化点）。
                # 去除发生在组装与引物设计之前，两者都消费 optimized_dna
                # 「优化后序列」保持完整 CDS 语义（含 ATG，与输入蛋白一一
                # 对应，步骤 1 已赋值）；位点自带 ATG 的裁剪只发生在**克隆
                # 工作序列**里——组装与引物退火用的是 optimized_dna（裁剪后）
                result.cloned_sequence, start_codon_warnings = _resolve_redundant_start_codon(
                    request, vector, backbone, optimized_dna, resolved_ends,
                    gibson_single_site=gibson_single,
                    gibson_arms_plan=gibson_arms_plan)
                optimized_dna = result.cloned_sequence
                result.warnings.extend(start_codon_warnings)
                # 前端徽章：克隆工作序列比优化序列少 3 bp 的原因（位点 ATG 提供起始密码子）
                result.start_codon_removed = any(
                    w.startswith("已自动去除") for w in start_codon_warnings)
            else:
                # 其余方法（Golden Gate / 全基因合成）不裁剪：克隆工作序列 = 优化序列
                result.cloned_sequence = optimized_dna
            # 按克隆方法建模切点：Gibson 单位点不切除碱基、双酶切（含 Gibson
            # 双酶切线性化）与 GG 切走两个切点之间的片段
            _rp = getattr(request, "cut_pair", None)
            _pair = tuple(_rp) if _rp and request.cloning_method == CloningMethod.RESTRICTION else None
            assembly = assemble_construct(
                vector, optimized_dna, request.sequence_name or "insert",
                method=("restriction" if (request.cloning_method
                                          == CloningMethod.RESTRICTION
                                          or (request.cloning_method
                                              == CloningMethod.GIBSON
                                              and not gibson_single
                                              and not gibson_arms_mode))
                        else request.cloning_method.value),
                insert_pos=gibson_pos,
                enzyme=request.enzyme,
                enzyme_5=(resolved_ends.enzyme_5 if resolved_ends
                          else request.enzyme_5 or request.enzyme),
                enzyme_3=(resolved_ends.enzyme_3 if resolved_ends
                          else request.enzyme_3 or request.enzyme),
                overhang_5=gg_oh5,
                overhang_3=gg_oh3,
                check_cassette=not (resolved_ends and resolved_ends.source == "user"),
                extra_elements=resolved_ends.detected if resolved_ends else None,
                join=("gibson" if request.cloning_method == CloningMethod.GIBSON
                      else "sticky"),
                keep_sites=(True if request.cloning_method == CloningMethod.RESTRICTION
                            else getattr(request, "keep_restriction_sites", True)),
                custom_plan=gibson_arms_plan,
                allow_backbone_cuts=getattr(request, "allow_backbone_cuts", False),
                cut_pair=_pair,
            )
            result.warnings.extend(assembly.warnings)


        if request.cloning_method == CloningMethod.GOLDEN_GATE:
            warning = _golden_gate_site_warning(optimized_dna, request.enzyme)
            if warning:
                result.warnings.append(warning)
        elif (request.cloning_method == CloningMethod.RESTRICTION
              or (request.cloning_method == CloningMethod.GIBSON
                  and not gibson_single and not gibson_arms_mode)):
            # 插入片段内部含所选酶位点时酶切会切断片段，必须在结果层告警
            # （Gibson 双酶切线性化同样用酶切载体，同一约束）
            from core.sequence_analysis import RESTRICTION_ENZYMES
            from core.enzyme_sites import canonical_enzyme_name
            from core.seq_utils import revcomp as _seq_revcomp
            for enz in dict.fromkeys(
                e for e in (request.enzyme_5 or request.enzyme,
                            request.enzyme_3 or request.enzyme) if e
            ):
                site = (RESTRICTION_ENZYMES.get(canonical_enzyme_name(enz))
                        or (None,))[0]
                if site and (site in optimized_dna
                             or _seq_revcomp(site) in optimized_dna):
                    result.warnings.append(
                        f"警告：插入序列内部含 {enz} 识别位点 {site}，"
                        f"酶切会切断插入片段，请改用其他酶或先做定点突变去除"
                    )

        result.final_length = len(assembly.sequence)
        result.construct_sequence = assembly.sequence
        result.construct_features = assembly.features
        result.insert_start = assembly.insert_start
        result.insert_end = assembly.insert_end
        # 实际使用的克隆酶（解析后的规范拼写）：图谱强制显示并高亮这些
        # 标签——多切点克隆酶的标签曾按拥挤规则被藏掉，用户选的位点反而
        # 不显示，巧合的单切点酶看起来像克隆位点
        if resolved_ends and resolved_ends.enzyme_5:
            result.cloning_enzymes = [e for e in (resolved_ends.enzyme_5,
                                                  resolved_ends.enzyme_3) if e]
        elif request.cloning_method == CloningMethod.GOLDEN_GATE and request.enzyme:
            result.cloning_enzymes = [request.enzyme]
        elif (request.cloning_method == CloningMethod.GIBSON
              and getattr(request, "gibson_linearization", "double_digest")
              == "single_site" and getattr(request, "gibson_site", None)):
            result.cloning_enzymes = [request.gibson_site]

        # 4. 引物
        gibson_double_plan = None
        if (request.cloning_method == CloningMethod.GIBSON and not gibson_single
                and not gibson_arms_mode):
            # 与 assemble_construct 用同一套参数重算（解析后的酶指派 +
            # 相同 window/join/keep_sites），保证引物/方案文本与构建体
            # 的切点、取向完全一致——用原始酶重算会在显式方向覆盖下
            # 与实际组装各说各话
            from core.construct_assembly import plan_restriction as _plan_restriction
            gibson_double_plan = _plan_restriction(
                backbone,
                (resolved_ends.enzyme_5 if resolved_ends else "")
                or (request.enzyme_5 or request.enzyme),
                (resolved_ends.enzyme_3 if resolved_ends else "")
                or (request.enzyme_3 or request.enzyme),
                window=_mcs_window(vector),
                circular=_vector_is_circular(vector),
                warnings=[], join="gibson",
                keep_sites=getattr(request, "keep_restriction_sites", True))
        result.primers = design_primers_for_method(
            request, optimized_dna, vector, backbone, assembly=assembly,
            enzyme_5=resolved_ends.enzyme_5 if resolved_ends else None,
            enzyme_3=resolved_ends.enzyme_3 if resolved_ends else None,
            gibson_double_digest=(request.cloning_method == CloningMethod.GIBSON
                                  and not gibson_single and not gibson_arms_mode),
            gibson_plan=gibson_double_plan,
            gibson_insert_pos=gibson_pos,
            gibson_arms_plan=gibson_arms_plan,
        )

        # 4.0 组装可行性自检（限制性 / Gibson 双酶切线性化）：回退、粘端
        # 方向性、位点复原、引物位点一致性——发现「按引物做不出这条构建体」
        # 的情形就显式告警，不留到用户实验失败
        if vector and (request.cloning_method == CloningMethod.RESTRICTION
                       or (request.cloning_method == CloningMethod.GIBSON
                           and not gibson_single and not gibson_arms_mode)):
            result.warnings.extend(_assembly_feasibility_warnings(
                request, assembly, resolved_ends, result.primers))

        # 4.0 Golden Gate：按完整扩增子数位点，引物尾与插入片段拼接处新生的位点也会多切
        if request.cloning_method == CloningMethod.GOLDEN_GATE and result.primers:
            gg_warning = _golden_gate_junction_warning(result.primers, optimized_dna,
                                                       request.enzyme)
            if gg_warning:
                result.warnings.append(gg_warning)

        # 4.0 Gibson：骨架含 N 占位时同源臂不可信，明确告警
        if request.cloning_method == CloningMethod.GIBSON and "N" in backbone:
            result.warnings.append(
                "警告：载体骨架序列不完整（含 N 占位碱基），Gibson 同源臂基于占位序列生成，"
                "不可直接合成；请先提供完整载体序列"
            )

        # 4.1 全基因合成：交叉杂交审查（DNAWorks 式 3' 端匹配检查）
        if request.insert_source == "gene_synthesis" and result.primers:
            from core.primer_designer import PrimerDesigner
            xh = PrimerDesigner().cross_hybridization_count(result.primers)
            if xh:
                result.warnings.append(
                    f"警告：检测到 {xh} 处寡核苷酸间潜在交叉杂交（3' 端 12mer 匹配），"
                    "建议调整 oligo 长度范围后重新设计"
                )

        # 4.1 全基因合成：克隆末端已合进 oligo（限制性/Gibson）——说明
        # 合成产物可直接组装，省掉扩增加末端一轮；并写明两端的成分与去向，
        # 避免用户拿 oligo 去图谱搜索时对不上就以为序列是乱加的
        if (request.insert_source == "gene_synthesis"
                and request.cloning_method in (CloningMethod.RESTRICTION,
                                               CloningMethod.GIBSON)):
            # 末端是否取自构建体：直接看每条合成 oligo 能否**全序列**命中
            # 构建体（顶链或反向互补，环状允许跨原点环绕）——取到了就是
            # 「构建体现成段落」模式，有保护碱基回退的必然剥 6nt 后才命中
            from core.seq_utils import revcomp as _cons_rc
            cons_up = (result.construct_sequence or "").upper()
            cons_rc = _cons_rc(cons_up) if cons_up else ""
            syn_oligos = [p for p in result.primers
                          if p.target_start is not None]
            flanked = bool(cons_up and syn_oligos) and all(
                _oligo_hits_construct(p.full_sequence.upper(), cons_up, cons_rc)
                for p in syn_oligos)
            ends_tail = ""
            if request.cloning_method == CloningMethod.RESTRICTION:
                ends_tail = ("限制性克隆合成分子两端各 6nt 直接取自载体连接点"
                             "外侧序列，oligo 每个碱基都可在构建体图谱上核对"
                             if flanked else
                             "限制性克隆首尾 oligo 两端的保护碱基只保障酶切效率，"
                             "酶切后随小片段去除，不在构建体图谱中")
            result.warnings.append(
                "全基因合成的 oligo 已包含克隆末端（同源臂/酶切位点+载体侧翼"
                "或保护碱基）：重叠延伸拼出全长分子后即可直接 Gibson 组装或"
                "酶切连接，无需再用引物扩增补末端。"
                + (ends_tail + "；" if ends_tail else "")
                + "负链插入时 oligo 与图谱序列互为反向互补")

            # 4.1b oligo↔构建体一致性自检：每条合成 oligo 都必须能在构建体
            # 序列（顶链或其反向互补）上定位；限制性分子首尾的保护碱基酶切
            # 后去除，折算时允许剥掉两端各 6nt 再比对。定位失败说明合成产物
            # 拼出的分子与构建体不是同一个东西，必须显式报警而不是让用户对
            # 图谱时自己发现
            if result.construct_sequence and getattr(assembly, "plan_used", True):
                from core.seq_utils import revcomp as _cons_rc
                cons_up = result.construct_sequence.upper()
                cons_rc = _cons_rc(cons_up)
                unlocated: List[str] = []
                for info in result.primers:
                    if info.target_start is None:
                        continue  # 克隆引物对（如 Golden Gate 回退路径）不参与
                    s = info.full_sequence.upper()
                    cands = [s]
                    if len(s) > 12:  # 端部 oligo 剥保护碱基后再比对
                        cands += [s[6:], s[:-6], s[6:-6]]
                    if not any(_oligo_hits_construct(c, cons_up, cons_rc)
                               for c in cands):
                        unlocated.append(info.name)
                if unlocated:
                    shown = "、".join(unlocated[:5]) + ("等" if len(unlocated) > 5 else "")
                    result.warnings.append(
                        f"警告：合成 oligo {shown} 无法在构建体序列上定位"
                        "（已按酶切去除两端保护碱基折算）——合成产物与构建体"
                        "不一致，请核对设计")

        # 5. 克隆方案
        method_map = {
            CloningMethod.GIBSON: CM.GIBSON,
            CloningMethod.GOLDEN_GATE: CM.GOLDEN_GATE,
            CloningMethod.RESTRICTION: CM.RESTRICTION,
            CloningMethod.GENE_SYNTHESIS: CM.GENE_SYNTHESIS,
        }
        strategy_kwargs: Dict = {}
        if request.cloning_method == CloningMethod.GIBSON:
            strategy_kwargs = {
                # 与组装用的线性化点一致（此前会重复算一次，两边可能不一致）
                "insert_position": gibson_pos if gibson_pos is not None else 0,
                "homology_arm": request.homology_arm,
            }
            if not gibson_single:
                strategy_kwargs.update(
                    enzyme_5=(resolved_ends.enzyme_5 if resolved_ends else "")
                    or request.enzyme_5 or request.enzyme or "",
                    enzyme_3=(resolved_ends.enzyme_3 if resolved_ends else "")
                    or request.enzyme_3 or request.enzyme or "",
                    insert_position=(gibson_double_plan.left_cut
                                     if gibson_double_plan else 0),
                )
        elif request.cloning_method == CloningMethod.GOLDEN_GATE:
            overhang_5 = gg_oh5 if gg_oh5 is not None else "AATG"
            overhang_3 = gg_oh3 if gg_oh3 is not None else "GCTT"
            if not gg_derived:
                result.warnings.append(
                    "警告：无法从载体 MCS 推导 Golden Gate overhang，"
                    f"已回退默认值 {overhang_5}/{overhang_3}，请自行确认 overhang 与阅读框及载体衔接"
                )
            gg_issues = screen_gg_overhang_pair(overhang_5, overhang_3)
            if gg_issues:
                result.warnings.append(
                    f"警告：Golden Gate overhang {overhang_5}/{overhang_3} 未通过"
                    "连接保真度筛查（" + "；".join(gg_issues) + "），"
                    "该载体推不出更好的组合，已按此继续；建议改用自定义 overhang 或换载体"
                )
            strategy_kwargs = {
                "enzyme": request.enzyme,
                "overhang_5": overhang_5,
                "overhang_3": overhang_3,
            }
        elif request.cloning_method == CloningMethod.RESTRICTION:
            # 终审 A-09：传入载体元件（elements，1-indexed），切点破坏必需
            # 元件时出告警；转成 find_enzyme_sites 消费的 {name,type,start,end} 形
            strategy_kwargs = {
                "enzyme_5": request.enzyme_5 or request.enzyme,
                "enzyme_3": request.enzyme_3 or request.enzyme,
                "vector_features": (
                    [{"name": el.name, "type": el.element_type.value,
                      "start": el.start, "end": el.end}
                     for el in vector.elements] if vector else None
                ),
            }
        elif request.cloning_method == CloningMethod.GENE_SYNTHESIS:
            strategy_kwargs = {
                "oligo_length": request.oligo_length,
                "overlap_length": request.overlap_length,
            }

        strategy = generate_cloning_strategy(
            method=method_map[request.cloning_method],
            insert_seq=optimized_dna,
            insert_name=request.sequence_name,
            vector_seq=backbone if vector else "",
            vector_name=result.vector_name or request.vector_id,
            **strategy_kwargs,
        )
        # 方案里的产物长度按**实际组装结果**校正：各策略自己估的 MCS 区间可能与
        # 真实切点不一致（Gibson 不切除、双酶切切走切点之间），协议文本与图谱
        # 必须同口径
        if vector and assembly.sequence:
            strategy.expected_product_size = len(assembly.sequence)
        result.clone_protocol = strategy.to_protocol(language=request.protocol_language)

        result.status = DesignStatus.COMPLETED
        result.completed_at = datetime.now()

    except Exception as e:
        # 结果对象只装一句 str(e)，堆栈必须落日志（否则失败不可诊断）
        logger.exception("design %s failed", result.design_id)
        result.status = DesignStatus.FAILED
        result.errors.append(str(e))
        result.completed_at = datetime.now()

    return result


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def generate_genbank_from_result(result: DesignResult) -> str:
    """基于完整构建体生成 GenBank。"""
    construct = result.construct_sequence or result.optimized_sequence or ""
    features = list(result.construct_features or [])
    insert_start = result.insert_start
    insert_end = result.insert_end

    if not features and result.optimized_sequence:
        features = [
            {
                "name": "insert",
                "type": "CDS",
                "start": 1,
                "end": len(result.optimized_sequence),
                "strand": "+",
                "description": "insert",
            }
        ]
        if insert_start is None:
            insert_start, insert_end = 1, len(result.optimized_sequence)

    lines = []
    # LOCUS 名优先用质粒名（序列名或「载体名-序列名」），旧结果回退 design_id
    locus_name = (result.plasmid_name or result.design_id.replace("-", "_"))
    length = len(construct) or result.final_length or 0

    lines.append(format_locus_line(locus_name, length, topology="circular"))
    lines.append(f"DEFINITION  {result.vector_name} with designed insert")
    lines.append(f"ACCESSION   {result.design_id}")
    lines.append(f"VERSION     {result.design_id}.1")
    lines.append("SOURCE      synthetic construct")
    lines.append("  ORGANISM  synthetic construct")
    lines.append("FEATURES             Location/Qualifiers")
    lines.append(f"     source          1..{length}")
    lines.append('                     /organism="synthetic construct"')
    lines.append('                     /mol_type="other DNA"')

    for feat in features:
        ftype = feat.get("type") or "misc_feature"
        start = feat.get("start", 1)
        end = feat.get("end", start)
        strand = feat.get("strand", "+")
        loc = f"complement({start}..{end})" if strand == "-" else f"{start}..{end}"
        lines.append(format_feature_line(ftype, loc))
        name = feat.get("name") or "feature"
        lines.append(format_qualifier("label", name))
        if feat.get("description"):
            lines.append(format_qualifier("note", feat["description"]))

    # 补一条兜底 CDS 只在「构建体里根本没有插入片段注释」时才需要。判据是
    # 「有 CDS 特征覆盖插入区间」，不是起止精确相等——位点 ATG 充当起始
    # 密码子时 CDS 特征被扩展几个 bp（insert_end 仍是克隆坐标），按精确
    # 相等判会误判成「没有注释」，导出里出现两条重叠的 insert CDS。
    # 也不是名字是否叫 insert——插入特征名跟随用户序列名
    covered = any(
        str(f.get("type") or "").upper() == "CDS"
        and _int_or_none(f.get("start")) is not None
        and _int_or_none(f.get("end")) is not None
        and _int_or_none(f.get("start")) <= insert_start
        and _int_or_none(f.get("end")) >= insert_end
        for f in features
    )
    if insert_start and insert_end and not covered:
        lines.append(f"     CDS             {insert_start}..{insert_end}")
        lines.append('                     /label="insert"')

    lines.append("ORIGIN")
    lines.extend(render_origin_block(construct))
    lines.append("//")
    return "\n".join(lines)
