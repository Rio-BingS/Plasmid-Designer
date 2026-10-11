#!/usr/bin/env python3
"""重建图谱自动注释用的常见元件库 data/features/common_features.json

序列只取自可核验的来源，不手写任何序列：
1. 仓库自带的载体库 data/vectors/*.yaml（按其特征坐标从载体序列截取，
   来源血统见各 YAML 的 data_provenance）；
2. NCBI GenBank 公开记录（efetch 下载，记录 accession.version 与坐标）。

每个条目都过校验：蛋白编码元件必须是完整 ORF（或融合标签无内部终止），
短肽标签/蛋白酶位点必须在来源区段的翻译里原样出现，期望序列（如 T7
启动子）必须与截取结果一致——任何一项不符脚本直接失败，不会悄悄写入。

用法（仓库根目录）：
    python scripts/build_feature_library.py              # 联网抓 NCBI 并重写库
    python scripts/build_feature_library.py --cache DIR  # GenBank 缓存目录（可复用）
    python scripts/build_feature_library.py --check      # 只校验，不写文件

扩展：在 VECTOR_SPECS / NCBI_SPECS / PEPTIDE_SPECS 末尾追加一行（来源、坐标、
名称、契约 FeatureType、校验方式），重跑脚本并提交生成的 JSON。详见
data/features/README.md。
"""

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import yaml
from Bio import SeqIO
from Bio.Seq import Seq

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "data" / "vectors"
OUT = ROOT / "data" / "features" / "common_features.json"
EFETCH = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
          "?db=nuccore&id={acc}&rettype=gbwithparts&retmode=text")

# ---------------------------------------------------------------------------
# 规格表
# check: "orf"    完整 ORF（ATG/GTG/TTG 起始、末尾终止、无内部终止、长度 3 的倍数）
#        "fusion" 融合标签（ATG 起始、无终止、长度 3 的倍数）
#        "seq:XXX" 截取结果必须等于 XXX（只用于核对坐标，XXX 本身也来自来源）
#        None     不校验（调控元件）
# ---------------------------------------------------------------------------

# (载体 id, 特征名, 第几个同名特征(0 起), 库内名称, FeatureType, check, 备注)
VECTOR_SPECS = [
    ("pcDNA3.1", "CMV enhancer", 0, "CMV enhancer", "enhancer", None,
     "human cytomegalovirus immediate early enhancer"),
    ("pcDNA3.1", "CMV promoter", 0, "CMV promoter", "promoter", None,
     "human cytomegalovirus immediate early promoter"),
    ("pcDNA3.1", "T7 promoter", 0, "T7 promoter", "promoter",
     "seq:TAATACGACTCACTATAGG", "promoter for bacteriophage T7 RNA polymerase"),
    ("pcDNA3.1", "bGH poly(A) signal", 0, "bGH poly(A) signal", "polyA_signal", None,
     "bovine growth hormone polyadenylation signal"),
    ("pcDNA3.1", "f1 ori", 0, "f1 ori", "rep_origin", None,
     "f1 bacteriophage origin of replication"),
    ("pcDNA3.1", "SV40 promoter", 0, "SV40 promoter", "promoter", None,
     "SV40 enhancer and early promoter"),
    ("pcDNA3.1", "SV40 ori", 0, "SV40 ori", "rep_origin", None,
     "SV40 origin of replication"),
    ("pcDNA3.1", "NeoR/KanR", 0, "NeoR/KanR", "marker", "orf",
     "aminoglycoside phosphotransferase (Tn5 aph(3')-II); neomycin/kanamycin/G418 resistance"),
    ("pcDNA3.1", "SV40 poly(A) signal", 0, "SV40 poly(A) signal", "polyA_signal", None,
     "SV40 polyadenylation signal"),
    ("pcDNA3.1", "ori", 0, "ori", "rep_origin", None,
     "high-copy-number ColE1/pMB1/pBR322/pUC origin of replication"),
    ("pcDNA3.1", "AmpR", 0, "AmpR", "marker", "orf",
     "beta-lactamase; ampicillin/carbenicillin resistance"),
    ("pcDNA3.1", "AmpR promoter", 0, "AmpR promoter", "promoter", None, "bla promoter"),
    ("pET-28a", "T7 terminator", 0, "T7 terminator", "terminator", None,
     "transcription terminator for bacteriophage T7 RNA polymerase"),
    ("pET-28a", "lac operator", 0, "lac operator", "protein_bind", None,
     "lac repressor binding site"),
    ("pET-28a", "lacI promoter", 0, "lacI promoter", "promoter", None, "lacI promoter"),
    ("pET-28a", "lacI", 0, "lacI", "cds", "orf", "lac repressor"),
    ("pET-28a", "rop", 0, "rop", "cds", "orf", "Rop protein (ColE1 copy-number control)"),
    ("pET-28a", "KanR", 0, "KanR", "marker", "orf",
     "aminoglycoside phosphotransferase (Tn903); kanamycin resistance"),
    ("pFastBac1", "Tn7R", 0, "Tn7R", "misc_feature", None, "mini-Tn7 right end"),
    ("pFastBac1", "GmR", 0, "GmR", "marker", "orf",
     "gentamicin acetyltransferase; gentamicin resistance"),
    ("pFastBac1", "Pc promoter", 0, "Pc promoter", "promoter", None, "class 1 integron promoter"),
    ("pFastBac1", "polyhedrin promoter", 0, "polyhedrin promoter", "promoter", None,
     "baculovirus polyhedrin promoter"),
    ("pFastBac1", "SV40 poly(A) signal", 0, "SV40 poly(A) signal", "polyA_signal", None,
     "SV40 polyadenylation signal"),
    ("pFastBac1", "Tn7L", 0, "Tn7L", "misc_feature", None, "mini-Tn7 left end"),
    ("pGEX-4T-1", "tac promoter", 0, "tac promoter", "promoter", None,
     "hybrid trp/lacUV5 promoter"),
    ("pGEX-4T-1", "GST", 0, "GST", "tag", "fusion",
     "glutathione S-transferase (Schistosoma japonicum) fusion tag"),
    ("pGEX-4T-1", "lacIq promoter", 0, "lacIq promoter", "promoter", None,
     "lacIq allele promoter"),
    ("pLVX", "5' LTR", 0, "5' LTR", "ltr", None, "HIV-1 5' long terminal repeat"),
    ("pLVX", "HIV-1 ψ", 0, "HIV-1 Psi", "misc_feature", None, "HIV-1 packaging signal"),
    ("pLVX", "RRE", 0, "RRE", "misc_feature", None, "HIV-1 Rev response element"),
    ("pLVX", "cPPT/CTS", 0, "cPPT/CTS", "misc_feature", None,
     "HIV-1 central polypurine tract / central termination sequence"),
    ("pLVX", "IRES", 0, "EMCV IRES", "misc_feature", None,
     "encephalomyocarditis virus internal ribosome entry site"),
    ("pLVX", "PuroR", 0, "PuroR", "marker", "orf",
     "puromycin N-acetyltransferase (pac); puromycin resistance"),
    ("pLVX", "WPRE", 0, "WPRE", "misc_feature", None,
     "woodchuck hepatitis virus posttranscriptional regulatory element"),
    ("pLVX", "3' LTR", 0, "3' LTR", "ltr", None, "HIV-1 3' long terminal repeat (pLVX)"),
    ("pYES2", "GAL1 promoter", 0, "GAL1 promoter", "promoter", None,
     "yeast GAL1 inducible promoter"),
    ("pYES2", "CYC1 terminator", 0, "CYC1 terminator", "terminator", None,
     "yeast CYC1 terminator"),
    ("pYES2", "URA3", 0, "URA3", "marker", "orf", "yeast auxotrophic marker"),
    ("pYES2", "URA3 promoter", 0, "URA3 promoter", "promoter", None, "URA3 promoter"),
    ("pYES2", "2μ ori", 0, "2μ ori", "rep_origin", None, "yeast 2μ plasmid origin"),
]

# (accession, start, end, strand, 库内名称, FeatureType, check, 备注)；坐标 1-based 闭区间
NCBI_SPECS = [
    ("U55762", 679, 1398, 1, "EGFP", "cds", "orf",
     "enhanced GFP (pEGFP-N1 egfp CDS)"),
    ("AY678264", 1, 711, 1, "mCherry", "cds", "orf",
     "monomeric red fluorescent protein mCherry (Shaner et al. 2004)"),
    ("D83710", 50, 442, 1, "BSD", "marker", "orf",
     "blasticidin S deaminase (Aspergillus terreus); blasticidin resistance"),
    ("K01193", 211, 1236, 1, "HygR", "marker", "orf",
     "hygromycin B phosphotransferase (hph); hygromycin resistance"),
    ("X52869", 3, 377, 1, "ZeoR", "marker", "orf",
     "Sh ble (Streptoalloteichus hindustanus); Zeocin/bleomycin resistance"),
    ("V00622", 244, 903, 1, "CmR", "marker", "orf",
     "chloramphenicol acetyltransferase (Tn9 cat); chloramphenicol resistance"),
    ("J01749", 86, 1276, 1, "TcR", "marker", "orf",
     "tetracycline efflux protein (pBR322 tet); tetracycline resistance"),
    ("L09137", 146, 469, -1, "lacZα", "cds", "orf",
     "LacZ alpha fragment (pUC19); blue/white screening"),
    ("OM304286", 1127, 1157, 1, "lac promoter", "promoter", None,
     "E. coli lac promoter"),
    ("OM304286", 1091, 1112, 1, "CAP binding site", "protein_bind", None,
     "catabolite activator protein binding site"),
    ("OM304286", 1165, 1181, 1, "lac operator", "protein_bind", None,
     "lac repressor binding site (17 bp)"),
    ("PV983739", 2246, 2270, 1, "attB1", "protein_bind", None,
     "Gateway attB1 recombination site"),
    ("PV983739", 3148, 3172, -1, "attB2", "protein_bind", None,
     "Gateway attB2 recombination site"),
    ("MZ215989", 2253, 3353, 1, "MBP", "tag", "fusion",
     "maltose binding protein (E. coli malE) fusion tag"),
]

# 短肽标签 / 蛋白酶位点：按翻译检索。peptide 必须出现在来源区段的翻译里。
# (来源类型, 来源 id, start, end, strand, 名称, FeatureType, peptide, 备注)
# 来源类型 vector 时 start/end/strand 为 None，按 (特征名, 序号) 取坐标
PEPTIDE_SPECS = [
    ("vector", ("pET-28a", "6xHis", 0), None, None, None, "6xHis", "tag", "HHHHHH",
     "polyhistidine tag"),
    ("vector", ("pET-28a", "T7 tag", 0), None, None, None, "T7 tag", "tag", "MASMTGGQQMG",
     "T7 gene 10 leader epitope tag"),
    ("vector", ("pET-28a", "thrombin site", 0), None, None, None, "thrombin site",
     "misc_feature", "LVPRGS", "thrombin cleavage site (LVPR^GS)"),
    ("ncbi", "U78872", 918, 941, 1, "PreScission site", "misc_feature", "LEVLFQGP",
     "HRV 3C / PreScission protease cleavage site (LEVLFQ^GP)"),
    ("ncbi", "MK533800", 3435, 3578, 1, "TEV site", "misc_feature", "ENLYFQG",
     "TEV protease cleavage site (ENLYFQ^G)"),
    ("ncbi", "PQ065984", 6984, 7007, 1, "FLAG", "tag", "DYKDDDDK",
     "FLAG epitope tag (enterokinase site DDDDK^)"),
    ("ncbi", "OR519721", 501, 527, 1, "HA", "tag", "YPYDVPDYA",
     "influenza hemagglutinin epitope tag"),
    ("ncbi", "MZ215986", 2214, 2246, 1, "Myc", "tag", "EQKLISEEDL",
     "c-Myc epitope tag"),
    ("ncbi", "OQ547858", 470, 511, 1, "V5", "tag", "GKPIPNPLLGLDST",
     "V5 epitope tag (simian virus 5)"),
]

STARTS = {"ATG", "GTG", "TTG"}


def revcomp(s: str) -> str:
    return str(Seq(s).reverse_complement())


def translate(s: str) -> str:
    return str(Seq(s[: len(s) - len(s) % 3]).translate(table=11))


def check(name: str, dna: str, rule):
    if rule is None:
        return
    if rule.startswith("seq:"):
        exp = rule[4:]
        assert dna == exp, f"{name}: 截取结果 {dna} ≠ 期望 {exp}（坐标错位？）"
        return
    assert len(dna) % 3 == 0, f"{name}: 长度 {len(dna)} 不是 3 的倍数"
    prot = translate(dna)
    if rule == "orf":
        assert dna[:3] in STARTS, f"{name}: 起始密码子 {dna[:3]}"
        assert prot.endswith("*") and "*" not in prot[:-1], f"{name}: 不是完整 ORF"
    elif rule == "fusion":
        assert dna[:3] == "ATG" and "*" not in prot, f"{name}: 融合标签含终止或无 ATG"
    else:  # pragma: no cover
        raise ValueError(rule)


def load_vector(vid: str):
    for path in VECTORS.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("id") == vid:
            return path, data
    raise SystemExit(f"找不到载体 {vid}")


def vector_feature(vid: str, fname: str, idx: int):
    path, data = load_vector(vid)
    hits = [f for f in data.get("features") or [] if f.get("name") == fname]
    assert len(hits) > idx, f"{vid} 没有第 {idx} 个 {fname}"
    f = hits[idx]
    seq = data["sequence"].upper()
    start, end = int(f["start"]), int(f["end"])
    sub = seq[start - 1:end] if start <= end else seq[start - 1:] + seq[:end]
    strand = -1 if str(f.get("strand")) == "-" else 1
    dna = revcomp(sub) if strand == -1 else sub
    prov = data.get("data_provenance") or {}
    src = {
        "kind": "vector_library",
        "file": f"data/vectors/{path.name}",
        "vector": vid,
        "location": f"{start}..{end}({'-' if strand == -1 else '+'})",
        "upstream": prov.get("url") or prov.get("source"),
    }
    return dna, src


def fetch_genbank(acc: str, cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{acc}.gb"
    if not path.exists() or path.stat().st_size < 200:
        with urllib.request.urlopen(EFETCH.format(acc=acc), timeout=60) as r:
            path.write_bytes(r.read())
        time.sleep(0.4)  # NCBI：无 API key 时 ≤3 请求/秒
    return SeqIO.read(str(path), "genbank")


def ncbi_region(acc: str, start: int, end: int, strand: int, cache: Path):
    rec = fetch_genbank(acc, cache)
    seq = str(rec.seq).upper()
    sub = seq[start - 1:end]
    dna = revcomp(sub) if strand == -1 else sub
    src = {
        "kind": "ncbi_genbank",
        "accession": rec.id,  # 含版本号，如 U55762.1
        "location": f"{start}..{end}({'-' if strand == -1 else '+'})",
        "url": f"https://www.ncbi.nlm.nih.gov/nuccore/{rec.id}",
        "definition": rec.description[:120],
    }
    return dna, src


def peptide_dna(dna: str, peptide: str, name: str):
    """在区段的三个读码框里找 peptide，返回编码它的 DNA"""
    for frame in range(3):
        prot = translate(dna[frame:])
        i = prot.find(peptide)
        if i >= 0:
            a = frame + 3 * i
            return dna[a:a + 3 * len(peptide)]
    raise AssertionError(f"{name}: 来源区段翻译中找不到 {peptide}")


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("α", "alpha").replace("μ", "u")
               .replace("'", "p")).strip("-")
    return s or "feature"


def add_entry(entries, name, ftype, dna, src, notes, match="dna", peptide=None):
    dna = dna.upper()
    assert set(dna) <= set("ACGT"), f"{name}: 含非 ACGT 碱基"
    key = peptide if match == "peptide" else dna
    for e in entries:
        same = (e["match"] == match and
                (e.get("peptide") == key if match == "peptide"
                 else e["sequence"] in (dna, revcomp(dna))))
        if same:
            e["sources"].append(src)  # 同一序列多来源：只记来源
            return
    entry = {
        "id": "",
        "name": name,
        "type": ftype,
        "match": match,
        "length": len(dna),
        "sequence": dna,
        "notes": notes,
        "sources": [src],
    }
    if peptide:
        entry["peptide"] = peptide
    entries.append(entry)


def build(cache: Path):
    entries = []
    for vid, fname, idx, name, ftype, rule, notes in VECTOR_SPECS:
        dna, src = vector_feature(vid, fname, idx)
        check(name, dna, rule)
        add_entry(entries, name, ftype, dna, src, notes)
    for acc, start, end, strand, name, ftype, rule, notes in NCBI_SPECS:
        dna, src = ncbi_region(acc, start, end, strand, cache)
        check(name, dna, rule)
        add_entry(entries, name, ftype, dna, src, notes)
    for kind, ref, start, end, strand, name, ftype, peptide, notes in PEPTIDE_SPECS:
        if kind == "vector":
            region, src = vector_feature(*ref)
        else:
            region, src = ncbi_region(ref, start, end, strand, cache)
        dna = peptide_dna(region, peptide, name)
        add_entry(entries, name, ftype, dna, src, notes, match="peptide", peptide=peptide)

    used = set()
    for e in entries:
        base = slug(e["name"])
        eid, n = base, 2
        while eid in used:
            eid, n = f"{base}-{n}", n + 1
        used.add(eid)
        e["id"] = eid
    entries.sort(key=lambda e: (e["type"], e["name"].lower(), e["id"]))
    return {
        "schema": "feature-library/1",
        "name": "common",
        "description": "Plasmid Designer 常见质粒元件库（图谱自动注释用）；"
                       "由 scripts/build_feature_library.py 生成，勿手改",
        "sources_policy": "仅收录仓库载体库（data/vectors，见 data_provenance）与 "
                          "NCBI GenBank 公开记录中可核验的序列；每条记录来源与坐标",
        "features": entries,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", type=Path, default=Path(".feature-library-cache"),
                    help="GenBank 下载缓存目录（默认 ./.feature-library-cache）")
    ap.add_argument("--check", action="store_true", help="只校验并打印摘要，不写文件")
    args = ap.parse_args(argv)
    lib = build(args.cache)
    text = json.dumps(lib, ensure_ascii=False, indent=1) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    by_type = {}
    for e in lib["features"]:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    print(f"{len(lib['features'])} 条元件，sha256 {digest}：{by_type}")
    if args.check:
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"已写入 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
