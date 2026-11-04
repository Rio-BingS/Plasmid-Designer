"""载体演示数据管线：从权威公开源拉取真实序列与注释，重写 data/vectors/*.yaml。

数据源优先级：
1. SnapGene Plasmid Library（官方整理的商业载体完整注释，免登录直链下载）
   端点: https://www.snapgene.com/local/fetch.php?set=<setID>&plasmid=<plasmidID>
   解析: snapgene-reader（.dna 二进制格式）
2. NCBI GenBank（efetch，用于有正式 GenBank 记录的载体，如 pUC19/pGEX-6P-1）

每个 YAML 写入 data_provenance 血统块（来源/地址/抓取时间），并做完整性自检：
- 序列仅含 ACGTN 且长度合理
- 特征坐标在 1..len(sequence) 内、类型合法
- mcs 位点识别序列能在新序列中命中

用法（后端 venv）：
    python scripts/fetch_vector_sequences.py [data/vectors]
"""
import io
import re
import sys
import time
import shutil
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "backend"))

SNAPGENE_BASE = "https://www.snapgene.com"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# YAML id base → (setID, plasmidID, 备注)
SNAPGENE_SOURCES = {
    "pET-28a": ("pet_and_duet_vectors_(novagen)", "pET-28a(%2B)", ""),
    "pET-21a": ("pet_and_duet_vectors_(novagen)", "pET-21a(%2B)", ""),
    "pGEX-4T-1": ("pgex_vectors_(ge_healthcare)", "pGEX-4T-1", ""),
    "pcDNA3.1": ("mammalian_expression_vectors", "pcDNA3.1(%2B)", ""),
    "pFastBac1": ("insect_cell_vectors", "pFastBac1", ""),
    "pYES2": ("yeast_plasmids", "pYES2", ""),
    # SnapGene 无通用 pLVX 空骨架，取最常用的 IRES-Puro 配置
    "pLVX": ("viral_expression_and_packaging_vectors", "pLVX-IRES-Puro",
             "通用 pLVX 无官方空骨架记录，采用 pLVX-IRES-Puro 配置"),
}

# 有正式 GenBank 记录的载体 → 登录号
NCBI_ACCESSIONS = {
    "pUC19": "L09137",
    "pGEX-6P-1": "U78872",
}

# NCBI 记录注释极简的载体：序列取权威源，特征保留 YAML 既有注释（需按基序核验）
KEEP_EXISTING_FEATURES = {"pUC19"}

VALID_TYPES = {
    "promoter", "terminator", "origin", "resistance", "tag",
    "CDS", "gene", "multiple_cloning_site", "regulatory", "other",
}


# ==================== SnapGene 源 ====================
def fetch_snapgene(set_id: str, plasmid_id: str) -> tuple:
    """下载并解析 .dna，返回 (sequence, features_raw)"""
    from snapgene_reader import snapgene_file_to_dict
    # set/plasmid 已是 URL 编码形式（如 pET-28a(%2B)），不要二次转义
    url = f"{SNAPGENE_BASE}/local/fetch.php?set={set_id}&plasmid={plasmid_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "plasmid-designer-data-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        blob = r.read()
    import tempfile, os
    fd, tmp = tempfile.mkstemp(suffix=".dna")
    os.close(fd)
    Path(tmp).write_bytes(blob)
    try:
        d = snapgene_file_to_dict(tmp)
    finally:
        try:
            os.unlink(tmp)
        except PermissionError:
            pass
    seq = (d.get("seq") or d.get("dna") or "").upper()
    return seq, d.get("features") or []


def classify_sg(f: dict) -> str:
    ftype = (f.get("type") or "").lower()
    name = (f.get("name") or "").lower()
    if "multiple cloning" in name or name == "mcs" or "mcs" in name.split():
        return "multiple_cloning_site"
    if ftype == "promoter":
        return "promoter"
    if ftype == "terminator":
        return "terminator"
    if ftype == "rep_origin" or "origin" in name:
        return "origin"
    if ftype == "cds":
        if any(k in name for k in ("his", "tag", "gst", "mbp", "flag", "ha", "myc")):
            return "tag"
        if any(k in name for k in ("kan", "amp", "bla", "puro", "hyg", "neo", "tet", "resistance", "aph")):
            return "resistance"
        return "CDS"
    if ftype == "gene":
        return "gene"
    if ftype in ("rbs", "protein_bind", "misc_binding", "regulatory"):
        return "regulatory"
    return "other"


def snapgene_features(raw: list, L: int) -> list:
    out = []
    for f in raw:
        name = (f.get("name") or f.get("type") or "feature").strip()
        ftype = classify_sg(f)
        try:
            start, end = int(f["start"]), int(f["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start > end:
            start, end = end, start
        start, end = max(1, start), min(L, end)
        if end - start + 1 < 6:
            continue
        strand = "-" if f.get("strand") == "-" else "+"
        quals = f.get("qualifiers") or {}
        note = ""
        for q in ("note", "product"):
            v = quals.get(q)
            if v:
                note = (v[0] if isinstance(v, list) else v)
                break
        out.append({
            "name": re.sub(r"\s+", " ", name)[:36],
            "type": ftype,
            "start": start,
            "end": end,
            "strand": strand,
            "description": re.sub(r"\s+", " ", note)[:120] if note else name,
        })
    out.sort(key=lambda x: (x["start"], x["end"]))
    seen, dedup = set(), []
    for f in out:
        key = (f["name"], f["start"], f["end"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(f)
    return dedup


# ==================== NCBI 源 ====================
def fetch_ncbi(accession: str) -> tuple:
    from Bio import SeqIO
    url = f"{EUTILS}/efetch.fcgi?db=nucleotide&id={accession}&rettype=gb&retmode=text"
    with urllib.request.urlopen(url, timeout=60) as r:
        text = r.read().decode("utf-8", errors="replace")
    rec = next(SeqIO.parse(io.StringIO(text), "genbank"), None)
    if rec is None:
        raise ValueError(f"NCBI 记录解析失败: {accession}")
    return str(rec.seq).upper(), rec


def ncbi_features(rec, L: int) -> list:
    out = []
    for f in rec.features:
        if f.type == "source":
            continue
        name = ""
        for q in ("label", "gene", "product", "standard_name", "note"):
            if f.qualifiers.get(q):
                name = f.qualifiers[q][0]
                break
        if not name:
            name = f.type
        n = name.lower()
        if f.type == "promoter" or "promoter" in n:
            ftype = "promoter"
        elif f.type == "terminator" or "terminator" in n:
            ftype = "terminator"
        elif f.type == "rep_origin":
            ftype = "origin"
        elif any(k in n for k in ("resistance", "kanr", "ampr", "bla", "aph", "tet")):
            ftype = "resistance"
        elif f.type == "CDS":
            ftype = "CDS"
        elif f.type == "gene":
            ftype = "gene"
        elif "multiple cloning site" in n or "mcs" in n.split():
            ftype = "multiple_cloning_site"
        elif f.type in ("protein_bind", "misc_binding", "regulatory"):
            ftype = "regulatory"
        else:
            ftype = "other"
        start = int(f.location.start) + 1
        end = int(f.location.end)
        strand = "-" if f.location.strand == -1 else "+"
        if start > end:
            start, end = end, start
        start, end = max(1, start), min(L, end)
        if end - start + 1 < 6:
            continue
        out.append({
            "name": re.sub(r"\s+", " ", name)[:36],
            "type": ftype,
            "start": start,
            "end": end,
            "strand": strand,
            "description": re.sub(r"\s+", " ", f.qualifiers.get("note", [name])[0])[:120],
        })
    out.sort(key=lambda x: (x["start"], x["end"]))
    seen, dedup = set(), []
    for f in out:
        key = (f["name"], f["start"], f["end"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(f)
    return dedup


# ==================== 公共处理 ====================
def relocate_mcs(mcs: dict, seq: str) -> dict:
    """按新序列重定位 MCS 位点坐标；找不到的保留原值。"""
    iupac = {"R": "[AG]", "Y": "[CT]", "S": "[GC]", "W": "[AT]", "K": "[GT]",
             "M": "[AC]", "B": "[CGT]", "D": "[AGT]", "H": "[ACT]", "V": "[ACG]", "N": "[ACGT]"}
    sites = mcs.get("sites", [])
    positions = []
    for s in sites:
        rec = (s.get("sequence") or "").upper().replace(" ", "")
        if rec and all(c in "ATGC" for c in rec):
            pat = "".join(iupac.get(c, c) for c in rec)
            m = re.search(pat, seq)
            if m:
                s["position"] = m.start() + 1
                positions.append((m.start() + 1, m.start() + len(rec)))
                continue
        positions.append((s.get("position", 0), s.get("position", 0) + len(rec)))
    hits = [p for p in positions if p[0] > 0]
    if hits:
        mcs["start"] = min(p[0] for p in hits)
        mcs["end"] = max(p[1] for p in hits)
    return mcs


def validate(seq: str, features: list, mcs: dict | None) -> list:
    """完整性自检，返回问题列表（空列表 = 通过）"""
    issues = []
    if not seq or len(seq) < 500:
        issues.append(f"序列过短: {len(seq)} bp")
    bad = set(seq) - set("ACGTN")
    if bad:
        issues.append(f"非法碱基: {bad}")
    for f in features:
        if f["type"] not in VALID_TYPES:
            issues.append(f"非法类型 {f['type']} @ {f['name']}")
        if not (1 <= f["start"] <= f["end"] <= len(seq)):
            issues.append(f"坐标越界 {f['name']} {f['start']}-{f['end']}")
    if len(features) < 3:
        issues.append(f"特征过少: {len(features)}")
    if mcs and mcs.get("sites"):
        hit = sum(1 for s in mcs["sites"]
                  if (s.get("sequence") or "").upper().replace(" ", "") in seq)
        if hit == 0:
            issues.append("mcs 位点无一命中序列")
    return issues


def process(path: Path) -> bool:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data.get("sequence"):
        print(f"  跳过（已有 sequence）: {path.name}")
        return False
    base = str(data["id"])
    seq, features, prov = None, None, None

    if base in SNAPGENE_SOURCES:
        set_id, pid, note = SNAPGENE_SOURCES[base]
        print(f"[{path.name}] SnapGene: set={set_id} plasmid={pid} ...", flush=True)
        seq, raw = fetch_snapgene(set_id, pid)
        features = snapgene_features(raw, len(seq))
        url = f"{SNAPGENE_BASE}/plasmids/{set_id}/{pid}"
        prov = {"source": "SnapGene Plasmid Library", "set": set_id, "plasmid": pid,
                "url": url, "fetched_at": date.today().isoformat()}
        if note:
            prov["note"] = note
    elif base in NCBI_ACCESSIONS:
        acc = NCBI_ACCESSIONS[base]
        print(f"[{path.name}] NCBI: {acc} ...", flush=True)
        seq, rec = fetch_ncbi(acc)
        if base in KEEP_EXISTING_FEATURES:
            # NCBI 记录无注释（仅 source），保留 YAML 既有特征（按序列基序核验过坐标）
            features = data.get("features") or []
            prov = {"source": "NCBI GenBank（序列）；注释为标准特征（基序核验坐标）",
                    "accession": acc,
                    "url": f"https://www.ncbi.nlm.nih.gov/nuccore/{acc}",
                    "fetched_at": date.today().isoformat()}
        else:
            features = ncbi_features(rec, len(seq))
            prov = {"source": "NCBI GenBank", "accession": acc,
                    "url": f"https://www.ncbi.nlm.nih.gov/nuccore/{acc}",
                    "fetched_at": date.today().isoformat()}
    else:
        print(f"[{path.name}] 无已知数据源，跳过")
        return False

    if isinstance(data.get("mcs"), dict):
        data["mcs"] = relocate_mcs(data["mcs"], seq)

    issues = validate(seq, features, data.get("mcs"))
    if issues:
        print(f"  !! 自检未通过，放弃写入: {'; '.join(issues)}")
        return False

    data["sequence"] = seq
    data["features"] = features
    data["data_provenance"] = prov

    shutil.copy(path, str(path) + ".bak")
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False, width=70)
    print(f"  ✓ {len(seq)} bp, {len(features)} features（自检通过）")
    return True


def main():
    vec_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/vectors")
    files = sorted(vec_dir.glob("*.yaml"))
    ok = 0
    for f in files:
        try:
            ok += bool(process(f))
        except Exception as e:
            print(f"  !! {f.name}: {type(e).__name__} {e}")
        time.sleep(0.4)
    print(f"完成: {ok}/{len(files)} 个已更新")


if __name__ == "__main__":
    main()
