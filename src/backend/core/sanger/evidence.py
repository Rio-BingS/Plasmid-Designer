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

from core.sanger.abif_reader import peak_window as _peak_window
from core.sanger.artifacts import artifact_at as _artifact_at
from core.sanger.homopolymer import POLY_COMPRESSION_RATIO, _read_ref_maps
from core.sanger.noise import peak_snr as _noise_peak_snr
# W12：头行符号映射（verdict.py 顶部反向 import 本模块——循环风险在
# verdict 侧已按惰性导入处理，此处为正向依赖，安全）
from core.sanger.verdict import status_symbol
from core.sanger.reliable_zone import core_zone, in_zone, zone_class, zone_reason


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


def _vstatus_of(v: Dict) -> str:
    """三态（读显式 status——T21 分歧降级等后处理写回的字段；旧记录无
    status 时按置信推导，与 verdict._vstatus 同口径）"""
    if v.get("status"):
        return v["status"]
    return _variant_status(v)


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
    if v.get("in_mixed_template"):
        codes.append("in_mixed_template")
    if v.get("type") in ("substitution", "insertion") and any(
            c not in "ACGT-" for c in alt_up):
        codes.append("n_call")
    if zone and rp and support < 2 and not in_zone(core_zone(zone), rp):
        z = zone.get("params") or {}
        if rp <= (z.get("front_gray") or 60):
            codes.append("front_hard_zone" if rp <= (z.get("front_hard") or 30)
                         else "front_gray_zone")
        else:
            codes.append("tail_ext_zone" if zone_class(zone, rp) == "tail_ext"
                         else "tail_zone")
    # 双证尾端等「跨过 zone 规则」的确证变体（goal T3；B P1-7）：支持证据
    # 全部来自可信区外时明示——判定成立但不属于可信区内的证据
    if (v.get("confidence") in ("medium", "high") and zone and rp
            and not in_zone(core_zone(zone), rp)):
        codes.append("all_evidence_out_of_zone")
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
        elif snr is None and "snr" in evidence:
            # T13：替换证据里 snr 键存在但为 None（局部噪声样本不可估）
            # = 不确定而非响亮；插入证据不带 snr 键，不误报
            codes.append("snr_unavailable")
    hp = v.get("homopolymer")
    if (hp and v.get("type") in ("insertion", "deletion")
            and hp.get("peak_amplitude_ratio") is not None
            and hp["peak_amplitude_ratio"] < POLY_COMPRESSION_RATIO):
        codes.append("poly_compressed")
    return codes


def _insertion_peak_ratio(trace: Dict[str, List[int]], peak_indices: List[int],
                          read_pos: int, alt_base: str) -> Optional[float]:
    """插入峰强度比：插入碱基通道峰面积 / 相邻峰主峰面积中位数

    纯合质粒中真实存在的插入，其峰高接近邻峰（~1）；部分克隆带插入（混合
    样品）或峰压缩区峰高偏弱（~0.3-0.6）；caller 伪影几乎无独立峰（~0）。
    仅支持单碱基插入（多碱基插入的峰形判别复杂，返回 None 走 Q 口径）。
    （T14 自 pipeline 迁入：峰级证据归证据模块）"""
    if len(alt_base) != 1 or alt_base not in "ACGT":
        return None
    if read_pos is None or read_pos < 1 or read_pos > len(peak_indices):
        return None
    i = read_pos - 1
    n = min(len(v) for v in trace.values()) if trace else 0
    if n == 0 or peak_indices[i] is None or not (0 <= peak_indices[i] < n):
        return None
    lo, hi = _peak_window(peak_indices, i, n)
    alt_area = sum(trace[alt_base][lo:hi])
    neigh: List[int] = []
    for j in (i - 2, i - 1, i + 1, i + 2):
        if j == i or not (0 <= j < len(peak_indices)):
            continue
        if peak_indices[j] is None or not (0 <= j < n):
            continue
        l2, h2 = _peak_window(peak_indices, j, n)
        neigh.append(max(sum(trace[b][l2:h2]) for b in "ACGT"))
    if not neigh:
        return None
    med = sorted(neigh)[len(neigh) // 2]
    return round(alt_area / med, 2) if med > 0 else None


def _variant_peak_evidence(trace: Dict[str, List[int]], peak_indices: List[int],
                           read_pos: Optional[int], ref_base: str,
                           alt_base: str, vtype: str = "substitution",
                           bases: str = "") -> Optional[Dict]:
    """变体的峰级证据：替换用峰强比（mutant_pct）+ 信噪比，插入用插入峰强度比。
    bases（修剪后调用碱基串）供 SNR 的局部噪声估计逐峰排除调用通道。
    （T14 自 pipeline 迁入）"""
    if vtype == "insertion":
        # Sanger read 前导 ~20bp 峰形未稳定（信号爬升/压缩高发），峰证据不可靠
        if read_pos is None or read_pos < 20:
            return None
        ratio = _insertion_peak_ratio(trace, peak_indices, read_pos, alt_base)
        return {"insertion_peak_ratio": ratio} if ratio is not None else None
    if not ref_base or not alt_base or len(ref_base) != 1 or len(alt_base) != 1:
        return None
    if alt_base not in "ACGT" or ref_base not in "ACGT":
        return None
    if not trace or not peak_indices or read_pos is None or read_pos < 1 or read_pos > len(peak_indices):
        return None
    pk = peak_indices[read_pos - 1]
    n = min(len(v) for v in trace.values())
    if n == 0 or pk is None or pk < 0 or pk >= n:
        return None
    lo, hi = _peak_window(peak_indices, read_pos - 1, n)
    areas = {b: sum(trace[b][lo:hi]) for b in "ACGT"}
    alt_a, ref_a = areas.get(alt_base, 0), areas.get(ref_base, 0)
    mutant_pct = round(100 * alt_a / (alt_a + ref_a), 1) if alt_a + ref_a > 0 else None
    return {"mutant_pct": mutant_pct,
            "snr": _noise_peak_snr(trace, peak_indices, read_pos, alt_base, bases)}


def _evidence_item(v: Dict, r: Dict, pr: Dict) -> Optional[Dict]:
    """单条支持 read 的证据项（EvidenceItem，goal §3 T14）。

    v 为合并变体（参考链方向），r 为该 read 的分析结果，pr 为合并时记录的
    {read, read_pos, read_q}；反向 read 的 ref/alt 按电泳链互补换算后取峰证据。"""
    fname = pr["read"]
    rp = pr.get("read_pos")
    direction = (r.get("alignment") or {}).get("direction", "+")
    if direction == "-":
        comp = str.maketrans("ACGTN", "TGCAN")
        ev_ref = (v.get("ref_base", "") or "").translate(comp)
        ev_alt = (v.get("alt_base", "") or "").translate(comp)[::-1]
    else:
        ev_ref, ev_alt = v.get("ref_base", ""), v.get("alt_base", "")
    ev = _variant_peak_evidence(
        r.get("trace") or {}, r.get("trimmed_peaks") or [], rp,
        ev_ref, ev_alt, v.get("type", "substitution"),
        bases=r.get("trimmed_bases") or "",
    ) or {}
    site = next((d for d in (r.get("mixed_detail") or [])
                 if d.get("pos") == rp), None)
    zone = r.get("reliable_zone")
    called = ""
    tb = (r.get("trimmed_bases") or "").upper()
    if rp and 1 <= rp <= len(tb):
        called = tb[rp - 1]
    return {
        "read": fname, "read_pos": rp, "strand": direction, "q": pr.get("read_q"),
        "mutant_pct": ev.get("mutant_pct"), "snr": ev.get("snr"),
        "mixed_ratio": (site or {}).get("ratio"),
        "mixed_z": (site or {}).get("mixed_z"),
        "in_zone": bool(zone and rp and in_zone(zone, rp)),
        "called_base": called,
        # 伪迹特征（goal T20）：位点落在压缩/dye blob/短滑移区段 → kind 值，
        # 评分权重待 T23 基准集拟合（当前不参与计算）
        "artifact": _artifact_at(r.get("artifacts") or [], rp),
    }


def _evidence_strength(item: Dict) -> tuple:
    """证据强度排序键（代表 read 择强，goal T14）：峰强比 > 信噪比 > Q；
    峰证据缺失（indel / trace 缺失）按「无峰证据」参与排序——确定性，与
    上传顺序无关，同名 read 兜底稳定。"""
    mp = item.get("mutant_pct")
    snr = item.get("snr")
    return (1 if mp is not None else 0, mp or 0,
            1 if snr is not None else 0, snr or 0,
            item.get("q") or 0)


def attach_evidence_by_read(v: Dict, by_read: Dict[str, Dict],
                            mixed_by_read: Dict[str, set]) -> None:
    """多 read 证据聚合（goal T14；A 3.4 / B P1-6，就地修改 v）：

    - 合并时记录的 `_per_read`（每条支持 read 的 read_pos/Q）逐条构造
      EvidenceItem → `evidence_by_read`（按 read 名排序，输出确定）；
    - 展示用「代表 read」取证据最强者（_evidence_strength 最大，平局按
      read 名升序），并重绑 v 的 read/read_pos/read_q——后续置信度分级
      （峰证据、混合背景、zone 强制、互检）全部基于代表 read，与上传
      顺序无关（M2）。
    """
    per = v.pop("_per_read", None) or [{"read": v.get("read"),
                                        "read_pos": v.get("read_pos"),
                                        "read_q": v.get("read_q")}]
    items: List[Dict] = []
    for pr in per:
        r = by_read.get(pr["read"])
        if r is None:
            continue
        item = _evidence_item(v, r, pr)
        if item is not None:
            items.append(item)
    items.sort(key=lambda e: e["read"])
    v["evidence_by_read"] = items
    if not items:
        return
    # 代表 read：证据最强者；平局按 read 名升序（_name_desc 逆转实现 max 语义）
    rep = max(items, key=_rep_key)
    v["read"] = rep["read"]
    v["read_pos"] = rep["read_pos"]
    v["read_q"] = rep["q"]
    r = by_read.get(rep["read"])
    if r is not None:
        direction = (r.get("alignment") or {}).get("direction", "+")
        if direction == "-":
            comp = str.maketrans("ACGTN", "TGCAN")
            ev_ref = (v.get("ref_base", "") or "").translate(comp)
            ev_alt = (v.get("alt_base", "") or "").translate(comp)[::-1]
        else:
            ev_ref, ev_alt = v.get("ref_base", ""), v.get("alt_base", "")
        ev = _variant_peak_evidence(
            r.get("trace") or {}, r.get("trimmed_peaks") or [], rep["read_pos"],
            ev_ref, ev_alt, v.get("type", "substitution"),
            bases=r.get("trimmed_bases") or "",
        )
        if ev is not None:
            v["peak_evidence"] = ev


def _rep_key(item: Dict) -> tuple:
    """max() 用的代表键：强度最大；平局 read 名升序（字符串直接比较即升序，
    但 max 取大——对名字取负序用元组包装）"""
    return (_evidence_strength(item), _name_desc(item["read"]))


def _name_desc(name: str) -> tuple:
    return tuple(-ord(c) for c in (name or ""))


def covering_not_supporting(v: Dict, ref2call_by_read: Dict[str, Dict[int, Dict]],
                            keys_by_read: Dict[str, set],
                            by_read: Dict[str, Dict]) -> List[Dict]:
    """覆盖该位点但未支持该变体的其他 read（goal T14：互检信息扩展到所有
    变体；A 3.4 covering_reads_not_supporting）。与 contradicts/weak 的
    「干净覆盖反证」不同：本清单只记录覆盖事实本身（含 N 列、质量不足、
    邻近有其他差异的 read），不做反证推断——判定仍走 _clean_cover_reads*。"""
    out: List[Dict] = []
    try:
        anchor = int(v["ref_pos"])
    except (KeyError, TypeError, ValueError):
        return out
    key = _variant_key_for_cover(v)
    for fname, r2c in ref2call_by_read.items():
        if fname == v.get("read"):
            continue
        c = r2c.get(anchor)
        if not c:
            continue
        if key in keys_by_read.get(fname, ()):
            continue
        r = by_read.get(fname) or {}
        rp = c.get("read_pos")
        tq = r.get("trimmed_quality") or []
        q = tq[rp - 1] if rp and 1 <= rp <= len(tq) else None
        out.append({"read": fname, "read_pos": rp, "q": q})
    return sorted(out, key=lambda h: h["read"])


def _variant_key_for_cover(v: Dict) -> tuple:
    """covering 判定用的变体键（与 pipeline._variant_key 同构；此处独立
    实现避免循环导入）"""
    return (int(v.get("ref_pos") or 0), v.get("type") or "",
            (v.get("alt_base") or "").upper(), int(v.get("length") or 1))


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
                              mixed_corroboration: Optional[Dict],
                              poly_calls: Optional[List[Dict]] = None) -> List[Dict]:
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
        _z = r.get("reliable_zone") or {}
        _zs, _ze = _z.get("start"), _z.get("end")
        for i, b in enumerate((r.get("trimmed_bases") or "").upper()):
            if b in "ACGT":
                continue
            if _zs is not None and _ze is not None and not (_zs <= i + 1 <= _ze):
                # v3.4（2026-11-11，公开数据 PKS 7 条）：可信区外的 N/IUPAC 调用
                # 与头行「已测」同源排除——可信区外本就不计入 verified，
                # 不能再以未决位点阻断（原口径用整段比对跨度，两口径不一致）
                r.setdefault("n_outside_zone", 0)
                r["n_outside_zone"] += 1
                continue
            rp = r2ref.get(i + 1)
            if rp:
                _add(rp, "n_call" if b == "N" else "iupac", r["filename"],
                     f"read 调用 {b}（未解析）")
    # poly 不可判定：峰图计数矛盾 / 不可判定 / 缺失证据（accepted 不算）
    if poly_calls is not None:
        # S4 PolyCall：只读结构化状态（CONFLICTING / UNDETERMINABLE /
        # DEFICIT_SUSPECTED）
        from core.sanger.polycall import LEGACY_VERDICT, unresolved_polys
        for pc in unresolved_polys(poly_calls):
            _add(pc["ref_start"], "poly_undetermined", "",
                 f"poly({pc.get('base')}) {pc['ref_start']}-{pc['ref_end']} "
                 f"峰图判读 {LEGACY_VERDICT[pc['state']]}")
    else:  # 兼容：未传 PolyCall 时按旧条目字段
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
    # 次等位 AF 随位点下发（2026-11-10）：verdict 按 AF 分流——AF≥0.23 的
    # 孤立双峰（S16 0.30、M7 0.50）进待核；低于此的是真实单克隆常见噪声
    # （M13F/M13R/11131/17647 及 Biopython 3730/3100 的散在位点 AF 0.15-0.21）
    _af_by: Dict[str, Dict[int, float]] = {}
    for r in read_results:
        r2ref, _ = _read_ref_maps(r)
        m = _af_by.setdefault(r.get("filename"), {})
        for e in r.get("mixed_detail") or []:
            rp0 = r2ref.get(e.get("pos"))
            if rp0:
                ratio = float(e.get("ratio") or 0)
                m[int(rp0)] = max(m.get(int(rp0), 0.0), round(ratio / (1 + ratio), 3))
    for fn, chk in ((mixed_corroboration or {}).get("by_read") or {}).items():
        for rp in chk.get("uncovered_ref") or []:
            _add(rp, "mixed_uncorroborated", fn, "双峰位点无其他引物覆盖，无法互检")
            af = (_af_by.get(fn) or {}).get(int(rp)) if rp else None
            if af is not None:
                ent = sites[(int(rp), "mixed_uncorroborated")]
                ent["af"] = max(ent.get("af") or 0.0, af)
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
    if zone and rp and support < 2 and zone_class(zone, rp) != "core":
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
                        resolved: int = 0, unresolved_sites: int = 0,
                        failed_reads: int = 0,
                        failed_names: str = "",
                        verdict: Optional[Dict] = None) -> str:
    """结论头部的一句话总结：✓/✗/△ 一眼给出判读结果，详细段落空两行后展开。
    优先级：确认差异 > 读取失败 > 疑似混合 > 未决（资格门）> 完全一致
    （T2 统一三出口：确证损伤不再让位于混合提示——多数克隆确实错了，
    混合降为正文提示；T4：解析失败的 read 不再隐身，结论头点名）。
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
    # 位点级混合（goal T7：mixed_variant 驱动的 status=mixed）与混合样品
    # 同待遇——verdict 缺省（直调/旧记录）不启用
    if (verdict or {}).get("status") == "mixed":
        has_mixed = True
    # 范围感知（goal v3 W2）：判定只对 scope 负责——声明范围外的确证差异
    # 不驱头行（out_of_scope_variant 已由 verdict 记 info，SC7/SC8）；
    # 未声明范围时头行用「已测区段」口径，不得输出「构建与设计一致」
    # （goal v3 §0.1）。旧记录（无 scope 字段）不启用过滤，行为不变
    vd_scope = (verdict or {}).get("scope") or {}
    declared = bool(vd_scope.get("declared"))
    scope_ranges = [(int(s), int(e)) for s, e in (vd_scope.get("ranges") or [])]
    # W8：接合区窗口（cds_target）属于需测范围——头行归因与 verdict 同口径
    junction_windows = [(int(s), int(e)) for s, e in (vd_scope.get("junctions") or [])]

    def _in_scope_v(v: Dict) -> bool:
        if not (declared and scope_ranges):
            return True
        rp = int(v.get("ref_pos") or 0)
        v_end = rp + int(v.get("length") or 1) - 1
        return (any(s <= v_end and rp <= e for s, e in scope_ranges)
                or any(s <= v_end and rp <= e for s, e in junction_windows))

    # W12 三出口统一：verdict 存在时头行符号由 verdict.status 经统一映射
    # 导出（OC1）；旧记录/直调（无 verdict）保持分支字面量，口径不变
    _vd_status = (verdict or {}).get("status")
    _sym = status_symbol(_vd_status) if _vd_status else None

    target = next((c for c in cds_reports if c.get("analysis_target")), None)
    tail = ""
    if target and not has_mixed:
        if target.get("coverage_status") == "uncovered":
            tail = f"目的 CDS「{target['name']}」未被任何 read 覆盖，未能验证"
        elif target.get("coverage_status") == "partial":
            tail = (f"目的 CDS 覆盖 {target.get('covered_percent') or 0:.0f}%"
                    "（未覆盖部分按参考填充、未验证）")
    if partial_reads and not has_mixed:
        confirmed0 = [v for v in variants if _vstatus_of(v) == "confirmed"]
        bad0 = [c for c in cds_reports if c.get("protein_identical") is False]
        if not confirmed0 and not bad0:
            return (f"△ 结论：已比对区域与设计一致，但 {partial_reads} 条 read "
                    "有大段未比对上参考，该部分未验证" + (f"；{tail}" if tail else ""))
    confirmed = [v for v in variants
                 if _vstatus_of(v) == "confirmed" and _in_scope_v(v)]
    out_scope = [v for v in variants
                 if _vstatus_of(v) != "rejected" and not _in_scope_v(v)]
    bad = [c for c in cds_reports if c.get("protein_identical") is False]
    names = "、".join(c["name"] for c in bad[:2]) + (" 等" if len(bad) > 2 else "")
    # 范围外差异注记（goal v3 W2）：不驱头行符号，但如实列出「未评估」
    oos_note = ""
    if out_scope:
        oos_note = f"；范围外 {len(out_scope)} 处差异未评估（见附带结果）"
    # 确证差异优先（T2 三出口统一：与 Excel「不合格」同序——混合样品提示
    # 保留在正文双峰章节，不再抢占结论头）
    if confirmed or bad:
        bits = []
        if bad:
            bits.append(f"{names}蛋白与设计不一致")
        if confirmed:
            bits.append(f"检出 {len(confirmed)} 处确认差异")
        if has_mixed:
            bits.append("另检出疑似混合样品（部分位点双峰），建议重新挑单克隆复测")
        if failed_reads:
            bits.append(f"另有 {failed_reads} 条 read 解析失败（{failed_names}），该部分未验证")
        return f"{_sym or '✗'} 结论：" + "，".join(bits) + (f"；{tail}" if tail else "")
    # 端部软剪切高质量段（goal T6；A 2.1）：连接/结构异常 → ✗，端部差异
    # 不再被局部比对静默解释成"没测到"（verdict 缺省的直调/旧记录不启用）
    hq_blks = [b for b in (verdict or {}).get("blocking") or []
               if b.get("kind") == "unaligned_hq_segment"]
    if hq_blks:
        who = "；".join(b["detail"] for b in hq_blks[:2])
        return (f"{_sym or '✗'} 结论：{who}"
                + ("等" if len(hq_blks) > 2 else "")
                + (f"；{tail}" if tail else ""))
    # 引物校验未通过（goal T17）：verdict 判 fail——引物加错/方向搞反/模板
    # 换位时差异判读不可信。结论头此前漏掉该分支，出现 ✓ 头行与 fail
    # status 并存（PR3 真值锁定暴露的三出口分叉，goal v3 W0）
    pm_blks = [b for b in (verdict or {}).get("blocking") or []
               if b.get("kind") == "primer_mismatch"]
    if pm_blks:
        who = "；".join((b.get("detail") or "引物校验未通过")
                        for b in pm_blks[:2])
        return (f"{_sym or '✗'} 结论：{who}" + ("等" if len(pm_blks) > 2 else "")
                + (f"；{tail}" if tail else ""))
    # 解析失败的 read（goal T4；B P0-3）：不再只在 read 表出现——结论头 △
    # 点名「哪几条没读出来」，失败的文件没有验证可言
    if failed_reads:
        head = (f"{_sym or '△'} 结论：{failed_reads} 条 read 解析失败（{failed_names}），"
                "该部分未验证")
        if variants or unresolved_sites:
            bits = []
            if variants:
                bits.append(f"{len(variants)} 处未决差异")
            if unresolved_sites:
                bits.append(f"{unresolved_sites} 处未决位点")
            head += "；" + "、".join(bits) + "需核对峰图或补测"
        return head + (f"；{tail}" if tail else "")
    if has_mixed:
        head = f"{_sym or '△'} 结论：疑似混合样品（部分位点双峰），主峰序列按多数碱基判读"
        return head + "——建议重新挑单克隆复测"
    # 无确认差异且目标零覆盖：△ 未能验证（✓ 会让没测到的目标隐身）
    if target and target.get("coverage_status") == "uncovered":
        low_note = (f"（{len(variants)} 处未决差异）"
                    if variants else "（已比对区域与设计一致）")
        return f"{_sym or '△'} 结论：目的 CDS「{target['name']}」未被任何 read 覆盖，未能验证{low_note}"
    # ROI 覆盖分级进结论头（goal T5；A 4.2）：partial/insufficient 不得出 ✓
    # ——「测到的地方一致」不等于「构建验证完毕」。verdict 缺省（旧记录/
    # 直调兼容）不启用，维持原分支；status 优先级保证走到这里时无未决项
    vd_status = (verdict or {}).get("status")
    if vd_status in ("partial", "insufficient"):
        roi_blk = next((b for b in verdict.get("blocking") or []
                        if b.get("kind") in ("scope_uncovered", "scope_beyond_primer", "scope_read_shortfall", "roi_uncovered", "roi_unverified",
                             "roi_insufficient_bp")
                        and b.get("detail")), None)
        if roi_blk:
            head = ("部分验证——" if vd_status == "partial" else "")
            return f"{_sym or '△'} 结论：{head}{roi_blk['detail']}" + (f"；{tail}" if tail else "")
    # ✓ 资格门（A 3.3 / B P0-1）：还有未决项就不得 ✓，也不再把它们预判为
    # 「疑似测序伪影」——△ 如实说「未决」，让用户核对峰图或补测
    ins_variants = [v for v in variants if _in_scope_v(v)]
    if ins_variants or unresolved_sites:
        bits = []
        if ins_variants:
            bits.append(f"{len(ins_variants)} 处未决差异，需核对峰图或补测")
        if unresolved_sites:
            bits.append(f"{unresolved_sites} 处未决位点（N/模糊调用、poly 或双峰未定）")
        return "△ 结论：" + "；".join(bits) + (f"；{tail}" if tail else "")
    # 出口一致兜底（goal v3 W0，PR3/SC2/SC3/SC9 真值锁定暴露）：verdict
    # 仍有阻断项（引物校验未通过、关键元件未验证等）时头行不得报 ✓——
    # 上文各专项分支未覆盖的 blocking kind 在此按明细如实降 △，终结
    # 「✓ 头行 + 非 pass status」的并存
    blk_all = (verdict or {}).get("blocking") or []
    if blk_all:
        first = next((b for b in blk_all if b.get("detail")), blk_all[0])
        more = f" 等 {len(blk_all)} 项" if len(blk_all) > 1 else ""
        return (f"△ 结论：{first.get('detail') or first.get('kind')}{more}"
                + (f"；{tail}" if tail else ""))
    # ✓ 的主张范围（goal v3 W2/§8 决策 1）：未声明范围只说「已测区段」，
    # 禁止「构建与设计一致」（goal v3 §0.1 对所有出口生效）；声明范围
    # 的头行三段式（符号 · 范围 · 证据）随 W5 重构
    if not declared and not target and scope_ranges:
        spans = "、".join(f"{s}–{e}" for s, e in scope_ranges[:3])
        more = f" 等 {len(scope_ranges)} 段" if len(scope_ranges) > 3 else ""
        claim = f"已测 {spans}{more} 与设计一致"
        note = "；范围外未评估" + oos_note
    else:
        claim = None
        note = oos_note
    if resolved:
        # variants 已剔除互检定案项：剩下的都排除了，结论是确定的
        ok = ("目的基因测序正确" if target
              else (claim or "构建与设计一致"))
        return f"✓ 结论：{ok}{note}" + (f"；{tail}" if tail else "")
    ok = ("目的基因测序正确，未检出差异" if target
          else ((claim + "，未检出差异") if claim
                else "构建与设计一致，未检出差异"))
    return f"✓ 结论：{ok}{note}" + (f"；{tail}" if tail else "")
