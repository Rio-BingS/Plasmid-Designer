"""MapDocument → GenBank 文本（POST /api/map/export/genbank）

用 Biopython 的写出器保证列格式规范（重新读入不触发 BiopythonParserWarning）。
颜色写成 /ApEinfo_fwdcolor + /ApEinfo_revcolor（ApE/SnapGene/Benchling 通用），
自动注释特征附 /note 说明来源库与 identity/coverage。原始文件里的 GenBank
特征键（qualifiers.genbank_type）在类型未被改动时原样写回，保证往返一致。
"""

import datetime
import io
import re
from typing import Dict, List, Optional

from core.plasmid_map.types import normalize_color, normalize_type

# FeatureType → GenBank 特征键（契约类型未被用户改动时优先用原始键）
_TYPE_TO_KEY = {
    "cds": "CDS",
    "marker": "CDS",
    "tag": "CDS",
    "promoter": "promoter",
    "terminator": "terminator",
    "rep_origin": "rep_origin",
    "rbs": "RBS",
    "signal_peptide": "sig_peptide",
    "polyA_signal": "polyA_signal",
    "enhancer": "enhancer",
    "ltr": "LTR",
    "primer_bind": "primer_bind",
    "protein_bind": "protein_bind",
    "misc_rna": "misc_RNA",
    "misc_feature": "misc_feature",
    "insert": "misc_feature",
    "mcs": "misc_feature",
}
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_INTERNAL_QUALIFIERS = {"genbank_type", "library_feature", "library_version"}
_LOCUS_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _location(start: int, end: int, strand: int, length: int,
              segments: Optional[List[Dict]]):
    """契约坐标 → Biopython 位置。

    segments 按生物学顺序（负链即沿负链 5'→3'，坐标降序）；跨原点的段在
    正链拆成 [s..L, 1..e]，负链拆成 [1..e, s..L]（负链先读到 1..e 的反向
    互补）。Biopython 把全负链复合位置的 parts 当生物学顺序，写出
    complement(join(s..L,1..e))，重新读入后 extract 与契约 extract_feature 一致。
    """
    from Bio.SeqFeature import CompoundLocation, SimpleLocation

    st = strand if strand in (1, -1) else None
    spans = [(s["start"], s["end"]) for s in segments] if segments else [(start, end)]
    parts = []
    for s, e in spans:
        if s <= e:
            parts.append(SimpleLocation(s - 1, e, strand=st))
        elif st == -1:  # 负链跨原点：生物学顺序先 1..e 再 s..L
            parts.append(SimpleLocation(0, e, strand=st))
            parts.append(SimpleLocation(s - 1, length, strand=st))
        else:  # 跨原点 → join(s..L, 1..e)
            parts.append(SimpleLocation(s - 1, length, strand=st))
            parts.append(SimpleLocation(0, e, strand=st))
    if len(parts) == 1:
        return parts[0]
    return CompoundLocation(parts)


def _feature_key(feat: Dict) -> str:
    quals = feat.get("qualifiers") or {}
    orig = quals.get("genbank_type")
    if orig and orig != "source":
        reparsed = normalize_type(orig, feat.get("name") or "",
                                  quals.get("regulatory_class"),
                                  " ".join(filter(None, (quals.get("gene"), quals.get("product"),
                                                         quals.get("note")))))
        if reparsed == feat.get("type"):
            return orig[:15]
    return _TYPE_TO_KEY.get(feat.get("type") or "", "misc_feature")


def _qualifiers(feat: Dict) -> Dict[str, List[str]]:
    q: Dict[str, List[str]] = {}
    q["label"] = [feat.get("name") or "feature"]
    for k, v in (feat.get("qualifiers") or {}).items():
        if k in _INTERNAL_QUALIFIERS or k in ("label",):
            continue
        if k.startswith("ApEinfo_") or v is None:
            continue
        q[k] = [str(v)]
    notes = feat.get("notes")
    if notes and "note" not in q:
        q["note"] = [notes]
    auto = feat.get("auto")
    if feat.get("source") == "auto" and auto:
        msg = (f"auto-annotated from {auto.get('library', 'common')} feature library: "
               f"identity {auto.get('identity')}, coverage {auto.get('coverage')}"
               + (" (fragment)" if auto.get("fragment") else ""))
        q.setdefault("note", []).append(msg)
    color = normalize_color(feat.get("color"))
    if color:
        q["ApEinfo_fwdcolor"] = [color]
        q["ApEinfo_revcolor"] = [color]
    return q


def document_to_genbank(doc: Dict) -> str:
    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqFeature import SeqFeature
    from Bio.SeqRecord import SeqRecord

    seq = doc["sequence"]
    length = len(seq)
    name = (doc.get("name") or "sequence").strip() or "sequence"
    locus = (_LOCUS_SAFE.sub("_", name).strip("_") or "sequence")[:16]
    rec = SeqRecord(Seq(seq), id=locus, name=locus, description=name[:200])
    rec.annotations["molecule_type"] = "DNA"
    rec.annotations["topology"] = "circular" if doc.get("topology") == "circular" else "linear"
    rec.annotations["data_file_division"] = "SYN"
    today = datetime.date.today()
    rec.annotations["date"] = f"{today.day:02d}-{_MONTHS[today.month - 1]}-{today.year}"
    for feat in doc.get("features") or []:
        loc = _location(feat["start"], feat["end"], feat.get("strand", 0), length,
                        feat.get("segments"))
        rec.features.append(SeqFeature(loc, type=_feature_key(feat),
                                       qualifiers=_qualifiers(feat)))
    for p in doc.get("primers") or []:
        loc = _location(p["start"], p["end"], p.get("strand", 1), length, None)
        q = {"label": [p.get("name") or "primer"]}
        if p.get("sequence"):
            q["note"] = [f"sequence: {p['sequence']}"]
        rec.features.append(SeqFeature(loc, type="primer_bind", qualifiers=q))
    buf = io.StringIO()
    SeqIO.write(rec, buf, "genbank")
    return buf.getvalue()
