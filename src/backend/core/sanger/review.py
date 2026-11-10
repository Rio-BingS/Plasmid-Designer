"""复核队列与人工确认（goal v3 W14；PL §F）

复核队列最小化（goal §0.7）：只收「人工不看就无法收口」且在声明范围内的
条目（PL F1）——确证差异（fail_impact）、范围内覆盖缺口（read_shortfall
与未知原因缺口）、未决位点（PENDING_KINDS）。`scope_beyond_primer` 不入队
（引物够不到的缺口动作明确：加引物——blocking 条目自身带 action 建议）；
混合样品另有逐 read 混合详情展示，读取失败单独成段，均不进队列。

W8 起 blocking 本身就只含声明范围内的问题（范围外差异走 out_of_scope
info，不判不阻断），队列从 blocking 派生即天然「范围内」，无需再过滤。

一键确认（PL F3）：对队列项做「伪影 / 突变」定夺，写入 review_decisions
（ref_pos/decision/by/at/note）留痕；build_verdict 接收 decisions 重算——
伪影条目移出 blocking（info 留痕），突变条目升级确证（fail_impact），
headline 追加「人工确认 N 处」。已确认条目不再进队列。

goal v3.1 X2（RB P0-2）：真实子集上未声明范围时队列曾达 330 项/30 行结论，
用户无法逐条看。改为三级聚合：

1. **read 级**——read 已判 mixed、整体低质量（grade 非 pass），或峰形退化
   区段占其范围 ≥30% 时，只生成一项「read 问题」，该 read 上的所有位点项
   折叠进来（`level="read"`、`sites=N`）；
2. **区段级**——其余同类且相邻 ≤ `SEGMENT_MERGE_BP`(20bp) 的位点合并为
   一项，显示区间、位点数、最大 AF 与最低 Q（`level="segment"`）；
3. **位点级**——只保留确证差异与范围内孤立未决位点（`level="site"`）。

未声明范围时不收 `unresolved_site` / `minor_af_site` 的位点项（只计数，
汇总进 `queue_summary`）；`scope_beyond_primer` 仍不入队。排序：
confirmed > junction > read 问题 > 区段 > 位点。

`minor_af_site` **任何范围下都只计数不入队**（goal v3.1 §3.2 第 3 条：位点级
只保留确证差异与「孤立未决位点」）。它是「次等位丰度不足、判不了混」的低
丰度判据结论，不是「读不清」的未决位点——真实子集上这一类有 118 处，按位点
入队会把队列从 14 项撑到 30 项。完整清单仍在 `verdict.blocking` 与明细表。
"""

from typing import Dict, List, Optional

from core.sanger.verdict import PENDING_KINDS

# prio 顺序（PL F4）：确证差异 > 覆盖缺口 > 未决位点
_PRIO_ORDER = ("fail", "read_shortfall", "pending")

# 区段级聚合：同类条目相邻 ≤ 该值即合并为一项（goal v3.1 §3.2）
SEGMENT_MERGE_BP = 20

# read 级折叠阈值：退化区段占 read 范围 ≥ 该比例时整条 read 折叠为一项
# （与 pipeline.DEGRADED_READ_SHARE 同值同义）
READ_FOLD_SHARE = 0.30

# read 级折叠时收哪些位点类 kind（确证差异不折叠——那是用户必须先看到的）
_READ_FOLD_KINDS = {"unresolved_variant", "unresolved_site", "minor_af_site",
                    "n_in_cds", "poly_unreliable", "dropout",
                    "overlap_discordance", "orf_annotation_suspect",
                    # 双峰位点：read 已判 mixed 时它们折叠到 read 项下
                    # （未折叠时每条都是一处「待核对双峰」，正是复核量爆炸）
                    "mixed_variant"}

# 未声明范围时不入队的位点类 kind（只计数）
_UNDECLARED_SITE_KINDS = {"unresolved_site", "minor_af_site"}

# 任何范围下都只计数不入队的位点类 kind（goal v3.1 §3.2 第 3 条：位点级
# 只收「确切差异 + 孤立未决位点」；minor_af_site 是丰度判据结论，不是未决）
_COUNT_ONLY_KINDS = {"minor_af_site"}

# 排序级别（goal v3.1 §3.2）：confirmed > junction > read 问题 > 区段 > 位点
_LEVEL_ORDER = ("confirmed", "junction", "read", "segment", "site")

# prio → 收哪些 blocking kind
_PRIO_KINDS = {
    "fail": None,  # None = 按 fail_impact 标记收（确证差异，kind 不限）
    "read_shortfall": {"scope_read_shortfall", "scope_uncovered"},
    "pending": PENDING_KINDS,
}


def _prio_of(b: Dict) -> Optional[str]:
    if b.get("fail_impact"):
        return "fail"
    for p in _PRIO_ORDER[1:]:
        if b.get("kind") in _PRIO_KINDS[p]:
            return p
    return None


def _level_of(b: Dict) -> str:
    """条目的展示级别（goal v3.1 §3.2 排序用）"""
    kind = b.get("kind")
    if kind == "junction_variant":
        return "junction"
    if b.get("fail_impact") or kind in ("critselement_variant",
                                       "critical_element_variant"):
        return "confirmed"
    return "site"


def _read_index(res: Dict) -> Dict[str, Dict]:
    """read 文件名 → read 结果（含 degraded_ranges/grade/混合分级）"""
    out: Dict[str, Dict] = {}
    profiles = res.get("mixed_profiles") or {}
    for r in res.get("reads") or []:
        fn = r.get("filename")
        if not fn:
            continue
        rec = dict(r)
        rec["_profile"] = profiles.get(fn) or {}
        out[fn] = rec
    return out


def _read_fold_reason(rec: Dict) -> Optional[str]:
    """该 read 是否整体折叠为「read 问题」，返回原因短语（否则 None）。

    三条触发条件（goal v3.1 §3.2）：
    - read 已判 mixed（mixed_profile.class ∈ widespread/local——这两种是
      「该 read 有真实的双峰问题」，位点级逐条核对没有增量信息；
      noise/scattered 是信号噪声与零散伪影，Q 比判别器已挡下降级，
      位点本身仍需逐个核对，不折叠）；
    - 整体低质量（grade 非 pass——grade 由 _score_grade 给出，read 级）；
    - 峰形退化区段占该 read 范围 ≥ READ_FOLD_SHARE。
    """
    prof = rec.get("_profile") or {}
    cls = prof.get("class")
    if cls in ("widespread", "local"):
        label = "疑似混合样品" if cls == "widespread" else "双峰集中"
        return f"{label}（{prof.get('count') or 0} 处双峰，{cls} 分级）"
    if rec.get("mixed_sample"):
        return "疑似混合样品"
    grade = str(rec.get("grade") or "")
    if grade and grade not in ("pass", "good", "ok", "excellent"):
        # 低质量 read：位点判读整体不可靠，逐条核对无意义
        if grade in ("fail", "poor", "bad", "unreliable"):
            return f"read 质量 {grade}（整体低质量）"
    runs = rec.get("degraded_ranges") or []
    total = len(rec.get("trimmed_bases") or "")
    if runs and total > 0:
        covered = set()
        for a, b in runs:
            covered.update(range(max(1, a), min(total, b) + 1))
        if len(covered) / total >= READ_FOLD_SHARE:
            first = min(a for a, _b in runs)
            return f"自 {first} 起峰形退化（{len(covered)}/{total}bp）"
    return None


def _merge_segments(items: List[Dict]) -> List[Dict]:
    """同类条目相邻 ≤ SEGMENT_MERGE_BP 合并为一项（保持输入顺序的区间序）"""
    if not items:
        return []
    items = sorted(items, key=lambda q: (q.get("kind") or "",
                                         q.get("ref_start") or 0))
    out: List[Dict] = []
    for it in items:
        rs = it.get("ref_start")
        if out and rs is not None and out[-1].get("ref_end") is not None \
                and out[-1]["kind"] == it["kind"] \
                and rs <= out[-1]["ref_end"] + SEGMENT_MERGE_BP:
            cur = out[-1]
            cur["ref_end"] = max(cur["ref_end"], it.get("ref_end") or rs)
            cur["sites"] = (cur.get("sites") or 1) + 1
            cur["_members"].append(it)
            if it.get("evidence", {}).get("af") is not None:
                cur["evidence"]["max_af"] = max(
                    cur["evidence"].get("max_af") or 0,
                    it["evidence"]["af"])
            if it.get("_q") is not None:
                cur["evidence"]["min_q"] = min(
                    cur["evidence"].get("min_q") or 99, it["_q"])
            continue
        it["_members"] = [it]
        out.append(it)
    return out


def build_queue(res: Dict, review_decisions: Optional[List[Dict]] = None
                ) -> List[Dict]:
    """从 analyze 结果生成复核队列（纯函数，只读）。

    goal v3.1 X2 三级聚合后的返回项：
    `{level, prio, kind, ref_start, ref_end, element, detail, action, evidence,
    sites?}`——`level` ∈ read|segment|site|confirmed|junction，`sites` 为聚合
    的位点数（read/segment 级 >1 时给出）。

    evidence 为该条目的核对素材（缺口带 coverage.gaps 的 E 归因；双峰带
    af/max_af；位点带 refs——±10bp 峰图由前端证据面板按 ref_start 拉取）。

    review_decisions（PL F3）：已人工确认的位点不再进队列（伪影条目
    已被 build_verdict 移出 blocking，自然不出现；突变条目在此剔除）。
    """
    done = {d.get("ref_pos") for d in review_decisions or []}
    vd = res.get("verdict") or {}
    scope = vd.get("scope")
    # 只有「确有 scope 对象且声明为未声明」才走「位点只计数不入队」；
    # scope 缺失（旧记录 / 最小构造）按已声明处理——缺信息不等于未声明，
    # 否则未决位点会静默从队列消失
    undeclared = bool(scope) and not scope.get("declared")
    gaps = {g.get("start"): g for g in
            (res.get("coverage") or {}).get("gaps") or []}
    reads = _read_index(res)
    # read 级折叠（用 mixed_positions 反查 read，blocking 条目只带 refs）
    fold_at: Dict[str, Dict] = {}
    for fn, rec in reads.items():
        why = _read_fold_reason(rec)
        if why:
            fold_at[fn] = {"reason": why, "rec": rec}

    confirmed: List[Dict] = []
    site_like: List[Dict] = []
    folded: Dict[str, Dict] = {}
    skipped_sites = 0
    for b in vd.get("blocking") or []:
        prio = _prio_of(b)
        if prio is None:
            continue
        if b.get("ref_start") in done:
            continue
        kind = b.get("kind")
        # read 级折叠先行：这条 read 本身已判 mixed / 低质量 / 大面积退化时，
        # 它的位点无论范围是否声明都收进那一项——read 问题是一处，不是 N 处
        # （否则未声明的双峰 read 会在「位点只计数」里整条消失，见 RQ3）
        _hit = next((fn for fn in (b.get("refs") or []) if fn in fold_at), None)
        _folded_here = bool(_hit) and kind in _READ_FOLD_KINDS
        # 只计数类（minor_af_site）与未声明范围位点：不入队，只在 summary 报数
        if kind in _COUNT_ONLY_KINDS:
            skipped_sites += 1
            continue
        if undeclared and kind in _UNDECLARED_SITE_KINDS and not _folded_here:
            skipped_sites += 1
            continue
        ev: Dict = {"refs": b.get("refs") or []}
        rs = b.get("ref_start")
        if kind in _PRIO_KINDS["read_shortfall"] and rs in gaps:
            g = gaps[rs]
            ev["gap"] = {"start": g.get("start"), "end": g.get("end"),
                         "length": g.get("length"), "cause": g.get("cause"),
                         "detail": g.get("detail")}
            ev["expected_ranges"] = (res.get("coverage") or {}).get(
                "expected_ranges") or []
        if kind == "minor_af_site":
            ev["af"] = b.get("af")
        item = {
            "prio": prio, "kind": kind,
            "ref_start": rs, "ref_end": b.get("ref_end"),
            "element": b.get("element"),
            "detail": b.get("detail"), "action": b.get("action"),
            "evidence": ev,
            "_q": b.get("q"),
        }
        level = _level_of(b)
        item["level"] = level
        if level in ("confirmed", "junction"):
            confirmed.append(item)
            continue
        # read 级折叠：位点类条目若属于某条已折叠的 read，收进那一项
        if _folded_here:
            slot = folded.setdefault(_hit, {"members": [], "prio": prio,
                                            "why": fold_at[_hit]["reason"],
                                            "rec": fold_at[_hit]["rec"]})
            slot["members"].append(item)
            continue
        site_like.append(item)

    queue: List[Dict] = list(confirmed)
    # read 问题项（每条折叠 read 一项，位点全部收在下面）
    for fn, slot in folded.items():
        rec = slot["rec"]
        zone = rec.get("reliable_zone") or {}
        n = len(slot["members"])
        queue.append({
            "level": "read", "prio": slot["prio"] or "pending",
            "kind": "read_problem", "sites": n,
            "ref_start": None, "ref_end": None, "element": fn,
            "detail": f"{fn}：{slot['why']}"
                      + (f"，{n} 处位点已折叠（read {zone.get('start')}-"
                         f"{zone.get('end')}）" if n else ""),
            "action": "核对整条 read 峰图；必要时重新测序或换引物",
            "evidence": {"read": fn, "reason": slot["why"],
                         "positions": [m.get("ref_start")
                                       for m in slot["members"]],
                         "zone": {"start": zone.get("start"),
                                  "end": zone.get("end")}},
            "_members": slot["members"],
        })
    # 区段级聚合 + 位点级
    merged = _merge_segments(site_like)
    for it in merged:
        n = it.get("sites") or 1
        if n > 1:
            it["level"] = "segment"
            it["detail"] = (f"{it.get('ref_start')}–{it.get('ref_end')} "
                            f"{n} 处同类位点（{it.get('kind')}）")
            if it["evidence"].get("max_af") is not None:
                it["detail"] += f"，最大 AF {round(it['evidence']['max_af'] * 100)}%"
        else:
            it["level"] = "site"
        it.pop("_q", None)
        it.pop("_members", None)
        queue.append(it)
    # 排序：level 优先（confirmed > junction > read > segment > site），
    # 组内按 prio（fail > read_shortfall > pending）、再按位点（read 项无
    # 位点，按文件名稳定排序）——缺口比孤立未决位点更该先看
    queue.sort(key=lambda q: (
        _LEVEL_ORDER.index(q.get("level") or "site"),
        _PRIO_ORDER.index(q.get("prio") or "pending"),
        q.get("ref_start") if q.get("ref_start") is not None else 0,
        q.get("element") or ""))
    for q in queue:
        q.pop("_q", None)
        q.pop("_members", None)
    # 未声明范围只计数：条数随队列一并下发（build_queue_with_summary），
    # build_queue 本身保持纯函数不改 res
    build_queue.last_skipped = skipped_sites
    return queue


def queue_for_record(record: Dict) -> List[Dict]:
    """记录的复核队列：新记录随存档带出 review_queue；旧记录（W14 前落库、
    无该键）从 verdict 现算一次（只读，不改存档）——展示层补齐入口。"""
    rq = record.get("review_queue")
    if rq is not None:
        return rq
    if not record.get("verdict"):
        return []
    return build_queue(record)


def build_queue_for_record(record: Dict) -> List[Dict]:
    """按记录当前 verdict/review_decisions 构建并应写回的队列
    （新分析登记与重算/确认端点共用；调用方负责赋值回 record）。"""
    return build_queue(record, record.get("review_decisions") or None)


def apply_review_decision(decisions: List[Dict], ref_pos: int, decision: str,
                          by: str, note: str = "") -> Dict:
    """追加一条人工确认记录并返回它（PL F3：reviewed_by/at 留痕）。

    decision ∈ {"artifact", "mutation"}；调用方负责校验并持久化。"""
    from datetime import datetime
    item = {
        "ref_pos": int(ref_pos),
        "decision": decision,
        "by": by,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    if note:
        item["note"] = note
    decisions.append(item)
    return item
