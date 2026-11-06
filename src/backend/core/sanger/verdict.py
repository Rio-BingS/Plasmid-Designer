"""结构化判定（goal T2；审计 A 5.1/5.2/5.5、B P1-8）

把 analyze 结果折叠成一个机器可读的 Verdict 对象，所有出口（网页结论头、
Excel 一句话结论、归档分拣、API 下发）由同一 status 驱动，终结三出口
各说各话的分叉：

    status: "pass" | "fail" | "pending" | "mixed" | "partial" | "insufficient"
            | "failed_reads"
    blocking/info: [{kind, ref_start, ref_end, element, detail, action, refs}]
    roi / reads / algo_version 见 goal §3。

状态优先级：fail > failed_reads > mixed > pending > partial > insufficient
> pass。failed_reads 在有 confirmed 差异时让位给 fail（blocking 中仍列出）。
本模块只读 result 各键做归并，不回改；T3/T5 落地后 partial 与 roi 才有
真实取值（T2 阶段 roi 为 whole 占位、partial 不触发）。
"""

from typing import Dict, List, Optional

from core.sanger.batch import MIN_VERDICT_COVERED_BP
from core.sanger.homopolymer import mixed_sample_confirmed

# pending 级阻断种类（goal §3 blocking.kind 枚举的待定子集；其余种类随
# 对应任务 T5/T6/T7/T9/T16/T17 落地后加入）
PENDING_KINDS = {
    "unresolved_variant", "unresolved_site", "n_in_cds", "poly_unreliable",
    "dropout", "overlap_discordance", "orf_annotation_suspect",
    "allele_mismatch",
}

# 未决位点 kind → blocking.kind 映射
_SITE_KIND_MAP = {
    "n_call": "unresolved_site",
    "iupac": "unresolved_site",
    "mixed_uncorroborated": "unresolved_site",
    "poly_undetermined": "poly_unreliable",
    "dropout": "dropout",
}

# 旧记录兼容（无 status 字段）的三态推导，与 batch._variant_status_of 同口径
def _vstatus(v: Dict) -> str:
    if v.get("status"):
        return v["status"]
    if v.get("cross_read_resolved"):
        return "rejected"
    return "confirmed" if v.get("confidence") != "low" else "unresolved"


def build_verdict(result: Dict, ref_length: int) -> Dict:
    """从 analyze 结果组装 Verdict（只读，不改 result 内其他键）"""
    variants = result.get("variants") or []
    sites = result.get("unresolved_sites") or []
    cds_reports = result.get("cds_reports") or []
    errors = result.get("errors") or []
    reads = result.get("reads") or []
    profiles = result.get("mixed_profiles") or {}

    blocking: List[Dict] = []

    def _blk(kind: str, ref_start, ref_end, element, detail: str, action: str,
             refs: Optional[List[str]] = None) -> None:
        blocking.append({
            "kind": kind,
            "ref_start": ref_start,
            "ref_end": ref_end,
            "element": element,
            "detail": detail,
            "action": action,
            "refs": refs or [],
        })

    # 变体三态（rejected 不进 blocking：resolved_regions 已有裁决行）
    for v in variants:
        st = _vstatus(v)
        if st == "rejected":
            continue
        feats = "、".join(sorted({f.get("name") or "" for f in v.get("features") or []})) or None
        if st == "confirmed":
            _blk("confirmed_variant", v.get("ref_pos"),
                 (v.get("ref_pos") or 0) + int(v.get("length") or 1) - 1,
                 feats, f"{v.get('ref_pos')} {v.get('type')}（置信 {v.get('confidence')}）",
                 "核对差异清单，必要时换引物复测")
        else:
            _blk("unresolved_variant", v.get("ref_pos"),
                 (v.get("ref_pos") or 0) + int(v.get("length") or 1) - 1,
                 feats,
                 f"{v.get('ref_pos')} {v.get('type')}"
                 + (f"（{v['low_reason']}）" if v.get("low_reason") else ""),
                 "核对峰图或补测")
    # 未决位点（N/模糊调用、poly 不可判定、dropout、无法互检的双峰）
    for u in sites:
        _blk(_SITE_KIND_MAP.get(u.get("kind"), "unresolved_site"),
             u.get("ref_pos"), u.get("ref_pos"), None,
             u.get("detail") or u.get("kind"), "核对峰图或换引物补测",
             refs=u.get("reads") or [])
    # CDS：蛋白不一致（fail）与 N 待定（pending）
    for c in cds_reports:
        if c.get("coverage_status") != "uncovered" and c.get("protein_identical") is False:
            hint = "（移码）" if c.get("frameshift_count") else ""
            _blk("confirmed_variant", c.get("start"), c.get("end"), c.get("name"),
                 f"{c.get('name')} 蛋白与设计不一致{hint}",
                 "核对差异清单，必要时重新挑克隆")
        if c.get("n_in_cds"):
            _blk("n_in_cds", c.get("start"), c.get("end"), c.get("name"),
                 f"CDS 内 {c['n_in_cds']} 处 N/模糊调用，蛋白判定待定",
                 "核对峰图或换引物补测")
    # 混合样品（widespread 分级 + 跨引物互检降级例外；scattered 只进
    # unresolved_sites 的 mixed_uncorroborated，不升级 mixed_sample）
    wide = [p for p in profiles.values() if p.get("class") == "widespread"]
    if wide and mixed_sample_confirmed(reads, result.get("mixed_corroboration")):
        counts = sorted(int(p.get("count") or 0) for p in wide)
        cr = str(counts[0]) if counts[0] == counts[-1] else f"{counts[0]}-{counts[-1]}"
        _blk("mixed_sample", None, None, None,
             f"{len(wide)} 条 read 检出双峰（每条 {cr} 处，疑似混合样品）",
             "重新挑单克隆复测")
    # read 解析失败（T4 起进结论文本；blocking 自 T2 起始终列出）
    for e in errors:
        _blk("read_failed", None, None, None,
             f"{e.get('filename')}: {e.get('error', '')}", "检查 ab1 文件完整性")
    # read 大段未比对（未比对部分没有任何验证 → insufficient）
    for r in reads:
        pa = r.get("partial_alignment")
        if pa:
            _blk("partial_alignment", None, None, None,
                 f"{r.get('filename')} 修剪后 {pa.get('trimmed_length')}bp 仅 "
                 f"{pa.get('aligned_read_len')}bp 比对上参考，其余部分未验证",
                 "核对样品身份/参考选择（或按环状参考分析）")
    # verified 覆盖（goal T3；B P0-4）：verified<100% 记 roi_unverified 阻断
    # 供前端明示，不升级状态（单 read 的首尾固有不可信区使 verified<100%
    # 几乎恒真，partial 状态随 T5 ROI 收窄判定区域后接入）。bp 闸门维持
    # measured 口径（与 batch._covered_bp/MIN 同源，full CDS 豁免同 batch）
    cov = result.get("coverage") or {}
    vp = cov.get("verified_percent")
    measured_bp = sum(int(e) - int(s) + 1
                      for s, e in (result.get("consensus") or {}).get("covered_ranges") or [])
    full_cds = any(c.get("coverage_status") == "full" for c in cds_reports)
    bp_gate_failed = (not full_cds and measured_bp < MIN_VERDICT_COVERED_BP)
    if bp_gate_failed:
        _blk("roi_unverified", None, None, None,
             f"实测仅 {measured_bp}bp，不足以确认是否为目标质粒",
             "补测引物或换参考")
    elif vp is not None and vp < 100:
        _blk("roi_unverified", None, None, None,
             f"验证覆盖 {vp}%（实测 {cov.get('measured_percent')}%）——"
             "不可信区与 N/模糊调用列不构成验证",
             "核对覆盖缺口，必要时补引物")

    has_fail = any(b["kind"] == "confirmed_variant" for b in blocking)
    has_failed_reads = any(b["kind"] == "read_failed" for b in blocking)
    has_mixed = any(b["kind"] == "mixed_sample" for b in blocking)
    has_pending = any(b["kind"] in PENDING_KINDS for b in blocking)
    has_insufficient = any(b["kind"] == "partial_alignment" for b in blocking)
    # 优先级（goal T2）：failed_reads 让位 fail；partial 随 T5 ROI 接入
    if has_fail:
        status = "fail"
    elif has_failed_reads:
        status = "failed_reads"
    elif has_mixed:
        status = "mixed"
    elif has_pending:
        status = "pending"
    elif has_insufficient or bp_gate_failed:
        status = "insufficient"
    else:
        status = "pass"

    cov = (result.get("consensus") or {}).get("coverage_percent")
    return {
        "status": status,
        "blocking": blocking,
        "info": [],
        # ROI（T5 落真实取值）：T2/T3 阶段判定区域 = 整条参考
        "roi": {
            "start": 1,
            "end": ref_length,
            "source": "whole",
            "verified_fraction": round(vp / 100, 4) if isinstance(vp, (int, float)) else None,
            "measured_fraction": round(cov / 100, 4) if isinstance(cov, (int, float)) else None,
            "double_strand_fraction": None,
        },
        "reads": {
            "total": len(reads) + len(errors),
            "analyzed": len(reads),
            "failed": [{"file": e.get("filename"), "error": e.get("error", "")}
                       for e in errors],
        },
        "algo_version": result.get("algo_version"),
    }
