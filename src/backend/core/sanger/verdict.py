"""结构化判定（goal T2/T5；审计 A 4.1/4.2/5.1/5.2/5.5、B P1-8、P1-10、P1-11）

把 analyze 结果折叠成一个机器可读的 Verdict 对象，所有出口（网页结论头、
Excel 一句话结论、归档分拣、API 下发）由同一 status 驱动，终结三出口
各说各话的分叉：

    status: "pass" | "fail" | "pending" | "mixed" | "partial" | "insufficient"
            | "failed_reads"
    blocking/info: [{kind, ref_start, ref_end, element, detail, action, refs}]
    roi / reads / algo_version 见 goal §3。

状态优先级：fail > failed_reads > mixed > pending > partial > insufficient
> pass。failed_reads 在有 confirmed 差异时让位给 fail（blocking 中仍列出）。

T5：
- ROI 来源：设计记录的插入区间（数据源随 T22 设计记录对接；当前
  实现）> 参考 CDS 特征并集（source="cds_union"）> 整条参考
  （source="whole"，partial 只记 info 不降状态——单引物测整质粒是常规策略）；
- 关键元件表（CRITICAL_TYPES / CRITICAL_NAME_RE，可按需扩充）：元件内
  confirmed 差异 → critical_element_variant（fail）；unresolved → pending；
  参考无任何抗性/ori 注释时在 info 提示盲区；
- 接合区（CDS 模式 analysis_target ±30bp；全质粒模式的插入边界随设计
  记录对接）：区内非 rejected 差异/未决位点 → junction_variant
  （confirmed 为 fail，否则 pending）。
"""

import re
from typing import Dict, List, Optional, Tuple

from core.sanger.batch import MIN_VERDICT_COVERED_BP
from core.sanger.homopolymer import mixed_sample_confirmed
from core.sanger.mixed_call import MINOR_AF_MIN
from core.sanger.overlap import (
    double_strand_fraction as _double_strand_fraction, REQUIRE_DOUBLE_STRAND,
)

# pending 级阻断种类（goal §3 blocking.kind 枚举的待定子集；其余种类随
# 对应任务 T6/T7/T9/T16/T17 落地后加入）
PENDING_KINDS = {
    "unresolved_variant", "unresolved_site", "n_in_cds", "poly_unreliable",
    "dropout", "overlap_discordance", "orf_annotation_suspect",
    "allele_mismatch", "junction_variant", "critical_element_variant",
}

# 未决位点 kind → blocking.kind 映射
_SITE_KIND_MAP = {
    "n_call": "unresolved_site",
    "iupac": "unresolved_site",
    "mixed_uncorroborated": "unresolved_site",
    "poly_undetermined": "poly_unreliable",
    "dropout": "dropout",
    "overlap_discordance": "overlap_discordance",  # T21：重叠区近平局分歧
    "orf_annotation_suspect": "orf_annotation_suspect",  # T16 护栏：ORF 校正越界
}

# 关键元件表（goal §5 T5；依据：克隆筛选/表达验证中「序列必须逐字正确，
# 差异即失败」的功能元件——启动子/RBS/复制原/终止/polyA/重组位点/选择标记/
# 亲和标签）。type 用 GenBank 惯用词；名称匹配不区分大小写。扩充即改表。
CRITICAL_TYPES = {"promoter", "rbs", "rep_origin", "polya_signal", "terminator",
                  "protein_bind", "misc_recomb"}
CRITICAL_NAME_RE = re.compile(
    r"kozak|loxp|att[bplr]|frt|ampr|kanr|cmr|neor|his|flag|ha|myc|strep|gst|mbp",
    re.IGNORECASE)
# 抗性/ori 盲区提示的名称线索（参考未注释这些元件时只提示，不自动注释）
_RESIST_ORI_NAME_RE = re.compile(r"ampr|kanr|cmr|neor|puc|ori|f1", re.IGNORECASE)

# 接合区半宽（goal §5 T5：CDS 模式载体-基因接合区 ±30bp）
JUNCTION_HALF_WIDTH = 30

# ROI 内 verified 覆盖低于该百分比且参考为 whole 口径时只记 info
_WHOLE_PARTIAL_INFO = True

# 旧记录兼容（无 status 字段）的三态推导，与 batch._variant_status_of 同口径
def _vstatus(v: Dict) -> str:
    if v.get("status"):
        return v["status"]
    if v.get("cross_read_resolved"):
        return "rejected"
    return "confirmed" if v.get("confidence") != "low" else "unresolved"


def _critical_features(features: List[Dict]) -> List[Dict]:
    """关键元件筛选（type 或名称命中 CRITICAL 表）"""
    out = []
    for f in features or []:
        if f.get("type", "").lower() in CRITICAL_TYPES or \
                CRITICAL_NAME_RE.search(f.get("name") or ""):
            out.append(f)
    return out


def _ranges_cover(ranges: List, start: int, end: int) -> float:
    """[start,end] 内被区间列表覆盖的 bp 占比（%）"""
    length = end - start + 1
    if length <= 0:
        return 0.0
    cov = sum(max(0, min(int(e), end) - max(int(s), start) + 1)
              for s, e in ranges)
    return round(min(cov, length) / length * 100, 2)


def _junction_ranges(features: List[Dict]) -> List[Tuple[int, int, str]]:
    """接合区（goal T5；B P1-11）：CDS 模式 analysis_target 的两端 ±30bp。
    返回 [(start, end, element)]；全质粒模式的插入边界随设计记录对接。"""
    out: List[Tuple[int, int, str]] = []
    for f in features or []:
        if not f.get("analysis_target") or f.get("type") != "CDS":
            continue
        s, e = int(f["start"]), int(f["end"])
        out.append((max(1, s - JUNCTION_HALF_WIDTH), s + JUNCTION_HALF_WIDTH,
                    f.get("name") or "CDS"))
        out.append((e - JUNCTION_HALF_WIDTH, e + JUNCTION_HALF_WIDTH,
                    f.get("name") or "CDS"))
    return out


def build_verdict(result: Dict, ref_length: int) -> Dict:
    """从 analyze 结果组装 Verdict（只读，不改 result 内其他键）"""
    variants = result.get("variants") or []
    sites = result.get("unresolved_sites") or []
    cds_reports = result.get("cds_reports") or []
    errors = result.get("errors") or []
    reads = result.get("reads") or []
    profiles = result.get("mixed_profiles") or {}
    features = result.get("features") or []
    cov = result.get("coverage") or {}
    verified_ranges = [tuple(r) for r in cov.get("verified_ranges") or []]
    measured_ranges = [tuple(r) for r in
                       (result.get("consensus") or {}).get("covered_ranges") or []]

    blocking: List[Dict] = []
    info: List[Dict] = []

    def _blk(kind: str, ref_start, ref_end, element, detail: str, action: str,
             refs: Optional[List[str]] = None, fail_impact: bool = False,
             **extra) -> None:
        blocking.append({
            "kind": kind,
            "ref_start": ref_start,
            "ref_end": ref_end,
            "element": element,
            "detail": detail,
            "action": action,
            "refs": refs or [],
            **({"fail_impact": True} if fail_impact else {}),
            **extra,
        })

    def _info(kind: str, element, detail: str) -> None:
        info.append({"kind": kind, "ref_start": None, "ref_end": None,
                     "element": element, "detail": detail, "action": "",
                     "refs": []})

    # ROI（goal T5）：CDS 特征并集 > 整条参考；设计记录的插入区间随 T22 对接
    cds_feats = [f for f in features if f.get("type") == "CDS"
                 and 1 <= int(f.get("start") or 0) <= int(f.get("end") or 0) <= ref_length]
    if cds_feats:
        roi_start = min(int(f["start"]) for f in cds_feats)
        roi_end = max(int(f["end"]) for f in cds_feats)
        roi_source = "cds_union"
    else:
        roi_start, roi_end, roi_source = 1, ref_length, "whole"

    # 变体三态（rejected 不进 blocking：resolved_regions 已有裁决行）
    junctions = _junction_ranges(features)
    critical = _critical_features(features)

    def _in_scope(rp: int) -> bool:
        """单点混合的升级范围（goal T7）：CDS/关键元件/接合区等功能区——
        骨架与无注释参考上的散在双峰不升级「疑似混合」（2026-11-06 用户
        定稿：个别双峰位点不升级，经 unresolved_sites 走 △待定）"""
        return (any(int(f["start"]) <= rp <= int(f["end"]) for f in cds_feats)
                or any(int(f["start"]) <= rp <= int(f["end"]) for f in critical)
                or any(j[0] <= rp <= j[1] for j in junctions))

    for v in variants:
        st = _vstatus(v)
        if st == "rejected":
            continue
        feats = "、".join(sorted({f.get("name") or "" for f in v.get("features") or []})) or None
        rp = v.get("ref_pos")
        if st == "confirmed":
            _blk("confirmed_variant", rp,
                 (rp or 0) + int(v.get("length") or 1) - 1,
                 feats, f"{rp} {v.get('type')}（置信 {v.get('confidence')}）",
                 "核对差异清单，必要时换引物复测", fail_impact=True)
        else:
            _blk("unresolved_variant", rp,
                 (rp or 0) + int(v.get("length") or 1) - 1,
                 feats,
                 f"{rp} {v.get('type')}"
                 + (f"（{v['low_reason']}）" if v.get("low_reason") else ""),
                 "核对峰图或补测")
        # 接合区（goal T5；B P1-11）：载体-基因接合处 ±30bp 内任何非 rejected
        # 差异 → junction_variant（confirmed 为 fail，否则 pending）
        jh = next((j for j in junctions if j[0] <= (rp or 0) <= j[1]), None)
        if jh:
            _blk("junction_variant", jh[0], jh[1], jh[2],
                 f"接合区 {rp} {v.get('type')}（{'确证' if st == 'confirmed' else '未决'}）"
                 "——载体-基因接合处差异直接影响插入正确性",
                 "核对峰图；必要时换引物覆盖接合区",
                 fail_impact=st == "confirmed")
    # 引物校验（goal T17；A 1.5）：mismatch 默认 fail——引物加错/方向搞反/
    # 模板换位时差异判读不可信；无法确定引物只记 info「未校验引物」
    for pc in result.get("primer_checks") or []:
        if pc.get("status") == "mismatch":
            _blk("primer_mismatch", None, None, pc.get("primer"),
                 pc.get("detail") or "引物校验未通过",
                 "核对引物与模板的对应关系；必要时换引物复测",
                 fail_impact=True)
        elif pc.get("status") == "unverified":
            _info("primer_unverified", pc.get("read"),
                  pc.get("detail") or "未校验引物")
    # 未决位点（N/模糊调用、poly 不可判定、dropout、无法互检的双峰）
    for u in sites:
        _blk(_SITE_KIND_MAP.get(u.get("kind"), "unresolved_site"),
             u.get("ref_pos"), u.get("ref_pos"), None,
             u.get("detail") or u.get("kind"), "核对峰图或换引物补测",
             refs=u.get("reads") or [])
        jh = next((j for j in junctions if j[0] <= (u.get("ref_pos") or 0) <= j[1]), None)
        if jh:
            _blk("junction_variant", jh[0], jh[1], jh[2],
                 f"接合区 {u['ref_pos']} 未决位点（{u.get('detail') or u.get('kind')}）",
                 "核对峰图；必要时换引物覆盖接合区")
    # CDS：蛋白不一致（fail）与 N 待定（pending）
    for c in cds_reports:
        if c.get("coverage_status") != "uncovered" and c.get("protein_identical") is False:
            hint = "（移码）" if c.get("frameshift_count") else ""
            _blk("confirmed_variant", c.get("start"), c.get("end"), c.get("name"),
                 f"{c.get('name')} 蛋白与设计不一致{hint}",
                 "核对差异清单，必要时重新挑克隆", fail_impact=True)
        if c.get("n_in_cds"):
            _blk("n_in_cds", c.get("start"), c.get("end"), c.get("name"),
                 f"CDS 内 {c['n_in_cds']} 处 N/模糊调用，蛋白判定待定",
                 "核对峰图或换引物补测")
    # 关键元件（goal T5；A 4.1）：启动子/RBS/复制原/终止/选择标记/标签等，
    # 元件内 confirmed → critical_element_variant（fail），unresolved → pending；
    # 元件级覆盖结论（full/partial/uncovered）进 info
    for f in critical:
        s_, e_ = int(f["start"]), int(f["end"])
        el_cov = _ranges_cover(verified_ranges, s_, e_)
        el_state = "full" if el_cov >= 99 else ("uncovered" if el_cov <= 0 else "partial")
        for v in variants:
            st = _vstatus(v)
            if st == "rejected":
                continue
            rp = v.get("ref_pos")
            if not (s_ <= (rp or 0) <= e_ or
                    (v.get("type") == "deletion"
                     and s_ <= (rp or 0) + int(v.get("length") or 1) - 1 <= e_)):
                continue
            _blk("critical_element_variant", s_, e_, f.get("name"),
                 f"{f.get('name')}（{f.get('type')}）内 {rp} {v.get('type')}"
                 f"（{'确证' if st == 'confirmed' else '未决'}）——"
                 "功能元件序列必须逐字正确",
                 "核对峰图/重新挑克隆", fail_impact=st == "confirmed")
        _info("element_coverage", f.get("name"),
              f"{f.get('name')}（{f.get('type')}）verified 覆盖 {el_cov}%（{el_state}）")
    if not critical and not any(
            _RESIST_ORI_NAME_RE.search(f.get("name") or "") for f in features):
        # 盲区提示（goal T5；A 4.1）：参考未注释抗性/ori 元件时不做元件级判读
        _info("unannotated_element", None,
              "参考未注释抗性/复制原点等关键元件，未做元件级判读（自动注释不在范围内）")
    # 单点双峰次等位（goal T7；A 3.2；B P2-20）：AF ≥0.15 且在判定范围
    # （ROI/关键元件/接合区）→ mixed_variant 阻断（status mixed）；其余
    # 只记 info。与同位点 unresolved 替换合并为一条 blocking
    mixed_vars = result.get("mixed_variants") or []
    for m in mixed_vars:
        rp, af = int(m["ref_pos"]), float(m.get("af") or 0)
        if af < MINOR_AF_MIN or not _in_scope(rp):
            _info("mixed_variant", None,
                  f"{rp} 双峰次等位 {m.get('secondary_base')}（AF {af}，"
                  "低于门槛或不在判定范围，仅记录）")
            continue
        _blk("mixed_variant", rp, rp, None,
             f"{rp} 双峰 {m.get('called_base')}/{m.get('secondary_base')}"
             f"（参考 {m.get('ref_base')}，次等位 AF {af}）——疑似混合样品",
             "重新挑单克隆复测，或 tracy 解卷积分离等位",
             refs=m.get("reads") or [], af=af)
    _mixed_pos = {int(m["ref_pos"]) for m in mixed_vars
                  if float(m.get("af") or 0) >= MINOR_AF_MIN and _in_scope(int(m["ref_pos"]))}
    if _mixed_pos:
        # 同位点合并（goal T7）：位点级混合与未决替换是同一证据的两面，
        # 只留 mixed_variant 一条（detail 已含双峰判读）
        blocking[:] = [b for b in blocking
                       if not (b["kind"] == "unresolved_variant"
                               and b.get("ref_start") in _mixed_pos)]
    # 混合样品（widespread 分级 + 跨引物互检降级例外；scattered 只进
    # unresolved_sites 的 mixed_uncorroborated，不升级 mixed_sample）
    wide = [p for p in profiles.values() if p.get("class") == "widespread"]
    if wide and mixed_sample_confirmed(reads, result.get("mixed_corroboration")):
        counts = sorted(int(p.get("count") or 0) for p in wide)
        cr = str(counts[0]) if counts[0] == counts[-1] else f"{counts[0]}-{counts[-1]}"
        _blk("mixed_sample", None, None, None,
             f"{len(wide)} 条 read 检出双峰（每条 {cr} 处，疑似混合样品）",
             "重新挑单克隆复测")
    # 解卷积等位（goal T10；A 3.6）：任一等位与设计不符 → allele_mismatch
    # （pending 级——主/次克隆须分开核对，不能只看主峰序列）
    for fname, als in (result.get("decomposed_alleles") or {}).items():
        for i, al in enumerate(als if isinstance(als, list) else [], 1):
            bad = [c for c in al.get("cds_reports") or []
                   if c.get("coverage_status") != "uncovered"
                   and c.get("protein_identical") is False]
            if bad:
                _blk("allele_mismatch", None, None, None,
                     f"{fname} 等位 {i} 与设计不符（{bad[0].get('name')} 蛋白不一致）"
                     "——解卷积等位须分别核对",
                     "按等位判读核对主/次克隆，必要时重新挑单克隆", refs=[fname])
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
        # 端部软剪切的高质量段（goal T6；A 2.1）+ split-read 定位（T19）：
        # 命中→结构断点（段落点坐标可查），未命中→未知序列；均 fail
        for hq in r.get("unaligned_hq_segments") or []:
            sr_ = hq.get("split_read") or {}
            if sr_.get("status") == "breakpoint":
                _blk("structural_breakpoint", sr_.get("ref_start"),
                     sr_.get("ref_end"), None,
                     f"{r.get('filename')} {hq.get('side')}端 "
                     f"{hq.get('length')}bp 高质量序列 split-read 定位到参考 "
                     f"{sr_.get('ref_start')}-{sr_.get('ref_end')}"
                     + ("（跨原点）" if sr_.get("wraps_origin") else "")
                     + f"（身份 {sr_.get('identity')}），连接/结构异常，该部分未验证",
                     "核对连接处/样品身份，必要时换引物或补测对侧",
                     fail_impact=True)
            else:
                _blk("unaligned_hq_segment", hq.get("adj_ref_start"),
                     hq.get("adj_ref_end"), None,
                     f"{r.get('filename')} {hq.get('side')}端 "
                     f"{hq.get('length')}bp 高质量序列未比对上参考"
                     f"（平均 Q{hq.get('mean_q')}），split-read 未在参考定位"
                     "（未知序列），该部分未验证",
                     "核对连接处/样品身份，必要时换引物或补测对侧",
                     fail_impact=True)
        # read 级伪迹区段（goal T20；A 3.5）：只作 info（特征已随
        # evidence_by_read 下发），不进任何判定分支
        arts = r.get("artifacts") or []
        if arts:
            brief = "、".join(f"{a['kind']} {a['start']}-{a['end']}"
                              for a in arts[:4])
            _info("read_artifacts", None,
                  f"{r.get('filename')} 检出伪迹区段：{brief}"
                  f"{' 等' if len(arts) > 4 else ''}"
                  "——区内调用与峰证据应谨慎采信")
    # ROI 覆盖分级（goal T5；A 4.2）：实测（measured）口径定「部分验证」。
    # 单 read 首尾不可信区是固有损耗、verified<100% 几乎恒真（T3 决策沿用：
    # verified 语义切换随 T12 复评，否则 123bp 全 CDS 对照必翻红）：
    # - ROI 实测 0% → roi_uncovered（insufficient：目标没测到）；
    # - ROI 实测 <99% → roi_uncovered（partial：测了但没测全，S8）；
    # - ROI 实测齐全且 verified<100% → roi_unverified 阻断明示（cds_union）/
    #   只记 info（whole：单引物测整质粒是常规策略）；均不升状态
    roi_measured_pct = _ranges_cover(measured_ranges, roi_start, roi_end)
    roi_verified_pct = _ranges_cover(verified_ranges, roi_start, roi_end)
    roi_measured_bp = round((roi_end - roi_start + 1) * roi_measured_pct / 100)
    measured_bp = sum(int(e) - int(s) + 1 for s, e in measured_ranges)
    full_cds = any(c.get("coverage_status") == "full" for c in cds_reports)
    bp_gate_failed = (not full_cds and measured_bp < MIN_VERDICT_COVERED_BP)
    roi_gap = roi_source != "whole" and roi_measured_pct < 99
    if roi_gap and roi_measured_pct <= 0:
        _blk("roi_uncovered", roi_start, roi_end, None,
             f"判定区域（{roi_start}-{roi_end}）未被任何 read 覆盖，目标未验证",
             "补测覆盖目标的引物或核对参考选择")
    elif roi_gap:
        _blk("roi_uncovered", roi_start, roi_end, None,
             f"判定区域实测覆盖 {roi_measured_pct}%（约 {roi_measured_bp}bp），"
             "未覆盖部分未验证",
             "补测缺口区域或核对参考选择")
    elif roi_verified_pct < 100:
        if roi_source == "whole":
            if _WHOLE_PARTIAL_INFO:
                _info("roi_unverified", None,
                      f"验证覆盖 {roi_verified_pct}%（实测 {roi_measured_pct}%）"
                      "——首尾不可信区与 N/模糊调用列不构成验证，判定以实测为准")
        else:
            _blk("roi_unverified", roi_start, roi_end, None,
                 f"判定区域验证覆盖 {roi_verified_pct}%"
                 f"（实测 {roi_measured_pct}%）——不可信区与 N/模糊调用列不构成验证",
                 "核对覆盖缺口，必要时补引物")
    if bp_gate_failed:
        _blk("roi_unverified", None, None, None,
             f"实测仅 {measured_bp}bp，不足以确认是否为目标质粒",
             "补测引物或换参考")
    # 双链覆盖（goal T21；A 4.4）：占比随 roi 下发；「必须双链」策略默认
    # 关闭（单引物测整质粒是常规策略），启用时未满双链记 pending
    dsf = _double_strand_fraction(reads, roi_start, roi_end)
    if (REQUIRE_DOUBLE_STRAND and dsf is not None
            and roi_measured_pct > 0 and dsf < 100):
        _blk("unresolved_site", roi_start, roi_end, None,
             f"ROI 双链覆盖 {dsf}%（策略：ROI 必须双链覆盖）——单链覆盖区域未经对侧验证",
             "补测对侧链引物，或在设置中关闭双链覆盖强制")

    has_fail = any(b.get("fail_impact") for b in blocking)
    has_failed_reads = any(b["kind"] == "read_failed" for b in blocking)
    # mixed：mixed_sample 或 ROI 内 mixed_variant（goal T2/T7）
    has_mixed = any(b["kind"] in ("mixed_sample", "mixed_variant") for b in blocking)
    has_pending = any(b["kind"] in PENDING_KINDS for b in blocking)
    has_partial = roi_gap and roi_measured_pct > 0
    has_insufficient = (any(b["kind"] == "partial_alignment" for b in blocking)
                        or (roi_gap and roi_measured_pct <= 0))
    # 优先级（goal §3）：fail > failed_reads > mixed > pending > partial
    # > insufficient > pass；failed_reads 让位 fail（blocking 中仍列出）
    if has_fail:
        status = "fail"
    elif has_failed_reads:
        status = "failed_reads"
    elif has_mixed:
        status = "mixed"
    elif has_pending:
        status = "pending"
    elif has_partial:
        status = "partial"
    elif has_insufficient or bp_gate_failed:
        status = "insufficient"
    else:
        status = "pass"

    return {
        "status": status,
        "blocking": blocking,
        "info": info,
        # ROI（goal T5）：设计记录插入区间（T22 对接）> CDS 特征并集 > 整条参考
        "roi": {
            "start": roi_start,
            "end": roi_end,
            "source": roi_source,
            "verified_fraction": round(roi_verified_pct / 100, 4),
            "measured_fraction": round(roi_measured_pct / 100, 4),
            # ROI 内正负两链都被覆盖的 bp 占比（goal T21；此前恒 None）
            "double_strand_fraction": dsf,
        },
        "reads": {
            "total": len(reads) + len(errors),
            "analyzed": len(reads),
            "failed": [{"file": e.get("filename"), "error": e.get("error", "")}
                       for e in errors],
        },
        "algo_version": result.get("algo_version"),
    }
