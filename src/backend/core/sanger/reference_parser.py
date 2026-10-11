"""测序分析参考序列文件解析

参考序列随 .ab1 一起上传（用户把图谱文件与测序文件放同一文件夹导入）。
支持三种格式，统一返回 (序列, 特征列表)：
- GenBank (.gb/.gbk/.genbank)：Biopython 解析，特征含名称/类型/坐标/方向
  复合位置（join/order，含环状跨原点）逐段各出一条记录，附 feature_id /
  segment_index / segment_count 分组键（单段特征不带这些键）
- FASTA (.fasta/.fa/.fna)：仅序列，无特征
- SnapGene (.dna)：snapgene-reader 解析（包缺失时报可读错误）

parse_reference_topology 额外返回拓扑：GenBank 读 LOCUS 行的
circular/linear，SnapGene 读文件的 topology 标记，FASTA 无此信息（None）。
"""

import io
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

GENBANK_EXTS = {"gb", "gbk", "genbank"}
FASTA_EXTS = {"fasta", "fa", "fna"}
SNAPGENE_EXTS = {"dna"}
SUPPORTED_EXTS = GENBANK_EXTS | FASTA_EXTS | SNAPGENE_EXTS


class ReferenceParseError(ValueError):
    """参考文件无法解析（消息面向终端用户）"""


def parse_reference(filename: str, data: bytes) -> Tuple[str, List[Dict]]:
    """按扩展名分发解析。失败抛 ReferenceParseError（消息可直接展示）。"""
    seq, features, _circular = parse_reference_topology(filename, data)
    return seq, features


def parse_reference_topology(filename: str, data: bytes
                             ) -> Tuple[str, List[Dict], Optional[bool]]:
    """同 parse_reference，另返回参考拓扑：True 环状 / False 线性 /
    None 文件未标明（FASTA，或 GenBank/SnapGene 缺少拓扑字段）。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext in GENBANK_EXTS:
            return _from_genbank(data)
        if ext in FASTA_EXTS:
            return _from_fasta(data)
        if ext in SNAPGENE_EXTS:
            return _from_snapgene(data)
        raise ReferenceParseError(
            f"无法识别的参考文件类型 .{ext or '(无扩展名)'}，支持 .gb / .fasta / .dna"
        )
    except ReferenceParseError:
        raise
    except Exception as e:
        raise ReferenceParseError(f"参考文件解析失败（{filename}）: {e}") from e


def _clean(text: str) -> str:
    import re
    return re.sub(r"\s+", " ", (text or "").strip())


def _from_genbank(data: bytes) -> Tuple[str, List[Dict], Optional[bool]]:
    from Bio import SeqIO

    text = data.decode("utf-8", errors="ignore")
    records = list(SeqIO.parse(io.StringIO(text), "genbank"))
    if not records:
        raise ReferenceParseError("GenBank 文件中没有记录")
    record = records[0]
    seq = str(record.seq).upper()
    circular = _topology_flag((record.annotations or {}).get("topology"))
    features: List[Dict] = []
    for feature_id, f in enumerate(record.features):
        if f.type == "source":
            continue
        try:
            # 复合位置（join/order，含跨原点的环状特征）逐段展开：取整体跨度
            # 会把 join(2701..3000,1..300) 变成覆盖全质粒的 1..3000。
            # Biopython 的 parts 已按生物学方向排列（负链为反向顺序）
            parts = [(int(p.start) + 1, int(p.end), p.strand) for p in f.location.parts]
        except (TypeError, ValueError, AttributeError):
            continue
        segments = []
        for start, end, part_strand in parts:
            if start > end or end < 1 or start > len(seq):
                continue
            segments.append((max(1, start), min(len(seq), end), part_strand))
        if not segments:
            continue
        quals = f.qualifiers or {}
        name = ""
        for key in ("label", "gene", "product", "name", "apifinfo"):
            v = quals.get(key)
            if v:
                name = str(v[0])
                break
        note = ""
        for key in ("note", "product", "function"):
            v = quals.get(key)
            if v:
                note = str(v[0])
                break
        whole_strand = f.location.strand
        for idx, (start, end, part_strand) in enumerate(segments):
            strand = whole_strand if whole_strand in (1, -1) else part_strand
            rec = {
                "name": _clean(name or f.type)[:40],
                "type": _clean(f.type)[:24],
                "start": start,
                "end": end,
                "strand": "-" if strand == -1 else "+",
                "description": _clean(note or name or f.type)[:120],
            }
            if len(segments) > 1:
                # 多段特征的分组键：同一 feature_id 的各段共享名称/类型/方向，
                # segment_index 为编码方向上的顺序（CDS 拼接与 UI 图例去重用）
                rec.update({
                    "feature_id": feature_id,
                    "segment_index": idx,
                    "segment_count": len(segments),
                })
            features.append(rec)
    features.sort(key=lambda x: (x["start"], x["end"]))
    return seq, features, circular


def _topology_flag(value) -> Optional[bool]:
    v = str(value or "").strip().lower()
    if v == "circular":
        return True
    if v == "linear":
        return False
    return None


def _from_fasta(data: bytes) -> Tuple[str, List[Dict], Optional[bool]]:
    from Bio import SeqIO

    text = data.decode("utf-8", errors="ignore")
    records = list(SeqIO.parse(io.StringIO(text), "fasta"))
    if not records:
        raise ReferenceParseError("FASTA 文件中没有序列")
    if len(records) > 1:
        raise ReferenceParseError(
            f"FASTA 含 {len(records)} 条序列，请只保留参考构建体对应的一条"
        )
    return str(records[0].seq).upper(), [], None


def _from_snapgene(data: bytes) -> Tuple[str, List[Dict], Optional[bool]]:
    try:
        from snapgene_reader import snapgene_file_to_dict
    except ImportError as e:
        raise ReferenceParseError("服务端未安装 snapgene-reader，无法解析 .dna 文件") from e

    fd, tmp = tempfile.mkstemp(suffix=".dna")
    os.close(fd)
    Path(tmp).write_bytes(data)
    try:
        d = snapgene_file_to_dict(tmp)
    finally:
        try:
            os.unlink(tmp)
        except PermissionError:
            pass

    dna = d.get("dna")
    circular = _topology_flag(dna.get("topology")) if isinstance(dna, dict) else None
    seq = (d.get("seq") or (dna if isinstance(dna, str) else "") or "").upper()
    if not seq:
        raise ReferenceParseError(".dna 文件中没有序列")
    features: List[Dict] = []
    for f in d.get("features") or []:
        try:
            # snapgene_reader 返回 0-based 半开区间 [start, end)：
            # start 比文件里的 1-based 坐标小 1，end 保持 1-based 含端点
            start, end = int(f["start"]) + 1, int(f["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start > end:
            start, end = end, start
        start, end = max(1, start), min(len(seq), end)
        if end - start + 1 < 6:
            continue
        name = _clean(f.get("name") or f.get("type") or "feature")
        strand_raw = str(f.get("strand"))
        features.append({
            "name": name[:40],
            "type": _clean(f.get("type") or "misc_feature")[:24],
            "start": start,
            "end": end,
            "strand": "-" if strand_raw in ("-", "-1") else "+",
            "description": name[:120],
        })
    features.sort(key=lambda x: (x["start"], x["end"]))
    return seq, features, circular
