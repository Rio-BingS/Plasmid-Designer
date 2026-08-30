"""统一设计服务：单任务与批量共用同一流水线。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.cache import cache
from app.config import settings
from app.routes.models import (
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


_vector_library_cache = None


def get_vector_library():
    """进程级载体库缓存。"""
    global _vector_library_cache
    if _vector_library_cache is None:
        from core.vector_library import VectorLibrary

        lib = VectorLibrary()
        lib.load_from_directory(settings.VECTORS_DIR)
        _vector_library_cache = lib
    return _vector_library_cache


def invalidate_vector_library_cache() -> None:
    global _vector_library_cache
    _vector_library_cache = None


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


def assemble_construct(vector, insert_dna: str, insert_name: str = "insert") -> ConstructAssembly:
    """在 MCS 处插入片段，生成环状完整构建体序列与注释。"""
    warnings: List[str] = []
    backbone = ensure_vector_backbone(vector)
    insert = insert_dna.upper().replace(" ", "").replace("\n", "")

    if vector.mcs and vector.mcs.start > 0:
        # YAML 坐标为 1-based inclusive
        cut_start = max(0, vector.mcs.start - 1)
        cut_end = min(len(backbone), vector.mcs.end)
    else:
        cut_start = len(backbone) // 2
        cut_end = cut_start
        warnings.append("载体缺少 MCS 定义，已在骨架中部插入")

    if not backbone or set(backbone) == {"N"}:
        warnings.append("载体骨架序列不完整，使用占位骨架组装")

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


def derive_gg_overhangs(vector, enzyme_name: str) -> Tuple[str, str]:
    """从载体 MCS 推导 Golden Gate 4bp overhang，无法推导时回退默认值。"""
    if vector and vector.mcs and vector.mcs.sites:
        unique = [s for s in vector.mcs.sites if s.is_unique and s.overhang]
        if len(unique) >= 2:
            return (unique[0].overhang[:4].upper().ljust(4, "A")[:4],
                    unique[-1].overhang[:4].upper().ljust(4, "A")[:4])
        if len(unique) == 1:
            oh = unique[0].overhang[:4].upper().ljust(4, "A")[:4]
            return oh, _DEFAULT_GG_OVERHANGS[1]
    return _DEFAULT_GG_OVERHANGS


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
        notes=notes or None,
    )


def process_sequence(
    sequence: str,
    sequence_type: SequenceType,
    optimize_codons: bool,
    target_species: str,
    gc_min: float,
    gc_max: float,
) -> Tuple[str, Optional[float], Optional[float], List[str]]:
    """处理输入序列 → DNA；返回 (dna, cai, gc, warnings)。"""
    from core.codon_optimizer import CodonOptimizer

    seq = sequence.upper().replace("\n", "").replace(" ", "")
    warnings: List[str] = []

    if sequence_type == SequenceType.DNA:
        return seq, None, None, warnings

    optimizer = CodonOptimizer(species=target_species)
    if optimize_codons:
        # 密码子优化结果缓存（24h TTL）：同 (序列, 物种, GC 区间) 直接复用
        try:
            cached_opt = cache.get_codon_optimization(
                sequence=seq, species=target_species, gc_min=gc_min, gc_max=gc_max
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

        result = optimizer.optimize(seq, gc_target=(gc_min / 100, gc_max / 100))
        try:
            cache.cache_codon_optimization(
                sequence=seq,
                species=target_species,
                gc_min=gc_min,
                gc_max=gc_max,
                result={
                    "dna_sequence": result.dna_sequence,
                    "cai": result.cai,
                    "gc_content": result.gc_content * 100,
                    "warnings": list(result.warnings),
                },
            )
        except Exception:
            pass
        return result.dna_sequence, result.cai, result.gc_content * 100, list(result.warnings)

    # 不优化：按物种频率忠实反翻译（最高频密码子，不做 GC 迭代）
    dna = optimizer.back_translate(seq)
    cai = optimizer._calculate_cai(dna, seq)
    gc = optimizer._calculate_gc_content(dna) * 100
    warnings.append("未启用密码子优化，使用物种最优密码子反翻译")
    return dna, cai, gc, warnings


def design_primers_for_method(
    request: DesignRequest,
    optimized_dna: str,
    vector,
    backbone: str,
) -> List[PrimerInfo]:
    """按「插入片段来源 × 克隆方法」设计引物/寡核苷酸。

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
            insert_pos = vector.mcs.start - 1 if vector and vector.mcs and vector.mcs.start else 100
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
            oh5, oh3 = derive_gg_overhangs(vector, request.enzyme)
            pair = designer.design_golden_gate_primers(
                optimized_dna,
                enzyme_name=request.enzyme,
                overhang_seq_5=oh5,
                overhang_seq_3=oh3,
                primer_name=name,
            )
            return [_primer_to_info(pair.forward), _primer_to_info(pair.reverse)]

        # restriction：双酶切（5'/3' 端可用不同酶；未提供时回落到单酶 enzyme）
        enzyme_5 = request.enzyme_5 or request.enzyme
        enzyme_3 = request.enzyme_3 or request.enzyme
        pair = designer.design_restriction_primers(
            optimized_dna, enzyme_5, enzyme_3, primer_name=name
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
    """返回插入序列内 Type IIS 位点警告（含双链方向）。"""
    from core.sequence_analysis import RESTRICTION_ENZYMES, RestrictionSiteAnalyzer

    if enzyme_name not in RESTRICTION_ENZYMES:
        return f"未知 Golden Gate 酶 {enzyme_name}，无法检查内部识别位点"
    sites = RestrictionSiteAnalyzer().find_sites(sequence, [enzyme_name])
    if not sites:
        return None
    recognition = RESTRICTION_ENZYMES[enzyme_name][0]
    return (
        f"插入序列内部含 {len(sites)} 个 {enzyme_name} 识别位点 {recognition}，"
        "Golden Gate 可能切碎"
    )


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
        # 1. 序列处理
        optimized_dna, cai, gc, seq_warnings = process_sequence(
            request.sequence,
            request.sequence_type,
            request.optimize_codons,
            request.target_species,
            request.gc_min,
            request.gc_max,
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
        library = get_vector_library()
        vector = library.get_vector(request.vector_id)
        backbone = "N" * 5000
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
            assembly = assemble_construct(vector, optimized_dna, request.sequence_name or "insert")
            result.warnings.extend(assembly.warnings)

        if request.cloning_method == CloningMethod.GOLDEN_GATE:
            warning = _golden_gate_site_warning(optimized_dna, request.enzyme)
            if warning:
                result.warnings.append(warning)

        result.final_length = len(assembly.sequence)
        result.construct_sequence = assembly.sequence
        result.construct_features = assembly.features
        result.insert_start = assembly.insert_start
        result.insert_end = assembly.insert_end

        # 4. 引物
        result.primers = design_primers_for_method(request, optimized_dna, vector, backbone)

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
                "insert_position": vector.mcs.start - 1 if vector and vector.mcs else 0,
                "homology_arm": request.homology_arm,
            }
        elif request.cloning_method == CloningMethod.GOLDEN_GATE:
            overhang_5, overhang_3 = derive_gg_overhangs(vector, request.enzyme)
            strategy_kwargs = {
                "enzyme": request.enzyme,
                "overhang_5": overhang_5,
                "overhang_3": overhang_3,
            }
        elif request.cloning_method == CloningMethod.RESTRICTION:
            strategy_kwargs = {
                "enzyme_5": request.enzyme_5 or request.enzyme,
                "enzyme_3": request.enzyme_3 or request.enzyme,
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
        result.clone_protocol = strategy.to_protocol(language=request.protocol_language)

        result.status = DesignStatus.COMPLETED
        result.completed_at = datetime.now()

    except Exception as e:
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
    locus_name = result.design_id[:16].replace("-", "_")
    length = len(construct) or result.final_length or 0
    date_str = datetime.now().strftime("%d-%b-%Y").upper()

    lines.append(f"LOCUS       {locus_name:<16} {length} bp    DNA     circular SYN {date_str}")
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
        ftype = (feat.get("type") or "misc_feature")[:15]
        start = feat.get("start", 1)
        end = feat.get("end", start)
        strand = feat.get("strand", "+")
        loc = f"complement({start}..{end})" if strand == "-" else f"{start}..{end}"
        lines.append(f"     {ftype:<15} {loc}")
        name = feat.get("name") or "feature"
        lines.append(f'                     /label="{name}"')
        if feat.get("description"):
            lines.append(f'                     /note="{feat["description"]}"')

    if insert_start and insert_end and not any(f.get("name") == "insert" for f in features):
        lines.append(f"     CDS             {insert_start}..{insert_end}")
        lines.append('                     /label="insert"')

    lines.append("ORIGIN")
    seq = construct.upper()
    for i in range(0, len(seq), 60):
        chunk = seq[i : i + 60]
        groups = " ".join(chunk[j : j + 10] for j in range(0, len(chunk), 10))
        lines.append(f"{i + 1:>9} {groups}")
    lines.append("//")
    return "\n".join(lines)


def map_data_from_result(result: DesignResult) -> Dict:
    construct = result.construct_sequence or result.optimized_sequence or ""
    features = list(result.construct_features or [])
    if not features:
        features = [
            {
                "name": "Insert",
                "type": "CDS",
                "start": 1,
                "end": len(result.optimized_sequence or ""),
                "strand": "+",
                "description": "Optimized insert",
            }
        ]
    return {
        "name": result.vector_name or "Construct",
        "length": len(construct) or result.final_length or 0,
        "sequence": construct,
        "features": features,
    }
