"""S7 呈现：CDS 判读文案（只读 _build_cds_reports 产出的 cds_facts）

- `verdict_text`：单条 CDS 报告的 verdict 句（覆盖前缀 + 主句 + 待复核 + 读框注）；
- `uncovered_text` / `failed_text`：未测、判读失败的占位句；
- `conclusion_lines`：结论正文里的「【X CDS】…」行。
facts 中的位置已映射回原参考坐标（多段 CDS），呈现层不再做坐标换算。
措辞与 v17 逐字一致（一期等价重构）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from core.sanger.crosscheck import pos_ranges_str
from core.sanger.evidence import variant_type_text

UNCOVERED_TEXT = "未测：不参与判读（覆盖区外按设计序列对待——部分测序是常规策略，无需测全）"


def uncovered_text() -> str:
    return UNCOVERED_TEXT


def failed_text(exc_name: str) -> str:
    return (f"CDS 判读失败（内部异常：{exc_name}），变体明细见突变表，"
            "可核对峰图或反馈日志")


def frame_note_text(frame: Optional[Dict]) -> str:
    if not frame:
        return ""
    if frame["kind"] == "orf_shift":
        shift = (f"起点相差 {frame['shift_bp']} bp" if frame["shift_side"] == "start"
                 else f"终点相差 {frame['shift_bp']} bp")
        return (f"（特征标注 {frame['feat_start']}-{frame['feat_end']} 与实际编码区 "
                f"{frame['start']}-{frame['end']} {shift}，已按编码区翻译）")
    return (f"（注意：该特征长度 {frame['length']} bp 不是 3 的倍数，"
            "翻译按参考阅读框截断）")


def _preview(items: List[str], n: int) -> str:
    return "、".join(items[:n]) + ("等" if len(items) > n else "")


def _main_text(f: Dict) -> str:
    name = f["name"]
    pi = f["protein_identical"]
    aa = f["aa_diffs"]
    if pi is None:
        main = (f"{name} 蛋白判定待定：CDS 内 {f['n_in_cds']} 处 N/模糊调用"
                f"（参考位置 {pos_ranges_str(f['n_positions'])}）未读清——需核对峰图或换引物补测后再判")
        if aa:
            main += f"；已确证 {len(aa)} 处氨基酸替换（{_preview(aa, 5)}）"
        return main
    if pi:
        main = (f"已测区域蛋白与设计一致（{f['ref_len']} aa）" if f["cov_pct"] < 99
                else f"{name} 蛋白与设计一致（{f['ref_len']} aa）")
        if f["synonymous"]:
            main += f"；另有 {f['synonymous']} 处同义突变（DNA 变、蛋白不变）"
        return main
    fss = f["frameshifts"]
    stop_idx = f["stop_idx"]
    if fss:
        fs0 = fss[0]
        kind = "插入" if fs0.get("type") == "insertion" else "缺失"
        vlen = int(fs0.get("length") or 1)
        main = (f"{fs0['ref_pos']} 处的{kind}{'' if vlen == 1 else f' {vlen}bp'}"
                f"使阅读框自第 {f['first_diff'] + 1} 位起移码")
        if stop_idx >= 0:
            main += (f"，翻译在第 {stop_idx + 1} 位提前终止（产物 {f['alt_len']} aa，"
                     f"设计为 {f['ref_len']} aa）")
        else:
            main += "，其后氨基酸序列与设计不再对应"
        if len(fss) > 1:
            main += f"（共 {len(fss)} 处移码）"
    elif stop_idx >= 0:
        main = (f"翻译在第 {stop_idx + 1} 位提前终止（产物 {f['alt_len']} aa，"
                f"设计为 {f['ref_len']} aa）")
    elif f["start_lost"]:
        a, b = f["start_lost"]
        main = f"起始密码子改变（{a} → {b}），翻译可能无法正常起始"
    elif f["stop_lost"]:
        main = "终止密码子丢失，翻译将读穿至下游"
    elif f["inframe_ins"] or f["inframe_del"]:
        main = (f"阅读框内{'插入' if f['inframe_ins'] else '缺失'} {f['inframe_bp']} bp，"
                f"自第 {f['first_diff'] + 1} 位起氨基酸序列改变")
    elif aa:
        main = f"{len(aa)} 处氨基酸替换（{_preview(aa, 5)}）"
    else:
        main = f"翻译产物与设计不同（长度 {f['ref_len']} → {f['alt_len']} aa）"
    if f["synonymous"] and (aa or f["inframe_ins"] or f["inframe_del"]):
        main += f"；另有 {f['synonymous']} 处同义突变（蛋白不变）"
    subs = f["confirmed_subs"]
    if subs:
        preview = _preview(
            [f"{v['ref_pos']} {v.get('ref_base', '')}>{v.get('alt_base', '')}" for v in subs], 4)
        main += f"；另有 {len(subs)} 处确证的碱基替换（{preview}）"
    return f"{name} 蛋白与设计不一致：{main}"


def _pending_text(f: Dict) -> str:
    out = ""
    plain = f["pending_plain"]
    conflicted = f["pending_conflicted"]
    if plain:
        detail = "；".join(
            f"{v['ref_pos']} {variant_type_text(v)}"
            + (f"（{v['low_reason']}）" if v.get("low_reason") else "")
            for v in plain[:3]) + ("等" if len(plain) > 3 else "")
        if f["pending_impact"] == "would_change":
            out += (f"。另有 {len(plain)} 处低置信差异未计入判定：{detail}"
                    "——若为真蛋白还会改变，建议核对峰图")
        else:
            out += f"。另有 {len(plain)} 处低置信差异，不影响以上判定：{detail}"
    if conflicted:
        parts = []
        for v in conflicted[:2]:
            kind = {"insertion": "插入", "deletion": "缺失"}.get(v.get("type"), "替换")
            rd = v.get("reads") or []
            reads = "、".join(rd[:2]) + ("等" if len(rd) > 2 else "")
            parts.append(f"{v['ref_pos']} 处的{kind}（{reads} 跨过同一位点未见差异）")
        head = (f"等 {len(conflicted)} 处" if len(conflicted) > 2
                else f"{len(conflicted)} 处")
        out += (f"。另有 {head}互检矛盾差异未计入判定：{'；'.join(parts)}"
                "——真实变异应被覆盖该位的每条 read 独立报出，如需确认请核对峰图或换引物复测")
    return out


def verdict_text(f: Dict) -> str:
    main = _main_text(f) + _pending_text(f)
    cov = f["cov_pct"]
    text = (f"CDS 覆盖 {cov}%（未覆盖部分按参考填充、未验证）。{main}" if cov < 99
            else f"CDS 已完整覆盖。{main}")
    return text + frame_note_text(f["frame"])


def conclusion_lines(cds_reports: List[Dict]) -> List[str]:
    return [f"【{cr['name']} CDS】{cr['verdict']}"
            for cr in cds_reports if cr["coverage_status"] != "uncovered"]
