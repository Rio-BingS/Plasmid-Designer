"""变体功能注释 — 将突变映射到载体特征（取代人工判断的关键层）

- 突变落在哪个特征（CDS/promoter/origin…）
- CDS 内：密码子/氨基酸变化、是否移码
- 酶切位点：是否破坏/新引入限制酶识别序列
"""

from typing import Dict, List

from core.enzyme_sites import ENZYME_TABLE, find_enzyme_sites

_COMPLEMENT = str.maketrans("ACGTNacgtn", "TGCANtgcan")

CODON_TABLE = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L", "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
    "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M", "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
    "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S", "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
    "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T", "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
    "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*", "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
    "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K", "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
    "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W", "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R", "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
}


def translate(seq: str) -> str:
    seq = seq.upper().replace("U", "T")
    return "".join(CODON_TABLE.get(seq[i:i + 3], "x") for i in range(0, len(seq) - len(seq) % 3, 3))


def annotate_variant(variant: Dict, features: List[Dict], reference: str) -> Dict:
    """为单个变体补充特征级注释（原地修改并返回）"""
    ref_pos = variant["ref_pos"]
    ref = reference.upper().replace("U", "T")
    L = len(ref)

    # 所在特征
    hit_features = []
    for f in features:
        s, e = int(f.get("start", 0)), int(f.get("end", 0))
        if s <= ref_pos <= e:
            hit_features.append({"name": f.get("name", "?"), "type": f.get("type", "other")})
    variant["features"] = hit_features

    # CDS 内的氨基酸变化与移码判定
    variant["codon_change"] = None
    variant["aa_change"] = None
    variant["frameshift"] = False
    for f in hit_features:
        if f["type"] not in ("CDS", "gene"):
            continue
        feat = next((x for x in features if x.get("name") == f["name"]), None)
        if not feat:
            continue
        s, e = int(feat["start"]), int(feat["end"])
        offset = ref_pos - s  # 0-based 在特征内偏移
        if variant["type"] == "substitution":
            frame = (offset // 3) * 3
            codon_ref = ref[s - 1 + frame: s - 1 + frame + 3]
            mut_codon = list(codon_ref)
            mut_codon[offset % 3] = variant["alt_base"][:1]
            aa_ref = CODON_TABLE.get(codon_ref, "x")
            aa_alt = CODON_TABLE.get("".join(mut_codon).upper(), "x")
            variant["codon_change"] = f"{codon_ref}>{''.join(mut_codon).upper()}"
            variant["aa_change"] = f"{feat.get('name', 'CDS')}:{aa_ref}{offset // 3 + 1}{aa_alt}"
        elif variant["type"] in ("insertion", "deletion"):
            variant["frameshift"] = variant["length"] % 3 != 0

    # 酶切位点变化：在变体附近 ±14bp 窗口比较参考与突变序列
    alt_seq = _apply_variant(ref, variant)
    lo = max(0, ref_pos - 15)
    hi = min(L, ref_pos + 15)
    ref_sites = find_enzyme_sites(ref[lo:hi])
    alt_sites = find_enzyme_sites(alt_seq[lo:hi])
    ref_names = {(x["name"], x["position"]) for x in ref_sites}
    alt_names = {(x["name"], x["position"]) for x in alt_sites}
    lost = sorted({n for n, _ in ref_names - alt_names})
    gained = sorted({n for n, _ in alt_names - ref_names})
    variant["enzyme_sites_lost"] = lost
    variant["enzyme_sites_gained"] = gained
    return variant


def _apply_variant(ref: str, variant: Dict) -> str:
    pos = variant["ref_pos"] - 1  # 0-based
    if variant["type"] == "substitution":
        return ref[:pos] + variant["alt_base"] + ref[pos + 1:]
    if variant["type"] == "insertion":
        return ref[:pos + 1] + variant["alt_base"] + ref[pos + 1:]
    if variant["type"] == "deletion":
        return ref[:pos] + ref[pos + variant["length"]:]
    return ref


def annotate_variants(variants: List[Dict], features: List[Dict], reference: str) -> List[Dict]:
    return [annotate_variant(v, features, reference) for v in variants]


def summarize_severity(variants: List[Dict]) -> List[str]:
    """生成变体影响摘要（供自动结论使用）"""
    notes = []
    for v in variants:
        loc = "、".join(f"{f['name']}({f['type']})" for f in v.get("features", [])) or "非编码区"
        if v.get("frameshift"):
            notes.append(f"位置 {v['ref_pos']} {v['type']}（{loc}）导致移码")
        elif v.get("aa_change"):
            notes.append(f"位置 {v['ref_pos']} 氨基酸改变 {v['aa_change']}（{loc}）")
        elif v["type"] == "substitution":
            notes.append(f"位置 {v['ref_pos']} {v['ref_base']}>{v['alt_base']}（{loc}）")
        else:
            kind = "插入" if v["type"] == "insertion" else "缺失"
            notes.append(f"位置 {v['ref_pos']} {kind} {v['length']}bp（{loc}）")
        if v.get("enzyme_sites_lost"):
            notes.append(f"  ↳ 破坏酶切位点: {', '.join(v['enzyme_sites_lost'])}")
        if v.get("enzyme_sites_gained"):
            notes.append(f"  ↳ 新增酶切位点: {', '.join(v['enzyme_sites_gained'])}")
    return notes
