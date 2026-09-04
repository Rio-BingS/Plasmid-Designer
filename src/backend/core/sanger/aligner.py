"""Sanger read 与参考序列比对 — Biopython PairwiseAligner

每条 read 与参考序列正向/反向互补各比一次（局部比对），取更高分者判定方向，
由比对坐标推导错配 / 插入 / 缺失列表。
"""

from typing import Dict, List, Tuple

from Bio.Align import PairwiseAligner

_ALIGNER = None

_COMPLEMENT = str.maketrans("ACGTNacgtn", "TGCANtgcan")


def revcomp(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]


def _get_aligner() -> PairwiseAligner:
    global _ALIGNER
    if _ALIGNER is None:
        al = PairwiseAligner(mode="local")
        # mismatch 罚分高于连续 indel 代价，保证 ≥3bp 插缺能以 gap 形式检出
        al.open_gap_score = -6
        al.extend_gap_score = -2
        al.match_score = 2
        al.mismatch_score = -4
        _ALIGNER = al
    return _ALIGNER


def align_read(read: str, reference: str) -> Dict:
    """将 read（自动判向）比对到参考序列

    返回 {direction: '+'/'-', ref_start, ref_end, score, identity, variants}
    variants: [{ref_pos(1-based), read_pos, type: substitution|insertion|deletion,
                ref_base, alt_base, length}]
    坐标为正向参考链 1-based；insertion 的 ref_pos 为插入点左侧参考位置。
    """
    ref_up = reference.upper()
    read_up = read.upper()
    aligner = _get_aligner()

    best = None  # (score, direction, query, alignment)
    for direction, query in (("+", read_up), ("-", revcomp(read_up))):
        alignments = aligner.align(ref_up, query)
        if len(alignments) == 0:
            continue
        aln = alignments[0]
        score = aln.score
        if best is None or score > best[0]:
            best = (score, direction, query, aln)

    if best is None:
        return {"direction": "+", "ref_start": 0, "ref_end": 0, "score": 0.0,
                "identity": 0.0, "variants": []}

    score, direction, query, aln = best
    # coordinates: [[ref块起,ref块止,...],[read块起,read块止,...]]
    coords = aln.coordinates
    variants: List[Dict] = []
    matches = 0
    compared = 0

    for k in range(coords.shape[1] - 1):
        rs, re_ = int(coords[0][k]), int(coords[0][k + 1])
        qs, qe = int(coords[1][k]), int(coords[1][k + 1])
        ref_block = ref_up[rs:re_]
        read_block = query[qs:qe]
        if rs == re_ or qs == qe:
            # 纯插入或纯缺失
            if rs == re_:  # read 有而参考无 → insertion
                variants.append({
                    "ref_pos": max(1, rs),  # 1-based：插入点左侧参考位置
                    "read_pos": qs + 1,
                    "type": "insertion",
                    "ref_base": "-",
                    "alt_base": read_block,
                    "length": len(read_block),
                })
            else:  # 参考有而 read 无 → deletion
                variants.append({
                    "ref_pos": rs + 1,
                    "read_pos": qs + 1,
                    "type": "deletion",
                    "ref_base": ref_block,
                    "alt_base": "-",
                    "length": len(ref_block),
                })
            continue
        for i, (rb, qb) in enumerate(zip(ref_block, read_block)):
            compared += 1
            if rb == qb:
                matches += 1
            else:
                variants.append({
                    "ref_pos": rs + i + 1,
                    "read_pos": qs + i + 1,
                    "type": "substitution",
                    "ref_base": rb,
                    "alt_base": qb,
                    "length": 1,
                })

    identity = matches / compared if compared else 0.0
    ref_start = int(coords[0][0]) + 1
    ref_end = int(coords[0][-1])
    return {
        "direction": direction,
        "ref_start": ref_start,
        "ref_end": max(ref_end, ref_start),
        "score": float(score),
        "identity": round(identity, 4),
        "variants": variants,
        "aligned_read_len": int(coords[1][-1]) - int(coords[1][0]),
    }


def merge_coverage(ranges: List[Tuple[int, int]], length: int) -> List[Tuple[int, int]]:
    """合并多个 read 的覆盖区间（1-based 闭区间），返回合并后区间列表"""
    if not ranges:
        return []
    intervals = sorted((max(1, s), min(length, e)) for s, e in ranges if e >= s)
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        if s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged
