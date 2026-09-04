"""限制酶位点扫描 — SnapGene 风格图谱的酶切位点数据源

内置常用限制酶表（识别序列 / 切割位置 / overhang），扫描给定序列生成
全部酶切位点（含反向链识别位点）。纯 Python 标准库实现。
"""

import re
from typing import Dict, List, Optional

# 内置常用限制酶。cutoff 相对识别序列 5' 端（正向链），overhang 描述切割后
# 突出端：'5prime'/'3prime'/None(blunt)。cut_offsets = (正向链切割位, 反向链切割位)
# 相对识别序列起点的偏移（可与识别序列长度相等，即切在识别序列外）。
ENZYME_TABLE: List[Dict] = [
    {"name": "EcoRI", "site": "GAATTC", "cut": (1, 5), "overhang": "5prime"},
    {"name": "BamHI", "site": "GGATCC", "cut": (1, 5), "overhang": "5prime"},
    {"name": "HindIII", "site": "AAGCTT", "cut": (1, 5), "overhang": "5prime"},
    {"name": "NotI", "site": "GCGGCCGC", "cut": (2, 6), "overhang": "5prime"},
    {"name": "XhoI", "site": "CTCGAG", "cut": (1, 5), "overhang": "5prime"},
    {"name": "SalI", "site": "GTCGAC", "cut": (1, 5), "overhang": "5prime"},
    {"name": "PstI", "site": "CTGCAG", "cut": (5, 1), "overhang": "3prime"},
    {"name": "SmaI", "site": "CCCGGG", "cut": (3, 3), "overhang": None},
    {"name": "XbaI", "site": "TCTAGA", "cut": (1, 5), "overhang": "5prime"},
    {"name": "SpeI", "site": "ACTAGT", "cut": (1, 5), "overhang": "5prime"},
    {"name": "NheI", "site": "GCTAGC", "cut": (1, 5), "overhang": "5prime"},
    {"name": "KpnI", "site": "GGTACC", "cut": (5, 1), "overhang": "3prime"},
    {"name": "SacI", "site": "GAGCTC", "cut": (5, 1), "overhang": "3prime"},
    {"name": "SphI", "site": "GCATGC", "cut": (5, 1), "overhang": "3prime"},
    {"name": "NcoI", "site": "CCATGG", "cut": (1, 5), "overhang": "5prime"},
    {"name": "NdeI", "site": "CATATG", "cut": (2, 4), "overhang": "5prime"},
    {"name": "NruI", "site": "TCGCGA", "cut": (3, 3), "overhang": None},
    {"name": "MluI", "site": "ACGCGT", "cut": (1, 5), "overhang": "5prime"},
    {"name": "AflII", "site": "CTTAAG", "cut": (1, 5), "overhang": "5prime"},
    {"name": "AflIII", "site": "ACRYGT", "cut": (1, 5), "overhang": "5prime"},
    {"name": "AgeI", "site": "ACCGGT", "cut": (1, 5), "overhang": "5prime"},
    {"name": "ApaI", "site": "GGGCCC", "cut": (5, 1), "overhang": "3prime"},
    {"name": "AscI", "site": "GGCGCGCC", "cut": (2, 6), "overhang": "5prime"},
    {"name": "AvaI", "site": "CYCGRG", "cut": (2, 4), "overhang": "5prime"},
    {"name": "BglII", "site": "AGATCT", "cut": (1, 5), "overhang": "5prime"},
    {"name": "BsaI", "site": "GGTCTC", "cut": (7, 11), "overhang": "5prime"},
    {"name": "BsmBI", "site": "CGTCTC", "cut": (7, 11), "overhang": "5prime"},
    {"name": "BspHI", "site": "TCATGA", "cut": (1, 5), "overhang": "5prime"},
    {"name": "BstBI", "site": "TTCGAA", "cut": (2, 4), "overhang": "5prime"},
    {"name": "ClaI", "site": "ATCGAT", "cut": (2, 4), "overhang": "5prime"},
    {"name": "DraI", "site": "TTTAAA", "cut": (3, 3), "overhang": None},
    {"name": "EcoRV", "site": "GATATC", "cut": (3, 3), "overhang": None},
    {"name": "HpaI", "site": "GTTAAC", "cut": (3, 3), "overhang": None},
    {"name": "KasI", "site": "GGCGCC", "cut": (2, 4), "overhang": "5prime"},
    {"name": "MluCI", "site": "AATT", "cut": (2, 2), "overhang": None},
    {"name": "NsiI", "site": "ATGCAT", "cut": (5, 1), "overhang": "3prime"},
    {"name": "PacI", "site": "TTAATTAA", "cut": (4, 4), "overhang": "3prime"},
    {"name": "PmeI", "site": "GTTTAAAC", "cut": (4, 4), "overhang": None},
    {"name": "SacII", "site": "CCGCGG", "cut": (2, 4), "overhang": "5prime"},
    {"name": "ScaI", "site": "AGTACT", "cut": (3, 3), "overhang": None},
    {"name": "SfoI", "site": "GGCGCGC", "cut": (3, 3), "overhang": None},
    {"name": "SnaBI", "site": "TACGTA", "cut": (3, 3), "overhang": None},
    {"name": "SrfI", "site": "GCCCGGGC", "cut": (4, 4), "overhang": None},
    {"name": "StuI", "site": "AGGCCT", "cut": (3, 3), "overhang": None},
    {"name": "SwaI", "site": "ATTTAAAT", "cut": (4, 4), "overhang": None},
    {"name": "XcmI", "site": "CCANNNNNNNTGG", "cut": (8, 9), "overhang": "3prime"},
    {"name": "BsaHI", "site": "GAYG", "cut": (2, 2), "overhang": "5prime"},
]

# IUPAC 模糊码 → 正则字符类
_IUPAC = {
    "A": "A", "C": "C", "G": "G", "T": "T",
    "R": "[AG]", "Y": "[CT]", "S": "[GC]", "W": "[AT]",
    "K": "[GT]", "M": "[AC]", "B": "[CGT]", "D": "[AGT]",
    "H": "[ACT]", "V": "[ACG]", "N": "[ACGT]",
}

_COMPLEMENT = str.maketrans("ACGTRYKMSWBDHVN", "TGCAYRMKSWVHDBN")


def _revcomp(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]


def _site_regex(site: str) -> "re.Pattern":
    return re.compile("".join(_IUPAC.get(ch, ch) for ch in site.upper()))


def find_enzyme_sites(
    sequence: str,
    enzymes: Optional[List[str]] = None,
) -> List[Dict]:
    """扫描序列，返回全部酶切位点（1-based 位置，含反向链识别）。

    返回条目: {name, position, strand, cut_fwd, cut_rev, overhang}
    position 为识别序列 5' 端在正向链上的 1-based 坐标；
    cut_fwd/cut_rev 为正向/反向链切割点（1-based，环上可能超出序列长度，需取模）。
    """
    seq = sequence.upper().replace("U", "T")
    seq_len = len(seq)
    results: List[Dict] = []

    for enzyme in ENZYME_TABLE:
        if enzymes and enzyme["name"] not in enzymes:
            continue
        site = enzyme["site"].upper()
        site_len = len(site)
        c1, c2 = enzyme["cut"]
        # 正向匹配 site，反向匹配 revcomp(site)（两者都直接落在正向链坐标系上）
        for strand, pattern in (
            ("+", _site_regex(site)),
            ("-", _site_regex(_revcomp(site))),
        ):
            for m in pattern.finditer(seq):
                a = m.start()  # 0-based：识别序列左端在正向链的位置
                if strand == "+":
                    # 正向链切在识别序列第 c1 位之后，反向链第 c2 位之后
                    cut_fwd = a + c1
                    cut_rev = a + c2 + 1
                else:
                    # 反向结合位点：位点沿反向阅读，切割坐标镜像
                    cut_rev = a + site_len - c1 + 1
                    cut_fwd = a + site_len - c2
                results.append({
                    "name": enzyme["name"],
                    "position": a + 1,
                    "strand": strand,
                    "cut_fwd": ((cut_fwd - 1) % seq_len) + 1,
                    "cut_rev": ((cut_rev - 1) % seq_len) + 1,
                    "overhang": enzyme["overhang"],
                })

    results.sort(key=lambda r: (r["position"], r["name"]))
    return results


def sites_cut_positions(sites: List[Dict]) -> Dict[str, List[int]]:
    """按酶名汇总切割位置（正向链，1-based 去重排序）"""
    out: Dict[str, List[int]] = {}
    for s in sites:
        cuts = out.setdefault(s["name"], [])
        for c in (s["cut_fwd"], s["cut_rev"]):
            if c not in cuts:
                cuts.append(c)
    out = {k: sorted(v) for k, v in out.items()}
    return out
