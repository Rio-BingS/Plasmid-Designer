"""外部/在线序列 → 载体库 YAML（形状与 data/vectors/*.yaml 完全一致）

背景（本次修复）：上传/NCBI/批量导入走的 legacy `external_vector_importer` 写出的
features 只有 `{type,start,end}`，而 `VectorLibrary._parse_vector_dict` 要求每个
feature 有 `name` → 整份 YAML 加载抛 KeyError、被 load_from_directory 的兜底
except 吞掉，载体**静默消失**；`.dna` 上传也被当文本 GenBank 解析，得到空序列。
现在统一走 `core.plasmid_map.parsers`（GenBank/FASTA/SnapGene .dna 同一套解析），
并由本模块负责落盘形状，两个导入入口共用。
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

import yaml

from core.plasmid_map.parsers import ParsedRecord

# 落盘文件名白名单：仅保留字母/数字/点/下划线/连字符（防路径分隔符注入）
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_slug(name: str, fallback: str = "vector") -> str:
    """输出文件名/ID 用的安全片段（旧 vector_routes._safe_filename 的同名实现）"""
    cleaned = _SLUG_RE.sub("_", str(name or "")).strip("._")
    return cleaned[:80] or fallback


def make_vector_id(source: str, name: str) -> str:
    """载体 ID：`<source>_<安全名>`（与既有 ncbi_<acc> / genbank_<LOCUS> 惯例一致）"""
    return f"{safe_slug(source, 'imported')}_{safe_slug(name, 'vector')}"


def _strand_str(strand) -> str:
    """core 用 1/-1/0，载体 YAML 用 +/-（与 data/vectors/*.yaml 一致）"""
    try:
        return "-" if int(strand) == -1 else "+"
    except (TypeError, ValueError):
        return "-" if str(strand).strip() == "-" else "+"


def record_to_vector_dict(record: ParsedRecord, *, source: str, url: Optional[str] = None,
                          vector_id: Optional[str] = None,
                          description: Optional[str] = None,
                          attribution: Optional[str] = None) -> Dict:
    """ParsedRecord → 载体 YAML 字典（features/name/type/start/end/strand/description）"""
    features: List[Dict] = []
    dropped_segments = 0
    for f in record.features or []:
        start, end = f.get("start"), f.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if f.get("segments"):
            dropped_segments += 1
        features.append({
            "name": str(f.get("name") or f.get("type") or "feature")[:80],
            "type": str(f.get("type") or "other"),
            "start": start,
            "end": end,
            "strand": _strand_str(f.get("strand", 1)),
            "description": str(f.get("notes") or "")[:300],
        })
    warnings = list(record.warnings or [])
    if dropped_segments:
        warnings.append(f"{dropped_segments} 个跨原点复合特征只保留了整体跨度（载体库不支持分段）")

    desc_parts = [p for p in (description, attribution) if p]
    data: Dict = {
        "id": vector_id or make_vector_id(source, record.name),
        "name": record.name,
        "source": source,
        "sequence": record.sequence,
        "topology": record.topology,
        "description": "\n".join(desc_parts),
        "features": features,
        "host": [],
        "antibiotic": [],
        "copy_number": "medium",
        "tags": [],
    }
    if url:
        data["url"] = url
    data["_warnings"] = warnings
    return data


def write_vector_yaml(data: Dict, vectors_dir: str, *, filename: Optional[str] = None) -> str:
    """落盘为 `<vectors_dir>/<safe_slug(id)>.yaml`，返回路径。

    `_warnings` 只用于接口回执，不写进 YAML。
    """
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    os.makedirs(vectors_dir, exist_ok=True)
    path = os.path.join(vectors_dir, f"{safe_slug(filename or data['id'])}.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(payload, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return path


def save_record(record: ParsedRecord, *, source: str, vectors_dir: str,
                url: Optional[str] = None, vector_id: Optional[str] = None,
                description: Optional[str] = None,
                attribution: Optional[str] = None) -> Dict:
    """解析结果 → 载体库；返回接口回执（id/name/length/features/path/warnings）"""
    data = record_to_vector_dict(record, source=source, url=url, vector_id=vector_id,
                                 description=description, attribution=attribution)
    path = write_vector_yaml(data, vectors_dir)
    return {
        "id": data["id"],
        "name": data["name"],
        "length": len(data["sequence"]),
        "topology": data["topology"],
        "features": len(data["features"]),
        "source": source,
        "url": url or "",
        "path": path,
        "warnings": data.get("_warnings", []),
    }
