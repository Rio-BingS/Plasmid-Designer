"""S7 呈现：poly 的结论文案（只读 PolyCall）

结论正文里 poly 相关的三类行都在这里生成，pipeline 不再读 run_verdict：
- `warning_lines`：无确认差异时的「注意：poly(X) … 峰图判读」行；
- `detail_lines`：有确认差异时的「↳ … 峰图判读」行（含逐 read 证据）；
- `anchor_notes`：计数确证但定位存疑；
- `confirmed_keys` / `unreliable_keys`：供端点歧义豁免、变体行去重使用。
措辞与 v17 逐字一致（一期等价重构）。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from core.sanger.polycall import COUNT_CONFIRMED, UNRESOLVED_STATES

_ANCHOR_RANK = {"reliable": 0, "one-sided": 1, "marginal": 2, "unreliable": 3}

_WARN = {
    "CONFLICTING": "可分辨 read 之间计数互证矛盾，不可信",
    "UNDETERMINABLE": "无可分辨峰证据，重复数不可判定（建议换引物或人工核对峰图）",
    "DEFICIT_SUSPECTED": "峰图缺失证据，以峰图可分辨峰为准，建议人工复核",
}
_DETAIL = {
    "CONFLICTING": "可分辨 read 之间计数互证矛盾，不可信",
    "UNDETERMINABLE": "无可分辨峰证据，长度不可判定（宽度法估计仅供参考）",
    "DEFICIT_SUSPECTED": "峰图缺失证据，以峰图可分辨峰为准",
}


def _poly_tier(pcs: Optional[List[Dict]]) -> List[Dict]:
    return [pc for pc in pcs or [] if pc.get("tier") == "poly"]


def unreliable(pcs: Optional[List[Dict]]) -> List[Dict]:
    """计数不可信的 poly 级结构（旧 count_reliable=False）"""
    return [pc for pc in _poly_tier(pcs) if pc["state"] in UNRESOLVED_STATES]


def label(pc: Dict) -> str:
    if (pc.get("period") or 1) == 1 and pc.get("base"):
        return f"poly({pc['base']}) 同聚物"
    unit = pc.get("unit") or ""
    if unit:
        return f"({unit}){pc.get('ref_len', '?')} 重复"
    return "重复序列"


def phrase(pc: Dict) -> str:
    """峰图主判读短语（以可分辨峰为准）"""
    ref = pc["ref_len"]
    m = pc.get("peak_measured")
    if m is None:
        ci = pc.get("width_ci")
        est = f"宽度法估计 {pc.get('width_est')} 个"
        if ci:
            est += f"（区间 {ci[0]}–{ci[1]}）"
        return f"峰图计数不可用，{est}"
    if pc.get("missing_max"):
        return f"缺失 {pc['missing_max']} 个 {pc['base']}：实测 {m} 个，参考 {ref} 个"
    if pc.get("inserted_max"):
        return f"插入 {pc['inserted_max']} 个 {pc['base']}：实测 {m} 个，参考 {ref} 个"
    return f"poly({pc['base']}) 碱基类型完整：实测 {m} 个，参考 {ref} 个"


def confirmed_keys(pcs: Optional[List[Dict]]) -> Set[Tuple]:
    """(base, start)：已确证计数的 run（端点歧义提示豁免用）"""
    return {(pc["base"], pc["ref_start"]) for pc in pcs or []
            if pc["state"] == COUNT_CONFIRMED}


def detail_keys(pcs: Optional[List[Dict]]) -> Set[Tuple]:
    """(base, unit, start)：已出峰图判读行的 run（变体行去重用）"""
    return {(pc["base"], pc["unit"], pc["ref_start"]) for pc in unreliable(pcs)
            if pc["per_read"]}


def warning_lines(pcs: Optional[List[Dict]]) -> List[str]:
    return [
        f"注意：poly({pc['base']}) 同聚物 {pc['ref_start']}-{pc['ref_end']} 峰图判读："
        f"{phrase(pc)}——{_WARN.get(pc['state'], '以峰图可分辨峰为准，建议人工复核')}"
        for pc in unreliable(pcs)
    ]


def _read_label(x: Dict) -> str:
    d = "反向" if x["direction"] == "-" else "正向"
    n = x["peak_count"] if x["peak_count"] is not None else "?"
    if x.get("coverage") == "partial":
        s, t = x["covered_span"]
        return (f"{x['filename']}{d}覆盖段{s}-{t}"
                f"调用{x['called_count']}/峰{n}个")
    return f"{x['filename']}{d}调用{x['called_count']}/峰{n}个"


def detail_lines(pcs: Optional[List[Dict]]) -> List[str]:
    out: List[str] = []
    for pc in unreliable(pcs):
        if not pc["per_read"]:
            continue
        var = pc.get("site")
        anchor = ""
        if var:
            kind = "插入" if var["type"] == "insertion" else "缺失"
            anchor = f"位置 {var['ref_pos']} {kind} {var['length']}bp；"
        detail = " / ".join(_read_label(x) for x in pc["per_read"])
        vt = _DETAIL.get(pc["state"], "峰压缩区，以峰图可分辨峰为准")
        out.append(
            f"  ↳ {label(pc)} {pc['ref_start']}-{pc['ref_end']} 峰图判读："
            f"{phrase(pc)}（{anchor}{detail}）"
            f"——{vt}，建议人工核对峰图")
    return out


def anchor_notes(pcs: Optional[List[Dict]]) -> List[str]:
    """计数确证但投票 read 锚定不可靠 → 「计数确证但定位存疑」"""
    notes: List[str] = []
    for pc in pcs or []:
        if pc["state"] != COUNT_CONFIRMED:
            continue
        g = pc.get("anchor_grade") or "reliable"
        if _ANCHOR_RANK.get(g, 1) < _ANCHOR_RANK["marginal"]:
            continue
        reason = ("路标落在信号异常区" if g == "unreliable"
                  else "路标处于信号边缘或单侧锚定")
        notes.append(
            f"注意：{label(pc)} {pc['ref_start']}-{pc['ref_end']} 重复数已由峰图"
            f"计数确证（{pc['measured'] if pc.get('measured') is not None else '?'} 个），但该 run"
            f"的锚定路标不可靠（{reason}）——计数确证但定位存疑，run 起止"
            "可能整体偏移数 bp，建议对照图谱核对边界")
    return notes


def dropout_lines(dropouts: Optional[List[Dict]]) -> List[str]:
    """poly 下游信号骤降注记（只读 homopolymer.collect_post_poly_dropouts）"""
    out = []
    for d in dropouts or []:
        ratio_txt = f"{round((d['ratio'] or 0) * 100)}%" if d.get("ratio") is not None else "?"
        rec = "" if d.get("recovered") else "且未见恢复"
        out.append(
            f"  ↳ poly({d['base']}) 同聚物 {d['ref_start']}-{d['ref_end']} 下游检测到信号骤降"
            f"（降至约 {ratio_txt}{rec}）：该段碱基判读不可信（区别于未覆盖），"
            "建议人工核对峰图或换引物复测"
        )
    return out


def edge_lines(edge_ambiguous_reads: List[Dict], pcs: Optional[List[Dict]]) -> List[str]:
    """比对端点落在同聚物内部的提示；按 PolyCall 豁免（已确证计数的 run
    端点滑动不影响判定）"""
    ok = confirmed_keys(pcs)
    notes = [x for x in edge_ambiguous_reads or []
             if not any((h["base"], h["start"]) in ok for h in x["hits"])]
    if not notes:
        return []
    names = "、".join(x["filename"] for x in notes[:3])
    more = f" 等 {len(notes)} 条" if len(notes) > 3 else ""
    first = notes[0]["hits"][0]
    run_txt = (f"poly({first['base']}) {first['start']}-{first['end']}"
               if first["base"] else f"{first['start']}-{first['end']}")
    return [f"注意：{names}{more} 的比对端点落在同聚物区内部"
            f"（如 {run_txt}）——同聚物内的比对落点存在歧义，覆盖边界与"
            "互检定位可能有数 bp 偏差，建议对照图谱核对"]


def repeat_variant_lines(shown_variants: List[Dict], pcs: Optional[List[Dict]]) -> List[str]:
    """重复区 indel 的重复数变化行（峰图判读行已覆盖的 run 不重复出）"""
    from core.sanger.homopolymer import run_label
    emitted = detail_keys(pcs)
    out = []
    for v in shown_variants:
        hp = v.get("homopolymer")
        if not (hp and v.get("type") in ("insertion", "deletion")):
            continue
        if (hp.get("base"), hp.get("unit"), hp.get("start")) in emitted:
            continue
        kind = "插入" if v["type"] == "insertion" else "缺失"
        rel = ("重复数计数可靠" if hp.get("count_reliable", True)
               else "峰压缩区，重复数计数可能不准，建议人工核对峰图")
        if hp.get("peak_count") is not None:
            rel += f"；峰图独立计数 {hp['peak_count']} 个"
        out.append(
            f"  ↳ {run_label(hp)} {hp['start']}-{hp['end']}："
            f"参考 {hp['ref_repeat_count']} 个，测得 {hp['observed_repeat_count']} 个"
            f"（位置 {v['ref_pos']} {kind} {int(v.get('length') or 1)}bp，{rel}）"
        )
    return out
