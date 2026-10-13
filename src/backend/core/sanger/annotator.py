"""变体功能注释 — 将突变映射到载体特征（取代人工判断的关键层）

- 突变落在哪个特征（CDS/promoter/origin…）
- CDS 内：密码子/氨基酸变化、是否移码
- 酶切位点：是否破坏/新引入限制酶识别序列
"""

from typing import Dict, List

from core.enzyme_sites import ENZYME_TABLE, find_enzyme_sites
from core.seq_utils import revcomp as _revcomp
from core.seq_utils import CODON_TO_AA as _STD_TABLE

# 兼容别名（方向：密码子 → 氨基酸，大写值，与历史表一致）
CODON_TABLE = _STD_TABLE

# 反向互补收敛到 core.seq_utils（导入名 _revcomp 与原函数名一致，调用点无需改动）


def translate(seq: str) -> str:
    """按标准遗传密码直译（U→T；未识别密码子 → 'x'，沿用历史口径）"""
    seq = seq.upper().replace("U", "T")
    return "".join(_STD_TABLE.get(seq[i:i + 3], "x") for i in range(0, len(seq) - len(seq) % 3, 3))


def group_feature_segments(features: List[Dict]) -> List[List[Dict]]:
    """把多段特征（reference_parser 对 join/order 逐段展开的记录）按
    feature_id 归组，组内按 segment_index（编码方向顺序）排列；
    无分组键的普通特征各自成组。组的顺序沿用首段在列表中的出现顺序。"""
    groups: List[List[Dict]] = []
    by_key: Dict[object, List[Dict]] = {}
    for f in features:
        fid = f.get("feature_id")
        if fid is None or int(f.get("segment_count") or 1) <= 1:
            groups.append([f])
            continue
        g = by_key.get(fid)
        if g is None:
            g = by_key[fid] = []
            groups.append(g)
        g.append(f)
    for g in groups:
        if len(g) > 1:
            g.sort(key=lambda x: int(x.get("segment_index") or 0))
    return groups


def coding_sequence(ref: str, parts: List[Dict]) -> str:
    """按编码方向拼接各段（负链段取反向互补），得到特征的编码序列"""
    out = []
    for p in parts:
        s, e = int(p["start"]), int(p["end"])
        seg = ref[s - 1:e]
        out.append(_revcomp(seg) if p.get("strand", "+") == "-" else seg)
    return "".join(out)


def coding_offset(parts: List[Dict], ref_pos: int):
    """参考坐标 → 编码序列内 0-based 偏移（不在任何段内返回 None）"""
    offset = 0
    for p in parts:
        s, e = int(p["start"]), int(p["end"])
        if s <= ref_pos <= e:
            return offset + (e - ref_pos if p.get("strand", "+") == "-" else ref_pos - s)
        offset += e - s + 1
    return None


def annotate_variant(variant: Dict, features: List[Dict], reference: str) -> Dict:
    """为单个变体补充特征级注释（原地修改并返回）"""
    ref_pos = variant["ref_pos"]
    ref = reference.upper().replace("U", "T")
    L = len(ref)

    # 所在特征（同时保留原始特征分段，供 CDS 注释直接使用——
    # 按名称二次反查在重名特征时会取错坐标）。多段特征（join）按组
    # 判定命中：只有变体落在某一段内才算命中，且同一特征只报一次
    # 环状参考上跨原点的缺失（ref_pos+len-1 > L）拆成 [ref_pos, L] 与
    # [1, ref_pos+len-1-L] 两段，每段按起点判定命中（与线性缺失只看
    # ref_pos 的口径一致）；线性参考上缺失不会越过 L，仍只有一段
    var_segs = [(ref_pos, int(variant.get("length") or 1))]
    if variant["type"] == "deletion" and L and ref_pos + var_segs[0][1] - 1 > L:
        n = var_segs[0][1]
        var_segs = [(ref_pos, L - ref_pos + 1), (1, n - (L - ref_pos + 1))]

    def _in_parts(parts: List[Dict], pos: int) -> bool:
        return any(int(p.get("start", 0)) <= pos <= int(p.get("end", 0)) for p in parts)

    hit_features = []
    hit_groups: List[List[Dict]] = []
    for parts in group_feature_segments(features):
        if any(_in_parts(parts, s) for s, _n in var_segs):
            f = parts[0]
            hit_features.append({"name": f.get("name", "?"), "type": f.get("type", "other")})
            hit_groups.append(parts)
    variant["features"] = hit_features

    # CDS 内的氨基酸变化与移码判定
    variant["codon_change"] = None
    variant["aa_change"] = None
    variant["frameshift"] = False
    for parts in hit_groups:
        feat = parts[0]
        if feat.get("type") not in ("CDS", "gene"):
            continue
        # 编码序列按段顺序拼接（跨原点的 join 先取 2701..3000 再接 1..300），
        # 负链段取反向互补，密码子坐标按编码方向计算
        coding = coding_sequence(ref, parts)
        hit_segs = [(s, n) for s, n in var_segs if _in_parts(parts, s)]
        hit_pos = hit_segs[0][0]
        cpos = coding_offset(parts, hit_pos)
        if cpos is None:
            continue
        strand = next(
            (p.get("strand", "+") for p in parts
             if int(p["start"]) <= hit_pos <= int(p["end"])),
            feat.get("strand", "+"),
        )
        if variant["type"] == "substitution":
            frame = (cpos // 3) * 3
            codon_ref = coding[frame: frame + 3]
            mut_codon = list(codon_ref)
            # 负链上变体的 alt_base 是参考链坐标，编码方向需取互补
            mut_base = variant["alt_base"][:1]
            if strand == "-":
                # 单碱基取互补 = 单碱基的 revcomp（core.seq_utils 支持 IUPAC/大小写）
                mut_base = _revcomp(mut_base)
            mut_codon[cpos % 3] = mut_base
            aa_ref = CODON_TABLE.get(codon_ref, "x")
            aa_alt = CODON_TABLE.get("".join(mut_codon).upper(), "x")
            variant["codon_change"] = f"{codon_ref}>{''.join(mut_codon).upper()}"
            if aa_alt != aa_ref:
                variant["aa_change"] = f"{feat.get('name', 'CDS')}:{aa_ref}{cpos // 3 + 1}{aa_alt}"
            elif aa_ref != "x":
                variant["synonymous"] = True  # 同义：DNA 变、蛋白不变，不算氨基酸改变
        elif variant["type"] in ("insertion", "deletion"):
            # 跨原点缺失只有一段落在该特征时按该段长度判移码
            variant["frameshift"] = sum(n for _s, n in hit_segs) % 3 != 0

    # 酶切位点变化：在变体附近 ±14bp 窗口比较参考与突变序列。
    # 插入/缺失会使下游位点整体平移 delta，直接按 (名称, 位置) 对比会把"平移"
    # 误报成 破坏+新增，故同名配对时允许下游位点按 delta 平移。
    alt_seq = _apply_variant(ref, variant)
    lo = max(0, ref_pos - 15)
    hi = min(L, ref_pos + 15)
    ref_sites = find_enzyme_sites(ref[lo:hi])
    alt_sites = find_enzyme_sites(alt_seq[lo:hi])
    delta = len(alt_seq) - len(ref)  # indel 引起的下游坐标偏移量（替换为 0）
    junction = ref_pos - 1 - lo      # 变异起点在窗口内的 0-based 下标
    ref_shift_floor = junction + max(0, -delta)  # 参考侧受平移影响的位点起点
    alt_shift_floor = junction + max(0, delta)   # 突变侧受平移影响的位点起点
    ref_pos_by_name: Dict[str, set] = {}
    for s in ref_sites:
        ref_pos_by_name.setdefault(s["name"], set()).add(s["position"])
    alt_pos_by_name: Dict[str, set] = {}
    for s in alt_sites:
        alt_pos_by_name.setdefault(s["name"], set()).add(s["position"])

    def _preserved(name: str, pos: int, from_ref: bool) -> bool:
        if from_ref:
            cands = alt_pos_by_name.get(name, set())
            return pos in cands or (pos > ref_shift_floor and pos + delta in cands)
        cands = ref_pos_by_name.get(name, set())
        return pos in cands or (pos > alt_shift_floor and pos - delta in cands)

    lost = sorted({s["name"] for s in ref_sites if not _preserved(s["name"], s["position"], True)})
    gained = sorted({s["name"] for s in alt_sites if not _preserved(s["name"], s["position"], False)})
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
            note = f"位置 {v['ref_pos']} {v['type']}（{loc}）导致移码"
        elif v.get("aa_change"):
            note = f"位置 {v['ref_pos']} 氨基酸改变 {v['aa_change']}（{loc}）"
        elif v.get("synonymous"):
            note = f"位置 {v['ref_pos']} {v['ref_base']}>{v['alt_base']}（{loc}）同义突变（蛋白不变）"
        elif v["type"] == "substitution":
            note = f"位置 {v['ref_pos']} {v['ref_base']}>{v['alt_base']}（{loc}）"
        else:
            kind = "插入" if v["type"] == "insertion" else "缺失"
            note = f"位置 {v['ref_pos']} {kind} {v['length']}bp（{loc}）"
        if v.get("confidence") == "low":
            # 低置信文案分三支（A4 + 互检反证）：poly 下游滑移 echo 与跨 read
            # 互检矛盾各有明确原因点名，其余维持原口径
            if v.get("cross_read_resolved"):
                rd = "、".join(((v.get("cross_read_conflict") or {}).get("reads") or [])[:2])
                note += f"，已被 {rd} 互检排除（该处读出设计碱基，判为测序误差）"
            elif v.get("cross_read_conflict"):
                note += "，低置信度（仅单 read 报告，其他 read 跨过同一位点未见差异——互检矛盾，疑似伪影）"
            elif v.get("slippage_artifact"):
                note += "，低置信度（poly 下游疑似滑移伪影，建议人工核对峰图）"
            else:
                note += "，低置信度（疑似测序噪声或混合峰，建议人工核对峰图）"
        notes.append(note)
        if v.get("enzyme_sites_lost"):
            notes.append(f"  ↳ 破坏酶切位点: {', '.join(v['enzyme_sites_lost'])}")
        if v.get("enzyme_sites_gained"):
            notes.append(f"  ↳ 新增酶切位点: {', '.join(v['enzyme_sites_gained'])}")
    return notes
