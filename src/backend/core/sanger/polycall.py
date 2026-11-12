"""S4 PolyCall：结构级 poly 的整段判定（重构一期，等价迁移）

契约：sitecall_contract v1.0「PolyCall」。

- `aggregate_run(run, run_reads)`（纯函数）：逐 read 峰图计数 → 跨 read /
  跨链综合评判（Step B），返回要并入 homopolymer 条目的字段。代码从
  pipeline._homopolymer_report_pass 原样迁出，口径不变。
- `poly_call(entry)` / `not_covered(run)`：条目 → PolyCall（6 个状态）。
- `build_poly_calls(poly_runs, homopolymer_report)`：按 poly_runs 顺序给出
  全部 PolyCall，未被任何 read 覆盖的 poly 记 NOT_COVERED（旧清单省略）。

「大量缺失」不在这里判，由 S6 按请求阈值判。本模块不生成用户文案。
部分覆盖（覆盖不到整段）不单独成状态：保留计数状态，reasons 带
PARTIAL_COVERAGE，covered_ranges 给出已覆盖段。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from core.sanger.homopolymer import _ANCHOR_RANK

POLYCALL_VERSION = "p4-1"

COUNT_CONFIRMED = "COUNT_CONFIRMED"
DEFICIT_SUSPECTED = "DEFICIT_SUSPECTED"
CONFLICTING = "CONFLICTING"
UNDETERMINABLE = "UNDETERMINABLE"
CALLER_ONLY = "CALLER_ONLY"
NOT_COVERED = "NOT_COVERED"
STATES = (COUNT_CONFIRMED, DEFICIT_SUSPECTED, CONFLICTING, UNDETERMINABLE,
          CALLER_ONLY, NOT_COVERED)

_FROM_LEGACY = {"accepted": COUNT_CONFIRMED, "deficit_observed": DEFICIT_SUSPECTED,
                "contradictory": CONFLICTING, "undetermined": UNDETERMINABLE,
                "caller_only": CALLER_ONLY}
LEGACY_VERDICT = {v: k for k, v in _FROM_LEGACY.items()}

# 计数不可信、要作为未决位点阻断的状态（与旧 evidence 口径一致：
# contradictory / undetermined / deficit_observed）
UNRESOLVED_STATES = frozenset({CONFLICTING, UNDETERMINABLE, DEFICIT_SUSPECTED})


def aggregate_run(run: Dict, run_reads: List[Dict]) -> Dict:
    """整段重复数的跨 read 综合评判（纯函数，不改 run / run_reads）"""
    out: Dict = {}
    # 整段重复数的峰图估计只聚合完整覆盖 read：部分覆盖 read 的峰数
    # 只对应覆盖段，混入会把"段内计数"误当"整段计数"；合并 read 的
    # peak_count=0 是"无可分辨峰"而非真实计数，混进中位数会把极端
    # [30,0,0] 压成 0——只取 >0 的可分辨计数（全合并则无证据 → None）
    full_pcs = [x["peak_count"] for x in run_reads
                if x["coverage"] == "full" and x["peak_count"]]
    out["peak_count_estimate"] = (sorted(full_pcs)[len(full_pcs) // 2]
                                    if full_pcs else None)
    est_pool = [x for x in run_reads if x["length_estimate"] is not None]
    use = [x for x in est_pool if x["coverage"] == "full"] or est_pool
    ests = [x["length_estimate"] for x in use]
    cis = [x["length_ci"] for x in use if x["length_ci"] is not None]
    methods = [x["length_method"] for x in use if x["length_method"]]
    out["length_estimate"] = sorted(ests)[len(ests) // 2] if ests else None
    # 众数平票时取 read 顺序中先出现者：旧写法 max(set(...)) 的平票结果依赖
    # 字符串哈希随机化，同一输入每次运行可能在 width/peaks 间跳变
    out["length_method"] = max(methods, key=methods.count) if methods else None
    out["length_ci"] = ([min(c[0] for c in cis), max(c[1] for c in cis)]
                          if cis else None)
    # Step B：跨 read / 跨链综合评判（后综合评判）
    # 只有 resolvable read（峰数==调用数）拥有完整的可见计数证据，才有
    # 投票权；deficit（可见缺失）与 merged（峰合并不可分辨）的 read 不
    # 投票。旧口径"任一 read 峰数≠调用数即不可信"会把真实难例（正向
    # 可分辨且互证一致 + 反向合并成压缩山丘）整体否决——反向合并是该
    # read 的信号极限，不是对正向证据的反驳
    votes = [x for x in run_reads
             if x.get("resolution") == "resolvable"
             and (x["coverage"] == "full"
                  or (x.get("covered_span") and x["called_count"] is not None
                      and x["peak_count"] == x["called_count"]
                      and x["covered_span"][0] <= run["start"]
                      and x["covered_span"][1] >= run["end"]))]
    deficit = [x for x in run_reads
               if x.get("resolution") == "deficit"
               and x["coverage"] == "full"]
    merged_any = [x for x in run_reads
                  if x.get("resolution") == "merged"]
    _vote_rec = lambda x: {"filename": x["filename"],
                           "direction": x["direction"],
                           "peak_count": x["peak_count"],
                           "anchor": x.get("anchor_grade")}
    # 联合覆盖投票（Step B 扩展）：无完整覆盖 read 时，多条部分覆盖且
    # 段内自洽（resolvable）的 read 拼满整段 run 也可联合确证——
    # 段间须恰好衔接（重叠段峰计数无法按位分解，保守跳过）、并集盖满
    # run 起止，联合观测数 = 各段调用数之和（各段 pc==called）
    joint_votes: List[Dict] = []
    joint_total: Optional[int] = None
    _partials = sorted(
        (x for x in run_reads
         if x.get("resolution") == "resolvable" and x["coverage"] == "partial"
         and x.get("covered_span") and x.get("called_count") is not None),
        key=lambda x: x["covered_span"][0])
    _cursor: Optional[int] = None
    for x in _partials:
        cs, ce = x["covered_span"]
        if _cursor is None:
            if cs != run["start"]:
                break                      # 首段未从 run 起点开始 → 有缺口
            _cursor = ce
            joint_total = int(x["called_count"])
            joint_votes.append(x)
        elif cs <= _cursor:
            continue                       # 与已覆盖段重叠：冗余，跳过
        elif cs == _cursor + 1:
            _cursor = ce                   # 恰好衔接，继续拼接
            joint_total += int(x["called_count"])
            joint_votes.append(x)
        else:
            break                          # 段间缺口
    joint_ok = bool(joint_votes) and _cursor == run["end"]
    # 投票 read 的锚定分级最差者（accepted 只确证「重复数」，不确证
    # 「位置」——路标踩在信号异常区时 run 起止定位仍可能偏移，结论
    # 需显式写明，见 _poly_anchor_notes）
    if votes or joint_votes:
        out["vote_anchor_grade"] = max(
            (x.get("anchor_grade") or "reliable" for x in (votes or joint_votes)),
            key=lambda g: _ANCHOR_RANK.get(g, 1))
    if votes:
        pcs = sorted({x["peak_count"] for x in votes})
        if len(pcs) == 1 and (not joint_ok or joint_total == pcs[0]):
            # 可分辨 read 互证一致（含单条）：重复数采信峰图实测；
            # 联合覆盖读数与完整覆盖一致时互为印证
            out["run_verdict"] = "accepted"
            out["count_reliable"] = True
            out["observed_repeat_count"] = pcs[0]
            out["verdict_votes"] = [_vote_rec(x) for x in votes]
        elif len(pcs) == 1 and joint_ok and joint_total != pcs[0]:
            # 完整覆盖 read 与联合覆盖读数冲突（两段拼接各自自洽但互相
            # 矛盾——两克隆混合的典型形态）：不可信
            out["run_verdict"] = "contradictory"
            out["count_reliable"] = False
            out["verdict_votes"] = [_vote_rec(x) for x in votes]
            out["verdict_votes"].append({
                "joint": True, "observed_total": joint_total,
                "reads": [_vote_rec(x) for x in joint_votes]})
        else:
            # 可分辨 read 之间互证矛盾：不可信，需人工核对或换引物
            out["run_verdict"] = "contradictory"
            out["count_reliable"] = False
            out["verdict_votes"] = [_vote_rec(x) for x in votes]
    elif joint_ok:
        # 联合覆盖确证：各段内部自洽且拼满整段——等价于一条完整覆盖
        # read 的证据（各 read 独立信号，段间无重叠无缺口）
        out["run_verdict"] = "accepted"
        out["count_reliable"] = True
        out["observed_repeat_count"] = joint_total
        out["joint_coverage"] = True
        out["verdict_votes"] = [_vote_rec(x) for x in joint_votes]
    elif deficit:
        # 无完整互证，但有可见缺失证据（B4 口径）：主判读以峰图为准，
        # 计数不确证（部分合并可能掩盖更多缺失）
        out["run_verdict"] = "deficit_observed"
        out["count_reliable"] = False
    elif merged_any:
        # 整段合并、无可分辨证据：不可判定。
        # 宽度法估计照常输出但仅为模型参考，不作为判定
        out["run_verdict"] = "undetermined"
        out["count_reliable"] = False
    else:
        # 无任何峰图计数证据（采样不足/仅部分覆盖且一致）：无矛盾证据，
        # 维持调用数（旧口径兼容，spare 合成稀疏数据场景）
        out["run_verdict"] = "caller_only"
        out["count_reliable"] = True
    # 主判读以峰图为准（B4）：每条 read 覆盖段内，参考段长 − 可分辨峰 =
    # 该段缺失数（峰只会被压缩合并、不会凭空多出，段内峰数即实测）；
    # 整段缺失取各 read 覆盖段缺失的最大值（缺失只能被覆盖它的 read 看见），
    # 实测 = 参考 − 缺失 + 插入。峰数法整体失效（None/0）时不编造实测数，
    # 交宽度法口径与人工判读
    usable = [x for x in run_reads if x["peak_count"] and x.get("covered_span")]
    if usable:
        seg_len = lambda x: x["covered_span"][1] - x["covered_span"][0] + 1
        out["peak_missing"] = max(0, max(seg_len(x) - x["peak_count"]
                                           for x in usable))
        out["peak_inserted"] = max(0, max(x["peak_count"] - seg_len(x)
                                            for x in usable))
        out["peak_measured"] = (run["repeat_count"] - out["peak_missing"]
                                  + out["peak_inserted"])
    cov_pos: set = set()
    for x in run_reads:
        if x.get("covered_span"):
            cov_pos.update(range(x["covered_span"][0], x["covered_span"][1] + 1))
    out["run_covered"] = len(cov_pos)
    return out


def _merge_spans(spans: List[List[int]]) -> List[List[int]]:
    merged: List[List[int]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def _poly_id(run: Dict) -> str:
    return f"{run.get('unit') or run.get('base')}@{run.get('start')}-{run.get('end')}"


def poly_call(entry: Dict) -> Dict:
    """homopolymer 条目 → PolyCall"""
    state = _FROM_LEGACY.get(entry.get("run_verdict"), CALLER_ONLY)
    ref_n = entry.get("ref_repeat_count")
    measured = (entry.get("observed_repeat_count") if state == COUNT_CONFIRMED
                else entry.get("peak_measured"))
    reads = entry.get("read_counts") or []
    covered = _merge_spans([list(x["covered_span"]) for x in reads
                            if x.get("covered_span")])
    reasons: List[str] = []
    if (entry.get("run_covered") is not None
            and entry["run_covered"] < (entry.get("length") or 0)):
        reasons.append("PARTIAL_COVERAGE")
    if entry.get("joint_coverage"):
        reasons.append("JOINT_COVERAGE")
    g = entry.get("vote_anchor_grade")
    if state == COUNT_CONFIRMED and g in ("marginal", "unreliable"):
        reasons.append("ANCHOR_" + g.upper())
    if entry.get("period", 1) > 1:
        reasons.append("STRUCTURE_ONLY")
    return {
        "poly_id": _poly_id(entry),
        "state": state,
        "tier": entry.get("tier"),
        "base": entry.get("base"), "unit": entry.get("unit"),
        "period": entry.get("period"),
        "ref_start": entry.get("start"), "ref_end": entry.get("end"),
        "ref_len": ref_n,
        "measured": measured,
        "delta": (measured - ref_n) if (measured is not None and ref_n is not None)
        else None,
        "missing_max": entry.get("peak_missing"),
        "width_est": entry.get("length_estimate"),
        "width_ci": entry.get("length_ci"),
        "per_read": [{k: x.get(k) for k in (
            "filename", "direction", "coverage", "covered_span",
            "called_count", "peak_count", "resolution")} for x in reads],
        "covered_ranges": covered,
        "reasons": reasons,
        "stage_version": POLYCALL_VERSION,
    }


def not_covered(run: Dict) -> Dict:
    return {
        "poly_id": _poly_id(run), "state": NOT_COVERED, "tier": run.get("tier"),
        "base": run.get("base"), "unit": run.get("unit"),
        "period": run.get("period"),
        "ref_start": run.get("start"), "ref_end": run.get("end"),
        "ref_len": run.get("repeat_count"), "measured": None, "delta": None,
        "missing_max": None, "width_est": None, "width_ci": None,
        "per_read": [], "covered_ranges": [], "reasons": [],
        "stage_version": POLYCALL_VERSION,
    }


def build_poly_calls(poly_runs: List[Dict],
                     homopolymer_report: List[Dict]) -> List[Dict]:
    by_key = {(e.get("unit"), e.get("start"), e.get("end")): e
              for e in homopolymer_report or []}
    out = []
    for run in poly_runs or []:
        e = by_key.get((run.get("unit"), run.get("start"), run.get("end")))
        out.append(poly_call(e) if e is not None else not_covered(run))
    return out


def unresolved_polys(poly_calls: Optional[List[Dict]]) -> List[Dict]:
    """计数不可信、需作未决位点的 poly（只看 poly 级 tier）"""
    return [pc for pc in poly_calls or []
            if pc.get("tier") == "poly" and pc["state"] in UNRESOLVED_STATES]
