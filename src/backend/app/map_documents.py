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


def _locate_primer_spans(result, seq: str, circular: bool) -> List[Dict]:
    """把引物/合成 oligo 定位到构建体序列上 → 图谱引物轨道。

    合成 oligo 按**全序列**定位（负链装配时 oligo 落在互补链上，图谱按
    负链画出——用户才能直接看到「首位引物对上了」，不用自己手工去序列
    里搜反向互补）；PCR 引物带保护碱基/酶切位点，全序列不在构建体上，
    按 3′ 端退火区（末 20nt）定位。跨原点的命中与定位不到的跳过。
    """
    from core.seq_utils import revcomp

    primers = getattr(result, "primers", None) or []
    if not primers or not seq:
        return []
    length = len(seq)
    seq_rc = revcomp(seq)
    out: List[Dict] = []
    for p in primers:
        s = str(getattr(p, "full_sequence", None) or getattr(p, "sequence", "")
                or "").upper().replace(" ", "")
        if not s or len(s) > length:
            continue
        hit = _find_span(seq, s)
        strand = 1
        if hit is None:
            hit = _find_span(seq_rc, s)
            strand = -1
        anneal = ""
        if hit is None and len(s) > 20:
            # PCR 引物：3′ 端退火区才结合模板，末 20nt 定位。退火序列落在
            # 哪条链，引物延伸方向就在哪条链（F 引物=基因正义链片段，负链
            # 装配时命中互补链；R 引物=模板链片段，命中正链）——按命中链
            # 自然标注即箭头方向，无需按名字翻转
            anneal = s[-20:]
            hit = _find_span(seq, anneal)
            strand = 1
            if hit is None:
                hit = _find_span(seq_rc, anneal)
                strand = -1
        if hit is None:
            continue
        i, span_len = hit
        if strand == 1:
            start, end = i + 1, i + span_len
        else:
            start, end = length - (i + span_len) + 1, length - i
        if start > end and not circular:
            continue  # 跨原点命中：环上允许 start>end，线性图跳过
        out.append({
            "id": f"primer-{getattr(p, 'name', '') or len(out)}",
            "name": getattr(p, "name", "") or f"primer {len(out) + 1}",
            "start": start, "end": end, "strand": strand,
            "sequence": anneal or s,
        })
        if len(out) >= 5000:
            break
    return out


def _find_span(text: str, pattern: str) -> Optional[tuple]:
    """首个非跨原点命中 → (0-based 起点, 长度)；找不到返回 None"""
    i = text.find(pattern)
    if i == -1 or i + len(pattern) > len(text):
        return None
    return i, len(pattern)


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
        # 图谱名与结果页/GenBank 同名：plasmid_name（序列名或「载体名-序列名」）。
        # 此前硬编码「载体名 + insert」，用户开了载体名前缀后图谱仍是旧名
        name=(result.plasmid_name
              or (result.vector_name and f"{result.vector_name} + insert")
              or "Construct"),
        sequence=seq, topology="circular" if circular else "linear",
        source_format="design", features=feats, warnings=[],
        # 引物轨道：合成 oligo / PCR 引物按实际结合位置与链向画在图谱上
        primers=_locate_primer_spans(result, seq, circular),
        annotate=False, enzyme_set=enzyme_set, orf_min_aa=orf_min_aa,
    )


def _vector_features(vector) -> List[Dict]:
    feats = [_feature({
        "name": e.name, "type": e.element_type.value, "start": e.start, "end": e.end,
        "strand": e.strand, "description": e.description,
    }, "vector") for e in vector.elements]
    if not feats and vector.mcs and vector.mcs.start > 0:
        feats.append(_feature({"name": vector.mcs.name or "MCS", "type": "multiple_cloning_site",
                               "start": vector.mcs.start, "end": vector.mcs.end,
                               "strand": "+", "description": "Multiple Cloning Site"}, "vector"))
    return feats


def _vector_topology(vector) -> str:
    """载体拓扑（linear/circular）：导入的线性片段不能被当环状画（默认环状）"""
    return "linear" if str(getattr(vector, "topology", "") or "").lower() == "linear" else "circular"


def vector_map_preview(vector) -> Optional[Dict]:
    """载体列表缩略图用的轻量图谱（GET /api/vectors 的 map_preview）：
    与 /api/vectors/{id}/map 同一套类型归一，但不带序列、酶切与 ORF。
    无序列的载体返回 None；越界特征丢弃（环状允许 start > end 跨原点）。"""
    length = len(vector.sequence or "")
    if length <= 0:
        return None
    feats = []
    for f in _vector_features(vector):
        if not (1 <= f["start"] <= length and 1 <= f["end"] <= length):
            continue
        feats.append({k: f[k] for k in ("name", "type", "start", "end", "strand")})
    return {"length": length, "topology": _vector_topology(vector), "features": feats}


def vector_map_document(vector, enzyme_set: str = "common", orf_min_aa: int = 100) -> Dict:
    seq = clean_sequence(vector.sequence)
    feats = _vector_features(vector)
    return build_document(
        name=vector.name, sequence=seq, topology=_vector_topology(vector), source_format="vector",
        features=feats, warnings=[], annotate=None if not feats else False,
        enzyme_set=enzyme_set, orf_min_aa=orf_min_aa,
    )
