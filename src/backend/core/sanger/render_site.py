"""S7 呈现：位点（变体）的结论文案（只读结构化原因）

分工（S7 第 2 片，v17 等价）：
- 判定在分析阶段：`evidence.low_confidence_cause` 按 v17 分支顺序选出低置信
  的主要原因，产出结构化 `low_cause`（code + 数值），写入 v["low_cause"]；
  可信区外原因由 `reliable_zone.zone_cause` 给出结构化 zone_kind；
- 文案在这里：`low_reason_text` 只读 low_cause 与 dye_blob / in_mixed_template /
  cross_read_weak 等结构化字段拼出 low_reason；`severity_lines` 生成变体
  影响摘要（原 annotator.summarize_severity）。
措辞与 v17 逐字一致（一期等价重构）。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core.sanger.reliable_zone import TAIL_EXT_REASON


def region_quality_text(ev: Dict) -> str:
    """验证 read 区域质量的文字（结论/悬停共用）"""
    return (f"该区 {ev.get('region_cols', '?')}bp 平均 Q{ev['region_mean_q']:g}、"
            f"≥Q20 占 {round(ev.get('region_hq_frac', 0) * 100)}%")


def zone_text_of(zc: Dict) -> str:
    """可信区外原因（reliable_zone.zone_cause 的结构化结果）→ 文案"""
    k = zc["zone_kind"]
    if k == "tail_ext":
        return TAIL_EXT_REASON.format(g=zc["tail_gray"])
    if k == "front_hard":
        return f"位于 read 首端 1–{zc['front_hard']}bp 硬不可信区"
    if k == "front":
        return f"位于 read 首端不可信区（{zc['front_reason']}）"
    if k == "tail_hard":
        return f"位于 read {zc['tail_hard']}bp 之后，一律不可信"
    if k == "tail_gray":
        return (f"位于 read 尾端灰区 {zc['tail_gray'] + 1}–{zc['tail_hard']}bp"
                f"（默认不可信，需其他引物覆盖）")
    return f"位于 read 尾端不可信区（{zc['tail_reason']}）"


def cause_text(c: Dict) -> str:
    """结构化低置信原因 → 主句（不含染料团/套峰/弱互检后缀）"""
    code = c["code"]
    if code == "cross_conflict":
        return f"互检矛盾：{'、'.join(c['reads'])} 在同一位点读出设计碱基"
    if code == "merged_zone":
        return "落在同聚物峰合并压缩区，该 read 此处无可分辨峰"
    if code == "slippage":
        return "poly 下游滑移伪影（同聚物后信号错位）"
    if code == "n_call":
        return "碱基判读为 N/模糊碱基（basecaller 未能解析）"
    if code == "zone":
        return zone_text_of(c)
    if code == "mixed_bg":
        return "该位点双峰（次级峰占比 >30%，疑似混合样品或杂峰）"
    if code == "poly_compressed":
        return f"同聚物峰压缩（峰幅为局部主峰的 {c['pct']}%），重复数计数不可靠"
    if code == "ins_none":
        return f"插入位置几乎无独立峰（插入峰为邻峰的 {c['pct']}%），疑似 basecaller 伪影"
    if code == "ins_visible":
        return f"插入峰可见（为邻峰的 {c['pct']}%），但仅单条 read 支持（Q{c['q']}）"
    if code == "ins_weak":
        return f"插入峰偏弱（为邻峰的 {c['pct']}%）且 Q 值低，Q{c['q']}"
    if code == "mutant_weak":
        return f"突变峰明显弱于参考峰（突变峰占比 {c['mp']}%），疑似误读"
    if code == "mutant_equal":
        return f"突变峰与参考峰高度相当（突变峰占比 {c['mp']}%），双峰/混合"
    if code == "low_snr":
        return f"突变峰信噪比低（SNR {c['snr']}），接近本底"
    return f"Q 值偏低，Q{c['q']}" + ("，仅单条 read 支持" if c.get("single") else "")


def low_reason_text(v: Dict) -> str:
    """低置信变体的 low_reason：主句 + 染料团 / 套峰 / 弱互检后缀"""
    out = cause_text(v["low_cause"])
    if v.get("dye_blob"):
        b = v["dye_blob"]
        out += (f"；位于染料团区（{b['channel']} 通道宽峰，"
                f"read 第 {b['start']}–{b['end']} 位）")
    if v.get("in_mixed_template"):
        mt = v["in_mixed_template"]
        out += (f"；{mt['read']} 在此处套峰（"
                + (f"第 {mt['breakpoint']} 位起整体套峰" if mt.get("breakpoint")
                   else "混合模板")
                + "），主峰差异不能作确证")
    wk = (v.get("cross_read_weak") or [None])[0]
    if wk:
        out += (f"；{wk['read']} 读出设计碱基，但{region_quality_text(wk)}，"
                f"质量不足以互检")
    return out


def severity_lines(variants: List[Dict]) -> List[Tuple[str, str]]:
    """变体影响摘要（供自动结论使用），返回 [(行文本, kind)]。

    kind 是机器可读的分组标记（前端折叠分组由它驱动）：
    - "low"：低置信变体行及其 ↳ 子注释（默认折叠组）；
    - "variant"：确证变体行 / 互检排除行及其子注释（正文常驻）。
    low ⟺ confidence=low 且非互检排除。"""
    notes: List[Tuple[str, str]] = []
    for v in variants:
        loc = "、".join(f"{f['name']}({f['type']})" for f in v.get("features", [])) or "非编码区"
        if v.get("frameshift"):
            kind = {"insertion": "插入", "deletion": "缺失"}.get(v["type"], v["type"])
            n = int(v.get("length") or 1)
            note = f"位置 {v['ref_pos']} {kind} {n}bp（{loc}）导致移码"
        elif v.get("aa_change"):
            bc = (f"{v['ref_base']}>{v['alt_base']} " if v.get("type") == "substitution"
                  else "")
            note = f"位置 {v['ref_pos']} {bc}氨基酸改变 {v['aa_change']}（{loc}）"
        elif v.get("synonymous"):
            note = f"位置 {v['ref_pos']} {v['ref_base']}>{v['alt_base']}（{loc}）同义突变（蛋白不变）"
        elif v["type"] == "substitution":
            note = f"位置 {v['ref_pos']} {v['ref_base']}>{v['alt_base']}（{loc}）"
        else:
            kind = "插入" if v["type"] == "insertion" else "缺失"
            note = f"位置 {v['ref_pos']} {kind} {v['length']}bp（{loc}）"
        line_kind = "variant"
        if v.get("confidence") == "low":
            if v.get("cross_read_resolved"):
                rd = "、".join(((v.get("cross_read_conflict") or {}).get("reads") or [])[:2])
                note += f"，已被 {rd} 互检排除（该处读出设计碱基，判为测序误差）"
            elif v.get("cross_read_conflict"):
                note += "，未决（仅单 read 报告，其他 read 跨过同一位点未见差异——互检矛盾，伪影与低频真实变异均无法排除）"
            elif v.get("slippage_artifact"):
                note += "，未决（poly 下游滑移待定，需人工核对峰图）"
            elif v.get("low_reason"):
                note += f"，未决（{v['low_reason']}，需人工核对峰图）"
            else:
                note += "，未决（测序噪声或混合峰无法排除，需人工核对峰图）"
            line_kind = "low"
        notes.append((note, line_kind))
        if v.get("enzyme_sites_lost"):
            notes.append((f"  ↳ 破坏酶切位点: {', '.join(v['enzyme_sites_lost'])}", line_kind))
        if v.get("enzyme_sites_gained"):
            notes.append((f"  ↳ 新增酶切位点: {', '.join(v['enzyme_sites_gained'])}", line_kind))
        if v.get("enzyme_sites_pending") and v.get("status") == "unresolved":
            # 酶位归属待定是下游实验要先确认的判读要点，不随低置信明细折叠
            notes.append(("  ↳ 酶位 " + ", ".join(v["enzyme_sites_pending"])
                          + " 在 ±10bp 内、indel 归属未定：位点归属待定", "variant"))
    return notes
