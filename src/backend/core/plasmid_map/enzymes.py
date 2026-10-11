"""图谱酶切位点：契约 MapEnzyme 形状

酶参数与扫描复用 core/enzyme_sites（REBASE / Bio.Restriction 唯一酶模型、
IUPAC 正则、环状跨原点扫描）。这里只做：

- 酶集合（common / unique6 / commercial / golden_gate）
- 每个位点只报一次（回文位点不再正反各一条），给出双链切点
- 切点口径：cut_top / cut_bottom = 该链在「第几位碱基之后」被切（正向链
  坐标，0..length）；环状分子取模到 1..length，线性分子上切点落在分子外
  （Type IIS 靠近末端）或恰在末端的位点不算切割，直接丢弃
- 每个粘端位点给出实际突出端 sites[].overhang（两切点之间的碱基，按正向链
  5'→3' 读）：Type IIS（REBASE 记作 NNNN）与简并识别序列（AvaI 等）的突出端
  因位点而异，酶级 overhang 只是模板
"""

from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple

from core.enzyme_sites import DEFAULT_ENZYMES, get_enzyme, scan_sites

GOLDEN_GATE_CANDIDATES = ("BsaI", "BsmBI", "BbsI", "SapI", "Esp3I", "PaqCI")


@lru_cache(maxsize=1)
def _commercial_names() -> Tuple[str, ...]:
    """REBASE 中有商业来源、单切割对、切点已知的全部酶（get_enzyme 认可）"""
    from Bio import Restriction as _R

    names = sorted({e.__name__ for e in _R.CommOnly})
    return tuple(n for n in names if get_enzyme(n) is not None)


def _specific_bases(site: str) -> int:
    return sum(1 for ch in site if ch != "N")


@lru_cache(maxsize=1)
def enzyme_sets() -> Tuple[Dict, ...]:
    """GET /api/map/enzyme-sets 的内容（按 id 稳定排序）"""
    commercial = _commercial_names()
    unique6 = tuple(n for n in commercial
                    if _specific_bases(get_enzyme(n)["site"]) >= 6)
    golden = tuple(n for n in GOLDEN_GATE_CANDIDATES if get_enzyme(n) is not None)
    return (
        {"id": "common", "name": "常用酶",
         "description": f"站内默认酶菜单（{len(DEFAULT_ENZYMES)} 种常用克隆酶）",
         "enzymes": list(DEFAULT_ENZYMES)},
        {"id": "unique6", "name": "6+ 碱基识别酶",
         "description": "全部可商购、识别 ≥6 个确定碱基的酶；前端按 cut_count==1 筛选单切位点",
         "enzymes": list(unique6)},
        {"id": "commercial", "name": "全部商品酶",
         "description": "REBASE（Bio.Restriction）中全部可商购、切点已知的单切割对酶",
         "enzymes": list(commercial)},
        {"id": "golden_gate", "name": "Golden Gate（Type IIS）",
         "description": "Golden Gate 组装常用 Type IIS 酶",
         "enzymes": list(golden)},
    )


def enzyme_set_names(set_id: str) -> Optional[List[str]]:
    for s in enzyme_sets():
        if s["id"] == set_id:
            return list(s["enzymes"])
    return None


@lru_cache(maxsize=None)
def enzyme_info(name: str) -> Optional[Dict]:
    """单把酶的静态属性：recognition 记法、末端类型、突出端序列"""
    spec = get_enzyme(name)
    if spec is None:
        return None
    from Bio import Restriction as _R

    enz = getattr(_R, name)
    site = spec["site"]
    size = len(site)
    c1, c2 = spec["cut"]
    if spec["overhang"] == "5prime":
        ends = "5'"
    elif spec["overhang"] == "3prime":
        ends = "3'"
    else:
        ends = "blunt"
    overhang = None
    if ends != "blunt":
        ov = str(enz.ovhgseq or "").upper()
        overhang = ov or None
    return {
        "name": name,
        "recognition": _recognition(site, c1, c2),
        "ends": ends,
        "overhang": overhang,
        "spec": spec,
    }


def _recognition(site: str, c1: int, c2: int) -> str:
    """NEB 习惯记法：位点内切 'G^AATTC'；位点外切 'GGTCTC(1/5)' / '(8/12)SITE'"""
    size = len(site)
    if 0 <= c1 <= size and 0 <= c2 <= size:
        return site[:c1] + "^" + site[c1:]
    if c1 > size and c2 > size:
        return f"{site}({c1 - size}/{c2 - size})"
    if c1 < 0 and c2 < 0:
        return f"({-c1}/{-c2}){site}"
    return f"{site}({c1 - size}/{c2 - size})"


def scan_enzyme(seq: str, name: str, circular: bool) -> Optional[Dict]:
    """单把酶 → MapEnzyme（不切返回带空 sites 的条目；未知酶返回 None）"""
    info = enzyme_info(name)
    if info is None:
        return None
    spec = info["spec"]
    length = len(seq)
    sites = []
    seen = set()
    for a, strand, cut_fwd, cut_rev in scan_sites(seq, spec, circular,
                                                  palindrome_both_strands=False):
        # enzyme_sites 的 cut_rev 是反向链「切点前一位」（沿反向链阅读，
        # 落在切点右侧），换成正向坐标下「切点左侧碱基」即减一
        raw_top, raw_bottom = cut_fwd, cut_rev - 1
        top, bottom = raw_top, raw_bottom
        if circular:
            top = ((top - 1) % length) + 1
            bottom = ((bottom - 1) % length) + 1
        elif not (0 < top < length and 0 < bottom < length):
            continue
        key = (a, top, bottom)
        if key in seen:
            continue
        seen.add(key)
        sign = 1 if strand == "+" else -1
        site = {
            "position": a + 1,
            "cut_top": top,
            "cut_bottom": bottom,
            "strand": sign,
        }
        if raw_top != raw_bottom:
            site["overhang"] = _site_overhang(seq, raw_top, raw_bottom, circular)
        sites.append(site)
    sites.sort(key=lambda s: (s["position"], s["strand"]))
    out = {
        "name": name,
        "recognition": info["recognition"],
        "ends": info["ends"],
        "overhang": info["overhang"],
        "cut_count": len(sites),
        "sites": sites,
    }
    return out


def _site_overhang(seq: str, raw_top: int, raw_bottom: int, circular: bool) -> str:
    """两切点之间的碱基（正向链 5'→3'）；raw_* 为未取模的「切点前一位」坐标。
    线性分子上调用方已保证两切点都在分子内部"""
    lo, hi = sorted((raw_top, raw_bottom))
    if circular:
        n = len(seq)
        return "".join(seq[i % n] for i in range(lo, hi))
    return seq[lo:hi]


def resolve_enzymes(enzyme_set: str = "common",
                    enzymes: Optional[Iterable[str]] = None) -> Tuple[List[str], List[str]]:
    """(酶名列表, 告警)。显式 enzymes 优先于 enzyme_set；未知名称告警跳过"""
    warnings: List[str] = []
    if enzymes:
        names, unknown = [], []
        for n in dict.fromkeys(str(x).strip() for x in enzymes if x):
            (names if get_enzyme(n) else unknown).append(n)
        if unknown:
            warnings.append("未知或不支持的酶已忽略：" + "、".join(unknown[:20]))
        return names, warnings
    names = enzyme_set_names(enzyme_set)
    if names is None:
        warnings.append(f"未知酶集合 {enzyme_set}，已改用 common")
        names = list(DEFAULT_ENZYMES)
    return names, warnings


def find_map_enzymes(seq: str, names: Iterable[str], circular: bool) -> List[Dict]:
    """扫描一组酶，只返回至少切一次的（契约：cut_count >= 1），按名称排序"""
    out = []
    for n in names:
        e = scan_enzyme(seq, n, circular)
        if e and e["cut_count"] >= 1:
            out.append(e)
    out.sort(key=lambda e: e["name"].lower())
    return out
