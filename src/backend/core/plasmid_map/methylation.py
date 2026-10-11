"""E. coli Dam / Dcm 甲基化对酶切位点的阻断（图谱 MapEnzyme.methylation / sites[].blocked_by）

Dam 甲基化 GATC 的 A（N6），Dcm 甲基化 CCWGG 的第二个 C（C5）。当甲基化位点与
酶识别序列重叠时，部分酶切不动（blocked）或明显变慢（impaired）。从常规
dam+/dcm+ 克隆菌株（DH5α、TOP10…）提取的质粒都带这两种甲基化。

数据来源（人工整理，只收录表中明确列出的酶）：
  NEB「Dam and Dcm Methylases of E. coli」表（数据源自 REBASE）
  https://www.neb.com/en-us/tools-and-resources/usage-guidelines/dam-and-dcm-methylases-of-e-coli
表中每条给出「识别序列 + 形成甲基化位点所需的侧翼碱基」，如 XbaI 的 TCTAGATC：
XbaI 位点后接 TC（或前接 GA，即反向互补 GATCTAGA）时被 Dam 阻断。

未收录：表中脚注「需要多处重叠才阻断」的 Acc65I、BsaHI、BslI（单处重叠不足以
判断）；BsmFI（表格排版下无法确定其重叠模式）。impaired（变慢）与 blocked
一并按「受影响」报出（BcgI、SfiI 的 GGCCWGGNNGGCC 形式属 impaired）。
表外的酶视为不敏感（如 BamHI、BglII、KpnI、KasI 在表中列为「不被重叠甲基化阻断」）。
"""

from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from core.seq_utils import revcomp

# 酶 → [(甲基化类型, 阻断模式)]；模式 = 识别序列 + 必需侧翼（IUPAC，正向写法）
METHYLATION_BLOCKING: Dict[str, Tuple[Tuple[str, str], ...]] = {
    # ---- 被重叠 Dam 阻断 ----
    "AlwI": (("dam", "GGATC"),),
    "BcgI": (("dam", "CGATCNNNNTGC"),),
    "BclI": (("dam", "TGATCA"),),
    "BsaBI": (("dam", "GATCNNNATC"),),
    "BspDI": (("dam", "ATCGATC"),),
    "BspEI": (("dam", "TCCGGATC"),),
    "BspHI": (("dam", "TCATGATC"),),
    "ClaI": (("dam", "ATCGATC"),),
    "DpnII": (("dam", "GATC"),),
    "HphI": (("dam", "GGTGATC"),),
    "Hpy188I": (("dam", "TCNGATC"),),
    "Hpy188III": (("dam", "TCNNGATC"),),
    "MboI": (("dam", "GATC"),),
    "MboII": (("dam", "GAAGATC"),),
    "NruI": (("dam", "TCGCGATC"),),
    "TaqI": (("dam", "TCGATC"),),
    "XbaI": (("dam", "TCTAGATC"),),
    # ---- 被重叠 Dcm 阻断 ----
    "AlwNI": (("dcm", "CAGNNCCTGG"),),
    "ApaI": (("dcm", "GGGCCCWGG"),),
    "AvaII": (("dcm", "GGWCCWGG"),),
    "BanI": (("dcm", "GGYRCCWGG"),),
    "BsaI": (("dcm", "GAGACCWGG"),),
    "BssKI": (("dcm", "CCWGG"),),
    "BstXI": (("dcm", "CCAGGNNNNTGG"),),
    "EaeI": (("dcm", "YGGCCAGG"),),
    "EcoO109I": (("dcm", "RGGNCCTGG"),),
    "FseI": (("dcm", "GGCCGGCCWGG"),),
    "MscI": (("dcm", "TGGCCAGG"),),
    "NlaIV": (("dcm", "GGNNCCWGG"),),
    "PflMI": (("dcm", "CCAGGNNNTGG"),),
    "PpuMI": (("dcm", "RGGWCCTGG"),),
    "PspGI": (("dcm", "CCWGG"),),
    "PspOMI": (("dcm", "GGGCCCWGG"),),
    "Sau96I": (("dcm", "GGNCCWGG"),),
    "ScrFI": (("dcm", "CCWGG"),),
    "SexAI": (("dcm", "ACCWGGT"),),
    "SfiI": (("dcm", "GGCCWGGNNGGCC"), ("dcm", "GGCCNNNNNGGCCWGG")),
    "SfoI": (("dcm", "GGCGCCWGG"),),
    "StuI": (("dcm", "AGGCCTGG"),),
}

_IUPAC_SETS = {
    "A": "A", "C": "C", "G": "G", "T": "T", "R": "AG", "Y": "CT", "S": "GC",
    "W": "AT", "K": "GT", "M": "AC", "B": "CGT", "D": "AGT", "H": "ACT",
    "V": "ACG", "N": "ACGT",
}


def _compatible(a: str, b: str) -> bool:
    """两个等长 IUPAC 串逐位有交集"""
    return all(set(_IUPAC_SETS.get(x, x)) & set(_IUPAC_SETS.get(y, y)) for x, y in zip(a, b))


def _matches(bases: str, pattern: str) -> bool:
    return all(b in _IUPAC_SETS.get(p, p) for b, p in zip(bases, pattern))


@lru_cache(maxsize=None)
def _alignments(name: str, site_fwd: str) -> Tuple[Tuple[str, str, int], ...]:
    """(甲基化类型, 模式, 识别序列在模式中的偏移)：模式取正反两个方向
    （甲基化位点是双链对称的，同一上下文在正链上可能以任一方向出现）。
    site_fwd 是位点在正向链上的写法（负链位点为识别序列的反向互补）。"""
    out = []
    n = len(site_fwd)
    for kind, pat in METHYLATION_BLOCKING.get(name, ()):
        for q in dict.fromkeys((pat, revcomp(pat))):
            for k in range(len(q) - n + 1):
                if _compatible(q[k:k + n], site_fwd):
                    out.append((kind, q, k))
    return tuple(out)


def methylation_kinds(name: str) -> Optional[List[str]]:
    """酶级 methylation：该酶对哪些 E. coli 甲基化敏感（表外返回 None）"""
    entries = METHYLATION_BLOCKING.get(name)
    if not entries:
        return None
    return sorted({k for k, _ in entries})


def site_blocked_by(seq: str, circular: bool, name: str, site: str,
                    position0: int, strand: int) -> List[str]:
    """位点（识别序列左端 0-based position0）是否落在会阻断该酶的甲基化上下文里"""
    if name not in METHYLATION_BLOCKING:
        return []
    length = len(seq)
    site_fwd = site if strand == 1 else revcomp(site)
    kinds = set()
    for kind, q, k in _alignments(name, site_fwd):
        if kind in kinds:
            continue
        start = position0 - k
        end = start + len(q)
        if circular:
            if len(q) > length:
                continue
            bases = "".join(seq[i % length] for i in range(start, end))
        else:
            if start < 0 or end > length:
                continue
            bases = seq[start:end]
        if _matches(bases, q):
            kinds.add(kind)
    return sorted(kinds)
