"""组装契约 MapDocument（plasmid-map/1）

所有入口（文件解析、编辑后重算、设计结果、载体库）都汇到 build_document：
校验/清洗特征 → 可选自动注释（去掉与已有特征重复的命中）→ 酶切位点 →
ORF → 分配稳定 id。纯函数，重计算由路由层放进线程池并缓存。
"""

from typing import Dict, Iterable, List, Optional

import core.plasmid_map.annotate as annotate_mod
from core.plasmid_map.annotate import annotate as run_annotate
from core.plasmid_map.annotate import circular_overlap
from core.plasmid_map.enzymes import find_map_enzymes, resolve_enzymes
from core.plasmid_map.orfs import DEFAULT_MIN_AA, find_orfs
from core.plasmid_map.parsers import ParsedRecord, extract_span
from core.plasmid_map.types import (
    FEATURE_TYPE_SET, MAX_FEATURES, MAX_ORFS, MAX_PRIMER_SEQ, MAX_PRIMERS, MAX_QUALIFIERS,
    MAX_SEGMENTS, MAX_WARNINGS, SCHEMA, normalize_color,
)

# 自动注释命中与已有特征（同类型或同名）重叠超过较短者此比例时视为重复
EXISTING_DUP_OVERLAP = 0.8


def _span_len(start: int, end: int, length: int) -> int:
    return end - start + 1 if start <= end else (length - start + 1) + end


def sanitize_features(features: Iterable[Dict], length: int, circular: bool,
                      warnings: List[str]) -> List[Dict]:
    """丢弃坐标非法的特征（告警计数），未知类型降为 misc_feature"""
    out, bad = [], 0
    for f in features:
        try:
            start, end = int(f["start"]), int(f["end"])
        except (KeyError, TypeError, ValueError):
            bad += 1
            continue
        if not (1 <= start <= length and 1 <= end <= length):
            bad += 1
            continue
        if start > end and not circular:
            bad += 1
            continue
        segs = f.get("segments") or None
        if segs:
            ok = all(1 <= int(s["start"]) <= length and 1 <= int(s["end"]) <= length and
                     (int(s["start"]) <= int(s["end"]) or circular) for s in segs)
            if not ok:
                bad += 1
                continue
            if len(segs) > MAX_SEGMENTS:
                bad += 1
                continue
            segs = [{"start": int(s["start"]), "end": int(s["end"])} for s in segs]
            if len(segs) == 1:
                segs = None
        g = dict(f)
        g["start"], g["end"] = start, end
        g["strand"] = int(f.get("strand") or 0) if int(f.get("strand") or 0) in (1, -1) else 0
        g["type"] = f.get("type") if f.get("type") in FEATURE_TYPE_SET else "misc_feature"
        g["name"] = str(f.get("name") or g["type"])[:80]
        g["source"] = f.get("source") if f.get("source") in ("file", "auto", "design", "vector") \
            else "file"
        if segs:
            g["segments"] = segs
        else:
            g.pop("segments", None)
        color = normalize_color(f.get("color")) if f.get("color") else None
        if color:
            g["color"] = color
        else:
            g.pop("color", None)
        quals = g.get("qualifiers")
        if isinstance(quals, dict) and len(quals) > MAX_QUALIFIERS:
            g["qualifiers"] = dict(list(quals.items())[:MAX_QUALIFIERS])
        for k in ("notes", "qualifiers", "auto", "id"):
            if g.get(k) in (None, "", {}):
                g.pop(k, None)
        if g["source"] != "auto":
            g.pop("auto", None)
        out.append(g)
        if len(out) >= MAX_FEATURES:
            warnings.append(f"特征超过 {MAX_FEATURES} 个，其余已忽略")
            break
    if bad:
        warnings.append(f"{bad} 个特征坐标无效，已忽略")
    return out


def _drop_duplicate_auto(auto: List[Dict], existing: List[Dict], length: int,
                         circular: bool) -> List[Dict]:
    keep = []
    for a in auto:
        alen = _span_len(a["start"], a["end"], length)
        dup = False
        for f in existing:
            if f["type"] != a["type"] and f["name"].lower() != a["name"].lower():
                continue
            flen = _span_len(f["start"], f["end"], length)
            ov = circular_overlap(a["start"] - 1, alen, f["start"] - 1, flen, length, circular)
            if ov >= EXISTING_DUP_OVERLAP * min(alen, flen):
                dup = True
                break
        if not dup:
            keep.append(a)
    return keep


def _assign_ids(features: List[Dict]) -> None:
    """保留调用方给的合法唯一 id（编辑后重算时前端选中状态不丢），其余按
    「来源-序号」补齐；重复 id 只有第一个保留"""
    claimed = set()
    pending = []
    for f in features:
        fid = f.get("id")
        if isinstance(fid, str) and 0 < len(fid) <= 64 and fid not in claimed:
            claimed.add(fid)
        else:
            pending.append(f)
    counters: Dict[str, int] = {}
    for f in pending:
        prefix = f.get("source") or "f"
        while True:
            counters[prefix] = counters.get(prefix, 0) + 1
            cand = f"{prefix}-{counters[prefix]}"
            if cand not in claimed:
                break
        f["id"] = cand
        claimed.add(cand)


def build_document(
    *,
    name: str,
    sequence: str,
    topology: str,
    source_format: str,
    features: Optional[List[Dict]] = None,
    primers: Optional[List[Dict]] = None,
    warnings: Optional[List[str]] = None,
    annotate: Optional[bool] = None,
    enzyme_set: str = "common",
    enzymes: Optional[List[str]] = None,
    orf_min_aa: int = DEFAULT_MIN_AA,
) -> Dict:
    """sequence 须已清洗（大写 IUPAC）；annotate=None 时「无特征且不超过
    AUTO_ANNOTATE_MAX_BP 才注释」，显式 True 总是注释（受比对预算约束）"""
    warns = list(warnings or [])
    length = len(sequence)
    circular = topology == "circular"
    feats = sanitize_features(features or [], length, circular, warns)

    if annotate is None:
        annotate = not any(f["source"] != "auto" for f in feats)
        if annotate and length > annotate_mod.AUTO_ANNOTATE_MAX_BP:
            annotate = False
            warns.append(f"序列超过 {annotate_mod.AUTO_ANNOTATE_MAX_BP:,} bp，未自动注释常见元件"
                         "（可显式开启 annotate）")
    if annotate:
        base = [f for f in feats if f["source"] != "auto"]
        auto = run_annotate(sequence, circular, warnings=warns)
        feats = base + _drop_duplicate_auto(auto, base, length, circular)

    names, ewarns = resolve_enzymes(enzyme_set, enzymes)
    warns.extend(ewarns)
    enz = find_map_enzymes(sequence, names, circular, warnings=warns)
    orfs = find_orfs(sequence, circular, orf_min_aa)
    if len(orfs) > MAX_ORFS:
        warns.append(f"ORF 超过 {MAX_ORFS} 个（最短 {orf_min_aa} aa），只返回前 {MAX_ORFS} 个；"
                     "可调高最短长度")
        del orfs[MAX_ORFS:]

    feats.sort(key=lambda f: (f["start"], -_span_len(f["start"], f["end"], length)))
    _assign_ids(feats)

    prims = []
    for i, p in enumerate(primers or [], 1):
        if len(prims) >= MAX_PRIMERS:
            warns.append(f"引物超过 {MAX_PRIMERS} 条，其余已忽略")
            break
        try:
            start, end = int(p["start"]), int(p["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (1 <= start <= length and 1 <= end <= length) or (start > end and not circular):
            continue
        strand = -1 if int(p.get("strand") or 1) == -1 else 1
        if _span_len(start, end, length) > MAX_PRIMER_SEQ:
            continue
        full_seq = str(p.get("full_sequence") or "").upper().strip()
        prims.append({
            "id": str(p.get("id") or f"primer-{i}")[:64],
            "name": str(p.get("name") or f"primer {i}")[:80],
            "start": start, "end": end, "strand": strand,
            "sequence": str(p.get("sequence") or extract_span(sequence, start, end, strand)).upper(),
            # 完整引物序列（含 5′ 突出）：序列视图 SnapGene 式名牌框需要画出
            # 悬在结合区外的突出部分；缺省与 sequence 等长（无突出）
            **({"full_sequence": full_seq} if full_seq else {}),
        })

    return {
        "schema": SCHEMA,
        "name": (name or "sequence")[:120],
        "topology": "circular" if circular else "linear",
        "length": length,
        "sequence": sequence,
        "features": feats,
        "enzymes": enz,
        "orfs": orfs,
        "primers": prims,
        "meta": {
            "source_format": source_format,
            "enzyme_set": "custom" if enzymes else enzyme_set,
            "warnings": warns[:MAX_WARNINGS],
        },
    }


def document_from_record(rec: ParsedRecord, **opts) -> Dict:
    return build_document(
        name=rec.name, sequence=rec.sequence, topology=rec.topology,
        source_format=rec.source_format, features=rec.features, primers=rec.primers,
        warnings=rec.warnings, **opts,
    )
