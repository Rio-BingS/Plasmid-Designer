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
- 需测范围（goal v3 W2 重构）：判定只对 scope 负责——声明范围直接采用；
  未声明时合成 measured 口径（R=实测区段），范围外「未评估」不降级、
  不判 partial，任何出口不得输出「构建与设计一致」（goal v3 §0.1、§8
  决策 1）。覆盖缺口只对声明范围判定（roi_uncovered→partial、
  roi_unverified→pending；W6 起按引物理论覆盖 E 归因）；
- 关键元件表（CRITICAL_TYPES / CRITICAL_NAME_RE，可按需扩充）：范围内
  元件必须验证齐全（缺口→critical_element_unverified pending）；元件内
  confirmed 差异 → critical_element_variant：范围内 fail、范围外 pending
  （§8 决策 2）；参考无任何抗性/ori 注释时在 info 提示盲区；
- 接合区（CDS 模式 analysis_target ±30bp；设计插入边界 ±30bp，W2 扩展）：
  区内非 rejected 差异/未决位点 → junction_variant（confirmed 为 fail，
  否则 pending）。
"""

import re
import hashlib
from typing import Dict, List, Optional, Tuple

from core.sanger.batch import MIN_VERDICT_COVERED_BP
from core.sanger.homopolymer import mixed_sample_confirmed
from core.sanger.mixed_call import MINOR_AF_MIN, MINOR_AF_SOFT, MIXED_AF_MIN
from core.sanger.overlap import (
    double_strand_fraction as _double_strand_fraction, REQUIRE_DOUBLE_STRAND,
)
from core.sanger.scope import (  # goal v3 W1：范围统一载体（§3.1）
    JUNCTION_HALF_WIDTH, Scope, coerce_scope, junction_ranges,
)

# pending 级阻断种类（goal §3 blocking.kind 枚举的待定子集；其余种类随
# 对应任务 T6/T7/T9/T16/T17 落地后加入）
PENDING_KINDS = {
    "unresolved_variant", "unresolved_site", "n_in_cds", "poly_unreliable",
    "dropout", "overlap_discordance", "orf_annotation_suspect",
    "allele_mismatch", "junction_variant", "critical_element_variant",
    # goal v3 W4：低丰度双峰（AF 0.15-0.23 及声明范围软带 0.12-0.15）——
    # 不足以判混但必须复核（RV P0-3「要么真正生效要么删除」的生效形态）
    "minor_af_site",
    # 2026-11-08 复审：cds_union 实测齐全但 verified<100% 的残余缺口
    # （首尾不可信区/N 列）——T12 复评挂账落地，不再出现 ✓ 头行与
    # 阻断项并存的矛盾；关键元件覆盖缺口同理（不能只写 info）；
    # 首端硬区内未比对的高质量段（≤30bp 连接错误原来被解析端豁免）
    "roi_unverified", "critical_element_unverified", "end_junction_unverified",
}

# 三出口统一（goal v3 W12，OC1/OC2；CM 审核三出口分叉的根因是各出口
# 自行维护符号/词表）：网页头行符号、Excel 结论前缀词全部由
# verdict.status 经这两个映射导出。headline 的中间状态词（部分验证等）
# 是头行专属措辞，不在 Excel 词表内（Excel 以「待定：」承载细节）。
STATUS_SYMBOLS = {
    "pass": "✓", "fail": "✗",
    # ✓/✗ 之外一律 △（证据不足/未决/混合/覆盖缺口都不是判读结果）
    "pending": "△", "partial": "△", "mixed": "△",
    "insufficient": "△", "failed_reads": "△",
}
# 头行中间状态词（X3，goal §3.3）：Excel 结论列 = headline 去掉符号，
# 因此这张表同时是 Excel 结论的前缀词源——两个出口不能再各有一套词
# （OC1/OC2 分叉的根因就是词表分家）。
STATUS_HEAD_WORDS = {
    "pass": "合格", "fail": "不合格", "pending": "待定",
    "partial": "部分验证", "mixed": "疑似混合", "insufficient": "无法判定",
    "failed_reads": "读取失败",
}
STATUS_EXCEL_WORDS = dict(STATUS_HEAD_WORDS)


def status_symbol(status: str) -> str:
    """verdict.status → 网页/结论文本头行符号（✓/✗/△；W12 三出口同源）"""
    return STATUS_SYMBOLS.get(status, "△")


def status_excel_word(status: str) -> str:
    """verdict.status → Excel 结论前缀词（合格/不合格/待定…；W12 同源）"""
    return STATUS_EXCEL_WORDS.get(status, "待定")


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

# 接合区半宽（goal §5 T5：CDS 模式载体-基因接合区 ±30bp）——常量与实现
# 已收敛到 core.sanger.scope（goal v3 W1），此处保留别名兼容旧引用
# JUNCTION_HALF_WIDTH = scope.JUNCTION_HALF_WIDTH（见顶部 import）

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
    """接合区（goal T5；B P1-11）：实现收敛到 core.sanger.junction_ranges
    （goal v3 W1）；保留私有别名兼容既有调用"""
    return junction_ranges(features)


def _range_text(scope_obj, ranges, ref_length: int,
                verified_ranges: List) -> str:
    """头行范围段（X3：三出口唯一来源，goal §3.3）。

    - 声明范围：有元件名时带名——单段「名称（a–b）」；多段同属一个元件
      「名称（a–b、c–d）」；环状参考上跨原点被拆成首尾两段的写成一段
      「名称（3701–300，跨原点）」（HC2）；
    - 未声明范围：只说已测区段，且区间取 **verified**（实测含首尾不可信
      区，不构成验证；measured 只在明细列出现，goal §3.3），无 verified
      时退回实测区段。
    """
    _MAX = 3
    if scope_obj is not None and scope_obj.declared and ranges:
        name = scope_obj.elements[0] if len(scope_obj.elements) == 1 else None
        if (name and len(ranges) >= 2 and ranges[0][0] == 1
                and ranges[-1][1] == ref_length):
            return (f"范围：{name}（{ranges[-1][0]}–{ranges[0][1]}，跨原点）")
        if name and len(ranges) == 1:
            return f"范围：{name}（{ranges[0][0]}–{ranges[0][1]}）"
        if name:
            inner = "、".join(f"{a}–{b}" for a, b in ranges[:_MAX])
            more = f" 等 {len(ranges)} 段" if len(ranges) > _MAX else ""
            return f"范围：{name}（{inner}{more}）"
        if scope_obj.elements and len(scope_obj.elements) == len(ranges):
            rng_txt = "、".join(
                f"{n}（{a}–{b}）" for n, (a, b) in
                zip(scope_obj.elements, ranges[:_MAX]))
        else:
            rng_txt = "、".join(f"{a}–{b}" for a, b in ranges[:_MAX])
        return f"范围：{rng_txt}"
    spans_src = verified_ranges or ranges
    spans = "、".join(f"{a}–{b}" for a, b in spans_src[:_MAX])
    more = f" 等 {len(spans_src)} 段" if len(spans_src) > _MAX else ""
    return f"已测 {spans}{more}；范围外未评估"


def _fail_site_count(blocking: List[Dict]) -> int:
    """确证差异**按位点去重计数**（X3；goal §3.3 / RB P1-1）。

    同一处变异会同时落成多条 fail 条目：普通确证差异 + 接合区变体（位置
    在接合区窗口内）+ CDS 蛋白不一致（移码，范围是整段 CDS）。三者对用户
    是 1 处差异，头行不得按条目数报成 2–3 处（HC1）。
    """
    pts, spans = set(), []
    for b in blocking:
        if not b.get("fail_impact"):
            continue
        p = b.get("site")
        if p is None and b.get("ref_start") is not None \
                and b.get("ref_start") == b.get("ref_end"):
            p = b.get("ref_start")
        if p is not None:
            pts.add(int(p))
        else:
            spans.append(b)
    n = len(pts)
    for b in spans:
        s_, e_ = b.get("ref_start"), b.get("ref_end")
        if s_ is None or e_ is None:
            n += 1
            continue
        # 区间型条目（接合区窗口 / CDS 整段）已被其中某个具体位点代表
        if not any(int(s_) <= p <= int(e_) for p in pts):
            n += 1
    return n


def _unres_blocks(v: Dict) -> bool:
    """未决差异是否进待核（2026-11-10 分区口径）：

    - 核心区 / 多 read / 无分区信息（旧记录）→ 待核（T1：证据不足 ≠ 一致）；
    - 尾端扩展区（tail_gray 之后、信号达标）→ 待核：不静默也不确证（T12）；
    - 首端（含 31–60bp 灰区）：仅干净替换（Q≥30、非双峰位）待核（S9），
      其余为引物区噪声；
    - 信号区外 → 只列出备查；Q≥40 且突变主峰 ≥85% 的替换例外，进待核（S13）。"""
    zc = v.get("zone_class")
    if zc in (None, "core", "tail_ext"):
        return True
    if zc in ("front", "front_gray"):
        return bool(v.get("front_clean"))
    if zc == "outside":
        return bool(v.get("outside_clean"))
    return False


def _mixed_site_artifact(m: Dict, profiles: Dict, reads: List[Dict]) -> str:
    """v3.4：mixed_variant 的伪影形态判别，返回原因（空串=不是伪影形态）。
    全部报告 read 的 mixed_profile 判为 noise，或位点 read 原始坐标全部
    ≤ 该 read 可信区参数 front_gray（复用既有参数，不新增阈值）"""
    rds = m.get("reads") or []
    if not rds:
        return ""
    if all((profiles.get(fn) or {}).get("class") == "noise" for fn in rds):
        return "read 级双峰模型判为噪声（比值不一致或位点 Q 低）"
    rp_by = m.get("read_pos") or {}
    by_fn = {r.get("filename"): r for r in reads}
    raws = []
    for fn in rds:
        z = (by_fn.get(fn) or {}).get("reliable_zone") or {}
        pos = rp_by.get(fn)
        if pos is None or not z.get("params"):
            return ""
        raws.append((pos + (z["raw_start"] - z["start"]), z["params"]["front_gray"]))
    if raws and all(raw <= fg for raw, fg in raws):
        return "位于 read 首端 front_gray 区（峰展宽/染料团区）"
    return ""


def build_verdict(result: Dict, ref_length: int, scope=None,
                  synonymous_action: str = "ok",
                  review_decisions: Optional[List[Dict]] = None) -> Dict:
    """从 analyze 结果组装 Verdict（只读，不改 result 内其他键）。

    goal v3 W1：build_verdict 是纯函数——除 result/scope/ref_length 外不读
    任何状态、不调用比对；同一份 result 换 scope 重算可复现（goal v3 §0.6）。
    scope：None / Scope / dict / str（元件名、区间、关键字，见 scope.py）。
    synonymous_action（goal v3 §8 决策 3，W5）：确证同义替换（CDS 内、蛋白
    不变）的处理——"ok" 默认合格口径（info 注明，不阻断）；"fail" 沿旧口径
    按确证差异阻断。review_decisions（goal v3 W14，PL F3）：人工确认记录
    [{ref_pos, decision: artifact|mutation, by, at, note}]——伪影条目移出
    blocking（info 留痕），突变条目升级确证（fail_impact）；headline 追加
    「人工确认 N 处」。"""
    variants = result.get("variants") or []
    sites = result.get("unresolved_sites") or []
    cds_reports = result.get("cds_reports") or []
    _cds_by_name = {c.get("name"): c for c in cds_reports if c.get("name")}
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

    def _info(kind: str, element, detail: str,
              ref_start=None, ref_end=None) -> None:
        info.append({"kind": kind, "ref_start": ref_start,
                     "ref_end": ref_end,
                     "element": element, "detail": detail, "action": "",
                     "refs": []})

    # 范围解析（goal v3 W2；PL A2/A4）：声明范围直接采用；未声明时合成
    # measured 口径——R = 实测区段（比对覆盖），判定只对实测负责：范围外
    # 「未评估」不降级，不判 partial，任何出口不得输出「构建与设计一致」
    # （goal v3 §0.1；§8 决策 1：未声明沿用 pass、不新增状态）
    scope_obj = coerce_scope(scope, features, ref_length)
    if scope_obj is None:
        scope_obj = Scope(
            ranges=[(int(s), int(e)) for s, e in measured_ranges],
            source="measured", declared=False,
            reason="未声明范围：仅描述实测区段，未测部分未评估")
    declared = bool(scope_obj.declared and scope_obj.ranges)
    scope_ranges = scope_obj.ranges
    # W8：接合区窗口（scope.junctions，cds_target 来源）属于需测范围——
    # 覆盖缺口/变异归因按并集判定；但不进头行区间渲染（cds_target 路径
    # 与「全质粒 + scope」的结论逐字同源，CM5）
    junction_windows = [(int(s), int(e)) for s, e in scope_obj.junctions]
    req_ranges = scope_obj.merged_ranges() or scope_ranges
    cds_feats = [f for f in features if f.get("type") == "CDS"
                 and 1 <= int(f.get("start") or 0) <= int(f.get("end") or 0) <= ref_length]
    # 旧字段 verdict.roi 保留（goal v3 §2 API 契约）：取 scope 外包框
    if scope_ranges:
        roi_start, roi_end = scope_obj.bounding_box()
    else:
        roi_start, roi_end = 1, ref_length
    roi_source = scope_obj.source

    # 变体三态（rejected 不进 blocking：resolved_regions 已有裁决行）
    junctions = _junction_ranges(features)
    junctions.extend((s_, e_, scope_obj.elements[0] if scope_obj.elements
                      else "接合区") for s_, e_ in junction_windows)
    if scope_obj.source == "design_insert":
        # goal v3 W2：接合区扩展——设计插入边界的两端 ±30bp 也是接合区
        # （载体-插入接缝与 CDS 模式同判）
        for s_, e_ in scope_ranges:
            junctions.append((max(1, s_ - JUNCTION_HALF_WIDTH),
                              s_ + JUNCTION_HALF_WIDTH, "插入5′接合区"))
            junctions.append((e_ - JUNCTION_HALF_WIDTH,
                              e_ + JUNCTION_HALF_WIDTH, "插入3′接合区"))
    critical = _critical_features(features)

    def _in_scope(rp: int) -> bool:
        """单点混合的升级范围（goal T7）：CDS/关键元件/接合区等功能区——
        骨架与无注释参考上的散在双峰不升级「疑似混合」（2026-11-06 用户
        定稿：个别双峰位点不升级，经 unresolved_sites 走 △待定）"""
        return (any(int(f["start"]) <= rp <= int(f["end"]) for f in cds_feats)
                or any(int(f["start"]) <= rp <= int(f["end"]) for f in critical)
                or any(j[0] <= rp <= j[1] for j in junctions))

    def _scope_hit(rp: int, v_end: int) -> bool:
        """变异是否落在需测范围内（与任一范围段相交即算在内；W8 起含
        接合区窗口——cds_target 的接合区属于需测范围）"""
        return (any(s_ <= v_end and rp <= e_ for s_, e_ in scope_ranges)
                or any(s_ <= v_end and rp <= e_ for s_, e_ in junction_windows))

    for v in variants:
        st = _vstatus(v)
        if st == "rejected":
            continue
        feats = "、".join(sorted({f.get("name") or "" for f in v.get("features") or []})) or None
        rp = v.get("ref_pos")
        v_end = (rp or 0) + int(v.get("length") or 1) - 1
        # 接合区（goal T5；B P1-11）：载体-基因接合处 ±30bp 内任何非 rejected
        # 差异 → junction_variant（confirmed 为 fail，否则 pending）
        jh = next((j for j in junctions if j[0] <= (rp or 0) <= j[1]), None)
        if not _scope_hit(rp or 0, v_end):
            # 范围外（goal v3 W2）：接合区仍按目标边界语义判；关键元件内
            # 的确证差异判 pending 不判 fail（§8 决策 2）；其余骨架只记
            # out_of_scope_variant（info）——范围外未评估，不降级不阻断
            ce = next((f for f in critical
                       if int(f["start"]) <= (rp or 0) <= int(f["end"])
                       or (v.get("type") == "deletion"
                           and int(f["start"]) <= v_end <= int(f["end"]))), None)
            if jh:
                _blk("junction_variant", jh[0], jh[1], jh[2],
                     f"接合区 {rp} {v.get('type')}（{'确证' if st == 'confirmed' else '未决'}，"
                     "范围外）——载体-基因接合处差异直接影响插入正确性",
                     "核对峰图；必要时换引物覆盖接合区",
                     fail_impact=st == "confirmed", site=rp)
            elif ce and (st == "confirmed" or _unres_blocks(v)):
                _blk("critical_element_variant", int(ce["start"]), int(ce["end"]),
                     ce.get("name"),
                     f"{ce.get('name')}（{ce.get('type')}）内 {rp} {v.get('type')}"
                     f"（{'确证' if st == 'confirmed' else '未决'}，范围外）——"
                     "功能元件差异，虽在需测范围外仍应关注",
                     "扩大验证范围复测，或单独核对该元件")
            else:
                _info("out_of_scope_variant", feats,
                      f"{rp} {v.get('type')}"
                      f"（{'确证' if st == 'confirmed' else '未决'}）——在需测范围外，未评估",
                      ref_start=rp, ref_end=v_end)
            continue
        # 同义突变可配置（goal v3 §8 决策 3，W5）：确证替换落在 CDS 内且
        # 该 CDS 蛋白判定一致（蛋白一致却有确证替换 ⇒ 只能是同义）→ 默认
        # 合格口径只记 info（克隆筛选最常问「这个差异动不动蛋白」）；
        # synonymous_action="fail" 沿旧口径走下面的确证差异阻断
        if (st == "confirmed" and synonymous_action != "fail"
                and v.get("type") == "substitution"):
            _names = {f.get("name") for f in v.get("features") or []
                      if f.get("type") == "CDS"}
            if _names and all(
                    (_cds_by_name.get(n) or {}).get("protein_identical") is True
                    for n in _names):
                _info("synonymous_variant", feats,
                      f"{rp} 同义替换（蛋白不变，合格口径注明备查；"
                      "如需按差异判定可在分析设置中改为「不合格」）")
                continue
        if st == "confirmed":
            _blk("confirmed_variant", rp, v_end,
                 feats, f"{rp} {v.get('type')}（置信 {v.get('confidence')}）",
                 "核对差异清单，必要时换引物复测", fail_impact=True)
        elif _unres_blocks(v):
            # refs 带上来源 read（2026-11-10）：复核队列按 read 折叠靠 refs，
            # 缺 refs 时判混 read 上的未决位点散落成独立条目（RQ1 实测 4 条）
            _blk("unresolved_variant", rp, v_end,
                 feats,
                 f"{rp} {v.get('type')}"
                 + (f"（{v['low_reason']}）" if v.get("low_reason") else ""),
                 "核对峰图或补测",
                 refs=[v["read"]] if v.get("read") else [])
        else:
            # 2026-11-10：信号区外 / 首端噪声（indel、低 Q、双峰位）的单 read
            # 低置信差异只列出备查、不阻断——真实单克隆 17647 的 969bp Q12
            # 替换、M13R 首端 Q25 双峰插入均属此类（work/fp_features.py）
            _info("unresolved_variant", feats,
                  f"{rp} {v.get('type')}（低置信，未计入判定）"
                  + (f"：{v['low_reason']}" if v.get("low_reason") else ""),
                  ref_start=rp, ref_end=v_end)
        if jh:
            # site=rp：头行证据按位点去重（X3/HC1），接合区窗口本身不是位点
            _blk("junction_variant", jh[0], jh[1], jh[2],
                 f"接合区 {rp} {v.get('type')}（{'确证' if st == 'confirmed' else '未决'}）"
                 "——载体-基因接合处差异直接影响插入正确性",
                 "核对峰图；必要时换引物覆盖接合区",
                 fail_impact=st == "confirmed", site=rp)
        # 关键元件内的范围内差异：critical_element_variant 与通用条目并列
        # （功能元件序列必须逐字正确；goal T5 沿用）
        ce_in = next((f for f in critical
                      if int(f["start"]) <= (rp or 0) <= int(f["end"])
                      or (v.get("type") == "deletion"
                          and int(f["start"]) <= v_end <= int(f["end"]))), None)
        if ce_in and (st == "confirmed" or _unres_blocks(v)):
            _blk("critical_element_variant", int(ce_in["start"]), int(ce_in["end"]),
                 ce_in.get("name"),
                 f"{ce_in.get('name')}（{ce_in.get('type')}）内 {rp} {v.get('type')}"
                 f"（{'确证' if st == 'confirmed' else '未决'}）——"
                 "功能元件序列必须逐字正确",
                 "核对峰图/重新挑克隆", fail_impact=st == "confirmed")
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
        elif pc.get("status") == "no_signal":
            # 引物无信号（goal v3 W7，PL B4）：承诺的引物没有对应 read，
            # 属 read_shortfall 语义——策略性 partial，不判 pass（W3 契约）
            _blk("scope_read_shortfall", None, None, pc.get("primer"),
                 pc.get("detail") or "引物无对应 read——引物无信号",
                 "重测该引物，或核对信息表引物列")
    # 未决位点（N/模糊调用、poly 不可判定、dropout、无法互检的双峰）。
    # 范围闸（goal v3 §3.4，与变体循环同款）：范围外的未决位点不阻断
    # （未评估，SC10 构造复核时暴露——声明 301-1299 时 1301+ 的双峰
    # 曾误入 blocking 驱 pending）；无参考坐标（ref_pos 缺失）无法归因，
    # 保留旧行为进 blocking
    for u in sites:
        rp = int(u.get("ref_pos") or 0)
        if rp >= 1 and scope_ranges and not any(
                s <= rp <= e for s, e in req_ranges):
            _info("out_of_scope_unresolved", u.get("ref_pos"),
                  f"参考位置 {rp} 的未决位点（{u.get('detail') or u.get('kind')}）"
                  f"在需测范围外——未评估", ref_start=rp, ref_end=rp)
            continue
        if (u.get("kind") == "mixed_uncorroborated"
                and float(u.get("af") or 0) < MIXED_AF_MIN):
            # 2026-11-10：非混合 read 上 AF<0.23 的孤立双峰（无他 read 覆盖、
            # 无法互检）列出备查、不阻断 ✓——真实单克隆普遍存在；AF≥0.23
            # 的孤立双峰仍进待核（T1 原意，S16/M7）
            _info("unresolved_site", u.get("ref_pos"),
                  f"参考位置 {rp} 个别双峰位点（{u.get('detail') or u.get('kind')}"
                  + (f"，次等位 AF {u['af']}" if u.get("af") is not None else "")
                  + "）——低于 0.23，可能为碱基噪声，未计入判定",
                  ref_start=rp, ref_end=rp)
            continue
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
    # 关键元件（goal T5；A 4.1）：启动子/RBS/复制原/终止/选择标记/标签等。
    # 元件级覆盖闸（goal v3 W2）：声明范围内相交的元件必须验证齐全；
    # 未声明范围只对完整落入实测区段的元件负责（部分相交 → 未评估），
    # 范围外元件不阻断（SC1：AmpR/ori not_assessed）。full 记 info，
    # partial/uncovered 进 pending 阻断（critical_element_unverified）。
    # 元件内差异条目移至变体循环（范围内 fail / 范围外 pending，§8 决策 2）
    for f in critical:
        s_, e_ = int(f["start"]), int(f["end"])
        if declared:
            gate = any(s_ <= e2 and s1 <= e_ for s1, e2 in scope_ranges)
        else:
            gate = any(s1 <= s_ and e_ <= e2 for s1, e2 in scope_ranges)
        if not gate:
            continue
        el_cov = _ranges_cover(verified_ranges, s_, e_)
        el_state = "full" if el_cov >= 99 else ("uncovered" if el_cov <= 0 else "partial")
        if el_state == "full":
            _info("element_coverage", f.get("name"),
                  f"{f.get('name')}（{f.get('type')}）verified 覆盖 {el_cov}%（full）")
        else:
            _blk("critical_element_unverified", s_, e_, f.get("name"),
                 f"{f.get('name')}（{f.get('type')}）verified 覆盖 {el_cov}%"
                 f"（{el_state}）——未验证部分可能存在差异，功能元件必须逐字正确",
                 "补测覆盖该元件的引物")
    if not critical and not any(
            _RESIST_ORI_NAME_RE.search(f.get("name") or "") for f in features):
        # 盲区提示（goal T5；A 4.1）：参考未注释抗性/ori 元件时不做元件级判读
        _info("unannotated_element", None,
              "参考未注释抗性/复制原点等关键元件，未做元件级判读（自动注释不在范围内）")
    # 单点双峰次等位（goal T7；A 3.2；B P2-20；W4 af 分带 RV P0-3）：
    # 功能区（CDS/关键元件/接合区）内 AF ≥0.23 → mixed_variant（疑似混合，
    # status mixed）；0.15-0.23 → minor_af_site（pending 复核项，丰度不足
    # 判混但不得静默）；0.12-0.15 软检出带仅在声明范围内升级 pending
    # （未声明范围按模型地板口径，软带=噪声级只记 info）；骨架散在双峰
    # 一律 info（2026-11-06 定稿：个别双峰位点不升级，经 unresolved_sites
    # 走 △待定）。与同位点 unresolved 替换合并为一条 blocking
    mixed_vars = result.get("mixed_variants") or []
    _declared_scope = bool(scope_obj is not None and scope_obj.declared)
    _mixed_pos: set = set()
    for m in mixed_vars:
        rp, af = int(m["ref_pos"]), float(m.get("af") or 0)
        if m.get("noise_evidence") and af >= MINOR_AF_SOFT and _in_scope(rp):
            # 2026-11-11：报该位点的每条 read 都有反证——其他引物在该位点
            # 高质量单峰读出设计碱基（互检否决同一口径），或批内同引物其他
            # 克隆同位点同碱基次峰且本 read 主峰未降（批内背景）——真实亚群
            # 会同时出现在同一模板的其他 read 上，且会压低主峰，故记 info
            _src = "、".join({"cross_primer": "其他引物干净读出",
                             "batch_background": "同批同引物其他克隆同位点同样有次峰"}[k]
                            for k in m["noise_evidence"])
            _info("mixed_variant", None,
                  f"{rp} 双峰 {m.get('called_base')}/{m.get('secondary_base')}"
                  f"（参考 {m.get('ref_base')}，次等位 AF {af}）——{_src}，"
                  "判为噪声，未计入判定", ref_start=rp, ref_end=rp)
            continue
        if af < MINOR_AF_SOFT or not _in_scope(rp):
            _info("mixed_variant", None,
                  f"{rp} 双峰次等位 {m.get('secondary_base')}（AF {af}，"
                  "低于门槛或不在判定范围，仅记录）")
            continue
        _pair = (f"{rp} 双峰 {m.get('called_base')}/{m.get('secondary_base')}"
                 f"（参考 {m.get('ref_base')}，次等位 AF {af}）")
        _demote = _mixed_site_artifact(m, profiles, reads)
        if af >= MIXED_AF_MIN and _demote:
            # v3.4：read 级模型已判该 read 双峰为噪声，或位点落在 read 首端
            # front_gray 区内——不判混合，降为待核（仍阻断 ✓，只加强不削弱）
            _blk("unresolved_site", rp, rp, None,
                 _pair + f"——{_demote}，不判混合，需复核",
                 "核对峰图或换引物补测", refs=m.get("reads") or [], af=af)
            continue
        if af >= MIXED_AF_MIN:
            _blk("mixed_variant", rp, rp, None,
                 _pair + "——疑似混合样品",
                 "重新挑单克隆复测，或 tracy 解卷积分离等位",
                 refs=m.get("reads") or [], af=af)
            _mixed_pos.add(rp)
        elif (af >= MINOR_AF_MIN or _declared_scope) and len(m.get("reads") or []) >= 2:
            # 低丰度双峰被 ≥2 条 read 同位点同次等位印证 → 待核（W4 原意：
            # AF 15-23% 带不得静默）
            _blk("minor_af_site", rp, rp, None,
                 _pair + "——多条 read 同位点印证的低丰度次等位，疑似低频亚群",
                 "重新挑单克隆复测",
                 refs=m.get("reads") or [], af=af)
            _mixed_pos.add(rp)
        elif af >= MINOR_AF_MIN or _declared_scope:
            # 单 read 低丰度双峰（2026-11-10）：真实单克隆 17647 在 CDS 内有 5 处
            # AF 0.15-0.18 的噪声双峰，单 read 无法区分噪声与低频亚群——
            # 列出备查，由另一条引物覆盖印证后才进待核
            _info("minor_af_site", None,
                  _pair + "——单 read 低丰度双峰，无法区分噪声与低频亚群，未计入判定",
                  ref_start=rp, ref_end=rp)
        else:
            _info("mixed_variant", None,
                  _pair + "（软检出带且未声明范围，仅记录）")
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
        # 命中→结构断点（段落点坐标可查），未命中→未知序列；均 fail。
        # 例外（2026-11-08 复审 P1-6）：首端硬区（≤front_hard）内的未定位
        # 段降为待核（end_junction_unverified）——≤30bp 连接错误原来在
        # 解析端被豁免成静默 ✓；split-read 定位到断点的仍按结构异常 fail
        for hq in r.get("unaligned_hq_segments") or []:
            sr_ = hq.get("split_read") or {}
            if sr_.get("status") == "located_outside":
                # W8 裁剪窗口路径：段在完整质粒上连续定位（越过窗口边缘
                # 的正常延续）——不算异常，记 info 供峰图核对
                _info("outside_window_segment", None,
                      f"{r.get('filename')} {hq.get('side')}端 "
                      f"{hq.get('length')}bp 高质量序列在完整质粒上定位到 "
                      f"{sr_.get('ref_start')}-{sr_.get('ref_end')}"
                      "（分析窗口外，已定位不算异常；该部分不在需测范围）",
                      ref_start=sr_.get("ref_start"), ref_end=sr_.get("ref_end"))
            elif sr_.get("status") == "breakpoint":
                _blk("structural_breakpoint", sr_.get("ref_start"),
                     sr_.get("ref_end"), None,
                     f"{r.get('filename')} {hq.get('side')}端 "
                     f"{hq.get('length')}bp 高质量序列 split-read 定位到参考 "
                     f"{sr_.get('ref_start')}-{sr_.get('ref_end')}"
                     + ("（跨原点）" if sr_.get("wraps_origin") else "")
                     + f"（身份 {sr_.get('identity')}），连接/结构异常，该部分未验证",
                     "核对连接处/样品身份，必要时换引物或补测对侧",
                     fail_impact=True)
            elif hq.get("zone") == "front_hard":
                _blk("end_junction_unverified", hq.get("adj_ref_start"),
                     hq.get("adj_ref_end"), None,
                     f"{r.get('filename')} 首端硬区内 "
                     f"{hq.get('length')}bp 高质量序列未比对上参考"
                     f"（平均 Q{hq.get('mean_q')}）——连接/结构待核"
                     "（亦可能为首端伪影），该部分未验证",
                     "核对峰图起点与连接处；必要时换引物或补测对侧")
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
    # 范围覆盖分级（goal v3 W2；替代旧 cds_union/whole ROI 分级，RV P0-1
    # 的 whole 豁免随外包框一并取消）：只对声明范围判定覆盖缺口——
    # - R 内实测 0% → roi_uncovered（insufficient：目标没测到）；
    # - R 内实测 <99% → roi_uncovered（partial：测了但没测全；W6 起按 E
    #   归因拆 scope_beyond_primer / scope_read_shortfall）；
    # - 实测齐全而 verified<100% → roi_unverified（pending：不可信区与
    #   N/模糊调用列不构成验证，RV P0-2）。
    # 未声明范围不判覆盖缺口（已测区段口径，§8 决策 1；SC3/SC4）——
    # 头行/Excel 只描述实测区段，未测部分未评估。
    def _cover_bp(ranges: List[Tuple[int, int]]) -> int:
        # W8：按需测并集（含接合区窗口）计——分子分母同口径，重叠不重复计
        return sum(max(0, min(int(e), te) - max(int(s), ts) + 1)
                   for ts, te in req_ranges for s, e in ranges)

    scope_bp = scope_obj.total_bp()
    measured_bp = sum(int(e) - int(s) + 1 for s, e in measured_ranges)
    measured_in_bp = _cover_bp(measured_ranges)
    verified_in_bp = _cover_bp(verified_ranges)
    roi_measured_pct = round(measured_in_bp / scope_bp * 100, 2) if scope_bp else 0.0
    roi_verified_pct = round(verified_in_bp / scope_bp * 100, 2) if scope_bp else 0.0
    roi_measured_bp = measured_in_bp
    full_cds = any(c.get("coverage_status") == "full" for c in cds_reports)
    bp_gate_failed = (not full_cds and measured_bp < MIN_VERDICT_COVERED_BP)
    if declared:
        if measured_in_bp <= 0:
            _blk("scope_uncovered", roi_start, roi_end, None,
                 f"需测范围（{scope_obj.spans_text('、')}）未被任何 read 覆盖，目标未验证",
                 "补测覆盖目标的引物或核对参考选择")
        elif roi_measured_pct < 99:
            # 缺口按 E 归因拆分（goal v3 W6，PL B2）：理论窗够不到 →
            # scope_beyond_primer（加引物）；理论可达而实测未达 →
            # scope_read_shortfall（看峰图/重测）；E 不可得或无 ≥100bp
            # 缺口可归因 → scope_uncovered 兜底整段说法
            _gaps = [g for g in (cov.get("gaps") or [])
                     if g.get("cause") in ("beyond_primer", "read_shortfall")]
            _emitted = False
            for g in _gaps[:3]:
                _kind = ("scope_beyond_primer" if g["cause"] == "beyond_primer"
                         else "scope_read_shortfall")
                _blk(_kind, g["start"], g["end"], None,
                     f"需测范围缺口 {g['start']}–{g['end']}（{g['length']}bp）"
                     + (f"——{g['detail']}" if g.get("detail") else ""),
                     ("在缺口另一侧加一条引物覆盖该区域" if _kind == "scope_beyond_primer"
                      else "查看峰图衰减位置信号，必要时重新测序或换引物"),
                 )
                _emitted = True
            if not _emitted:
                _blk("scope_uncovered", roi_start, roi_end, None,
                     f"需测范围实测覆盖 {roi_measured_pct}%（约 {roi_measured_bp}bp），"
                     "未覆盖部分未验证",
                     "补测缺口区域或核对参考选择")
        elif roi_verified_pct < 100:
            _blk("roi_unverified", roi_start, roi_end, None,
                 f"需测范围验证覆盖 {roi_verified_pct}%"
                 f"（实测 {roi_measured_pct}%）——不可信区与 N/模糊调用列不构成验证",
                 "核对覆盖缺口，必要时补引物")
    if bp_gate_failed:
        # bp 门是「数据量不足」语义（insufficient），与覆盖缺口（roi_unverified，
        # pending 级）分开——2026-11-08 复审：roi_unverified 入 PENDING_KINDS
        # 后，bp 门若沿用该 kind 会被误升 pending，故独立 kind
        _blk("roi_insufficient_bp", None, None, None,
             f"实测仅 {measured_bp}bp，不足以确认是否为目标质粒",
             "补测引物或换参考")
    # 双链覆盖（goal T21；A 4.4）：占比随 roi 下发；「必须双链」策略默认
    # 关闭（单引物测整质粒是常规策略），启用时未满双链记 pending
    dsf = _double_strand_fraction(reads, roi_start, roi_end)
    # 双链要求（goal T21/A 4.4 + W6 SC11）：全局策略开关或 scope 显式要求
    # （require_double_strand）时，单链覆盖的 ROI 未经对侧验证 → 归因阻断
    if ((REQUIRE_DOUBLE_STRAND
         or bool(getattr(scope_obj, "require_double_strand", False)))
            and dsf is not None
            and roi_measured_pct > 0 and dsf < 100):
        _blk("scope_single_strand", roi_start, roi_end, None,
             f"需测范围双链覆盖 {dsf}%（策略：必须双链覆盖）——"
             "单链覆盖区域未经对侧验证",
             "补测对侧链引物，或在设置中关闭双链覆盖强制")

    # 人工确认（goal v3 W14，PL F3）：按位点回填复核决定——伪影移出
    # blocking（info 留痕，不再阻断），突变升级确证（fail_impact，按 fail
    # 口径收口）。留痕条目带 by/at（复决可审计），N 进 headline。
    for _d in review_decisions or []:
        _pos = _d.get("ref_pos")
        if _pos is None:
            continue
        _hit = [b for b in blocking if b.get("ref_start") == _pos]
        for b in _hit:
            b["reviewed"] = {"decision": _d.get("decision"),
                             "by": _d.get("by"), "at": _d.get("at")}
            if _d.get("decision") == "artifact":
                blocking.remove(b)
                info.append({
                    "kind": "reviewed_artifact",
                    "ref_start": b.get("ref_start"),
                    "ref_end": b.get("ref_end"),
                    "element": b.get("element"),
                    "detail": (f"位置 {_pos} 人工确认为测序伪影"
                               f"（{_d.get('by')}，{_d.get('at')}）——"
                               + str(b.get("detail") or "")),
                    "action": "", "refs": b.get("refs") or [],
                })
            elif _d.get("decision") == "mutation":
                b["fail_impact"] = True
                b["detail"] = (str(b.get("detail") or "")
                               + f"（人工确认为真实突变：{_d.get('by')}，{_d.get('at')}）")
    _reviewed_n = len(review_decisions or [])

    has_fail = any(b.get("fail_impact") for b in blocking)
    has_failed_reads = any(b["kind"] == "read_failed" for b in blocking)
    # mixed：mixed_sample 或 ROI 内 mixed_variant（goal T2/T7）
    has_mixed = any(b["kind"] in ("mixed_sample", "mixed_variant") for b in blocking)
    has_pending = any(b["kind"] in PENDING_KINDS for b in blocking)
    # partial/insufficient 只由声明范围的覆盖缺口驱动（goal v3 W2：
    # 未声明范围不判 partial，已测区段口径）；例外（W6/W7）：策略性
    # 阻断——双链要求下的单链区、引物无信号——是「承诺了但没验证到」
    # 的 read_shortfall 语义，不依赖声明范围也判 partial
    _policy_partial = any(b["kind"] in ("scope_single_strand",
                                        "scope_read_shortfall")
                          for b in blocking)
    has_partial = _policy_partial or (
        declared and measured_in_bp > 0 and roi_measured_pct < 99)
    has_insufficient = (any(b["kind"] == "partial_alignment" for b in blocking)
                        or (declared and measured_in_bp <= 0))
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

    # pass 契约（goal v3 W3，RV P0-2）：verified 口径下任何 blocking
    # （roi_unverified、关键元件未验证等 pending 级缺口在内）都不得与
    # pass 并存——上面 status 优先级本应保证这一点，此处真断言兜底，
    # 未来分支回归直接暴露而不是静默发出矛盾结论
    if status == "pass":
        assert not blocking, (
            "pass 契约违反（pass 不得携带 blocking）："
            + ", ".join(sorted({b["kind"] for b in blocking})))

    # element_verdicts（goal v3 W5，PL A5）：CDS/关键元件/接合区各一条，
    # 字段 覆盖（verified %）/单双链/问题数/是否在范围内。范围闸与关键
    # 元件闸同款：声明范围相交才判、未声明范围须完整落入实测；范围外
    # 一律 not_assessed（SC1：AmpR/ori 未评估）。问题数=相交 blocking
    # 数（范围级 roi_* 不算到元件头上）
    element_verdicts: List[Dict] = []
    _el_seen = set()

    def _el_in_scope(s_: int, e_: int) -> bool:
        if declared:
            return any(s_ <= e2 and s1 <= e_ for s1, e2 in scope_ranges)
        return any(s1 <= s_ and e_ <= e2 for s1, e2 in scope_ranges)

    def _push_el(name, typ, s_: int, e_: int) -> None:
        key = (name, s_, e_)
        if key in _el_seen:
            return
        _el_seen.add(key)
        if not _el_in_scope(s_, e_):
            element_verdicts.append({
                "name": name, "type": typ, "start": s_, "end": e_,
                "status": "not_assessed", "coverage": None,
                "double_strand": None, "issues": 0, "in_scope": False})
            return
        el_cov = _ranges_cover(verified_ranges, s_, e_)
        issues = sum(1 for b in blocking
                     if b["kind"] not in ("roi_uncovered", "roi_unverified",
                                          "roi_insufficient_bp")
                     and (b.get("ref_start") is None
                          or (b.get("ref_start") or 10**9) <= e_)
                     and (b.get("ref_end") is None
                          or (b.get("ref_end") or -1) >= s_))
        el_dsf = _double_strand_fraction(reads, s_, e_) if el_cov > 0 else None
        element_verdicts.append({
            "name": name, "type": typ, "start": s_, "end": e_,
            "status": "ok" if (el_cov >= 99 and issues == 0) else "issue",
            "coverage": round(el_cov, 1), "double_strand": el_dsf,
            "issues": issues, "in_scope": True})

    for f in features:
        if f.get("type") == "CDS":
            _push_el(f.get("name") or "(未命名 CDS)", "CDS",
                     int(f["start"]), int(f["end"]))
    for f in critical:
        _push_el(f.get("name") or "(未命名元件)", f.get("type") or "element",
                 int(f["start"]), int(f["end"]))
    for s_, e_, jname in junctions:
        _push_el(jname or "接合区", "junction", int(s_), int(e_))

    # headline（goal v3 W5，PL A6）：三段式「符号 状态 · 范围 · 证据」，
    # W12 起作为三出口唯一文案来源（本阶段随 verdict 下发，出口尚各自措辞）。
    # 范围段：声明范围用「元件名（起–止）」（元件数与段数对应时）或区间；
    # 未声明范围只说「已测 x–y…；范围外未评估」（goal §0.1 禁整参考主张）
    sym_, word_ = status_symbol(status), STATUS_HEAD_WORDS[status]
    range_part = _range_text(scope_obj, scope_ranges, ref_length,
                             verified_ranges)
    if status == "pass":
        n_syn = len([i for i in info if i.get("kind") == "synonymous_variant"])
        ev_txt = (f"verified 覆盖 {roi_verified_pct:.0f}%，"
                  + (f"{n_syn} 处同义替换（蛋白不变）" if n_syn else "未检出差异"))
    elif status == "failed_reads":
        ev_txt = f"{len(errors)} 条 read 解析失败"
    elif status == "insufficient":
        ev_txt = "实测数据不足以判定"
    elif has_fail:
        # X3（RB P1-1、goal §3.3）：证据计数**按位点去重**——同一处变异
        # 可能同时进 confirmed_variant 与 junction_variant（接合区插入），
        # 甚至再加一条 CDS 蛋白不一致条目；按位点合计只算 1 处
        ev_txt = f"{_fail_site_count(blocking)} 处确证差异"
        # 蛋白层面的后果是用户第一关心的信息（此前只在 Excel 明细里），
        # 现在并入证据段——头行仍是唯一来源，出口不得重复判断
        bad_cds = [c for c in cds_reports
                   if c.get("coverage_status") != "uncovered"
                   and c.get("protein_identical") is False]
        if bad_cds:
            names_ = "、".join(str(c.get("name")) for c in bad_cds[:2]) \
                + (" 等" if len(bad_cds) > 2 else "")
            ev_txt += (f"（{names_}蛋白与设计不一致"
                       + ("，移码" if any(c.get("frameshift_count")
                                          for c in bad_cds) else "")
                       + "）")
    elif status == "mixed":
        # 两种判混形态（goal T2/T7）：read 级 widespread（mixed_sample）与
        # 位点级次等位（mixed_variant）。证据段按实际命中的形态说，避免出现
        # 「双峰判混（0 处）」这种自相矛盾的计数（X3）
        _n_site = len([b for b in blocking if b["kind"] == "mixed_variant"])
        _samp = [b for b in blocking if b["kind"] == "mixed_sample"]
        if _samp:
            ev_txt = (_samp[0].get("detail") or "").split("（")[0]                 or f"{len(_samp)} 条 read 检出双峰"
        else:
            ev_txt = f"双峰判混（{_n_site} 处）"
    else:
        ev_txt = f"{len(blocking)} 项待核"
    headline = f"{sym_} {word_} · {range_part} · 证据：{ev_txt}"
    if _reviewed_n:
        headline += f"，人工确认 {_reviewed_n} 处"
    # 沿用上次范围（goal v3.1 X5）：范围是记忆带出的，不是这次声明的——
    # 必须在头行标注，否则用户会以为这次真的检查了这个范围
    if getattr(scope_obj, "remembered", False):
        headline += "（沿用上次范围）"
    # X3：Excel 结论列 = 去掉符号后的 headline（RB P0-3：三出口逐字同源）。
    # 明细列（缺口原因/范围外差异/补测建议）由 _scope_cells 从结构化字段填
    excel_txt = headline.lstrip("✓✗△ ").strip()

    # 范围字典 + 参考指纹（X5）：前端「清除记忆」按指纹调 DELETE 端点，
    # 不必回传整条参考序列（sha256 是序列的纯函数，不影响 build_verdict 的纯函数性）
    _sc_out = scope_obj.to_dict() if scope_obj is not None else None
    if _sc_out is not None and result.get("reference"):
        _sc_out["reference_hash"] = hashlib.sha256(
            str(result["reference"]).upper().encode("utf-8")).hexdigest()
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
        # 需测范围（goal v3 §3.3；None=未声明）。X5 起附 reference_hash：
        # 前端「清除记忆」按它调 DELETE，不必回传整条参考序列
        "scope": _sc_out,
        # goal v3 W5（PL A5/A6）：元件级判定与三段式头行
        "element_verdicts": element_verdicts,
        "headline": headline,
        # X3：Excel 一句话结论（= headline 去符号，出口不得自行拼接）
        "excel_conclusion": excel_txt,
        # goal v3 W14：人工确认计数（review_decisions 留痕在 payload，重算
        # 时随参数传入——同一份 result+decisions 重算可复现）
        "reviewed_count": _reviewed_n,
        "algo_version": result.get("algo_version"),
    }
