"""S7 呈现：双峰（疑似混合样品）的结论文案（只读 crosscheck.mixed_summary）

- `read_lines`：⚠ 单条 / 多条 widespread 汇总行 + ↳ 逐 read 待核子行
  （进折叠组；scattered 待核行由 resolved_regions 追加在其后）；
- `signal_lines`：多引物同报双峰的 ⚠ 行（正文常驻）。
措辞与 v17 逐字一致（一期等价重构）。
"""

from __future__ import annotations

from typing import Dict, List

from core.sanger.crosscheck import pos_ranges_str


def _frac_range(fracs: List) -> str:
    fr = sorted(f for f in fracs if f is not None)
    if not fr:
        return ""
    lo, hi = fr[0], fr[-1]
    return f"约 {lo}%" if lo == hi else f"约 {lo}–{hi}%"


def check_phrase(chk: Dict) -> str:
    """单条 read 互检摘要短语（仅统计非零项，全零返回空串）"""
    parts = []
    if chk["multi"]:
        parts.append(f"{chk['multi']} 处其他引物同报双峰（倾向真实混合）")
    if chk["clean"]:
        parts.append(f"{chk['clean']} 处其他引物覆盖且峰形单一（倾向噪声）")
    if chk.get("batch"):
        parts.append(f"{chk['batch']} 处同批同引物其他克隆同位点同样有次峰（批内背景）")
    if chk["uncovered"]:
        ref_txt = pos_ranges_str(chk.get("uncovered_ref") or [])
        parts.append(f"{chk['uncovered']} 处无其他引物覆盖待核"
                     + (f"（参考位置 {ref_txt}）" if ref_txt else ""))
    return "互检：" + "、".join(parts) if parts else ""


def _single_wide(w: Dict, crossable: bool) -> str:
    chk = w["chk"]
    frac_txt = _frac_range([w["frac"]])
    if w["noise_like"]:
        src = ("跨引物互检：多数双峰位点被其他引物测过且峰形单一、判读一致，"
               if not chk.get("batch") else
               "批内同引物对照：多数双峰位点在同批同引物其他克隆的同一位点同样"
               "出现同碱基次峰（序列上下文背景）"
               + ("或被其他引物干净读出，" if chk.get("clean") else "，"))
        line = (f"⚠ {w['filename']} 检出 {w['count']} 处双峰位点"
                + (f"（估计次要克隆占比{frac_txt}）" if frac_txt else "")
                + f"——{src}"
                "更倾向该 read 自身信号问题（信号不稳区以引物首端为主）"
                "而非真实混合，建议核对峰图与互检位置后复测该段")
        if w["stretch"] and w["span_start"] is not None:
            line += (f"；自位置 {w['span_start']} 起存在连续双峰段"
                     "（若互检未覆盖该段，需警惕两个克隆相差插入/缺失）")
        return line
    line = (f"⚠ 疑似混合样品：{w['filename']} 检出 {w['count']} 处双峰位点"
            + (f"（估计次要克隆占比{frac_txt}）" if frac_txt else "")
            + "，主峰序列按多数碱基判读——建议重新挑单克隆划线培养后复测")
    if w["stretch"]:
        line += (f"；自位置 {w['span_start']} 起存在连续双峰段"
                 "（两个克隆相差插入/缺失时的典型形态）")
    note = check_phrase(chk) if crossable else ""
    if note:
        line += f"；{note}"
    return line


def _seq_overlap(w: Dict) -> bool:
    """ReadCall 口径下未经其他 read 印证的单条 read 套峰（头行判待定）"""
    return "rc_state" in w and not w.get("corroborated")


def _seq_overlap_line(w: Dict) -> str:
    if w["rc_state"] == "SHIFT_AFTER_POINT" and w.get("breakpoint") is not None:
        how, act = (f"自 read 第 {w['breakpoint']} 位起整体套峰",
                    "建议从另一侧引物测序确认；若复现，可能是该点附近的 indel 杂合")
    else:
        how, act = ("全程套峰",
                    "建议重测；若复现，检查引物特异性（多位点结合、二聚体）与模板纯度")
    return (f"⚠ 测序套峰：{w['filename']} {how}（{w['count']} 处双峰），"
            f"仅此一条 read，未经其他引物印证——{act}")


def read_lines(ms: Dict) -> List[str]:
    lines: List[str] = []
    for w in ms["wide"]:
        if _seq_overlap(w):
            lines.append(_seq_overlap_line(w))
    wide = [w for w in ms["wide"] if not _seq_overlap(w)]
    if len(wide) == 1:
        lines.append(_single_wide(wide[0], ms["crossable"]))
    elif wide:
        counts = sorted(w["count"] for w in wide)
        cr = (str(counts[0]) if counts[0] == counts[-1]
              else f"{counts[0]}–{counts[-1]}")
        frac_txt = _frac_range([w["frac"] for w in wide])
        line = (f"⚠ {len(wide)} 条 read 均疑似混合样品（每条双峰位点 {cr} 处"
                + (f"，估计次要克隆占比{frac_txt}" if frac_txt else "")
                + "）——样品为两种质粒的混合，主峰序列按多数碱基判读，"
                "建议重新挑单克隆划线培养后复测")
        stretchy = [w["filename"] for w in wide
                    if w["stretch"] and w["span_start"] is not None]
        if stretchy:
            line += f"；{'、'.join(stretchy[:2])} 存在连续双峰段（两个克隆相差插入/缺失时的典型形态）"
        lines.append(line)
    for lc in ms.get("local") or []:
        rng = "、".join(f"{a}–{b}" for a, b in lc["segments"])
        lines.append(f"△ {lc['filename']}：read 第 {rng} 位双峰集中（{lc['n_sites']} 处），"
                     "不判为混合样品，可能是该区段测序质量问题——建议复测")
    for row in ms["pending"]:
        if row.get("scattered_only"):
            u = row.get("uncovered_ref")
            tail = (f"，其中 {len(u)} 处无其他引物覆盖（参考位置 {pos_ranges_str(u)}）"
                    if u else "")
            if "positions" in row:
                tail = f"（read 位置 {pos_ranges_str(row['positions'])}）"
            lines.append(f"注意：{row['filename']} 有 {row['count']} 处零散双峰{tail}，"
                         "未形成套峰，不构成混合——供核对峰图")
            continue
        if "uncovered_ref" in row:
            u = row["uncovered_ref"]
            lines.append(
                f"  ↳ {row['filename']}：双峰 {row['count']} 处中 "
                f"{len(u)} 处无其他引物覆盖待核"
                f"（参考位置 {pos_ranges_str(u)}）")
            continue
        z = row["zone"]
        zone_txt = f"{z['start']}–{z['end']}"
        if (z["raw_start"], z["raw_end"]) != (z["start"], z["end"]):
            zone_txt += f"（原始 {z['raw_start']}–{z['raw_end']}）"
        lines.append(
            f"  ↳ {row['filename']}：双峰 {row['count']} 处，位于 read "
            f"{pos_ranges_str(row['positions'])}"
            f"（read 坐标；可信区 {zone_txt} 以外未计入）")
    return lines


def signal_lines(ms: Dict) -> List[str]:
    sites = ms["multi_sites"]
    if not sites:
        return []
    pos_txt = "、".join(str(rp) for rp in sites[:4])
    more = " 等" if len(sites) > 4 else ""
    return [f"⚠ {len(sites)} 处参考位点被多条引物同报双峰"
            f"（参考位置 {pos_txt}{more}，倾向真实混合）——建议重新挑单克隆复测"]
