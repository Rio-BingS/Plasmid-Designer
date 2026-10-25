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
    join: str = "sticky",
) -> RestrictionEnds:
    """把「两把酶」解析成「接基因 5′ 端 / 3′ 端」的指派

    方向按载体表达盒判定（cassette_hint）：启动子紧邻哪一端、RBS/终止子在
    哪一端。direction 取 auto / forward / reverse：auto 听载体的，forward
    与 reverse 是用户显式指定（反义构建），此时覆盖载体的判定。
    """
    from core.construct_assembly import cassette_hint, plan_restriction

    ends = RestrictionEnds(enzyme_5=enzyme_a or "", enzyme_3=enzyme_b or "")
    if not enzyme_a or not enzyme_b or enzyme_a == enzyme_b:
        return ends
    probe = plan_restriction(backbone, enzyme_a, enzyme_b, window=window,
                             circular=circular, warnings=[], join=join)
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
    ends.strand = desired
    ends.hint = hint
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
    """限制性克隆的实时预览：把两把酶解析成**成品构建体**的布局

    做法是用占位基因跑一遍真实组装（assemble_construct），所以返回的坐标、
    元件位置、连接处碱基与正式设计完全一致——示意图画的就是成品，被切走的
    载体片段不在图上（真实质粒上它也不存在）。
    """
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
    if len(enzymes) < 2 or enzymes[0] == enzymes[1]:
        raise ValueError("预览需要两个不同的限制酶")
    backbone = ensure_vector_backbone(vector)
    circular = str(getattr(vector, "topology", "circular") or "circular").lower() != "linear"
    ends = resolve_restriction_ends(
        vector, backbone, enzymes[0], enzymes[1],
        direction=getattr(request, "direction", "auto"),
        circular=circular, window=_mcs_window(vector))
    warnings.extend(ends.warnings)

    gene_len = int(getattr(request, "insert_length", 0) or 600)
    estimated = not getattr(request, "insert_length", None)
    asm = assemble_construct(
        vector, "N" * gene_len, "insert", method="restriction",
        enzyme_5=ends.enzyme_5, enzyme_3=ends.enzyme_3, check_cassette=False,
        extra_elements=ends.detected)
    warnings.extend(asm.warnings)
    construct = asm.sequence

    # 切除长度取规划值：构建体两端还补了位点残留（head/tail），
    # 用长度差反推会把补回的碱基算进切除量
    removed = max(0, ends.removed_bp)
    sites: List[Dict] = []
    for role, enzyme, boundary in (
        ("five", ends.enzyme_5, asm.insert_start),
        ("three", ends.enzyme_3, asm.insert_end),
    ):
        hits = physical_sites(construct, enzyme, circular=circular) if enzyme else []
        if not hits:
            continue
        best = min(hits, key=lambda hit: abs(int(hit["cut_fwd"]) - boundary))
        sites.append({
            "role": role,
            "enzyme": enzyme,
            "position": int(best["position"]),
            "cut": int(best["cut_fwd"]),
            "end": int(best["position"]) + len(best.get("recognition") or "") - 1,
        })

    lo = asm.insert_start - 3000
    hi = asm.insert_end + 3000
    features = []
    for feat in asm.features:
        start = int(feat.get("start") or 0)
        end = int(feat.get("end") or 0)
        if start == asm.insert_start and str(feat.get("type") or "").upper() == "CDS":
            continue  # 占位基因本身不是元件
        if end < lo or start > hi:
            continue
        features.append({
            "name": feat.get("name") or "feature",
            "type": feat.get("type") or "other",
            "start": start, "end": end,
            "strand": feat.get("strand") or "+",
        })
    features.sort(key=lambda item: item["start"])
    features = features[:16]

    five_at = max(0, asm.insert_start - 7)
    three_at = max(0, asm.insert_end - 6)
    return {
        "vector": {"name": vector.name or "vector", "length": len(backbone),
                   "topology": "linear" if not circular else "circular"},
        "sites": sites,
        "gene": {"start": asm.insert_start, "end": asm.insert_end,
                 "length": gene_len, "estimated": estimated},
        "removed_bp": removed,
        "construct_length": len(construct),
        "strand": ends.strand,
        "source": ends.source,
        "ambiguous": bool(ends.hint.get("ambiguous")),
        "gene_5prime_enzyme": ends.enzyme_5,
        "gene_3prime_enzyme": ends.enzyme_3,
        "evidence": ends.hint.get("evidence") or [],
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

    return ConstructAssembly(
        sequence=full,
        insert_start=insert_start,
        insert_end=insert_end,
        features=features,
        warnings=warnings,
    )


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
) -> ConstructAssembly:
    """按克隆方法把插入片段拼进载体，生成完整构建体序列与注释。

    method 取 gibson / restriction / golden_gate 时按**真实切点**建模
    （见 core/construct_assembly.py）：

    * Gibson 只在一位点线性化，不切除载体碱基；
    * 双酶切切走两个切点之间的片段，基因两端补回位点残留碱基；
    * Golden Gate 切走两个 Type IIS 切点之间的片段，两端补 4 nt overhang。

    取 mcs（或缺省）时沿用旧的「MCS 区间整段替换」——只在按切点建模做不了时
    作回退，且由 warnings 说明原因，不静默退回。
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
    if method in ("gibson", "restriction", "golden_gate") and backbone:
        circular = str(getattr(vector, "topology", "circular") or "circular").lower() != "linear"
        window = (anchor, anchor) if anchor is not None else _mcs_window(vector)
        plan_warnings: List[str] = []   # 单独收集，避免与 apply_plan 的返回重复
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
                plan = plan_restriction(backbone, enzyme_5 or "", enzyme_3 or "",
                                        window=window, circular=circular,
                                        warnings=plan_warnings, join=join,
                                        keep_sites=keep_sites)
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
        # Gibson 方向自检：基因取向由臂/同源臂设计决定，载体表达盒判出的
        # 方向与之相反时，组装出的基因是反义的——与 restriction 的「两端酶
        # 填反」同性质，必须在结果层说明而非默默给出反义构建体
        right_hint = (plan.right_cut if plan.right_cut > plan.left_cut
                      else plan.left_cut + 1)
        hint = cassette_hint(
            _vector_element_dicts(vector) + list(extra_elements or []),
            plan.left_cut, right_hint)
        if hint.get("strand") == "-":
            advice = ("请交换两条重组臂的来源位置（取载体另一侧的同源区）重新填写"
                      if custom_plan is not None else
                      "请确认引物同源臂取向与此一致，或改用双酶切克隆（自动判向）")
            plan.warnings.append(
                "Gibson 插入片段按正链组装，与载体表达盒方向（负链）相反："
                f"转录产物为反义 RNA。{advice}")

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
    if plan.method == "restriction" and not plan.head and not plan.tail:
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
            request, backbone, gibson_arms_plan, optimized_dna)
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
    request, backbone: str, plan, optimized_dna: str,
) -> Tuple[str, List[str]]:
    """自定义同源臂：起始密码子按基因 5′ 连接点的载体碱基判定。

    正链装配看左连接点前 3bp，负链装配（臂角色互换）看右连接点后 3bp 的
    反向互补；去除的是插入序列自己开头的 ATG（基因读法），蛋白仍与输入
    氨基酸一致。融合克隆检测第一版仅覆盖正链装配（负链臂落进 minus CDS
    的场景挂账）。
    """
    if not backbone or not optimized_dna:
        return optimized_dna, []
    if not str(optimized_dna).upper().startswith("ATG"):
        return optimized_dna, []
    if plan.insert_reverse:
        from core.seq_utils import revcomp as _rc
        if plan.right_cut + 3 > len(backbone):
            return optimized_dna, []
        leading_ok = _rc(backbone[plan.right_cut:plan.right_cut + 3]) == "ATG"
    else:
        if plan.left_cut < 3:
            return optimized_dna, []
        leading_ok = backbone[plan.left_cut - 3:plan.left_cut].upper() == "ATG"
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
    if pos < 3 or pos >= len(backbone):
        return optimized_dna, []
    elements = _vector_element_dicts(vector)
    fusion = gibson_junction_fusion(elements, pos)
    if fusion is not None:
        # 融合克隆：读码框与内部 Met 告警与插入序列首密码子无关
        name = fusion.get("name") or fusion.get("type") or "CDS"
        cnt = pos - int(fusion["start"]) + 1   # 融合元件在插入点前的碱基数
        if cnt % 3 == 0:
            return optimized_dna, [
                "Gibson 融合克隆提示："
                f"线性化点紧邻 {name}（CDS）末端且同框，插入序列开头的 ATG 会作为"
                "内部甲硫氨酸进入融合蛋白；如需纯融合请去掉序列开头的 ATG，"
                "保留与否请按实验意图确认"
            ]
        return optimized_dna, [
            "Gibson 融合克隆读码框不匹配："
            f"线性化点位于 {name}（CDS）内，插入点前该 CDS 已有 {cnt} bp"
            f"（非 3 的倍数），直接拼接会使下游读码框移位 {3 - cnt % 3} bp；"
            "请调整线性化位置或同源臂设计"
        ]

    if not str(optimized_dna).upper().startswith("ATG"):
        return optimized_dna, []
    if backbone[pos - 3:pos].upper() != "ATG":
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

    # 酶名合法性检查，取识别序列作为避让 motif（仅适用时）
    motifs: List[str] = []
    unknown: List[str] = []
    for enzyme in exclude_enzymes:
        entry = RESTRICTION_ENZYMES.get(enzyme)
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


def design_primers_for_method(
    request: DesignRequest,
    optimized_dna: str,
    vector,
    backbone: str,
    *,
    enzyme_5: Optional[str] = None,
    enzyme_3: Optional[str] = None,
    gibson_double_digest: bool = False,
    gibson_plan=None,
) -> List[PrimerInfo]:
    """按「插入片段来源 × 克隆方法」设计引物/寡核苷酸。

    enzyme_5 / enzyme_3 是**已解析**的指派（接基因 5′/3′ 端的酶）：由
    resolve_restriction_ends 按载体表达盒定下来，这里不再读 request 里的原值，
    否则引物和构建体会各说各话。

    - 来源=PCR：产出带克隆末端的扩增引物对（末端形态由克隆方法决定）
    - 来源=全基因合成：产出重叠组装 oligo 组 + 带克隆末端的
      组装后扩增引物对（用于拼装完成后的扩增加克隆末端）
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
                # 自定义同源臂：插入分子 = 臂5 + 基因 + 臂3（臂由用户提供，
                # 与载体连接点同源；构建体里臂就是载体自身的碱基）
                pair = designer.design_custom_arm_primers(
                    optimized_dna, request.homology_arm_5,
                    request.homology_arm_3, primer_name=name)
                return [_primer_to_info(pair.forward), _primer_to_info(pair.reverse)]
            if gibson_double_digest and gibson_plan is not None:
                # 双酶切线性化：插入分子 = 位点残留 + 基因 + 位点残留，
                # 同源臂覆盖线性化载体末端（含各自的位点前缀）
                fragment = gibson_plan.head + optimized_dna + gibson_plan.tail
                pair = designer.design_gibson_primers(
                    fragment,
                    backbone if backbone else "N" * 5000,
                    gibson_plan.left_cut,
                    homology_arm=request.homology_arm,
                    primer_name=name,
                )
                return [_primer_to_info(pair.forward), _primer_to_info(pair.reverse)]
            insert_pos = resolve_gibson_insert_pos(request, vector)
            insert_pos = max(0, min(insert_pos, max(0, len(backbone) - 1)))
            pair = designer.design_gibson_primers(
                optimized_dna,
                backbone if backbone else "N" * 5000,
                insert_pos,
                homology_arm=request.homology_arm,
                primer_name=name,
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
        # 全基因合成：成对正反链重叠 oligo 覆盖完整插入片段（双链完全覆盖）
        oligos = designer.design_synthesis_oligos(
            optimized_dna,
            oligo_length_min=request.oligo_length_min or request.oligo_length,
            oligo_length_max=request.oligo_length_max or request.oligo_length,
            overlap_length=request.overlap_length,
            primer_name=name,
        )
        primers.extend(_primer_to_info(o) for o in oligos)
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
        gg_oh5: Optional[str] = None
        gg_oh3: Optional[str] = None
        gg_derived = False
        # Gibson 双酶切线性化（主流实践）：载体用两把酶切开、区间被切走，
        # 组装几何与 restriction 完全一致——方向判定、位点残留起始密码子、
        # CDS 完整性逻辑全套复用；区别仅在引物（同源臂）与方案文本。
        # 位点不全时回退单位点线性化（旧模型）并明确告警
        gibson_single = False
        gibson_arms_mode = False
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
                gibson_pos = resolve_gibson_insert_pos(request, vector)
                result.warnings.append(
                    "Gibson 双酶切线性化需要两个不同的酶切位点：未提供完整位点，"
                    "已按单位点线性化处理")
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
            gibson_arms_plan = None
            if gibson_arms_mode:
                from core.construct_assembly import plan_custom_arms
                gibson_arms_plan = plan_custom_arms(
                    backbone, request.homology_arm_5, request.homology_arm_3,
                    circular=str(getattr(vector, "topology", "circular")
                                 or "circular").lower() != "linear")

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

            if request.cloning_method in (CloningMethod.RESTRICTION,
                                          CloningMethod.GIBSON):
                # 起始密码子一致性（restriction 位点残留 / gibson 线性化点）。
                # 去除发生在组装与引物设计之前，两者都消费 optimized_dna
                optimized_dna, start_codon_warnings = _resolve_redundant_start_codon(
                    request, vector, backbone, optimized_dna, resolved_ends,
                    gibson_single_site=gibson_single,
                    gibson_arms_plan=gibson_arms_plan)
                result.warnings.extend(start_codon_warnings)
                result.optimized_sequence = optimized_dna
            # 按克隆方法建模切点：Gibson 单位点不切除碱基、双酶切（含 Gibson
            # 双酶切线性化）与 GG 切走两个切点之间的片段
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
            from core.seq_utils import revcomp as _seq_revcomp
            for enz in dict.fromkeys(
                e for e in (request.enzyme_5 or request.enzyme,
                            request.enzyme_3 or request.enzyme) if e
            ):
                site = (RESTRICTION_ENZYMES.get(enz) or (None,))[0]
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

        # 4. 引物
        gibson_double_plan = None
        if (request.cloning_method == CloningMethod.GIBSON and not gibson_single
                and not gibson_arms_mode):
            from core.construct_assembly import plan_restriction as _plan_restriction
            gibson_double_plan = _plan_restriction(
                backbone, request.enzyme_5 or request.enzyme,
                request.enzyme_3 or request.enzyme,
                window=_mcs_window(vector),
                circular=str(getattr(vector, "topology", "circular")
                             or "circular").lower() != "linear",
                warnings=[], join="gibson",
                keep_sites=getattr(request, "keep_restriction_sites", True))
        result.primers = design_primers_for_method(
            request, optimized_dna, vector, backbone,
            enzyme_5=resolved_ends.enzyme_5 if resolved_ends else None,
            enzyme_3=resolved_ends.enzyme_3 if resolved_ends else None,
            gibson_double_digest=(request.cloning_method == CloningMethod.GIBSON
                                  and not gibson_single and not gibson_arms_mode),
            gibson_plan=gibson_double_plan,
        )

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
                    enzyme_5=request.enzyme_5 or request.enzyme or "",
                    enzyme_3=request.enzyme_3 or request.enzyme or "",
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
    # 坐标是否已经覆盖插入区间，不是名字是否叫 insert——用户给序列起了名
    # （如 A01）时会走到这里，再补一条**没有 complement 的正链 CDS**，
    # 负链插入的构建体一导入 SnapGene 就会看到两条方向相反的 CDS/箭头。
    covered = any(
        int(f.get("start") or -1) == insert_start
        and int(f.get("end") or -1) == insert_end
        for f in features
    )
    if insert_start and insert_end and not covered:
        lines.append(f"     CDS             {insert_start}..{insert_end}")
        lines.append('                     /label="insert"')

    lines.append("ORIGIN")
    lines.extend(render_origin_block(construct))
    lines.append("//")
    return "\n".join(lines)
