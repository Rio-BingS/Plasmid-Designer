"""设计结果 / 载体库 → MapDocument（GET /api/design/{id}/map、/api/vectors/{id}/map）

两处端点此前各自拼 PlasmidMapData（字符串链方向、无 ORF、回文位点正反
各一条），现统一走 core.plasmid_map.build_document：设计插入片段
source=design，载体元件 source=vector，类型归一到契约 FeatureType。
"""

from typing import Dict, List, Optional

from core.plasmid_map.document import build_document
from core.plasmid_map.parsers import clean_sequence
from core.plasmid_map.types import normalize_type


def _strand(v) -> int:
    if v in (-1, "-", "-1"):
        return -1
    if v in (1, "+", "1"):
        return 1
    return 0


def _feature(raw: Dict, source: str, ftype: Optional[str] = None) -> Dict:
    name = str(raw.get("name") or "feature")
    desc = str(raw.get("description") or "")
    f = {
        "name": name,
        "type": ftype or normalize_type(raw.get("type"), name, None, desc),
        "start": int(raw.get("start") or 1),
        "end": int(raw.get("end") or 1),
        "strand": _strand(raw.get("strand")),
        "source": source,
    }
    note = desc.strip()
    if note and not note.lower().startswith("color:"):
        f["notes"] = note[:300]
    if raw.get("type"):
        f["qualifiers"] = {"element_type": str(raw["type"])}
    return f


def design_map_document(result, enzyme_set: str = "common", orf_min_aa: int = 100) -> Dict:
    construct = result.construct_sequence or result.optimized_sequence or ""
    circular = bool(result.construct_sequence)
    seq = clean_sequence(construct)
    feats: List[Dict] = []
    raw_feats = list(result.construct_features or [])
    ins_s, ins_e = result.insert_start, result.insert_end
    if not raw_feats:
        ins_s, ins_e = 1, len(result.optimized_sequence or seq)
        raw_feats = [{"name": "Insert", "type": "CDS", "start": ins_s, "end": ins_e,
                      "strand": "+", "description": "Optimized insert"}]
    design_marked = False
    for raw in raw_feats:
        is_insert = (not design_marked and ins_s is not None
                     and raw.get("start") == ins_s and raw.get("end") == ins_e)
        if is_insert:
            design_marked = True
            # 插入片段是密码子优化后的编码序列：固定为 cds，不按名称猜测
            feats.append(_feature(raw, "design", "cds"))
        else:
            feats.append(_feature(raw, "vector"))
    return build_document(
        name=result.vector_name and f"{result.vector_name} + insert" or "Construct",
        sequence=seq, topology="circular" if circular else "linear",
        source_format="design", features=feats, warnings=[],
        annotate=False, enzyme_set=enzyme_set, orf_min_aa=orf_min_aa,
    )


def vector_map_document(vector, enzyme_set: str = "common", orf_min_aa: int = 100) -> Dict:
    seq = clean_sequence(vector.sequence)
    feats = [_feature({
        "name": e.name, "type": e.element_type.value, "start": e.start, "end": e.end,
        "strand": e.strand, "description": e.description,
    }, "vector") for e in vector.elements]
    if not feats and vector.mcs and vector.mcs.start > 0:
        feats.append(_feature({"name": vector.mcs.name or "MCS", "type": "multiple_cloning_site",
                               "start": vector.mcs.start, "end": vector.mcs.end,
                               "strand": "+", "description": "Multiple Cloning Site"}, "vector"))
    return build_document(
        name=vector.name, sequence=seq, topology="circular", source_format="vector",
        features=feats, warnings=[], annotate=None if not feats else False,
        enzyme_set=enzyme_set, orf_min_aa=orf_min_aa,
    )
