"""跨 read 互检簇（goal T8/T10 自 homopolymer.py 迁出，纯搬运）

变异互检（junction 干净覆盖反证）与混合互检（_corroborate_mixed）共用
同一套区域质量标准（_cross_region_quality + _region_ok）：验证 read 的
对应列必须在其可信区内、±25bp 区域质量达标（均值 Q≥30、≥Q20 占 90%、
core 最低 Q≥20）、且该区未报其他差异。pipeline 经 homopolymer 的
re-export 维持全部旧导入路径。
"""

from typing import Dict, List, Optional, Set, Tuple

from core.sanger.aligner import fold_pos
from core.sanger.mixed_call import MINOR_AF_MIN
from core.sanger.readmap import _circ_len, _read_ref_maps
from core.sanger.reliable_zone import in_zone

# 互检常量（自 homopolymer 迁入；语义见行内注释）
CROSS_CHECK_MIN_Q = 20   # 跨 read 反证：junction 处 Q 低于此值时"未报"不构成反证
# 跨 read 反证看的是验证 read 在该位点周边一段区域的整体质量，不只是单个
# 碱基的 Q：单碱基 Q 高但周围信号塌陷（峰压缩、拖尾、信号衰减段）时，
# 这条 read 在此处同样不可靠。区域 = 位点两侧各 CROSS_REGION_HALF bp
# （±5bp 太窄，反映不出该引物这一段的测序质量；取 ±25bp 约 50bp 窗口，
# 与 Phred 质量窗口/修剪窗口同量级），截掉 read 首尾不可信区后至少要有
# CROSS_REGION_MIN_COLS 列。窗口宽了，单个低 Q 碱基不该一票否决，改用
# "≥Q20 的碱基占比"衡量整体；位点本身 ±CROSS_CORE_HALF 的核心区仍要求每个碱基 ≥Q20
CROSS_REGION_HALF = 25
CROSS_REGION_MIN_COLS = 30
CROSS_REGION_MEAN_Q = 30   # 区域平均 Q 下限
CROSS_REGION_HQ_FRAC = 0.9  # 区域内 ≥Q20 碱基占比下限
CROSS_CORE_HALF = 2
CROSS_CLEAN_HALF = 5       # 验证 read 自身在位点 ±5bp 内报了差异 → 不作反证


def pos_ranges_str(positions: List[int], max_ranges: int = 6) -> str:
    """位点列表压缩成连续区段（如 81–140、201–260）：结论给范围概览，
    超出 max_ranges 段截断并注明总段数，逐位细节在峰图里核对"""
    if not positions:
        return ""
    spans: List[List[int]] = []
    for p in positions:
        if spans and p == spans[-1][1] + 1:
            spans[-1][1] = p
        else:
            spans.append([p, p])
    parts = [f"{a}–{b}" if b > a else str(a) for a, b in spans[:max_ranges]]
    out = "、".join(parts)
    if len(spans) > max_ranges:
        out += f" 等 {len(spans)} 段"
    return out



def _cross_region_quality(r2c: Dict[int, Dict], lo: int, hi: int, L: int,
                          zone: Optional[Dict]) -> Optional[Dict]:
    """验证 read 在参考区间 [lo-HALF, hi+HALF] 的区域质量。

    区域截掉验证 read 可信区之外的部分与未覆盖列后统计：平均 Q、
    最低 Q、≥Q20 占比、列数；核心区（位点 ±CROSS_CORE_HALF）任一列缺失或
    落进不可信区返回 None——位点本身都测不好，谈不上反证。"""
    def call(p: int) -> Optional[Dict]:
        c = r2c.get(fold_pos(p, L) if L else p)
        if not c or not in_zone(zone, c["read_pos"]):
            return None
        return c
    core: List[int] = []
    for p in range(lo - CROSS_CORE_HALF, hi + CROSS_CORE_HALF + 1):
        c = call(p)
        if c is None:
            return None
        core.append(int(c["q"] or 0))
    qs: List[int] = []
    for p in range(lo - CROSS_REGION_HALF, hi + CROSS_REGION_HALF + 1):
        c = call(p)
        if c is not None:
            qs.append(int(c["q"] or 0))
    return {
        "region_mean_q": round(sum(qs) / len(qs), 1),
        "region_min_q": min(qs),
        "region_hq_frac": round(sum(1 for q in qs if q >= CROSS_CHECK_MIN_Q) / len(qs), 3),
        "region_cols": len(qs),
        "core_min_q": min(core),
    }


def _region_ok(rq: Optional[Dict]) -> bool:
    return (rq is not None
            and rq["region_cols"] >= CROSS_REGION_MIN_COLS
            and rq["region_mean_q"] >= CROSS_REGION_MEAN_Q
            and rq["region_hq_frac"] >= CROSS_REGION_HQ_FRAC
            and rq["core_min_q"] >= CROSS_CHECK_MIN_Q)


def region_quality_text(ev: Dict) -> str:
    """验证 read 区域质量的文字（结论/悬停共用）"""
    return (f"该区 {ev.get('region_cols', '?')}bp 平均 Q{ev['region_mean_q']:g}、"
            f"≥Q20 占 {round(ev.get('region_hq_frac', 0) * 100)}%")


def _corroborate_mixed(read_results: List[Dict]) -> Dict:
    """跨 read 双峰互检：一条引物的双峰位点映射到参考坐标后，看其他引物
    在同一位点的表现——用户视角的关键一层：另一条引物测过且峰形单一、
    判读碱基一致 → 多半是这条 read 自己的信号噪声（常见于引物首端）；
    其他引物同样报双峰 → 倾向真实混合；无其他引物覆盖 → 无法互检。

    返回 {by_read: {filename: {clean, multi, uncovered, multi_ref, clean_ref,
    uncovered_ref}},
          multi_sites: [(ref_pos, [filenames])], total, clean_total,
          multi_total, uncov_total}；单条 read 的分析互检无意义，字段照常
          返回（clean/multi 均为 0），由结论层判断是否展示。"""
    maps = [(r, *_read_ref_maps(r)) for r in read_results]
    # 环状跨原点 read 的折回坐标在原点处不单调，内插用展开坐标再折回
    unfolded = {r["filename"]: (_read_ref_maps(r, fold=False)[0], _circ_len(r["alignment"]))
                for r in read_results if _circ_len(r.get("alignment") or {})}

    def _approx(r: Dict, r2ref: Dict[int, int], pos: int) -> Optional[int]:
        u = unfolded.get(r["filename"])
        if u is None:
            return _approx_ref(r2ref, pos)
        rp = _approx_ref(u[0], pos)
        return fold_pos(rp, u[1]) if rp is not None else None

    def _approx_ref(r2ref: Dict[int, int], pos: int) -> Optional[int]:
        """位点在 read2ref 查不到时的近似定位。局部比对会在 read 端部留
        悬空（poly-A 同聚物区两端尤其常见），悬空段的双峰位点若直接归
        "待核"，会把实际被邻居 read 覆盖、可互检的位点误报成无法互检。
        以比对块两端（原始 read 坐标 ↔ 参考坐标）为锚点线性内插/外推
        ——Sanger read 与参考共线，锚点间斜率近似 ±1（反向 read 同式）"""
        if not r2ref:
            return None
        k_lo, k_hi = min(r2ref), max(r2ref)
        if k_hi == k_lo:
            return r2ref[k_lo]
        return round(r2ref[k_lo] + (pos - k_lo) * (r2ref[k_hi] - r2ref[k_lo])
                     / (k_hi - k_lo))

    # 各 read 报了双峰的参考坐标集合（互检时判"同报双峰"用）
    mixed_ref_by_read: Dict[str, set] = {}
    for r, r2ref, _c in maps:
        s = set()
        for e in r.get("mixed_detail") or []:
            rp = r2ref.get(e["pos"])
            if rp is None:
                rp = _approx(r, r2ref, e["pos"])
            if rp is not None:
                s.add(rp)
        mixed_ref_by_read[r["filename"]] = s
    # 各 read 在参考上已报的差异位点（变体 ∪ 自身双峰）：验证 read 在位点
    # 邻近报过任何差异 → 区域不干净，不作反证（与变异互检同口径，T8）
    var_pos_by_read: Dict[str, set] = {}
    for r in read_results:
        s = {int(vv["ref_pos"]) for vv in (r.get("alignment") or {}).get("variants") or []
             if vv.get("ref_pos")}
        var_pos_by_read[r["filename"]] = s | mixed_ref_by_read[r["filename"]]

    by_read: Dict[str, Dict] = {}
    multi_sites: Dict[int, List[str]] = {}
    for r, r2ref, _c in maps:
        fname = r["filename"]
        clean: List[int] = []
        multi: List[int] = []
        uncovered: List[int] = []
        uncovered_ref: List[int] = []
        for e in r.get("mixed_detail") or []:
            # 2026-11-08 复审 R3：低于次等位门槛的双峰（AF < MINOR_AF_MIN，
            # ratio_floor 降到 0.15 后检测带随之打开）是噪声级——不进互检/
            # 未决位点，由 verdict 端按 info 记录（与 mixed_variant 的
            # AF 门槛同一数值，af = ratio/(1+ratio) 同 mixed_call 口径）
            _ratio = float(e.get("ratio") or 0)
            if _ratio / (1 + _ratio) < MINOR_AF_MIN:
                continue
            rp = r2ref.get(e["pos"])
            if rp is None:
                rp = _approx(r, r2ref, e["pos"])
                if rp is None:
                    uncovered.append(e["pos"])
                    continue
            others_multi = [f for f, s in mixed_ref_by_read.items()
                            if f != fname and rp in s]
            if others_multi:
                multi.append(e["pos"])
                multi_sites.setdefault(rp, []).append(fname)
                continue
            # 其他引物在该位点峰形单一（自己没报双峰）→ 与变异互检同一
            # 标准复核（goal T8；B P0-5）：对应列在验证 read 可信区内、
            # ±25bp 区域质量达标（_region_ok：均值 Q≥30、≥Q20 占 90%、
            # core 最低 Q≥20）、验证 read 在位点邻近未报其他差异；达不到
            # 即 uncovered，不算 clean。源 read 位点无验证 read 覆盖时
            # call 为 None，永远归 uncovered，不能据此判为噪声
            ok = False
            for o, _o2ref, ocall in maps:
                if o["filename"] == fname:
                    continue
                call = ocall.get(rp)
                if not call or call["base"] != _c.get(rp, {}).get("base") \
                        or rp in mixed_ref_by_read[o["filename"]]:
                    continue
                if any(abs(k - rp) <= CROSS_CLEAN_HALF
                       for k in var_pos_by_read[o["filename"]]):
                    continue
                rq = _cross_region_quality(ocall, rp, rp,
                                           _circ_len(o.get("alignment") or {}),
                                           o.get("reliable_zone"))
                if _region_ok(rq):
                    ok = True
                    break
            (clean if ok else uncovered).append(e["pos"])
            if not ok:
                uncovered_ref.append(rp)
        by_read[fname] = {
            "clean": len(clean), "multi": len(multi), "uncovered": len(uncovered),
            "clean_ref": sorted({r2ref[p] for p in clean if p in r2ref}),
            "multi_ref": sorted({r2ref[p] for p in multi if p in r2ref}),
            "uncovered_ref": sorted(set(uncovered_ref)),
        }
    total = sum(d["clean"] + d["multi"] + d["uncovered"] for d in by_read.values())
    return {
        "by_read": by_read,
        "multi_sites": sorted(multi_sites.items()),
        "total": total,
        "clean_total": sum(d["clean"] for d in by_read.values()),
        "multi_total": sum(d["multi"] for d in by_read.values()),
        "uncov_total": sum(d["uncovered"] for d in by_read.values()),
    }



def _mixed_check_phrase(chk: Dict) -> str:
    """单条 read 互检摘要短语（仅统计非零项，全零返回空串）"""
    parts = []
    if chk["multi"]:
        parts.append(f"{chk['multi']} 处其他引物同报双峰（倾向真实混合）")
    if chk["clean"]:
        parts.append(f"{chk['clean']} 处其他引物覆盖且峰形单一（倾向噪声）")
    if chk["uncovered"]:
        ref_txt = pos_ranges_str(chk.get("uncovered_ref") or [])
        parts.append(f"{chk['uncovered']} 处无其他引物覆盖待核"
                     + (f"（参考位置 {ref_txt}）" if ref_txt else ""))
    return "互检：" + "、".join(parts) if parts else ""

