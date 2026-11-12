"""S4 SiteCall：位点结论的唯一判定表

第 2 步（2026-11-12，v17 等价）：SiteCall 成为判定方——管线对每条变体调用
`decide_variant`（ReadCall 阻断 + p_real + 裁决表），再由 `to_legacy` 反写
status/confidence/action；重叠区分歧降级改为 `apply_overlap_discordance`
（显式硬阻断后重新裁决）。scoring.attach_score 与
overlap.downgrade_overlap_conflicts 只保留为委托本模块的兼容入口。
末尾的 build_site_calls + dual_run_diffs 仍保留，校验反写字段与表一致。

以下为一期说明（保留备查）：

契约：sitecall_contract v1.0（测试结果优化2 项目）。

一期只做「等价重构」：
- 输入是管线已有的结构化事实（variants / mixed_variants 的字段与
  reason_codes），由一张有序规则表一次算出 state，命中即停；
- `to_legacy()` 把 SiteCall 反写成旧三元组 (status, confidence, action)；
- 管线把结果挂在 result["site_calls"]（影子字段，不被任何出口消费），
  测试对全部场景断言 to_legacy(SiteCall) == 旧字段（双跑比对）。
旧字段仍由原路径写出；等双跑在金标准快照上 0 差异后，二期才把出口
切到 SiteCall。

状态（4 个 + 人工复核覆盖层）：CONFIRMED / MIXED / UNRESOLVED / ARTIFACT。
只有 ART_ 类原因码能导出 ARTIFACT；证据不足一律 UNRESOLVED。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from core.sanger.scoring import load_params, p_real_for

SITECALL_VERSION = "s4-2"

CONFIRMED = "CONFIRMED"
MIXED = "MIXED"
UNRESOLVED = "UNRESOLVED"
ARTIFACT = "ARTIFACT"
STATES = (CONFIRMED, MIXED, UNRESOLVED, ARTIFACT)

# 旧 reason_codes / 标志位 → 契约原因码。含义永不修改，只增不改。
_LEGACY_CODE_MAP = {
    "cross_check_clean": "ART_CROSS_READ_REF",
    "slip_artifact": "ART_POLY_SLIPPAGE",
    "poly_compressed": "UNR_MERGED_ZONE",
    "n_call": "UNR_N_CALL",
    "front_hard_zone": "UNR_ZONE_FRONT",
    "front_gray_zone": "UNR_ZONE_FRONT",
    "tail_ext_zone": "UNR_ZONE_OUTSIDE",
    "tail_zone": "UNR_ZONE_OUTSIDE",
    "all_evidence_out_of_zone": "FLAG_EVIDENCE_OUT_OF_ZONE",
    "mixed_bg_high": "UNR_IN_MIXED_REGION",
    "no_insertion_peak": "UNR_NO_INSERTION_PEAK",
    "weak_mutant_peak": "UNR_WEAK_MUTANT_PEAK",
    "low_snr": "UNR_LOW_SNR",
    "snr_unavailable": "UNR_SNR_UNAVAILABLE",
    "overlap_discordance": "UNR_OVERLAP_DISCORDANCE",
    "in_mixed_template": "UNR_IN_MIXED_TEMPLATE",
}
_FLAG_CODE_MAP = {
    "cross_read_conflict": "UNR_CROSS_CONFLICT",
    "cross_read_weak": "UNR_CROSS_WEAK",
    "merged_zone_artifact": "UNR_MERGED_ZONE",
    "dye_blob": "UNR_DYE_BLOB",
    "front_clean": "FLAG_FRONT_CLEAN",
    "outside_clean": "FLAG_OUTSIDE_CLEAN",
}
_NOISE_SRC_MAP = {
    "cross_primer": "ART_CROSS_READ_REF",
    "batch_background": "ART_BATCH_CONTROL",
}

# UNRESOLVED 的 primary 取优先级最高者（越靠前越优先）
_UNR_PRIORITY = (
    "UNR_OVERLAP_DISCORDANCE", "UNR_IN_MIXED_TEMPLATE", "UNR_CROSS_CONFLICT", "UNR_MERGED_ZONE",
    "UNR_DYE_BLOB", "UNR_N_CALL", "UNR_IN_MIXED_REGION", "UNR_ZONE_FRONT",
    "UNR_ZONE_OUTSIDE", "UNR_NO_INSERTION_PEAK", "UNR_WEAK_MUTANT_PEAK",
    "UNR_LOW_SNR", "UNR_SNR_UNAVAILABLE", "UNR_CROSS_WEAK",
)
# 显式硬阻断：命中则不能 CONFIRMED。一期只放「旧路径事后改写 status」的
# 那一条（overlap 分歧降级）；其余阻断在旧路径里已折进 p_real（把
# confidence 降为 low），二期再逐条显式化并与快照对齐。
_HARD_BLOCKS = frozenset({"UNR_OVERLAP_DISCORDANCE", "UNR_IN_MIXED_TEMPLATE"})

_EVIDENCE_KEYS = ("mutant_pct", "snr", "insertion_peak_ratio")


def _dedup(seq: Iterable[str]) -> List[str]:
    out: List[str] = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def _variant_reasons(v: Dict) -> List[str]:
    codes = [_LEGACY_CODE_MAP.get(c, "LEGACY_" + c.upper())
             for c in (v.get("reason_codes") or [])]
    for flag, code in _FLAG_CODE_MAP.items():
        if v.get(flag):
            codes.append(code)
    support = v.get("support_reads") or 1
    codes.append("SUP_MULTI_READ" if support >= 2 else "UNR_SINGLE_READ")
    if v.get("corroborated_by_basecall"):
        codes.append("SUP_BASECALL_CORROB")
    return _dedup(codes)


_FROM_RECORD = object()


def _has_peak_evidence(v: Dict, evidence=_FROM_RECORD) -> bool:
    ev = (v.get("peak_evidence") if evidence is _FROM_RECORD else evidence) or {}
    return any(ev.get(k) is not None for k in _EVIDENCE_KEYS)


def _site_id(ref_pos, vtype: str, alt: str) -> str:
    return f"{ref_pos}:{vtype}:{(alt or '').upper()}"


def call_variant(v: Dict, params=None,
                 extra_reasons: Iterable[str] = (),
                 evidence=_FROM_RECORD) -> Dict:
    """一条 variants 记录 → SiteCall（裁决表按序命中即停）"""
    p = params or load_params()
    reasons = _dedup(list(_variant_reasons(v)) + list(extra_reasons))
    p_real = v.get("p_real")
    pr = p_real if p_real is not None else 0.0
    art = [c for c in reasons if c.startswith("ART_")]
    review = v.get("review")  # 人工复核覆盖层（一期管线不产出，预留）

    # 规则 2：有正向反证且 P(真实) < exclude_p → ARTIFACT
    # （一期反证仅 ART_CROSS_READ_REF 进入旧 rejected 判据，与 attach_score 同口径）
    if pr < p.exclude_p and v.get("cross_read_resolved"):
        state, primary = ARTIFACT, "ART_CROSS_READ_REF"
    # 规则 4：P(真实) ≥ confirmed_min 且无显式硬阻断 → CONFIRMED
    elif pr >= p.confirmed_min and not _HARD_BLOCKS.intersection(reasons):
        state = CONFIRMED
        primary = next((c for c in reasons if c.startswith("SUP_")),
                       "SUP_CLEAN_PEAK")
    # 规则 5：其余 → UNRESOLVED
    else:
        state = UNRESOLVED
        primary = next((c for c in _UNR_PRIORITY if c in reasons),
                       next((c for c in reasons if c.startswith("UNR_")),
                            "UNR_LOW_Q"))
    machine_state = state
    if review and review.get("state") in STATES:  # 规则 1：覆盖层
        state = review["state"]

    if pr < p.exclude_p and v.get("cross_read_resolved"):
        action = "exclude"
    elif pr > p.accept_p:
        action = "accept"
    else:
        action = ("review_trace" if _has_peak_evidence(v, evidence)
                  else "resequence")

    tier = ("high" if pr > p.accept_p
            else "medium" if pr >= p.confirmed_min else "low")
    return {
        "site_id": _site_id(v.get("ref_pos"), v.get("type") or "", v.get("alt_base")),
        "kind": "variant",
        "ref_pos": v.get("ref_pos"),
        "state": state,
        "machine_state": machine_state,
        "primary_reason": primary,
        "reasons": reasons,
        "art_reasons": art,
        "tier": tier,
        "p_real": p_real,
        "af": None,
        "support": {"read": v.get("read"), "support_reads": v.get("support_reads")},
        "action": action,
        "review": review,
        "stage_version": SITECALL_VERSION,
    }


def decide_variant(v: Dict, evidence: Optional[Dict] = None,
                   read_calls=None, params=None) -> Dict:
    """S4 第 2 步：位点结论的唯一判定入口（就地反写旧字段，返回 SiteCall）。

    输入是上游已写好的结构化事实（confidence 锚点与各降级标志、
    evidence_by_read、peak_evidence）与 ReadCall 阶段结果。
    - ReadCall 阻断：支持该差异的每条 read 都落在套峰段内 →
      UNR_IN_MIXED_TEMPLATE（硬阻断），p_real 以 low 为锚；
    - p_real：p_real_for(锚点 tier, 峰级证据)；
    - 裁决表 call_variant → to_legacy 反写 status/confidence/action。
    """
    p = params or load_params()
    extra: List[str] = []
    if read_calls is not None:
        hit = read_calls.blocking_hit(v.get("evidence_by_read"))
        if hit:
            v["in_mixed_template"] = hit
            v["confidence"] = "low"
            extra.append("UNR_IN_MIXED_TEMPLATE")
    v["p_real"] = p_real_for(v, v.get("confidence") or "low", evidence)
    sc = call_variant(v, p, extra, evidence)
    v.update(to_legacy(sc))
    return sc


def apply_overlap_discordance(variants: List[Dict], discordances: List[Dict],
                              params=None) -> int:
    """重叠区近平局分歧（共识之后才知道）：该位点已确证的替换加
    UNR_OVERLAP_DISCORDANCE 硬阻断后重新裁决（goal：pending 而非双 ✗）。
    p_real 与评分字段保留。返回降级数。"""
    p = params or load_params()
    pos_set = {d["ref_pos"] for d in discordances}
    n = 0
    for v in variants:
        if (v.get("type") == "substitution" and v.get("status") == "confirmed"
                and v.get("ref_pos") in pos_set):
            rc = v.get("reason_codes") or []
            if "overlap_discordance" not in rc:
                rc.append("overlap_discordance")
            v["reason_codes"] = rc
            v.update(to_legacy(call_variant(v, p)))
            n += 1
    return n


def call_mixed(m: Dict) -> Dict:
    """一条 mixed_variants 记录 → SiteCall（MIXED，或有反证时 ARTIFACT）"""
    srcs = m.get("noise_evidence") or []
    art = _dedup(_NOISE_SRC_MAP.get(s, "ART_" + s.upper()) for s in srcs)
    if art:
        state, primary = ARTIFACT, art[0]
    else:
        state, primary = MIXED, "SUP_MIXED_PEAK"
    alt = f"{m.get('called_base', '')}/{m.get('secondary_base', '')}"
    return {
        "site_id": _site_id(m.get("ref_pos"), "mixed", alt),
        "kind": "mixed",
        "ref_pos": m.get("ref_pos"),
        "state": state,
        "machine_state": state,
        "primary_reason": primary,
        "reasons": _dedup([primary] + art),
        "art_reasons": art,
        "tier": None,
        "p_real": None,
        "af": m.get("af"),
        "support": {"reads": list(m.get("reads") or [])},
        "action": "exclude" if art else "report",
        "review": None,
        "stage_version": SITECALL_VERSION,
    }


def build_site_calls(variants: Optional[List[Dict]],
                     mixed_variants: Optional[List[Dict]]) -> List[Dict]:
    p = load_params()
    return ([call_variant(v, p) for v in variants or []]
            + [call_mixed(m) for m in mixed_variants or []])


_LEGACY_STATUS = {CONFIRMED: "confirmed", UNRESOLVED: "unresolved",
                  ARTIFACT: "rejected"}


def to_legacy(sc: Dict) -> Dict:
    """适配层：SiteCall → 旧字段（一期双跑比对用）"""
    if sc["kind"] == "mixed":
        return {"noise_evidence": bool(sc["art_reasons"])}
    return {"status": _LEGACY_STATUS[sc["machine_state"]],
            "confidence": sc["tier"], "action": sc["action"]}


def legacy_of_variant(v: Dict) -> Dict:
    return {"status": v.get("status"), "confidence": v.get("confidence"),
            "action": v.get("action")}


def dual_run_diffs(variants: Optional[List[Dict]],
                   mixed_variants: Optional[List[Dict]],
                   site_calls: Optional[List[Dict]] = None) -> List[Dict]:
    """双跑比对：返回 SiteCall 反写与旧字段不一致的条目（空 = 等价）"""
    scs = site_calls if site_calls is not None else build_site_calls(
        variants, mixed_variants)
    olds = ([legacy_of_variant(v) for v in variants or []]
            + [{"noise_evidence": bool(m.get("noise_evidence"))}
               for m in mixed_variants or []])
    out = []
    for sc, old in zip(scs, olds):
        new = to_legacy(sc)
        if new != old:
            out.append({"site_id": sc["site_id"], "old": old, "new": new})
    if len(scs) != len(olds):
        out.append({"site_id": "*", "old": len(olds), "new": len(scs)})
    return out
