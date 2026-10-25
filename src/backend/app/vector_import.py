"""外部/在线序列 → 载体库 YAML（形状与 data/vectors/*.yaml 完全一致）

背景（本次修复）：上传/NCBI/批量导入走的 legacy `external_vector_importer` 写出的
features 只有 `{type,start,end}`，而 `VectorLibrary._parse_vector_dict` 要求每个
feature 有 `name` → 整份 YAML 加载抛 KeyError、被 load_from_directory 的兜底
except 吞掉，载体**静默消失**；`.dna` 上传也被当文本 GenBank 解析，得到空序列。
现在统一走 `core.plasmid_map.parsers`（GenBank/FASTA/SnapGene .dna 同一套解析），
并由本模块负责落盘形状，两个导入入口共用。

落盘形状的另外两条约定：

* `data_provenance`：出处结构化记录（来源/URL/许可/抓取时间 + 来源特有字段，
  形状与内置载体 YAML 一致，见 `provenance_block`）——描述里那句可读标注不能替代它
* `topology`：真实拓扑（线性片段不能被当环状画，见 `app/map_documents`）
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import yaml
from fastapi import HTTPException

from core.plasmid_map.parsers import ParsedRecord, clean_sequence
from core.vector_library import is_url_key, safe_http_url
from app.filename import sanitize_filename


class VectorIdConflict(HTTPException):
    """目标 YAML 已被**另一条**载体占用（文件名撞车）：409，不静默覆盖。"""

    def __init__(self, filename: str, existing_id: str, new_id: str):
        super().__init__(
            status_code=409,
            detail=(f"载体文件 {filename} 已被另一条载体（{existing_id}）占用，"
                    f"新载体 {new_id} 未保存；请换一个名称后重试"))


def now_iso() -> str:
    """出处记录用的抓取时间（UTC，秒级；与内置载体的 fetched_at 同字段名）"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def provenance_block(source: str, *, url: str = "", license: str = "",
                     **extra: object) -> Dict[str, str]:
    """统一出处块：形状与内置载体 `data_provenance` 一致（source/url/fetched_at）
    并带 `license` 与来源特有字段（set/plasmid/identifier/filename…），空值丢弃。
    """
    data: Dict[str, object] = {
        "source": source, "url": url, "fetched_at": now_iso(), "license": license,
    }
    data.update(extra)
    return {str(k): str(v) for k, v in data.items() if v not in (None, "")}



def safe_slug(name: str, fallback: str = "vector") -> str:
    """输出文件名/ID 用的安全片段（统一实现见 app/filename.py，ASCII 白名单）"""
    return sanitize_filename(name, allow_cjk=False, fallback=fallback)


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
                          attribution: Optional[str] = None,
                          provenance: Optional[Dict[str, str]] = None) -> Dict:
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

    # 出处 URL 只收 http(s)（审查 M5）：非法值直接丢弃，不落盘
    url = safe_http_url(url) or None
    desc_parts = [p for p in (description, attribution) if p]
    data: Dict = {
        # ID 与文件名同一套归一（safe_slug）：此前 ID 保留空格/冒号而文件名已归一，
        # 两者不一致，不同 ID 还会撞到同一文件（审查 L2）
        "id": safe_slug(vector_id) if vector_id else make_vector_id(source, record.name),
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
    if provenance:
        # 结构化出处（界面展示 + 日后重取依据）；缺 url 时用参数补，避免两处不一致
        prov = {str(k): str(v) for k, v in provenance.items() if v not in (None, "")}
        prov = {k: v for k, v in prov.items() if not is_url_key(k) or safe_http_url(v)}
        if url and not prov.get("url"):
            prov["url"] = url
        prov.setdefault("fetched_at", now_iso())
        data["data_provenance"] = prov
    data["_warnings"] = warnings
    return data


def record_from_parts(*, name: str, sequence: str, topology: str = "circular",
                      features: Optional[List[Dict]] = None,
                      source_format: str = "vector",
                      max_length: Optional[int] = None) -> ParsedRecord:
    """已解析好的序列 + 特征 → ParsedRecord（图谱页「另存为载体」等复用）。

    序列经 `clean_sequence`（去空白/数字、大写、U→T、非法字符 422、超长 413），
    特征只保留 start/end 为整数的条目，跨原点复合特征按整体跨度收下。
    """
    seq = clean_sequence(sequence, max_length)
    feats: List[Dict] = []
    dropped = 0
    for f in features or []:
        start, end = f.get("start"), f.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < 1:
            continue
        if f.get("segments"):
            dropped += 1
        feats.append({
            "name": str(f.get("name") or f.get("type") or "feature")[:80],
            "type": str(f.get("type") or "misc_feature"),
            "start": start,
            "end": end,
            "strand": f.get("strand", 0),
            "notes": str(f.get("notes") or f.get("description") or "")[:300],
            "segments": f.get("segments"),
            "color": f.get("color"),
        })
    warns = ["序列由图谱页保存（可编辑注释与酶切结果不随载体库保存）"]
    if dropped:
        warns.append(f"{dropped} 个跨原点复合特征只保留了整体跨度（载体库不支持分段）")
    return ParsedRecord(
        name=str(name or "sequence")[:80],
        topology="linear" if str(topology).lower() == "linear" else "circular",
        sequence=seq,
        source_format=source_format,
        features=feats,
        warnings=warns,
    )


def vector_yaml_path(data: Dict, vectors_dir: str, *, filename: Optional[str] = None) -> str:
    return os.path.join(vectors_dir, f"{safe_slug(filename or data['id'])}.yaml")


def _existing_id(path: str) -> Optional[str]:
    """已存在的 YAML 里的载体 id（读不出来时返回 None，按「别人的文件」处理）"""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None
    if isinstance(doc, dict) and doc.get("id") is not None:
        return str(doc.get("id"))
    return None


def write_vector_yaml(data: Dict, vectors_dir: str, *, filename: Optional[str] = None) -> str:
    """落盘为 `<vectors_dir>/<safe_slug(id)>.yaml`，返回路径。

    同名文件已存在时：若其中是**同一个 id**（再次导入/更新同一条载体）则覆盖；
    若是另一条载体（或读不出 id）则抛 `VectorIdConflict`（409），不静默覆盖。
    `_warnings` 只用于接口回执，不写进 YAML。
    """
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    os.makedirs(vectors_dir, exist_ok=True)
    path = vector_yaml_path(data, vectors_dir, filename=filename)
    if os.path.exists(path):
        existing = _existing_id(path)
        if existing != str(data["id"]):
            raise VectorIdConflict(os.path.basename(path), existing or "未知", str(data["id"]))
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(payload, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return path


def save_record(record: ParsedRecord, *, source: str, vectors_dir: str,
                url: Optional[str] = None, vector_id: Optional[str] = None,
                description: Optional[str] = None,
                attribution: Optional[str] = None,
                provenance: Optional[Dict[str, str]] = None) -> Dict:
    """解析结果 → 载体库；返回接口回执（id/name/length/features/replaced/warnings）"""
    data = record_to_vector_dict(record, source=source, url=url, vector_id=vector_id,
                                 description=description, attribution=attribution,
                                 provenance=provenance)
    replaced = os.path.exists(vector_yaml_path(data, vectors_dir))
    write_vector_yaml(data, vectors_dir)
    # 覆盖同 id 载体时明确告知（审查 L2）；回执不带服务器上的绝对路径（审查 L5）
    warnings = list(data.get("_warnings", []))
    if replaced:
        warnings.append(f"已覆盖载体库中同 ID 的载体 {data['id']}")
    return {
        "id": data["id"],
        "name": data["name"],
        "length": len(data["sequence"]),
        "topology": data["topology"],
        "features": len(data["features"]),
        "source": source,
        "url": data.get("url", ""),
        "replaced": replaced,
        "provenance": data.get("data_provenance", {}),
        "warnings": warnings,
    }
