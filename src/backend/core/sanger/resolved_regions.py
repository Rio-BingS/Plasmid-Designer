"""互检定案的区间裁决行构建（2026-11-07 自 pipeline._build_conclusion 迁出）。

被其他引物高质量互检排除的单 read 差异，按「源 read × 不相连位置段」聚合
为区间裁决行：每行 = 差异区间 + 源 read（方向 | 引物内坐标）+ 排除裁决。
文本行进结论文本；结构化区间随 payload 下发（resolved_regions）供前端
高亮（2026-11-05 用户定稿结构）。

位置段合并（间隔 ≤50bp 同段）与目的基因内坐标换算同时供 scattered 双峰
「无覆盖待核」行共用，两处表述同款。
"""

from typing import Dict, List, Optional, Tuple

from .homopolymer import pos_ranges_str
from .readmap import ReadRefMap

# 互检排除注记的验证质量分档：验证区域平均 Q ≥ 此值措辞「验证质量充分」
RESOLVED_STRONG_Q = 40

# 相邻位置间隔 ≤ 此值（bp）合并为同一个区段（read 末端信号衰减通常覆盖一小段）
SEG_GAP_BP = 50


def seg_label(positions: List[int], gap: int = SEG_GAP_BP) -> str:
    """连续/近邻位置合并成区段文本（参考坐标，间隔 ≤gap 同段）"""
    pos = sorted(set(positions))
    ranges = [[pos[0], pos[0]]]
    for p in pos[1:]:
        if p - ranges[-1][1] <= gap:
            ranges[-1][1] = p
        else:
            ranges.append([p, p])
    return "、".join(f"{a}-{b}" if a != b else str(a) for a, b in ranges)


def inner_label(positions: List[int], cds_offset: int,
                gap: int = SEG_GAP_BP) -> Optional[str]:
    """目的基因内坐标文本（无分析目标 / offset=0 时返回 None）"""
    if not cds_offset:
        return None
    pos = sorted(set(positions))
    ranges = [[pos[0], pos[0]]]
    for p in pos[1:]:
        if p - ranges[-1][1] <= gap:
            ranges[-1][1] = p
        else:
            ranges.append([p, p])
    return "、".join(
        f"{a - cds_offset}-{b - cds_offset}" if a != b else str(a - cds_offset)
        for a, b in ranges)


def cds_offset_of(features: List[Dict]) -> int:
    """目的基因模式的参考偏移（analysis_target 特征 1-based 起点 - 1）"""
    target = next((f for f in features if f.get("analysis_target")), None)
    return (target["start"] - 1) if target else 0


def _range_text(positions: List[int], cds_offset: int) -> str:
    seg = seg_label(positions)
    inner = inner_label(positions, cds_offset)
    return seg + (f"（目的基因内 {inner}）" if inner else "")


def scattered_pending_lines(read_results: List[Dict],
                            mixed_corroboration: Dict,
                            crossable: bool,
                            features: List[Dict]) -> List[str]:
    """scattered（个别双峰位点）中「无其他引物覆盖待核」的位点行。

    与区间裁决行同款表述（差异区间 + 参考位置，2026-11-06 定稿）；互检能
    归为噪声的位点不出行——双峰是源 read 自身属性，与构建判读无关。
    """
    lines: List[str] = []
    offset = cds_offset_of(features)
    by_read = mixed_corroboration["by_read"]
    for r in read_results:
        if r["mixed_profile"]["class"] != "scattered":
            continue
        chk = by_read.get(r["filename"]) or {}
        refs = sorted(chk.get("uncovered_ref") or [])
        if not crossable or refs:
            lines.append(
                f"  ↳ 差异区间 {_range_text(refs, offset)}：{r['filename']} 报出 "
                f"{len(refs)} 处双峰（参考位置 {pos_ranges_str(refs)}；"
                "无其他引物覆盖，无法互检，可能为个别碱基噪声）")
    return lines


def build_resolved_regions(resolved_variants: List[Dict],
                           read_results: List[Dict],
                           features: List[Dict],
                           ) -> Tuple[List[str], List[Dict]]:
    """互检定案差异 → （区间裁决文本行, 结构化 resolved_regions）。

    按源 read 分组聚合；引物内坐标为修剪后 read 内电泳链 1-based，经
    ReadRefMap 从参考位点反查（goal T9 统一口径；反向 read 的 read_pos
    已由 aligner 镜像回电泳链，旧 `trim_len - read_pos + 1` 是镜像时代的
    二次折算，带软剪切时错位——M10）。验证 read 取逐条变体证据的
    并集（排除源 read 自身），质量分档取最弱区域均值 Q。
    """
    lines: List[str] = []
    regions: List[Dict] = []
    if not resolved_variants:
        return [], []
    by_src: Dict[str, List[Dict]] = {}
    for v in resolved_variants:
        by_src.setdefault(v.get("read", "?"), []).append(v)
    by_filename = {r["filename"]: r for r in read_results}
    cds_offset = cds_offset_of(features)

    for src_name, vs in by_src.items():
        src = by_filename.get(src_name)
        direction = (src or {}).get("alignment", {}).get("direction") or "+"
        dir_txt = "反向" if direction == "-" else "正向"
        # 引物内坐标（修剪后电泳链 1-based）：ReadRefMap 反查优先
        # （权威列遍历口径），旧记录无对齐视图时回退变体 read_pos（同帧）
        rmap = ReadRefMap.from_alignment(src) if src else None
        coords = []
        for v in vs:
            c = rmap.primer_coord(v.get("ref_pos")) if rmap else None
            c = c if c is not None else v.get("read_pos")
            if c:
                coords.append(c)
        primer_txt = f"{min(coords)}-{max(coords)}" if coords else None
        # 逐条变体独立取验证 read（并集排除源 read 自身）；分档取最弱证据
        verifiers: set = set()
        min_q: Optional[float] = None
        for v in vs:
            for h in (v.get("cross_read_conflict") or {}).get("evidence") or []:
                if h.get("read") and h["read"] != src_name:
                    verifiers.add(h["read"])
                    mq = h.get("region_mean_q")
                    if mq is not None:
                        min_q = mq if min_q is None else min(min_q, mq)
        strength = ("验证质量充分" if min_q is not None and min_q >= RESOLVED_STRONG_Q
                    else "验证质量中等" if min_q is not None else "")
        verifier_txt = "、".join(sorted(verifiers)) or "其他引物"
        positions = sorted(v["ref_pos"] for v in vs)
        seg_txt = _range_text(positions, cds_offset)
        q_txt = "，验证质量充分" if strength == "验证质量充分" else ""
        lines.append(
            f"差异区间 {seg_txt}：{src_name}（{dir_txt}"
            + (f" | 引物内坐标 {primer_txt}" if primer_txt else "")
            + f"）报出 {len(vs)} 处差异 → 已被 {verifier_txt} "
            f"高质量覆盖排除{q_txt}，无需核对"
        )
        regions.append({
            "ref_range": seg_label(positions),
            "cds_offset": cds_offset or None,
            "source_read": src_name,
            "direction": direction,
            "primer_range": primer_txt,
            "variant_count": len(vs),
            "verifier_reads": sorted(verifiers),
            "verifier_quality": strength or None,
            "positions": positions,
        })
    return lines, regions
