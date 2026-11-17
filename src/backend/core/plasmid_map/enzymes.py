"""图谱酶切位点：契约 MapEnzyme 形状

酶参数与扫描复用 core/enzyme_sites（REBASE / Bio.Restriction 唯一酶模型、
IUPAC 正则、环状跨原点扫描）。酶数据（酶集合 common / unique6 / commercial /
golden_gate / type_iis、单酶属性 enzyme_info、详情 enzyme_detail、厂商表
suppliers）同样只在 core/enzyme_sites 维护，这里原名 re-export。本模块只做：

- 每个位点只报一次（回文位点不再正反各一条），给出双链切点
- 切点口径：cut_top / cut_bottom = 该链在「第几位碱基之后」被切（正向链
  坐标，0..length）；环状分子取模到 1..length，线性分子上切点落在分子外
  （Type IIS 靠近末端）或恰在末端的位点不算切割，直接丢弃
- 每个粘端位点给出实际突出端 sites[].overhang（两切点之间的碱基，按正向链
  5'→3' 读）：Type IIS（REBASE 记作 NNNN）与简并识别序列（AvaI 等）的突出端
  因位点而异，酶级 overhang 只是模板
- Dam/Dcm 甲基化：酶级 methylation（敏感类型）与位点级 blocked_by（该位点
  落在会阻断/抑制切割的甲基化上下文里），数据见 methylation.py
- 酶详情（enzyme_detail，GET /api/map/enzymes/{name}）与厂商表（suppliers，
  GET /api/map/suppliers）：REBASE 厂商代码/名称、可商购同切酶/新切酶、
  Type IIS 标记。不放进 MapDocument，避免大图谱载荷膨胀；前端按需取。
  Bio.Restriction 的 opt_temp/inact_temp 对全部酶都是 37/65 的占位值
  （TaqI 也是 37 °C），不作为数据暴露
"""

import os
from typing import Dict, Iterable, List, Optional, Tuple

# 酶数据（菜单、酶集合、单酶属性/详情、厂商表）的唯一数据源是 core.enzyme_sites，
# 这里原样 re-export，对外接口不变；本模块只保留图谱契约形状的扫描与裁剪。
from core.enzyme_sites import (  # noqa: F401  (re-export)
    DEFAULT_ENZYMES, GOLDEN_GATE_CANDIDATES, _commercial_names, _commercial_relatives,
    _recognition, _specific_bases, _supplier_table, enzyme_detail, enzyme_info,
    enzyme_set_names, enzyme_sets, get_enzyme, golden_gate_geometry,
    is_modification_dependent, is_type_iis, scan_sites, suppliers, type_iis_enzymes,
)
from core.plasmid_map.methylation import methylation_kinds, site_blocked_by  # noqa: F401

# 位点明细上限：cut_count 仍是全部位点数，超出的 sites 截断并标 sites_truncated。
# 单把酶超过 MAX_SITES_PER_ENZYME 只留前若干个；整份文档位点明细超过
# MAX_TOTAL_SITES 时，从切点最多的酶起清空其明细（高频切酶在图上本就不画）
MAX_SITES_PER_ENZYME = int(os.environ.get("MAP_MAX_SITES_PER_ENZYME", "500"))
MAX_TOTAL_SITES = int(os.environ.get("MAP_MAX_TOTAL_SITES", "60000"))


def scan_enzyme(seq: str, name: str, circular: bool,
                max_sites: Optional[int] = None) -> Optional[Dict]:
    """单把酶 → MapEnzyme（不切返回带空 sites 的条目；未知酶返回 None）。

    cut_count 统计全部识别位点，包括被甲基化阻断的（blocked_by 只是提示）；
    max_sites 给定时 sites 只保留前 max_sites 个（按扫描顺序 = 位置），
    截断时带 sites_truncated=true"""
    info = enzyme_info(name)
    if info is None:
        return None
    spec = info["spec"]
    length = len(seq)
    raw = []
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
        raw.append((a, 1 if strand == "+" else -1, top, bottom, raw_top, raw_bottom))
    raw.sort(key=lambda r: (r[0], r[1]))
    keep = raw if max_sites is None else raw[:max_sites]
    sites = []
    # 明细（突出端、甲基化阻断）只为保留的位点生成
    for a, sign, top, bottom, raw_top, raw_bottom in keep:
        site = {
            "position": a + 1,
            "cut_top": top,
            "cut_bottom": bottom,
            "strand": sign,
        }
        if raw_top != raw_bottom:
            site["overhang"] = _site_overhang(seq, raw_top, raw_bottom, circular)
        blocked = site_blocked_by(seq, circular, name, spec["site"], a, sign)
        if blocked:
            site["blocked_by"] = blocked
        sites.append(site)
    out = {
        "name": name,
        "recognition": info["recognition"],
        "ends": info["ends"],
        "overhang": info["overhang"],
        "cut_count": len(raw),
        "sites": sites,
    }
    if len(sites) < len(raw):
        out["sites_truncated"] = True
    if info["methylation"]:
        out["methylation"] = list(info["methylation"])
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


def find_map_enzymes(seq: str, names: Iterable[str], circular: bool,
                     warnings: Optional[List[str]] = None) -> List[Dict]:
    """扫描一组酶，只返回至少切一次的（契约：cut_count >= 1），按名称排序。

    位点明细受 MAX_SITES_PER_ENZYME / MAX_TOTAL_SITES 约束（cut_count 不受影响），
    截断的酶带 sites_truncated=true，并在 warnings 里列出"""
    out = []
    for n in names:
        e = scan_enzyme(seq, n, circular, max_sites=MAX_SITES_PER_ENZYME)
        if e and e["cut_count"] >= 1:
            out.append(e)
    total = sum(len(e["sites"]) for e in out)
    if total > MAX_TOTAL_SITES:
        for e in sorted(out, key=lambda e: (-e["cut_count"], e["name"])):
            if total <= MAX_TOTAL_SITES:
                break
            total -= len(e["sites"])
            e["sites"] = []
            e["sites_truncated"] = True
    truncated = [e["name"] for e in out if e.get("sites_truncated")]
    if truncated and warnings is not None:
        shown = "、".join(truncated[:10]) + (f" 等 {len(truncated)} 种" if len(truncated) > 10 else "")
        warnings.append(f"{shown}酶切位点过多，只返回部分位点明细（cut_count 为完整计数）")
    out.sort(key=lambda e: e["name"].lower())
    return out
