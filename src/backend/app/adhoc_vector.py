"""设计页「一次性载体」：本地上传 / 在线导入的载体只在本次设计里使用

背景：设计页原先只能从载体库选载体，而载体库是**共享库**、写入口只对管理员开放
（/api/vectors/import/* 全部依赖 get_admin_user）。用户手里的 .dna/.gb 或在线来源
的质粒因此完全无法参与设计。

这里把解析结果转成「一次性载体」：随本次设计请求内联提交（DesignRequest.vector），
由 app.design_service.run_design 现场构造 core.vector_library.Vector，
**不写载体库、不落盘、不额外要求管理员权限**——共享库不被普通用户的上传污染，
服务端也不会留下无主文件。

两个容易踩的点：

* MCS：载体库 YAML 是手写的 mcs 块，上传/在线导入的载体没有。没有 MCS 时
  assemble_construct 会在骨架正中插入、Golden Gate 只能用默认 overhang。
  这里先找文件里的 MCS 元件，找不到就用「唯一位点最密集的一段」推测，
  并把推测结果写进告警——用户必须知道线性化点是怎么来的。
* 拓扑：线性片段（PCR 产物 / Ensembl 片段）不能按环状扫描位点。
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

from core.enzyme_sites import DEFAULT_ENZYMES, find_enzyme_sites, get_enzyme
from core.plasmid_map.parsers import ParsedRecord, clean_sequence
from core.vector_library import ElementType, MCS, CloningSite, Vector, VectorLibrary
from app.vector_import import make_vector_id, safe_slug

# 推测 MCS 的窗口上限（bp）：随机质粒上两个唯一位点挨得再近，也很难在 250 bp
# 内凑齐 2 个以上；上限太小会漏掉真实 MCS，太大则可能把整段多克隆区之外
# 的位点卷进来，导致「替换 MCS 区间」把载体切走一大块
MCS_SPAN_MAX = 250
# 至少要有这么多个唯一位点才认为是 MCS
MCS_MIN_SITES = 2
# 随设计请求回传的元件上限（与 DesignVectorInput.features 的 max_length 一致）：
# 真实 .dna 里上千条注释不罕见，全塞进请求会白白撑大 body
MAX_INLINE_FEATURES = 5000

_parser = VectorLibrary()  # 只用它的 dict → Vector 归一实现（parse_dict）


def _field(obj, key: str, default=None):
    """同时吃 dict 与 pydantic 模型（DesignVectorInput/DesignVectorFeature）"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _strand(value) -> str:
    """图谱契约/core 的 strand（1/-1/0）↔ 载体 YAML 的 +/-"""
    if isinstance(value, str):
        return "-" if value.strip() in ("-", "-1") else "+"
    try:
        return "-" if int(value) == -1 else "+"
    except (TypeError, ValueError):
        return "+"


def design_features(features: Optional[Iterable[Dict]], length: int) -> Tuple[List[Dict], int]:
    """解析结果 features → 可回传的载体元件（丢弃坐标非法/越界的条目）

    返回 (元件列表, 丢弃条数)。跨原点复合特征（segments）按整体跨度收下，
    与 app/vector_import.record_to_vector_dict 的口径一致。
    """
    out: List[Dict] = []
    dropped = 0
    for f in features or []:
        try:
            start = int(_field(f, "start"))
            end = int(_field(f, "end"))
        except (TypeError, ValueError):
            dropped += 1
            continue
        if start < 1 or end < 1 or start > end or end > length:
            dropped += 1
            continue
        out.append({
            "name": str(_field(f, "name") or _field(f, "type") or "feature")[:80],
            "type": str(_field(f, "type") or "misc_feature")[:40],
            "start": start,
            "end": end,
            "strand": _strand(_field(f, "strand")),
            "description": str(_field(f, "notes") or _field(f, "description") or "")[:300],
        })
    return out, dropped


def scan_unique_sites(sequence: str, *, circular: bool) -> Tuple[List[Dict], Dict[str, List[int]]]:
    """扫描默认酶表 → (唯一位点列表, 酶名 → 唯一切点列表)

    回文酶在 find_enzyme_sites 里正反链各报一条（图谱口径），这里按
    (酶名, 位点起点) 去重，避免把同一个物理位点算成两个。
    """
    if not sequence:
        return [], {}
    raw = find_enzyme_sites(sequence, list(DEFAULT_ENZYMES), circular=circular)
    seen = set()
    counts: Counter = Counter()
    unique_sites: List[Dict] = []
    for s in raw:
        key = (s["name"], s["position"])
        if key in seen:
            continue
        seen.add(key)
        counts[s["name"]] += 1
        unique_sites.append(s)
    single = [s for s in unique_sites if counts[s["name"]] == 1]
    single.sort(key=lambda s: (s["position"], s["name"]))
    cuts: Dict[str, List[int]] = {s["name"]: [s["cut_fwd"]] for s in single}
    return single, cuts


def _sites_within(sites: List[Dict], start: int, end: int) -> List[Dict]:
    return [s for s in sites if start <= s["position"] <= end]


def infer_mcs_span(sites: List[Dict]) -> Optional[List[Dict]]:
    """唯一位点里找最密集的一段（MCS 的典型特征：多个酶都只切一次且挨在一起）

    窗口长度固定 MCS_SPAN_MAX，取位点最多的一窗；并列时取更靠前的一窗
    （确定性优先，避免同一份文件在不同机器上给出不同线性化点）。
    """
    if len(sites) < MCS_MIN_SITES:
        return None
    best: List[Dict] = []
    for i, anchor in enumerate(sites):
        window = [s for s in sites[i:] if s["position"] - anchor["position"] <= MCS_SPAN_MAX]
        if len(window) > len(best):
            best = window
    return best if len(best) >= MCS_MIN_SITES else None


def overhang_bases(sequence: str, site: Dict, *, circular: bool = True) -> str:
    """位点的实际 4 nt 5' 突出端碱基（取不到返回 ""）

    扫描结果里的 overhang 是**类型**（Biopython 的 "5prime"/"3prime"/"blunt"），
    不是碱基——直接塞进 CloningSite.overhang 会让 Golden Gate 的 overhang 变成
    "5PRI" 这种非法值（真实踩过）。这里按几何从序列里取：

    cut_fwd 是 1-based 切点坐标，其数值恰好等于切点前的 0-based 索引，
    5' 突出端 = 从该索引起的 |c2 - c1| 个碱基（BamHI (1,5) → GATC）。
    """
    spec = get_enzyme(site.get("name") or "")
    if not spec or spec.get("overhang") != "5prime":
        return ""
    c1, c2 = spec.get("cut") or (0, 0)
    if abs(c2 - c1) != 4:
        return ""
    start = int(site.get("cut_fwd") or 0)
    if start < 0:
        return ""
    seq = sequence.upper()
    if not seq:
        return ""
    if start + 4 <= len(seq):
        return seq[start:start + 4]
    if not circular:
        return ""
    return (seq + seq)[start:start + 4]


def _cloning_sites(sites: List[Dict], sequence: str, *, circular: bool) -> List[CloningSite]:
    """扫描结果 → CloningSite（cut_position_5 沿用「切割点前一位」的 1-based 口径）"""
    return [
        CloningSite(
            enzyme_name=s["name"],
            recognition_seq=s["recognition"],
            cut_position_5=s["cut_fwd"],
            cut_position_3=s["cut_rev"],
            overhang=overhang_bases(sequence, s, circular=circular),
            is_unique=True,
        )
        for s in sites
    ]


def _mcs_from_sites(sites: List[Dict], sequence: str, *, circular: bool, name: str,
                    span: Optional[Tuple[int, int]] = None) -> MCS:
    """文件标注的 MCS：按注释跨度，组装时整段被插入片段替换（与载体库口径一致）

    span 由注释给出时以注释为准（区间内的唯一位点只用来推 overhang）；
    不给时退化为「位点覆盖的区间」。
    """
    start, end = span if span else (
        min(s["position"] for s in sites),
        max(s["position"] + len(s["recognition"]) - 1 for s in sites))
    return MCS(name=name, start=start, end=end,
               sites=_cloning_sites(sites, sequence, circular=circular),
               reading_frame=1)


def _point_mcs(sites: List[Dict], point: int, sequence: str, *,
               circular: bool, name: str) -> MCS:
    """推测的 MCS：只给插入点，不给区间。

    推测不出真正的 MCS 边界，若把整段「唯一位点密集区」当 MCS，组装时会把
    这段骨架整段删掉换成插入片段（pET-28a 这类载体一删就是 150–250 bp）。

    start = point、end = point - 1：assemble_construct 替换的是
    [start - 1, end) 这个 0-based 半开区间，空区间即「纯插入一个点」，
    骨架一个碱基都不丢。这是全站唯一一处分段区间反向的 MCS，故注释在此。
    """
    return MCS(name=name, start=point, end=point - 1,
               sites=_cloning_sites(sites, sequence, circular=circular),
               reading_frame=1)


def _mcs_from_elements(elements, single_sites: List[Dict], sequence: str, *,
                       circular: bool) -> Optional[MCS]:
    """文件里自带 MCS 元件时用它的跨度（注释作者标出来的才是权威）"""
    for elem in elements:
        if elem.element_type != ElementType.MCS:
            continue
        inner = _sites_within(single_sites, elem.start, elem.end)
        if not inner:
            # 有 MCS 注释但区间内没有唯一位点：仍按注释跨度组装，sites 为空
            # （Golden Gate 会因此回落到默认 overhang，由告警说明）
            return MCS(name=elem.name or "MCS", start=elem.start, end=elem.end, sites=[],
                       reading_frame=1)
        return _mcs_from_sites(inner, sequence, circular=circular,
                               name=elem.name or "MCS", span=(elem.start, elem.end))
    return None


def build_vector(
    *,
    name: str,
    sequence: str,
    features: Optional[Iterable[Dict]] = None,
    topology: str = "circular",
    source: str = "custom",
    vector_id: Optional[str] = None,
    url: str = "",
    max_length: Optional[int] = None,
) -> Tuple[Vector, Dict]:
    """一次性载体：序列 + 元件 → Vector（含 MCS 判定与唯一位点表）

    返回 (载体, 元信息)，元信息里带 mcs/unique_enzymes/warnings，
    供接口回执与设计结果告警复用（两条路径口径因此不会分叉）。
    """
    seq = clean_sequence(sequence, max_length)
    circular = str(topology or "").lower() != "linear"
    feats, dropped = design_features(features, len(seq))
    warnings: List[str] = []
    if dropped:
        warnings.append(f"{dropped} 个元件的坐标非法或越界（序列 {len(seq)} bp），已忽略")

    vector = _parser.parse_dict({
        "id": vector_id or make_vector_id(source, name or "vector"),
        "name": name or "vector",
        "sequence": seq,
        "source": source,
        "type": "expression",
        "topology": "linear" if not circular else "circular",
        "features": feats,
    })

    single_sites, cuts = scan_unique_sites(seq, circular=circular)
    mcs = _mcs_from_elements(vector.elements, single_sites, seq, circular=circular)
    mcs_origin = "feature" if mcs else ""
    if mcs is None:
        inferred = infer_mcs_span(single_sites)
        if inferred:
            point = min(s["cut_fwd"] for s in inferred)
            mcs = _point_mcs(inferred, point, seq, circular=circular,
                             name="MCS（按唯一位点推测）")
            mcs_origin = "inferred"
    vector.mcs = mcs
    vector.unique_site_cuts = cuts

    if mcs is not None:
        if mcs_origin == "feature":
            warnings.append(
                f"载体 MCS 取自文件注释（{mcs.start}–{mcs.end} bp，"
                f"{len(mcs.sites)} 个唯一位点），组装时该段由插入片段替换")
        else:
            warnings.append(
                f"载体未标注 MCS：已按唯一位点最密集区（{mcs.start} bp 附近，"
                f"{len(mcs.sites)} 个唯一位点）取插入点，插入为纯插入（不删骨架碱基）；"
                "如需在其他位置线性化，请在「定位酶切位点」里指定那把酶")
    else:
        warnings.append(
            "载体未识别到 MCS/唯一位点：Gibson 同源臂按骨架中点设计、"
            "Golden Gate 用默认 overhang，请核对线性化点后再下单合成")
    if not circular:
        warnings.append("该载体为线性序列，位点扫描与组装按线性片段处理")

    meta = {
        "id": vector.id,
        "name": vector.name,
        "source": source,
        "url": url,
        "length": len(seq),
        "topology": vector.topology,
        "mcs": ({"start": mcs.start, "end": mcs.end, "origin": mcs_origin,
                 "enzymes": [s.enzyme_name for s in mcs.sites]} if mcs else None),
        "unique_enzymes": list(cuts.keys()),
        "warnings": warnings,
        "features": len(feats),
    }
    return vector, meta


def payload_from_record(
    record: ParsedRecord,
    *,
    source: str,
    url: str = "",
    license_text: str = "",
    max_length: Optional[int] = None,
    display_name: Optional[str] = None,
) -> Dict:
    """解析记录 → 接口回执 / 设计请求可回传的一次性载体载荷

    display_name：上传文件名去扩展名。给了就优先用它——用户对文件名的
    记忆远强于文件内部的 LOCUS 名（真实案例：SnapGene 导出的 GenBank 文
    件名叫「1009-1 Aa01-1-1 (1).dna」，内部 LOCUS 却是「A01-1」，导入后
    显示内部名让用户以为传错了文件）。vector_id 同样跟随，保持 id↔名一致。
    """
    name = (display_name or record.name or "vector")[:80]
    vector, meta = build_vector(
        name=name, sequence=record.sequence, features=record.features,
        topology=record.topology, source=source,
        vector_id=f"adhoc_{safe_slug(source, 'src')}_{safe_slug(name, 'vector')}",
        url=url, max_length=max_length,
    )
    warnings = list(record.warnings or []) + meta["warnings"]
    feats = design_features(record.features, len(vector.sequence))[0]
    if len(feats) > MAX_INLINE_FEATURES:
        warnings.append(f"元件过多，只回传前 {MAX_INLINE_FEATURES} 条（共 {len(feats)} 条）")
        feats = feats[:MAX_INLINE_FEATURES]
    return {
        "id": vector.id,
        "name": vector.name,
        "source": source,
        "url": url,
        "license": license_text,
        "sequence": vector.sequence,
        "topology": vector.topology,
        "length": meta["length"],
        "features": feats,
        "mcs": meta["mcs"],
        "unique_enzymes": meta["unique_enzymes"],
        "warnings": warnings,
    }


def vector_from_inline(payload, *, max_length: Optional[int] = None) -> Tuple[Vector, Dict]:
    """设计请求里的内联载体（DesignRequest.vector）→ Vector + 元信息

    payload 是 dict 或带同名属性的对象；序列再清洗一次（不信任客户端），
    元件坐标重新过滤（与上传入口同一套规则）。
    """
    def _get(key, default=None):
        return _field(payload, key, default)

    return build_vector(
        name=_get("name") or "vector",
        sequence=_get("sequence") or "",
        features=_get("features") or [],
        topology=_get("topology") or "circular",
        source=_get("source") or "inline",
        vector_id=_get("id") or None,
        url=_get("url") or "",
        max_length=max_length,
    )
