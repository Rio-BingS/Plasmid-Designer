"""序列文件 → 图谱记录（GenBank / FASTA / SnapGene .dna / 粘贴文本）

纯函数，不依赖 web 层。解析一律走 Bio.SeqIO（含 "snapgene"），统一产出
ParsedRecord：序列（大写 IUPAC，U→T）、拓扑、已归一的特征（契约
MapFeature 形状，尚无 id）、引物（primer_bind）与告警。

坐标：Biopython 0-based 半开 → 契约 1-based 闭区间；join()/order() 复合位置
保留为 segments，整体跨度在环状分子上可跨原点（start > end）。
"""

import io
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.seq_utils import revcomp
from core.plasmid_map.types import (
    normalize_color,
    normalize_type,
    strip_color_note,
)

GENBANK_EXTS = {"gb", "gbk", "genbank", "gbff", "ape"}
FASTA_EXTS = {"fa", "fasta", "fas", "fna", "fsa"}
SNAPGENE_EXTS = {"dna"}

# 契约 sequence 字段：大写 IUPAC DNA
_IUPAC_DNA = set("ACGTRYSWKMBDHVN")
_WS_DIGITS_RE = re.compile(r"[\s0-9]+")

MAX_NOTE_CHARS = 300
MAX_QUALIFIER_CHARS = 1000
# 不进 qualifiers 的键：翻译可再生且体积大；颜色已提升为 color 字段
_SKIP_QUALIFIERS = {"translation", "ApEinfo_fwdcolor", "ApEinfo_revcolor",
                    "ApEinfo_graphicformat"}


class MapParseError(ValueError):
    """解析失败；status 供路由映射 HTTP 状态（413/415/422），消息面向用户"""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class ParsedRecord:
    name: str
    topology: str  # "circular" | "linear"
    sequence: str
    source_format: str
    features: List[Dict] = field(default_factory=list)
    primers: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ==================== 序列清洗 ====================

def clean_sequence(raw: str, max_length: Optional[int] = None) -> str:
    """去空白/数字、转大写、U→T；含非 IUPAC 字符抛 422，超长抛 413"""
    seq = _WS_DIGITS_RE.sub("", raw or "").upper().replace("U", "T")
    if not seq:
        raise MapParseError("序列为空", 422)
    bad = sorted(set(seq) - _IUPAC_DNA)
    if bad:
        shown = "".join(bad[:10])
        raise MapParseError(f"序列含非法字符：{shown}（只接受 IUPAC DNA 碱基）", 422)
    if max_length is not None and len(seq) > max_length:
        raise MapParseError(
            f"序列过长（{len(seq)} bp > {max_length} bp），请截取后再打开", 413)
    return seq


# ==================== 入口 ====================

def detect_format(filename: str) -> Optional[str]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext in GENBANK_EXTS:
        return "genbank"
    if ext in FASTA_EXTS:
        return "fasta"
    if ext in SNAPGENE_EXTS:
        return "snapgene"
    return None


def parse_file(filename: str, data: bytes, max_length: Optional[int] = None,
               topology: Optional[str] = None) -> ParsedRecord:
    """按扩展名解析上传文件；未知扩展名时按内容嗅探文本格式，仍不认识 → 415"""
    fmt = detect_format(filename)
    stem = _stem(filename)
    if fmt == "snapgene" or (fmt is None and data[:1] == b"\x09" and b"SnapGene" in data[:16]):
        return _parse_snapgene(data, stem, max_length, topology)
    if fmt is None:
        text = _decode(data)
        sniffed = _sniff_text(text)
        if sniffed == "raw":
            raise MapParseError(
                "不支持的文件格式：请上传 GenBank（.gb/.gbk）、FASTA（.fa/.fasta）"
                "或 SnapGene（.dna）文件", 415)
        fmt = sniffed
    text = _decode(data)
    if fmt == "genbank":
        return _parse_genbank_text(text, stem, max_length, topology)
    return _parse_fasta_text(text, stem, max_length, topology)


def parse_text(text: str, name: Optional[str] = None, topology: Optional[str] = None,
               max_length: Optional[int] = None) -> ParsedRecord:
    """粘贴文本：自动识别 GenBank / FASTA / 纯序列"""
    fmt = _sniff_text(text or "")
    if fmt == "genbank":
        rec = _parse_genbank_text(text, name or "sequence", max_length, topology)
    elif fmt == "fasta":
        rec = _parse_fasta_text(text, name or "sequence", max_length, topology)
    else:
        seq = clean_sequence(text, max_length)
        rec = ParsedRecord(name=name or "sequence", topology="circular",
                           sequence=seq, source_format="raw")
        _apply_topology(rec, None, topology)
    if name:
        rec.name = name
    return rec


# ==================== 内部：格式实现 ====================

def _stem(filename: str) -> str:
    base = (filename or "sequence").replace("\\", "/").rsplit("/", 1)[-1]
    return (base.rsplit(".", 1)[0] if "." in base else base)[:80] or "sequence"


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")  # pragma: no cover


def _sniff_text(text: str) -> str:
    head = text.lstrip()[:200]
    if head.startswith("LOCUS"):
        return "genbank"
    if head.startswith(">"):
        return "fasta"
    return "raw"


def _apply_topology(rec: ParsedRecord, file_topology: Optional[str],
                    requested: Optional[str]) -> None:
    """显式请求 > 文件标注 > 默认环状（并告警）"""
    if requested in ("circular", "linear"):
        rec.topology = requested
    elif file_topology in ("circular", "linear"):
        rec.topology = file_topology
    else:
        rec.topology = "circular"
        rec.warnings.append("文件未标明拓扑，已按环状质粒处理")


def _first_record(records: list, rec_warnings: List[str], label: str):
    if not records:
        raise MapParseError(f"{label} 中没有可读取的序列记录", 422)
    if len(records) > 1:
        rec_warnings.append(f"文件含 {len(records)} 条记录，只打开第一条")
    return records[0]


def _parse_genbank_text(text: str, fallback_name: str, max_length, topology) -> ParsedRecord:
    import warnings as _w

    from Bio import BiopythonParserWarning, SeqIO

    warns: List[str] = []
    try:
        with _w.catch_warnings():
            # LOCUS 行不规范时 Biopython 只告警；对用户文件放行（pytest.ini
            # 把该告警升级为错误是为了守护本项目自己的导出）
            _w.simplefilter("ignore", BiopythonParserWarning)
            records = list(SeqIO.parse(io.StringIO(text), "genbank"))
    except Exception as e:
        raise MapParseError(f"GenBank 文件解析失败：{e}", 422) from e
    record = _first_record(records, warns, "GenBank 文件")
    return _from_seqrecord(record, "genbank", fallback_name, max_length, topology, warns)


def _parse_fasta_text(text: str, fallback_name: str, max_length, topology) -> ParsedRecord:
    from Bio import SeqIO

    warns: List[str] = []
    try:
        records = list(SeqIO.parse(io.StringIO(text), "fasta"))
    except Exception as e:  # pragma: no cover - fasta 解析极少抛错
        raise MapParseError(f"FASTA 文件解析失败：{e}", 422) from e
    record = _first_record(records, warns, "FASTA 文件")
    rec = ParsedRecord(
        name=(record.id or fallback_name)[:80],
        topology="circular",
        sequence=clean_sequence(str(record.seq), max_length),
        source_format="fasta",
        warnings=warns,
    )
    # FASTA 头部可写 "circular"/"linear"（部分工具的约定）
    desc = (record.description or "").lower()
    file_topo = "linear" if "linear" in desc else ("circular" if "circular" in desc else None)
    _apply_topology(rec, file_topo, topology)
    return rec


def _snapgene_location(rangespec, strand, record, is_primer=False):
    """Bio.SeqIO.SnapGeneIO._parse_location 的修正版（Biopython 1.8x）。

    原实现把跨原点的段一律拆成 [s..L, 1..e]；负链特征的多段顺序它会整体
    倒排成生物学顺序，唯独单段内的这两截没倒，于是负链跨原点特征的
    parts 不是生物学顺序（extract 得到 rc(s..L)+rc(1..e)，应为
    rc(1..e)+rc(s..L)）。这里负链跨原点时直接给出 [1..e, s..L]。"""
    from Bio.SeqFeature import SimpleLocation

    start, end = (int(x) for x in rangespec.split("-"))
    start -= 1  # SnapGene 1-based
    if is_primer:  # 引物坐标在 SnapGene 文件里整体偏 -1（与上游实现一致）
        start += 1
        end += 1
    if start >= end:
        l1 = SimpleLocation(start, len(record), strand=strand)
        l2 = SimpleLocation(0, end, strand=strand)
        return l2 + l1 if strand == -1 else l1 + l2
    return SimpleLocation(start, end, strand=strand)


def _install_snapgene_location_fix() -> None:
    from Bio.SeqIO import SnapGeneIO

    if getattr(SnapGeneIO, "_parse_location", None) is not _snapgene_location:
        SnapGeneIO._parse_location = _snapgene_location


def _parse_snapgene(data: bytes, fallback_name: str, max_length, topology) -> ParsedRecord:
    from Bio import SeqIO

    _install_snapgene_location_fix()
    warns: List[str] = []
    try:
        records = list(SeqIO.parse(io.BytesIO(data), "snapgene"))
    except Exception as e:
        raise MapParseError(f"SnapGene 文件解析失败：{e}", 422) from e
    record = _first_record(records, warns, "SnapGene 文件")
    colors = _snapgene_feature_colors(data)
    return _from_seqrecord(record, "snapgene", fallback_name, max_length, topology,
                           warns, snapgene_colors=colors)


def _snapgene_feature_colors(data: bytes) -> List[Optional[str]]:
    """SnapGene 特征颜色存在 Segment 的 color 属性里，Bio.SeqIO 不读：
    这里按文件中特征出现顺序取第一段颜色（与 SeqIO 的非引物特征顺序一致）"""
    from xml.dom.minidom import parseString

    colors: List[Optional[str]] = []
    pos = 0
    try:
        while pos + 5 <= len(data):
            ptype = data[pos]
            (length,) = struct.unpack(">I", data[pos + 1:pos + 5])
            body = data[pos + 5:pos + 5 + length]
            pos += 5 + length
            if ptype != 0x0A:
                continue
            xml = parseString(body.decode("utf-8"))
            for feat in xml.getElementsByTagName("Feature"):
                color = None
                for seg in feat.getElementsByTagName("Segment"):
                    if seg.getAttribute("type") == "gap":
                        continue
                    color = normalize_color(seg.getAttribute("color"))
                    break
                colors.append(color)
    except Exception:
        return []
    return colors


def _from_seqrecord(record, source_format: str, fallback_name: str, max_length,
                    topology, warns: List[str],
                    snapgene_colors: Optional[List[Optional[str]]] = None) -> ParsedRecord:
    try:
        raw_seq = str(record.seq)
    except Exception as e:  # 未定义序列（Bio UndefinedSequenceError）
        raise MapParseError("文件不含序列（只有特征表）", 422) from e
    seq = clean_sequence(raw_seq, max_length)
    name = record.name if record.name and record.name not in ("<unknown name>", ".") else ""
    rec = ParsedRecord(name=(name or fallback_name)[:80], topology="circular",
                       sequence=seq, source_format=source_format, warnings=warns)
    file_topo = (record.annotations or {}).get("topology")
    _apply_topology(rec, file_topo, topology)

    length = len(seq)
    circular = rec.topology == "circular"
    color_iter = iter(snapgene_colors or [])
    cds_spans = set()
    pending_genes: List[Tuple[Dict, Tuple]] = []
    dropped = 0
    for sf in record.features:
        if sf.type == "source":
            continue
        sg_color = next(color_iter, None) if sf.type != "primer_bind" else None
        try:
            built = _feature_from_seqfeature(sf, length, circular, sg_color)
        except Exception:
            dropped += 1
            continue
        if built is None:
            dropped += 1
            continue
        if sf.type == "primer_bind":
            rec.primers.append(_primer_from_feature(built, seq))
            continue
        span_key = (built["start"], built["end"], built["strand"])
        if sf.type == "CDS":
            cds_spans.add(span_key)
        if sf.type == "gene":
            pending_genes.append((built, span_key))
            continue
        rec.features.append(built)
    # gene 与同跨度 CDS 重复（GenBank 惯例成对出现）：只保留 CDS
    for built, span_key in pending_genes:
        if span_key not in cds_spans:
            rec.features.append(built)
    if dropped:
        rec.warnings.append(f"{dropped} 个特征坐标无效或超出序列，已忽略")
    return rec


def _first(q: Dict, key: str) -> Optional[str]:
    v = q.get(key)
    if not v:
        return None
    v0 = v[0] if isinstance(v, (list, tuple)) else v
    return str(v0).strip() if v0 is not None else None


def _feature_name(q: Dict, ftype: str) -> str:
    for key in ("label", "name", "gene", "product", "locus_tag", "standard_name",
                "allele", "bound_moiety", "regulatory_class"):
        v = _first(q, key)
        if v:
            return v[:80]
    note = strip_color_note(_first(q, "note"))
    if note and len(note) <= 40:
        return note
    return ftype


def _feature_notes(q: Dict, name: str) -> Optional[str]:
    parts = []
    for key in ("note", "product", "gene", "function"):
        v = _first(q, key)
        if key == "note":
            v = strip_color_note(v)
        if v and v != name and v not in parts:
            parts.append(v)
    if not parts:
        return None
    text = "; ".join(parts)
    return text[:MAX_NOTE_CHARS]


def _feature_color(q: Dict, strand: int, sg_color: Optional[str]) -> Optional[str]:
    keys = ("ApEinfo_revcolor", "ApEinfo_fwdcolor") if strand == -1 else \
        ("ApEinfo_fwdcolor", "ApEinfo_revcolor")
    for key in keys + ("color", "colour"):
        c = normalize_color(_first(q, key))
        if c:
            return c
    note = _first(q, "note") or ""
    if note.lower().lstrip().startswith("color:"):
        c = normalize_color(note)
        if c:
            return c
    return sg_color


def location_to_span(location, length: int, circular: bool) -> Tuple[int, int, int, Optional[List[Dict]]]:
    """Biopython 位置 → (start, end, strand, segments)，1-based 闭区间。

    跨原点的复合位置（环状）整体跨度 start > end；segments 按生物学顺序
    给出（GenBank 的 complement(join(..)) 由 Biopython 倒排成生物学顺序，
    SnapGene 负链跨原点段经 _snapgene_location 修正），单段时为 None。
    契约约定：负链 segments 沿负链 5'→3'，见 extract_feature。
    坐标越界抛 ValueError。
    """
    parts = list(getattr(location, "parts", None) or [location])
    segs = []
    strands = set()
    for p in parts:
        s, e = int(p.start), int(p.end)
        if s < 0 or e > length or e <= s:
            raise ValueError("feature location out of range")
        segs.append((s + 1, e))
        strands.add(p.strand or 0)
    strand = strands.pop() if len(strands) == 1 else 0
    strand = strand if strand in (1, -1) else 0
    if len(segs) == 1:
        return segs[0][0], segs[0][1], strand, None
    # 整体跨度与 parts 的排列顺序无关（GenBank 负链 join 按生物学顺序倒排，
    # SnapGene 解析器跨原点段却按正向排列）：按起点排序后找最大空隙，
    # 环状分子上若最大空隙不是「末段→首段」的绕圈空隙，说明特征跨原点
    srt = sorted(segs)
    start, end = srt[0][0], max(e for _, e in srt)
    if circular:
        wrap_gap = srt[0][0] - 1 + (length - end)
        best_gap, best_i = wrap_gap, None
        reach = srt[0][1]
        for i in range(1, len(srt)):
            gap = srt[i][0] - reach - 1
            if gap > best_gap:
                best_gap, best_i = gap, i
            reach = max(reach, srt[i][1])
        if best_i is not None:
            start = srt[best_i][0]
            end = max(e for _, e in srt[:best_i])
    return start, end, strand, [{"start": s, "end": e} for s, e in segs]


def _feature_from_seqfeature(sf, length: int, circular: bool,
                             sg_color: Optional[str]) -> Optional[Dict]:
    if sf.location is None:
        return None
    start, end, strand, segments = location_to_span(sf.location, length, circular)
    q = sf.qualifiers or {}
    name = _feature_name(q, sf.type)
    extra = " ".join(filter(None, (_first(q, "gene"), _first(q, "product"),
                                   _first(q, "note"))))
    ftype = normalize_type(sf.type, name, _first(q, "regulatory_class"), extra)
    feat: Dict = {
        "name": name,
        "type": ftype,
        "start": start,
        "end": end,
        "strand": strand,
        "source": "file",
    }
    if segments:
        feat["segments"] = segments
    color = _feature_color(q, strand, sg_color)
    if color:
        feat["color"] = color
    notes = _feature_notes(q, name)
    if notes:
        feat["notes"] = notes
    quals = {"genbank_type": sf.type}
    for k, v in q.items():
        if k in _SKIP_QUALIFIERS:
            continue
        val = v[0] if isinstance(v, (list, tuple)) and v else v
        if val is None or isinstance(val, (list, tuple)):
            continue
        quals[str(k)[:40]] = str(val)[:MAX_QUALIFIER_CHARS]
    feat["qualifiers"] = quals
    return feat


def extract_span(seq: str, start: int, end: int, strand: int = 1) -> str:
    """按 1-based 闭区间取子序列（start > end 时跨原点），负链取反向互补"""
    if start <= end:
        sub = seq[start - 1:end]
    else:
        sub = seq[start - 1:] + seq[:end]
    return revcomp(sub) if strand == -1 else sub


def extract_feature(seq: str, feat: Dict) -> str:
    """特征的契约序列（沿特征自身方向 5'→3'）：segments 按生物学顺序逐段
    extract_span 后拼接（负链每段取反向互补，段顺序即负链上的阅读顺序）；
    无 segments 时就是整体跨度。GenBank 导出 → 重新读入后 Biopython
    SeqFeature.extract 应与此一致。"""
    strand = feat.get("strand") or 0
    segs = feat.get("segments") or [{"start": feat["start"], "end": feat["end"]}]
    return "".join(extract_span(seq, int(s["start"]), int(s["end"]), strand) for s in segs)


def _primer_from_feature(feat: Dict, seq: str) -> Dict:
    strand = feat["strand"] if feat["strand"] in (1, -1) else 1
    return {
        "name": feat["name"],
        "start": feat["start"],
        "end": feat["end"],
        "strand": strand,
        "sequence": extract_span(seq, feat["start"], feat["end"], strand),
    }
