"""图谱契约 plasmid-map/1 的常量与特征类型归一

坐标约定（全包一致）：1-based、闭区间；环状分子上 start > end 表示跨原点。
契约原文见仓库 docs/MAP.md。
"""

import re
from typing import Dict, Optional

SCHEMA = "plasmid-map/1"

FEATURE_TYPES = (
    "cds", "promoter", "terminator", "rep_origin", "rbs", "marker", "tag",
    "signal_peptide", "polyA_signal", "enhancer", "ltr", "primer_bind",
    "protein_bind", "misc_rna", "misc_feature", "insert", "mcs",
)
FEATURE_TYPE_SET = frozenset(FEATURE_TYPES)

SOURCE_FORMATS = ("genbank", "fasta", "snapgene", "raw", "design", "vector")

# GenBank 特征键 → FeatureType（未列出的键归 misc_feature）
_GENBANK_TYPE: Dict[str, str] = {
    "cds": "cds",
    "promoter": "promoter",
    "terminator": "terminator",
    "rep_origin": "rep_origin",
    "orit": "rep_origin",
    "origin": "rep_origin",
    "rbs": "rbs",
    "ribosome_binding_site": "rbs",
    "sig_peptide": "signal_peptide",
    "signal_peptide": "signal_peptide",
    "polya_signal": "polyA_signal",
    "polya_site": "polyA_signal",
    "enhancer": "enhancer",
    "ltr": "ltr",
    "primer_bind": "primer_bind",
    "primer": "primer_bind",
    "protein_bind": "protein_bind",
    "misc_rna": "misc_rna",
    "ncrna": "misc_rna",
    "trna": "misc_rna",
    "rrna": "misc_rna",
    "mrna": "misc_rna",
    "misc_feature": "misc_feature",
    # 载体库 YAML 的 element_type 值（core/vector_library.ElementType）
    "resistance": "marker",
    "tag": "tag",
    "multiple_cloning_site": "mcs",
    "mcs": "mcs",
    "signalling_peptide": "signal_peptide",
    "insert": "insert",
    "marker": "marker",
}

# /regulatory_class（INSDC 受控词表）→ FeatureType
_REGULATORY_CLASS: Dict[str, str] = {
    "promoter": "promoter",
    "minus_10_signal": "promoter",
    "minus_35_signal": "promoter",
    "terminator": "terminator",
    "ribosome_binding_site": "rbs",
    "polya_signal_sequence": "polyA_signal",
    "enhancer": "enhancer",
}

# 抗性/筛选标记（名称、/gene、/product 任一命中即视为 marker）。
# 注意 SnapGene 惯例 TcR = 四环素抗性、TetR = tet 阻遏蛋白，故不收 tetr/tet
_MARKER_RE = re.compile(
    r"\b(ampr|amp\(r\)|bla|beta-lactamase|kanr|kan\(r\)|neor|neo|nptii|aph\(3'\)|"
    r"cmr|cat|chloramphenicol|tcr|tet[ac]|tetracycline|hygr|hph|hyg|puror|pac|puromycin|"
    r"bsd|bsr|blasticidin|zeor|sh ?ble|ble|bleomycin|zeocin|gmr|aacc1?|gentamicin|"
    r"specr|smr|aada|spectinomycin|streptomycin|neomycin|kanamycin|hygromycin|"
    r"ampicillin|carbenicillin|g418)\b"
    r"|resistance|抗性",
    re.IGNORECASE,
)
_TAG_RE = re.compile(
    r"\b(\d+x ?his|his ?\d*|6xhis|his-tag|histag|flag|3xflag|ha|myc|c-myc|v5|"
    r"gst|mbp|strep(-tag)?( ii)?|t7 ?tag|s-tag|avi(tag)?|sumo|trx|acv5)\b",
    re.IGNORECASE,
)
_PROMOTER_RE = re.compile(r"promoter|启动子", re.IGNORECASE)
_TERMINATOR_RE = re.compile(r"terminator|终止子", re.IGNORECASE)
_POLYA_RE = re.compile(r"poly ?\(?a\)?( signal)?|polya", re.IGNORECASE)
_ORI_RE = re.compile(r"\bori\b|origin|复制起", re.IGNORECASE)
_ENHANCER_RE = re.compile(r"enhancer", re.IGNORECASE)
_LTR_RE = re.compile(r"\bltr\b|long terminal repeat", re.IGNORECASE)
_RBS_RE = re.compile(r"\brbs\b|ribosome binding|shine.?dalgarno", re.IGNORECASE)
_OPERATOR_RE = re.compile(r"operator|binding site|\bcap\b", re.IGNORECASE)
_MCS_RE = re.compile(r"\bmcs\b|multiple cloning|多克隆", re.IGNORECASE)


def _text(*parts: Optional[str]) -> str:
    return " ".join(p for p in parts if p)


def is_marker_name(*names: Optional[str]) -> bool:
    return bool(_MARKER_RE.search(_text(*names)))


def normalize_type(
    raw_type: Optional[str],
    name: str = "",
    regulatory_class: Optional[str] = None,
    extra_text: str = "",
) -> str:
    """GenBank 特征键 / 载体库元件类型 → 契约 FeatureType。

    name/extra_text 用于细化：抗性 CDS → marker，regulatory/other 按名称
    归到 promoter/rbs/protein_bind…，短标签 CDS（His/FLAG…）→ tag。
    """
    key = (raw_type or "misc_feature").strip().lower()
    text = _text(name, extra_text)

    if key == "regulatory":
        cls = (regulatory_class or "").strip().lower()
        if cls in _REGULATORY_CLASS:
            return _REGULATORY_CLASS[cls]
        key = "other"
    if key in ("cds", "gene"):
        if is_marker_name(name, extra_text):
            return "marker"
        if _TAG_RE.search(name or "") and len(name or "") <= 24:
            return "tag"
        return "cds"
    mapped = _GENBANK_TYPE.get(key)
    if mapped and mapped != "misc_feature":
        return mapped

    # misc_feature / other / regulatory(无类) / 未知键：按名称推断
    if _MCS_RE.search(name or ""):
        return "mcs"
    if _LTR_RE.search(text):
        return "ltr"
    if _POLYA_RE.search(name or ""):
        return "polyA_signal"
    if _ENHANCER_RE.search(name or ""):
        return "enhancer"
    if _PROMOTER_RE.search(name or ""):
        return "promoter"
    if _TERMINATOR_RE.search(name or ""):
        return "terminator"
    if _RBS_RE.search(name or ""):
        return "rbs"
    if _ORI_RE.search(name or ""):
        return "rep_origin"
    if _OPERATOR_RE.search(name or ""):
        return "protein_bind"
    if _TAG_RE.search(name or "") and len(name or "") <= 24:
        return "tag"
    return "misc_feature"


_HEX_RE = re.compile(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


def normalize_color(value: Optional[str]) -> Optional[str]:
    """'#AbC'/'abcdef'/'color: #abcdef' → '#abcdef'；不是颜色返回 None"""
    if not value or not isinstance(value, str):
        return None
    m = _HEX_RE.search(value.strip())
    if not m:
        return None
    h = m.group(1).lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


_NOTE_COLOR_RE = re.compile(r"^\s*color:\s*#[0-9a-fA-F]{3,6}\s*;?\s*", re.IGNORECASE)


def strip_color_note(note: Optional[str]) -> str:
    """SnapGene 导出的 /note="color: #xxxxxx" 不是描述：剥掉颜色前缀"""
    return _NOTE_COLOR_RE.sub("", note or "").strip()
