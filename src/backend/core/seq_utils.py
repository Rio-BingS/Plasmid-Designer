"""共享序列工具 — 反向互补 / GC 含量 / 标准遗传密码表 / 翻译的唯一实现

此前这些基础操作在 core 下散落着 ≥5 份手写副本（对 IUPAC 模糊码、大小写、
U/T 的处理各不相同）。本模块是唯一权威实现，约定：

- revcomp：支持 IUPAC 全部 15 个模糊码（大小写均可），未知字符原样保留；
- gc_fraction / gc_percent：G/C 大小写均计入（U 不计），空序列返回 0；
- CODON_TABLE / CODON_TO_AA / translate：NCBI 标准遗传密码（table 1），
  未识别密码子译为 'X'。

纯标准库、零依赖，可直接被 HF Spaces 裁剪部署复用。
"""

from typing import Dict, List

# IUPAC 互补映射（含模糊码）：A<->T(U->A)，C<->G，R<->Y，K<->M，
# S/W/N 自反，B<->V，D<->H；小写对称。未列出的字符原样保留。
_COMPLEMENT = str.maketrans(
    "ACGTURYSWKMBDHVNacgturyswkmbdhvn",
    "TGCAAYRSWMKVHDBNtgcaayrswmkvhdbn",
)


def revcomp(seq: str) -> str:
    """反向互补（支持 IUPAC 模糊码与大小写；未知字符原样保留）"""
    return seq.translate(_COMPLEMENT)[::-1]


def gc_fraction(seq: str) -> float:
    """GC 含量（0-1 比例；G/C 大小写均计入，空序列返回 0.0）"""
    if not seq:
        return 0.0
    s = seq.upper()
    return (s.count("G") + s.count("C")) / len(s)


def gc_percent(seq: str) -> float:
    """GC 含量（百分数 0-100；空序列返回 0.0）"""
    return gc_fraction(seq) * 100.0


# 标准遗传密码（NCBI table 1）：氨基酸（含终止 '*'）→ 密码子列表
CODON_TABLE: Dict[str, List[str]] = {
    "F": ["TTT", "TTC"],
    "L": ["TTA", "TTG", "CTT", "CTC", "CTA", "CTG"],
    "I": ["ATT", "ATC", "ATA"],
    "M": ["ATG"],
    "V": ["GTT", "GTC", "GTA", "GTG"],
    "S": ["TCT", "TCC", "TCA", "TCG", "AGT", "AGC"],
    "P": ["CCT", "CCC", "CCA", "CCG"],
    "T": ["ACT", "ACC", "ACA", "ACG"],
    "A": ["GCT", "GCC", "GCA", "GCG"],
    "Y": ["TAT", "TAC"],
    "H": ["CAT", "CAC"],
    "Q": ["CAA", "CAG"],
    "N": ["AAT", "AAC"],
    "K": ["AAA", "AAG"],
    "D": ["GAT", "GAC"],
    "E": ["GAA", "GAG"],
    "C": ["TGT", "TGC"],
    "W": ["TGG"],
    "R": ["CGT", "CGC", "CGA", "CGG", "AGA", "AGG"],
    "G": ["GGT", "GGC", "GGA", "GGG"],
    "*": ["TAA", "TAG", "TGA"],
}

# 密码子 → 氨基酸（由 CODON_TABLE 反转生成，避免维护第二份表）
CODON_TO_AA: Dict[str, str] = {
    codon: aa for aa, codons in CODON_TABLE.items() for codon in codons
}


def translate(dna: str, stop_at_stop: bool = True) -> str:
    """按标准遗传密码翻译 DNA（U 视作 T；未识别密码子 → 'X'）

    stop_at_stop=True：遇到终止密码子截断（ORF 翻译语义）；
    stop_at_stop=False：完整翻译到底，终止密码子保留为 '*'（验证语义，
    便于检出内部终止密码子）。长度不足整密码子的尾部忽略。
    """
    seq = dna.upper().replace("U", "T")
    protein = []
    for i in range(0, len(seq) - 2, 3):
        aa = CODON_TO_AA.get(seq[i:i + 3], "X")
        if aa == "*" and stop_at_stop:
            break
        protein.append(aa)
    return "".join(protein)
