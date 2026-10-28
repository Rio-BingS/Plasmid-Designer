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
    按 3′ 端退火区（末 20nt）定位。环状构建体允许跨原点命中（切除区间
    跨原点被旋转后，跨连接点的 oligo 必然绕过原点——start>end 的环绕
    表示，前端 unroll 同特征一样展开）；线性图上跨原点的跳过。
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
        hit = _find_span(seq, s, circular)
        strand = 1
        if hit is None:
            hit = _find_span(seq_rc, s, circular)
            strand = -1
        anneal = ""
        if hit is None and len(s) > 20:
            # PCR 引物：3′ 端退火区才结合模板，末 20nt 定位。退火序列落在
            # 哪条链，引物延伸方向就在哪条链（F 引物=基因正义链片段，负链
            # 装配时命中互补链；R 引物=模板链片段，命中正链）——按命中链
            # 自然标注即箭头方向，无需按名字翻转
            anneal = s[-20:]
            hit = _find_span(seq, anneal, circular)
            strand = 1
            if hit is None:
                hit = _find_span(seq_rc, anneal, circular)
                strand = -1
        if hit is None:
            continue
        i, span_len = hit
        # 环状时 i 可能越出 [0, length)（跨原点命中），按环长取模换算；
        # 非环绕时与旧公式完全一致
        if strand == 1:
            start = i % length + 1
            end = (i + span_len - 1) % length + 1
        else:
            start = (length - i - span_len) % length + 1
            end = (length - i - 1) % length + 1
        if start > end and not circular:
            continue  # 跨原点命中：环上允许 start>end，线性图跳过
        out.append({
            "id": f"primer-{getattr(p, 'name', '') or len(out)}",
            "name": getattr(p, "name", "") or f"primer {len(out) + 1}",
            "start": start, "end": end, "strand": strand,
            "sequence": anneal or s,
            # 完整引物序列（含 5′ 突出）：序列视图按 SnapGene 式画出「名牌框 +
            # 引物序列」，突出部分悬在结合区外。与 span 等长时前端画不出差异
            "full_sequence": s,
        })
        if len(out) >= 5000:
            break
    return out


def _find_span(text: str, pattern: str, circular: bool = False) -> Optional[tuple]:
    """首个命中 → (0-based 起点, 长度)；找不到返回 None。

    circular 时在双倍序列上找：跨原点命中的起点可能 ≥ len(text)，调用方
    按环长取模换算成 start>end 的环绕坐标。"""
    hay = text + text[:len(pattern) - 1] if circular else text
    i = hay.find(pattern)
    if i == -1:
        return None
    return i, len(pattern)


def _is_insert_feature(raw: Dict, ins_s: Optional[int], ins_e: Optional[int]) -> bool:
    """识别构建体特征列表里的插入片段。

    常规情况起点终点与克隆坐标（insert_start/insert_end）完全相等；位点
    自带 ATG 充当起始密码子时 CDS 注释会被扩展几个 bp（把载体 ATG 包进
    编码区），扩展只朝外扩一端（正链扩 start、负链扩 end）——所以用
    「CDS 覆盖克隆区间」判断，原样与两个扩展方向都成立，且不依赖特征名
    （插入片段名跟随用户序列名，未必叫 insert）。assemble_construct 把
    插入特征放在列表首位，同名匹配到多条时先到先得。
    """
    if ins_s is None or ins_e is None:
        return False
    if str(raw.get("type") or "").upper() != "CDS":
        return False
    try:
        start, end = int(raw.get("start")), int(raw.get("end"))
    except (TypeError, ValueError):
        return False
    return start <= ins_s and end >= ins_e


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
        if not design_marked and _is_insert_feature(raw, ins_s, ins_e):
            design_marked = True
            # 插入片段是密码子优化后的编码序列：固定为 cds，不按名称猜测
            feats.append(_feature(raw, "design", "cds"))
        else:
            feats.append(_feature(raw, "vector"))
    doc = build_document(
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
    clone_enzymes = _clone_enzymes_of(result)
    if clone_enzymes:
        # 图谱前端据此强制显示并高亮克隆酶标签：多切点克隆酶（如 pcDNA3.1
        # 上的 NcoI 有 4 个切点）按拥挤规则会被藏掉，巧合的单切点酶看起来
        # 像克隆位点，用户会以为插入落在了别的酶之间
        doc["meta"]["clone_enzymes"] = clone_enzymes
    return doc


def _clone_enzymes_of(result) -> List[str]:
    """本设计实际使用的克隆酶；旧结果无 cloning_enzymes 字段时两级回退：

    1. 方案文本的「酶/试剂:」行——只保留真实限制酶（Gibson 行是
       'Gibson Assembly Master Mix' 之类的试剂，过滤掉）；
    2. 构建体连接点推断：识别序列恰好紧贴插入区间左端/右端的酶——
       旧 Gibson 双酶切设计的线性化酶不在试剂行里，只能这样恢复。"""
    from core.enzyme_sites import canonical_enzyme_name, get_enzyme

    out: List[str] = []
    seen = set()

    def _add(name: str) -> None:
        c = canonical_enzyme_name(name)
        if c and c not in seen:
            seen.add(c)
            out.append(c)

    if getattr(result, "cloning_enzymes", None):
        for e in result.cloning_enzymes:
            if e:
                _add(e)
        return out

    protocol = getattr(result, "clone_protocol", None) or ""
    for line in protocol.splitlines():
        s = line.strip()
        if s.startswith("酶/试剂:") or s.startswith("Enzymes:"):
            for e in s.split(":", 1)[1].split(","):
                e = e.strip()
                if e and get_enzyme(e):
                    _add(e)

    construct = getattr(result, "construct_sequence", None) or ""
    i_s, i_e = getattr(result, "insert_start", None), getattr(result, "insert_end", None)
    if construct and i_s and i_e:
        from core.sequence_analysis import RESTRICTION_ENZYMES

        for name, entry in RESTRICTION_ENZYMES.items():
            site = (entry[0] or "").upper()
            if not site:
                continue
            # 左连接点：识别序列结尾紧贴插入起点（无缝连接位点被消费，无匹配）
            left_hit = i_s - 1 - len(site) >= 0 and \
                construct[i_s - 1 - len(site):i_s - 1] == site
            # 右连接点：识别序列开头紧贴插入终点
            right_hit = i_e + len(site) <= len(construct) and \
                construct[i_e:i_e + len(site)] == site
            if left_hit or right_hit:
                _add(name)
    return out


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
