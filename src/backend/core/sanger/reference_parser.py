"""测序分析参考序列文件解析

参考序列随 .ab1 一起上传（用户把图谱文件与测序文件放同一文件夹导入）。
支持三种格式，统一返回 (序列, 特征列表)：
- GenBank (.gb/.gbk/.genbank)：Biopython 解析，特征含名称/类型/坐标/方向
- FASTA (.fasta/.fa/.fna)：仅序列，无特征
- SnapGene (.dna)：snapgene-reader 解析（包缺失时报可读错误）
"""

import io
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

GENBANK_EXTS = {"gb", "gbk", "genbank"}
FASTA_EXTS = {"fasta", "fa", "fna"}
SNAPGENE_EXTS = {"dna"}
SUPPORTED_EXTS = GENBANK_EXTS | FASTA_EXTS | SNAPGENE_EXTS


class ReferenceParseError(ValueError):
    """参考文件无法解析（消息面向终端用户）"""


def parse_reference(filename: str, data: bytes) -> Tuple[str, List[Dict]]:
    """按扩展名分发解析。失败抛 ReferenceParseError（消息可直接展示）。"""
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


def _from_genbank(data: bytes) -> Tuple[str, List[Dict]]:
    from Bio import SeqIO

    text = data.decode("utf-8", errors="ignore")
    records = list(SeqIO.parse(io.StringIO(text), "genbank"))
    if not records:
        raise ReferenceParseError("GenBank 文件中没有记录")
    record = records[0]
    seq = str(record.seq).upper()
    features: List[Dict] = []
    for f in record.features:
        if f.type == "source":
            continue
        try:
            # 复合位置（join/complement 多段）取整体跨度，足够用于差异注释
            start = int(f.location.start) + 1
            end = int(f.location.end)
        except (TypeError, ValueError):
            continue
        if start > end or end < 1 or start > len(seq):
            continue
        start, end = max(1, start), min(len(seq), end)
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
        features.append({
            "name": _clean(name or f.type)[:40],
            "type": _clean(f.type)[:24],
            "start": start,
            "end": end,
            "strand": "-" if f.location.strand == -1 else "+",
            "description": _clean(note or name or f.type)[:120],
        })
    features.sort(key=lambda x: (x["start"], x["end"]))
    return seq, features


def _from_fasta(data: bytes) -> Tuple[str, List[Dict]]:
    from Bio import SeqIO

    text = data.decode("utf-8", errors="ignore")
    records = list(SeqIO.parse(io.StringIO(text), "fasta"))
    if not records:
        raise ReferenceParseError("FASTA 文件中没有序列")
    if len(records) > 1:
        raise ReferenceParseError(
            f"FASTA 含 {len(records)} 条序列，请只保留参考构建体对应的一条"
        )
    return str(records[0].seq).upper(), []


def _from_snapgene(data: bytes) -> Tuple[str, List[Dict]]:
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

    seq = (d.get("seq") or d.get("dna") or "").upper()
    if not seq:
        raise ReferenceParseError(".dna 文件中没有序列")
    features: List[Dict] = []
    for f in d.get("features") or []:
        try:
            start, end = int(f["start"]), int(f["end"])
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
    return seq, features
