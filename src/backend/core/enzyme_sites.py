"""限制酶位点扫描 — 全站唯一的限制酶模型

酶参数（识别序列 / 正反链切割偏移 / 突出端）一律取自 Biopython 自带的
Bio.Restriction（REBASE 数据），不再手写酶表：此前三套手写表（本模块、
sequence_analysis.RESTRICTION_ENZYMES、clone_strategy 的回退扫描）曾多次
各自写错（KNOWN_ISSUES 6.1/6.4、BsaHI 等）。扫描器本身保持原有实现
（IUPAC 正则、双链、环状跨原点），输出字典形状不变。

Bio.Restriction 首次导入会构建全部 REBASE 酶类（约 6 MB RSS / 0.1 s），
因此只在第一次真正需要酶参数时才导入，模块导入本身不触发。
"""

import re
from collections.abc import Sequence as _SequenceABC
from functools import lru_cache
from typing import Dict, Iterator, List, Optional, Tuple

from core.seq_utils import revcomp as _revcomp

# 默认酶菜单：图谱/位点扫描在未指定酶时扫描的集合。即此前 enzyme_sites
# 手写表的 47 种 + 只存在于 sequence_analysis 旧表的 4 种（AvrII/BbsI/
# PvuII/SbfI），合并为 51 种。显式点名时还可使用 REBASE 中任何一把
# 有商业来源、单一切割的酶（见 get_enzyme）。
DEFAULT_ENZYMES: Tuple[str, ...] = (
    "EcoRI", "BamHI", "HindIII", "NotI", "XhoI", "SalI", "PstI", "SmaI",
    "XbaI", "SpeI", "NheI", "KpnI", "SacI", "SphI", "NcoI", "NdeI", "NruI",
    "MluI", "AflII", "AflIII", "AgeI", "ApaI", "AscI", "AvaI", "BglII",
    "BsaI", "BsmBI", "BspHI", "BstBI", "ClaI", "DraI", "EcoRV", "HpaI",
    "KasI", "MluCI", "NsiI", "PacI", "PmeI", "SacII", "ScaI", "SfoI",
    "SnaBI", "SrfI", "StuI", "SwaI", "XcmI", "BsaHI",
    "AvrII", "BbsI", "PvuII", "SbfI",
)

# IUPAC 模糊码 → 正则字符类
_IUPAC = {
    "A": "A", "C": "C", "G": "G", "T": "T",
    "R": "[AG]", "Y": "[CT]", "S": "[GC]", "W": "[AT]",
    "K": "[GT]", "M": "[AC]", "B": "[CGT]", "D": "[AGT]",
    "H": "[ACT]", "V": "[ACG]", "N": "[ACGT]",
}


def _bases(ch: str) -> set:
    return set(_IUPAC.get(ch, ch).strip("[]"))


def _can_self_overlap(site: str) -> bool:
    """识别序列能否与自身错位重叠（如 ACRYGT 在 ACACGTGT 上的两个实例）"""
    n = len(site)
    return any(
        all(_bases(site[i]) & _bases(site[i + k]) for i in range(n - k))
        for k in range(1, n)
    )


@lru_cache(maxsize=None)
def _site_regex(site: str) -> "re.Pattern":
    body = "".join(_IUPAC.get(ch, ch) for ch in site.upper())
    # 可自重叠的识别序列用前瞻断言包裹以报出全部重叠实例，与 REBASE/
    # Bio.Restriction 的计数口径一致（非重叠 finditer 会漏报）；不可能
    # 重叠的序列用普通匹配，扫描快约 4 倍
    if _can_self_overlap(site.upper()):
        return re.compile("(?=" + body + ")")
    return re.compile(body)


@lru_cache(maxsize=None)
def get_enzyme(name: str) -> Optional[Dict]:
    """按名称从 REBASE 取酶参数；未知/不适用的酶返回 None。

    返回 {name, site, cut: (cut_fwd_offset, cut_rev_offset), overhang}，
    与旧手写表同形：cut 为正/反向链切割点相对识别序列 5' 端的偏移
    （= (fst5, size + fst3)，可落在识别序列之外，如 Type IIS），
    overhang 为 '5prime' / '3prime' / None（平末端）。

    只接受有商业来源、每个位点只切一次（单切割对）、切割位置已知的酶；
    两侧双切的酶（BaeI 等）与切点未定的酶返回 None。
    """
    if not name or not isinstance(name, str):
        return None
    from Bio import Restriction as _R  # 惰性导入：见模块文档
    from Bio.Restriction.Restriction import RestrictionType

    enz = getattr(_R, name, None)
    if not isinstance(enz, RestrictionType) or enz.__name__ != name:
        return None
    if not enz.is_comm() or enz.is_unknown() or enz.cut_twice():
        return None
    if enz.fst5 is None or enz.fst3 is None:
        return None
    site = str(enz.site).upper()
    if not site or any(ch not in _IUPAC for ch in site):
        return None
    if enz.is_5overhang():
        overhang = "5prime"
    elif enz.is_3overhang():
        overhang = "3prime"
    else:
        overhang = None
    return {
        "name": name,
        "site": site,
        "cut": (enz.fst5, enz.size + enz.fst3),
        "overhang": overhang,
    }


class _LazyEnzymeTable(_SequenceABC):
    """DEFAULT_ENZYMES 的参数表；首次访问时才从 REBASE 构建。

    兼容旧的模块级 ENZYME_TABLE 列表（可迭代/索引/len），但导入本模块
    不会因此加载 Bio.Restriction。
    """

    def _rows(self) -> List[Dict]:
        return _default_table()

    def __getitem__(self, i):
        return self._rows()[i]

    def __len__(self) -> int:
        return len(self._rows())

    def __iter__(self) -> Iterator[Dict]:
        return iter(self._rows())

    def __repr__(self) -> str:
        return f"ENZYME_TABLE({len(DEFAULT_ENZYMES)} enzymes, REBASE-backed)"


@lru_cache(maxsize=1)
def _default_table() -> List[Dict]:
    rows = []
    for name in DEFAULT_ENZYMES:
        spec = get_enzyme(name)
        if spec is None:  # pragma: no cover - 由 test_default_menu_complete 守护
            raise RuntimeError(f"默认酶 {name} 不在 Bio.Restriction 中")
        rows.append(spec)
    return rows


ENZYME_TABLE = _LazyEnzymeTable()


def is_palindromic(spec: Dict) -> bool:
    return _revcomp(spec["site"]) == spec["site"]


def scan_sites(
    seq: str,
    spec: Dict,
    circular: bool = False,
    palindrome_both_strands: bool = True,
) -> Iterator[Tuple[int, str, int, int]]:
    """单把酶的原始扫描结果：逐个产出 (a, strand, cut_fwd, cut_rev)。

    seq 须已大写、U→T。a 为识别序列左端在正向链的 0-based 位置；
    cut_fwd/cut_rev 为正/反向链「切点前一位」的 1-based 坐标，未取模
    （线性序列上的 Type IIS 切点可能落在序列之外，由调用方决定如何处理）。
    palindrome_both_strands=False 时回文识别序列只按正链报一次。
    """
    seq_len = len(seq)
    if seq_len == 0:
        return
    site = spec["site"]
    site_len = len(site)
    c1, c2 = spec["cut"]
    scan_seq = seq
    if circular:
        ext = site_len - 1
        # 极短环：扩展可能超过一圈，按需重复后截取
        scan_seq = seq + (seq * (ext // seq_len + 1))[:ext]
    patterns = [("+", _site_regex(site))]
    if palindrome_both_strands or not is_palindromic(spec):
        # 反向匹配 revcomp(site)，同样直接落在正向链坐标系上
        patterns.append(("-", _site_regex(_revcomp(site))))
    for strand, pattern in patterns:
        for m in pattern.finditer(scan_seq):
            a = m.start()
            if a >= seq_len:
                break  # 起点已绕回原序列：由原位置的匹配代表
            if strand == "+":
                # 正向链切在识别序列第 c1 位之后，反向链第 c2 位之后
                yield a, strand, a + c1, a + c2 + 1
            else:
                # 反向结合位点：位点沿反向阅读，切割坐标镜像
                yield a, strand, a + site_len - c2, a + site_len - c1 + 1


def find_enzyme_sites(
    sequence: str,
    enzymes: Optional[List[str]] = None,
    circular: bool = False,
) -> List[Dict]:
    """扫描序列，返回全部酶切位点（1-based 位置，含反向链识别）。

    enzymes 为空时扫描 DEFAULT_ENZYMES；点名的未知酶静默跳过。

    返回条目: {name, position, strand, cut_fwd, cut_rev, overhang, recognition}
    position 为识别序列 5' 端在正向链上的 1-based 坐标；
    cut_fwd/cut_rev 为正向/反向链切割点（1-based，已对序列长度取模）。
    回文位点按正反链各报一条（图谱/注释依赖此口径）。

    circular=True（质粒/载体）时额外扫描跨越原点的位点：在序列末尾接上
    开头 site_len-1 个碱基再扫描，只保留起点落在原序列内的匹配，
    因此不会重复计数。
    """
    seq = sequence.upper().replace("U", "T")
    seq_len = len(seq)
    results: List[Dict] = []
    if seq_len == 0:
        return results

    if enzymes:
        specs = [s for s in (get_enzyme(n) for n in dict.fromkeys(enzymes)) if s]
    else:
        specs = list(ENZYME_TABLE)

    for spec in specs:
        for a, strand, cut_fwd, cut_rev in scan_sites(seq, spec, circular):
            results.append({
                "name": spec["name"],
                "position": a + 1,
                "strand": strand,
                "cut_fwd": ((cut_fwd - 1) % seq_len) + 1,
                "cut_rev": ((cut_rev - 1) % seq_len) + 1,
                "overhang": spec["overhang"],
                "recognition": spec["site"],
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
