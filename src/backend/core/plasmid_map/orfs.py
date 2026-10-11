"""ORF 查找：双链、六框、环状感知（契约 MapOrf）

规则（与 SnapGene/Benchling 默认一致）：ATG 起始、遇终止密码子结束，
同一读码段只报最靠上游的 ATG（最长 ORF，不报嵌套）；aa_length 不含终止
密码子；坐标含终止密码子。线性分子上没有终止密码子的尾段不报。
环状分子：在三倍长序列上找，只保留起点落在中间一份内、长度不超过一圈
的 ORF，于是跨原点的 ORF 只报一次，且其上游终止密码子可在原点另一侧。

frame：该 ORF 所在链上（负链按反向互补序列计）起点坐标 mod 3。
"""

from typing import Dict, List

from Bio.Seq import Seq

from core.seq_utils import revcomp

DEFAULT_MIN_AA = 100


def _translate_frame(seq: str, frame: int) -> str:
    sub = seq[frame:]
    sub = sub[: len(sub) - len(sub) % 3]
    if not sub:
        return ""
    # Bio 的翻译在 C/Python 混合层面比逐密码子 dict 查找快一个数量级；
    # 模糊碱基译为 X，不会误当终止
    return str(Seq(sub).translate(table=1))


def _orfs_on_strand(strand_seq: str, length: int, circular: bool, min_aa: int):
    """在单链（已按阅读方向）上找 ORF，产出 (start0, nt_len, frame_on_strand)"""
    scan = strand_seq * 3 if circular else strand_seq
    lo, hi = (length, 2 * length) if circular else (0, length)
    for frame in range(3):
        prot = _translate_frame(scan, frame)
        pos = 0  # 当前读码段起点（氨基酸下标）
        for chunk in prot.split("*"):
            seg_end = pos + len(chunk)  # 终止密码子所在氨基酸下标
            if seg_end < len(prot):  # 有终止密码子收尾
                m = chunk.find("M")
                if m >= 0:
                    aa_len = len(chunk) - m
                    if aa_len >= min_aa:
                        start0 = frame + 3 * (pos + m)
                        nt_len = 3 * (aa_len + 1)
                        if lo <= start0 < hi and (not circular or nt_len <= length):
                            yield start0 - lo, nt_len, aa_len
            pos = seg_end + 1


def find_orfs(seq: str, circular: bool, min_aa: int = DEFAULT_MIN_AA) -> List[Dict]:
    length = len(seq)
    if length < 3:
        return []
    min_aa = max(1, int(min_aa))
    out: List[Dict] = []
    rc = revcomp(seq)
    for strand, s in ((1, seq), (-1, rc)):
        for start0, nt_len, aa_len in _orfs_on_strand(s, length, circular, min_aa):
            end0 = start0 + nt_len - 1  # 该链坐标，闭区间，可能 ≥ length（跨原点）
            frame = start0 % 3
            if strand == 1:
                start, end = start0 + 1, end0 + 1
            else:
                # 反向互补链坐标 i ↔ 正向 length-1-i
                start, end = length - end0, length - start0
            if circular:
                start = ((start - 1) % length) + 1
                end = ((end - 1) % length) + 1
            out.append({"start": start, "end": end, "strand": strand,
                        "frame": frame, "aa_length": aa_len})
    out.sort(key=lambda o: (-o["aa_length"], o["start"], o["strand"]))
    return out
