"""证据三态与结论资格门（goal T1；审计 A 3.3 / B P0-1、P0-2）

变体与位点的证据只有三种归宿，判定层不再自行发明第四种话术：

- **rejected**：有正面反证（跨引物互检干净读出设计碱基并定案）——不写入
  共识、不进判定文本；
- **confirmed**：有正证据（中/高置信，峰级证据与多 read 支持达标）——照实
  判定，✗/✓ 由它决定；
- **unresolved**：证据不足（低置信：可信区外、低 Q、混合背景、poly 压缩、
  N/模糊调用、无法互检的双峰等）——不参与判定，也不冒充「与参考一致」，
  并且使 ✓ 失去资格（「证据不足」≠「与设计一致」）。

本模块只做与入口无关的纯逻辑：状态归并、机器可读原因码、IUPAC 合并、
未决位点收集、低置信原因文案与结论头部资格门。管线组装见 pipeline.py
（`_variant_confidence_pass` 消费 status/reason_codes，`analyze` 下发
unresolved_sites，`_build_conclusion` 调用 `_conclusion_summary`）。
"""

from typing import Dict, List, Optional, Tuple

from core.sanger.homopolymer import POLY_COMPRESSION_RATIO, _read_ref_maps
from core.sanger.reliable_zone import in_zone, zone_reason


def _variant_status(v: Dict) -> str:
    """证据三态（goal T1；A 3.3 / B P0-1）：

    - rejected：仅当有正面反证（跨引物互检干净读出设计碱基并定案）
    - confirmed：有正证据（中/高置信，峰级证据与多 read 支持达标）
    - unresolved：证据不足（低置信：可信区外、低 Q、混合背景、poly 压缩等）。
      「证据不足」不再等于「疑似伪影」——它不参与判定且使 ✓ 失去资格
    """
    if v.get("cross_read_resolved"):
        return "rejected"
    if v.get("confidence") in ("medium", "high"):
        return "confirmed"
    return "unresolved"


def _variant_reason_codes(v: Dict, evidence: Optional[Dict],
                          zone: Optional[Dict], mixed_positions: set) -> List[str]:
    """机器可读的原因码（goal §3 Variant.reason_codes），从结构化事实推导、
    不做文案匹配。置信度分支与 _low_confidence_reason 一一对应。"""
    codes: List[str] = []
    support = v.get("support_reads") or 1
    rp = v.get("read_pos")
    alt_up = (v.get("alt_base") or "").upper()
    if v.get("cross_read_resolved"):
        codes.append("cross_check_clean")
    if v.get("merged_zone_artifact"):
        codes.append("poly_compressed")
    if v.get("slippage_artifact"):
        codes.append("slip_artifact")
    if v.get("type") in ("substitution", "insertion") and any(
            c not in "ACGT-" for c in alt_up):
        codes.append("n_call")
    if zone and rp and support < 2 and not in_zone(zone, rp):
        z = zone.get("params") or {}
        if rp <= (z.get("front_gray") or 60):
            codes.append("front_hard_zone" if rp <= (z.get("front_hard") or 30)
                         else "front_gray_zone")
        else:
            codes.append("tail_zone")
    if rp and rp in mixed_positions:
        codes.append("mixed_bg_high")
    if evidence:
        ins_ratio = evidence.get("insertion_peak_ratio")
        if ins_ratio is not None and ins_ratio < 0.3:
            codes.append("no_insertion_peak")
        mp = evidence.get("mutant_pct")
        if mp is not None and mp < 70:
            codes.append("weak_mutant_peak")
        snr = evidence.get("snr")
        if snr is not None and snr < 5:
            codes.append("low_snr")
        elif snr is None and evidence.get("snr_checked"):
            codes.append("snr_unavailable")
    hp = v.get("homopolymer")
    if (hp and v.get("type") in ("insertion", "deletion")
            and hp.get("peak_amplitude_ratio") is not None
            and hp["peak_amplitude_ratio"] < POLY_COMPRESSION_RATIO):
        codes.append("poly_compressed")
    return codes


_IUPAC_MERGE = {
    frozenset("AG"): "R", frozenset("CT"): "Y", frozenset("GC"): "S",
    frozenset("AT"): "W", frozenset("GT"): "K", frozenset("AC"): "M",
    frozenset("CGT"): "B", frozenset("AGT"): "D", frozenset("ACT"): "H",
    frozenset("ACG"): "V",
}


def _iupac_merge(a: str, b: str) -> str:
    """两个碱基的 IUPAC 合并（unresolved 替换写入共识用）；无法合并回退 N"""
    a, b = (a or "N").upper(), (b or "N").upper()
    if a == b:
        return a if a in "ACGT" else "N"
    return _IUPAC_MERGE.get(frozenset({a, b}) - {"N"}, "N")


def _collect_unresolved_sites(read_results: List[Dict],
                              homopolymer_report: List[Dict],
                              mixed_corroboration: Optional[Dict]) -> List[Dict]:
    """未决位点清单（goal T1；B P0-2 / A 3.3）：N/IUPAC 调用列、poly 不可判定、
    poly 下游骤降（dropout）区段、无法互检的双峰位点。

    这些位点的共同点是「没读清」：既无正证据也无反证据。单 read 覆盖时
    共识如实保留 N、多 read 投票压制 N——两种情形都记录在案；它们不进
    已验证口径，也使 ✓ 失去资格（A 3.3：证据不足 ≠ 与设计一致）。
    返回 [{ref_pos, kind, reads, detail}]，按 (ref_pos, kind) 去重合并 reads。"""
    sites: Dict[Tuple[int, str], Dict] = {}

    def _add(ref_pos, kind: str, read: str, detail: str) -> None:
        if not ref_pos:
            return
        key = (int(ref_pos), kind)
        ent = sites.setdefault(key, {"ref_pos": int(ref_pos), "kind": kind,
                                     "reads": [], "detail": detail})
        if read and read not in ent["reads"]:
            ent["reads"].append(read)

    # N/IUPAC 调用列（比对列上的模糊碱基，无论是否被投票压制都记录）
    for r in read_results:
        r2ref, _ = _read_ref_maps(r)
        for i, b in enumerate((r.get("trimmed_bases") or "").upper()):
            if b in "ACGT":
                continue
            rp = r2ref.get(i + 1)
            if rp:
                _add(rp, "n_call" if b == "N" else "iupac", r["filename"],
                     f"read 调用 {b}（未解析）")
    # poly 不可判定：峰图计数矛盾 / 不可判定 / 缺失证据（accepted 不算）
    for e in homopolymer_report or []:
        if e.get("tier") == "poly" and e.get("run_verdict") in (
                "contradictory", "undetermined", "deficit_observed"):
            _add(e.get("start"), "poly_undetermined", "",
                 f"poly({e.get('base')}) {e.get('start')}-{e.get('end')} "
                 f"峰图判读 {e.get('run_verdict')}")
    # poly 下游信号骤降区段
    for r in read_results:
        for d in r.get("post_poly_dropouts") or []:
            _add(d.get("ref_start"), "dropout", r["filename"],
                 f"{d.get('base', '?')} run 后信号骤降"
                 f"（{d.get('ref_start')}-{d.get('ref_end')}）")
    # 无法互检的双峰位点：其他引物未覆盖，既不能定噪声也不能定混合
    for fn, chk in ((mixed_corroboration or {}).get("by_read") or {}).items():
        for rp in chk.get("uncovered_ref") or []:
            _add(rp, "mixed_uncorroborated", fn, "双峰位点无其他引物覆盖，无法互检")
    return [sites[k] for k in sorted(sites, key=lambda k: (k[0], k[1]))]


def variant_type_text(v: Dict) -> str:
    """差异类型的明确文字（人工核对用）：替换 C>A / 插入 1bp（G）/ 缺失 2bp（AT）"""
    t = v.get("type")
    if t == "insertion":
        n = int(v.get("length") or len((v.get("alt_base") or "").strip("-")) or 1)
        seq = (v.get("alt_base") or "").strip("-")
        return f"插入 {n}bp" + (f"（{seq}）" if seq and len(seq) <= 6 else "")
    if t == "deletion":
        n = int(v.get("length") or 1)
        seq = (v.get("ref_base") or "").strip("-")
        return f"缺失 {n}bp" + (f"（{seq}）" if seq and len(seq) <= 6 else "")
    return f"替换 {v.get('ref_base', '')}>{v.get('alt_base', '')}"


def _low_confidence_reason(v: Dict, mixed_positions: set,
                           evidence: Optional[Dict] = None,
                           zone: Optional[Dict] = None) -> str:
    """低置信的具体原因（与 _variant_confidence 的判定分支一一对应），
    把"疑似测序噪声"这种笼统说法换成人工核对时能直接对照峰图的错误类型"""
    q = int(v.get("read_q") or 0)
    rp = v.get("read_pos")
    support = v.get("support_reads") or 1
    if v.get("cross_read_conflict") and not v.get("cross_read_resolved"):
        rd = "、".join(v["cross_read_conflict"].get("reads", [])[:2])
        return f"互检矛盾：{rd} 在同一位点读出设计碱基"
    if v.get("merged_zone_artifact"):
        return "落在同聚物峰合并压缩区，该 read 此处无可分辨峰"
    if v.get("slippage_artifact"):
        return "poly 下游滑移伪影（同聚物后信号错位）"
    alt_up = (v.get("alt_base") or "").upper()
    if v.get("type") in ("substitution", "insertion") and any(
            c not in "ACGT-" for c in alt_up):
        return "碱基判读为 N/模糊碱基（basecaller 未能解析）"
    if zone and rp and support < 2 and not in_zone(zone, rp):
        return zone_reason(zone, rp)
    ins_ratio = evidence.get("insertion_peak_ratio") if evidence else None
    if rp and rp in mixed_positions and (ins_ratio is None or ins_ratio < 0.6):
        return "该位点双峰（次级峰占比 >30%，疑似混合样品或杂峰）"
    hp = v.get("homopolymer")
    if (hp and v.get("type") in ("insertion", "deletion")
            and hp.get("peak_amplitude_ratio") is not None
            and hp["peak_amplitude_ratio"] < POLY_COMPRESSION_RATIO):
        return f"同聚物峰压缩（峰幅为局部主峰的 {round(hp['peak_amplitude_ratio'] * 100)}%），重复数计数不可靠"
    if evidence is not None:
        if ins_ratio is not None:
            pct = round(ins_ratio * 100)
            if ins_ratio < 0.3:
                return f"插入位置几乎无独立峰（插入峰为邻峰的 {pct}%），疑似 basecaller 伪影"
            if ins_ratio >= 0.6:
                return f"插入峰可见（为邻峰的 {pct}%），但仅单条 read 支持（Q{q}）"
            return f"插入峰偏弱（为邻峰的 {pct}%）且 Q 值低，Q{q}"
        mp, snr = evidence.get("mutant_pct"), evidence.get("snr")
        if mp is not None and mp < 30:
            return f"突变峰明显弱于参考峰（突变峰占比 {mp}%），疑似误读"
        if mp is not None and mp < 70:
            return f"突变峰与参考峰高度相当（突变峰占比 {mp}%），双峰/混合"
        if snr is not None and snr < 5:
            return f"突变峰信噪比低（SNR {snr}），接近本底"
    return f"Q 值偏低，Q{q}" + ("，仅单条 read 支持" if support < 2 else "")


def _conclusion_summary(variants: List[Dict], cds_reports: List[Dict],
                        has_mixed: bool, partial_reads: int = 0,
                        resolved: int = 0, unresolved_sites: int = 0) -> str:
    """结论头部的一句话总结：✓/✗/△ 一眼给出判读结果，详细段落空两行后展开。
    优先级：疑似混合 > 确认差异 > 未决（资格门）> 完全一致。
    partial_reads>0（有 read 大段未比对上参考：载体外序列、污染、参考
    有误，或线性参考上跨原点的 read）时不得给出干净的 ✓——未比对部分
    根本没有验证。环状参考的跨原点 read 已在主流程整条比对，不再计入。

    ✓ 资格门（A 3.3 / B P0-1）：传入的 variants 均为未决变体（互检定案的
    rejected 已被调用方剔除），判定区域内存在未决变体或未决位点
    （unresolved_sites：N/模糊调用、poly 不可判定、dropout、无法互检的双峰）
    时不得给 ✓——「证据不足」不等于「与设计一致」，降级 △ 需核对峰图或补测。

    目的基因模式的覆盖资格门（cds_extract 伪特征带 analysis_target 标记）：
    分析目标零覆盖时结论头必须降级为 △ 未能验证——「引物没测到目的 CDS」
    不许因为所测区域（侧翼/骨架）恰好一致而报 ✓；部分覆盖时任何结论都要
    追加覆盖提示。全质粒模式的 CDS 无此标记，行为逐字不变（部分测序是
    常规策略，见 _build_cds_reports 的未测 verdict）。"""
    target = next((c for c in cds_reports if c.get("analysis_target")), None)
    tail = ""
    if target and not has_mixed:
        if target.get("coverage_status") == "uncovered":
            tail = f"目的 CDS「{target['name']}」未被任何 read 覆盖，未能验证"
        elif target.get("coverage_status") == "partial":
            tail = (f"目的 CDS 覆盖 {target.get('covered_percent') or 0:.0f}%"
                    "（未覆盖部分按参考填充、未验证）")
    if partial_reads and not has_mixed:
        confirmed0 = [v for v in variants if v.get("confidence") != "low"]
        bad0 = [c for c in cds_reports if c.get("protein_identical") is False]
        if not confirmed0 and not bad0:
            return (f"△ 结论：已比对区域与设计一致，但 {partial_reads} 条 read "
                    "有大段未比对上参考，该部分未验证" + (f"；{tail}" if tail else ""))
    confirmed = [v for v in variants if v.get("confidence") != "low"]
    bad = [c for c in cds_reports if c.get("protein_identical") is False]
    names = "、".join(c["name"] for c in bad[:2]) + (" 等" if len(bad) > 2 else "")
    if has_mixed:
        head = "△ 结论：疑似混合样品（部分位点双峰），主峰序列按多数碱基判读"
        if bad:
            head += f"，{names}蛋白与设计不一致"
        return head + "——建议重新挑单克隆复测"
    if confirmed or bad:
        bits = []
        if bad:
            bits.append(f"{names}蛋白与设计不一致")
        if confirmed:
            bits.append(f"检出 {len(confirmed)} 处确认差异")
        return "✗ 结论：" + "，".join(bits) + (f"；{tail}" if tail else "")
    # 无确认差异且目标零覆盖：△ 未能验证（✓ 会让没测到的目标隐身）
    if target and target.get("coverage_status") == "uncovered":
        low_note = (f"（{len(variants)} 处未决差异）"
                    if variants else "（已比对区域与设计一致）")
        return f"△ 结论：目的 CDS「{target['name']}」未被任何 read 覆盖，未能验证{low_note}"
    # ✓ 资格门（A 3.3 / B P0-1）：还有未决项就不得 ✓，也不再把它们预判为
    # 「疑似测序伪影」——△ 如实说「未决」，让用户核对峰图或补测
    if variants or unresolved_sites:
        bits = []
        if variants:
            bits.append(f"{len(variants)} 处未决差异，需核对峰图或补测")
        if unresolved_sites:
            bits.append(f"{unresolved_sites} 处未决位点（N/模糊调用、poly 或双峰未定）")
        return "△ 结论：" + "；".join(bits) + (f"；{tail}" if tail else "")
    if resolved:
        # variants 已剔除互检定案项：剩下的都排除了，结论是确定的
        ok = "目的基因测序正确" if target else "构建与设计一致"
        return f"✓ 结论：{ok}" + (f"；{tail}" if tail else "")
    ok = "目的基因测序正确，未检出差异" if target else "构建与设计一致，未检出差异"
    return f"✓ 结论：{ok}" + (f"；{tail}" if tail else "")
