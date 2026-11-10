"""goal_sanger_full_fix_v2 §4 场景红测（T0 建立，全程使用）。

每个场景断言「最终期望」（goal §4 表格右列）；当前行为不达期望的以
xfail(strict=True, reason=<task>) 标注——由对应任务实现后移除标记，
移除即本场景转绿，全部移除 = §6.1 验收达成。场景构造见 sanger_scenarios.py。
"""

import json

import pytest

from core.sanger.batch import excel_conclusion
from core.sanger.homopolymer import _aligned_run_window, _read_ref_maps
from core.sanger.pipeline import analyze
from tests.sanger_scenarios import SCENARIOS, _af1_make
from tests.abif_utils import make_trace_ab1

S = {sc.id: sc for sc in SCENARIOS}


def _run(sid):
    spec = S[sid].build()
    kwargs = {k: v for k, v in (spec.get("kwargs") or {}).items()
              if not k.startswith("_")}
    # T25.2：核心 analyze() 拓扑必填——场景 runner 是调用方，场景未声明
    # 拓扑时显式按线性跑（与旧默认行为一致；M9 的直接调用仍必须报错）
    kwargs.setdefault("circular", False)
    res = analyze(spec["ab1_files"], spec["reference"], spec["features"], **kwargs)
    return spec, res


def _status(res):
    return (res.get("verdict") or {}).get("status")


def _kinds(res):
    vd = res.get("verdict") or {}
    return {b.get("kind") for b in vd.get("blocking", [])} | \
           {b.get("kind") for b in vd.get("info", [])}


def _v(res, pos):
    return next((v for v in res["variants"] if v["ref_pos"] == pos), None)


# ---------- S：假 ✓ / 假合格（漏报） ----------

def test_s1_tail_zone_q50_missense_not_silent():
    """S1：780bp Q50 错义必须 ✗ 确证（T12 信号驱动可信区后）"""
    spec, res = _run("S1")
    v = _v(res, 780)
    assert v and v["confidence"] == "high"
    assert res["conclusion"].startswith("✗")
    assert _status(res) == "fail"


def test_s2_cds_mixed_35_detected():
    """S2：CDS 内 35% 混合 → △ 疑似混合（AF≈0.35）"""
    spec, res = _run("S2")
    assert not res["conclusion"].startswith("✓")
    assert _status(res) == "mixed"
    mv = [b for b in (res["verdict"]["blocking"] if res.get("verdict") else [])
          if b.get("kind") == "mixed_variant" and
          (b.get("ref_start") == 450 or 450 in (b.get("positions") or []))]
    assert mv and abs(mv[0].get("af", 0) - 0.35) < 0.1


def test_s3_cds_mixed_5050_detected():
    """S3：CDS 内 50:50 混合 → △（AF≈0.5）"""
    spec, res = _run("S3")
    assert not res["conclusion"].startswith("✓")
    assert _status(res) == "mixed"
    mv = [b for b in (res["verdict"]["blocking"] if res.get("verdict") else [])
          if b.get("kind") == "mixed_variant" and
          (b.get("ref_start") == 450 or 450 in (b.get("positions") or []))]
    assert mv and abs(mv[0].get("af", 0) - 0.5) < 0.1


def test_s4_mixed_65pct_main_never_pass():
    """S4：65% 突变为主峰 → △，不得 ✓"""
    spec, res = _run("S4")
    assert not res["conclusion"].startswith("✓")
    assert _status(res) in ("mixed", "pending")


def test_s5_front_hq_clip_flagged():
    """S5：5' 端 175bp 高质量连接错误 → ✗ unaligned_hq_segment"""
    spec, res = _run("S5")
    assert _status(res) == "fail"
    assert "unaligned_hq_segment" in _kinds(res)


def test_s6_tail_hq_clip_flagged():
    """S6：3' 端 188bp 高质量差异 → ✗ unaligned_hq_segment"""
    spec, res = _run("S6")
    assert _status(res) == "fail"
    assert "unaligned_hq_segment" in _kinds(res)


def test_s7_promoter_variant_fails_everywhere():
    """S7：启动子确证突变三出口一致不合格"""
    spec, res = _run("S7")
    excel = excel_conclusion("P", res, 1, True)
    assert not excel.startswith("合格")
    assert _status(res) == "fail"
    assert "critical_element_variant" in _kinds(res)


def test_s8_partial_cds_never_qualified():
    """S8：唯一 CDS 覆盖 34.5% → 不得整参考「合格：与设计一致」。
    goal v3 §8 决策 1（W2 重编码）：未声明范围 → 已测区段口径 pass
    （旧口径 partial 的整参考主张被取代，SC3/SC4 为真值锁）；CDS 级
    覆盖缺口如实进 cds_reports，W5 起 element_verdicts 展示"""
    spec, res = _run("S8")
    excel = excel_conclusion("P", res, 1, True)
    assert not excel.startswith("合格：与设计一致")
    assert "构建与设计一致" not in excel
    assert res["verdict"]["scope"]["declared"] is False
    assert _status(res) == "pass"
    cr = next(c for c in res["cds_reports"] if c["name"] == "Solo")
    assert float(cr.get("covered_percent") or 0) < 50, "CDS 报告须如实报覆盖"


def test_s9_front_hard_zone_variant_unresolved():
    """S9：首端 30bp 硬区 Q40 变异 → △ 未决，需核对峰图/补测"""
    spec, res = _run("S9")
    v = _v(res, 20)
    assert v and v.get("status") == "unresolved"
    assert not res["conclusion"].startswith("✓")
    assert _status(res) == "pending"


def test_s10_n_call_listed_and_blocks():
    """S10：中段 N → △，列出 N 位点（verified 扣除由 T3/S13 覆盖）"""
    spec, res = _run("S10")
    us = [u for u in res.get("unresolved_sites", [])
          if u.get("kind") == "n_call" and u.get("ref_pos") == 500]
    assert us, "N 调用必须进 unresolved_sites"
    assert not res["conclusion"].startswith("✓")
    assert _status(res) == "pending"


def test_s11_n_in_cds_blocks_protein_verdict():
    """S11：CDS 内错义位点被 N → 蛋白判定待定，不得「一致」"""
    spec, res = _run("S11")
    cr = next(c for c in res["cds_reports"] if c["name"] == "GeneX")
    assert cr.get("n_in_cds") or cr.get("pending_impact")
    assert "n_in_cds" in _kinds(res)
    assert "蛋白与设计一致" not in cr["verdict"]


def test_s12_failed_reads_block_qualified():
    """S12：3 read 2 条损坏 → △ read_failed 点名，不进「正确/」"""
    from app.sequencing_report import _conclusion_bucket
    spec, res = _run("S12")
    assert len(res["verdict"]["reads"]["failed"]) == 2
    excel = excel_conclusion("P", res, 1, True)
    assert not excel.startswith("合格")
    assert _conclusion_bucket(excel) != "正确"
    assert _status(res) == "failed_reads"


def test_s13_coverage_by_untrusted_not_verified():
    """S13：不可信碱基凑满的覆盖 → verified < measured 如实下发；
    「合格」只对 verified 区段主张（goal v3 §8 决策 1，W2 重编码——
    旧口径的 roi_unverified blocking 只对声明范围生效，SC10 为真值锁）"""
    spec, res = _run("S13")
    excel = excel_conclusion("P", res, 1, True)
    assert not excel.startswith("合格")
    # 950 确证差异（可信区外但峰证据齐全，S17 同口径）→ fail；覆盖语义：
    # verified（≈1-700）< measured（1-1000）如实下发，不可信尾段不构成验证
    assert _status(res) == "fail"
    cov = res.get("coverage") or {}
    assert cov, "coverage 必须下发"
    assert cov["verified_percent"] < cov["measured_percent"]


def test_s14_true_mixed_not_flipped_by_weak_corroboration():
    """S14：45% 真混合 + Q25 验证 read → 互检不算 clean，保持 △ 混合"""
    spec, res = _run("S14")
    r1 = next(r for r in res["reads"] if r["filename"] == "mixed.ab1")
    assert r1["mixed_profile"]["class"] == "widespread"
    chk = res["mixed_corroboration"]["by_read"]["mixed.ab1"]
    assert chk["clean"] == 0
    assert not res["conclusion"].startswith("✓")
    assert _status(res) == "mixed"


def test_s15_junction_variant_blocked():
    """S15：CDS 模式接合区 ±30bp 非 low 差异 → junction_variant（confirmed ✗）"""
    spec, res = _run("S15")
    assert "junction_variant" in _kinds(res)
    assert _status(res) == "fail"


def test_s16_mixed_site_after_poly_survives():
    """S16：poly 后 20+bp 的真实混合双峰不被滑移序列吞掉（场景构造：
    polyA20 @100-119、真 echo @122、混合位点 @150——断言随构造取 150，
    xfail 掩盖期的 140 是笔误）"""
    spec, res = _run("S16")
    r = res["reads"][0]
    assert 150 in r["mixed_positions"], "真实混合位点被 slip 序列剔除"
    assert 122 not in r["mixed_positions"], "真滑移 echo 未被剔除"


def test_s17_all_evidence_out_of_zone_noted():
    """S17：双 read 尾端灰区同报仍 ✗，但必须带 out_of_zone 注记"""
    spec, res = _run("S17")
    v = _v(res, 800)
    assert v and v["confidence"] == "high"
    assert res["conclusion"].startswith("✗")
    assert "all_evidence_out_of_zone" in (v.get("reason_codes") or [])


# ---------- F：误报 ----------

def _bench_mixed_conclusion(sid):
    spec, res = _run(sid)
    return res


def test_f1_3730_dye_shoulders_not_mixed():
    """F1：3730.ab1 染料肩峰 → 非混合（最多 info）"""
    res = _bench_mixed_conclusion("F1")
    assert "疑似混合样品" not in res["conclusion"]
    assert _status(res) != "mixed"


def test_f2_3100_decay_segment_not_mixed():
    """F2：3100.ab1 信号衰减段 → 非混合，该段标「信号衰减」"""
    res = _bench_mixed_conclusion("F2")
    assert "疑似混合样品" not in res["conclusion"]
    assert _status(res) != "mixed"


def test_t11_uniform30_mixed_sample_detected():
    """T11 规格：全 read 均匀 30% 混合 → mixed_sample（widespread）。

    均匀混合会让自适应背景模型「把背景学到手上」而站点层自熄（局部中位
    ≈ 信号本身），兜底路径按 floor 位点密度（≈96/100bp）+ 低 CV + 三段
    散布升级——这是 floor 密度路径存在的意义。"""
    import random as _r
    rng = _r.Random("t11u30")
    ref = "".join(rng.choice("ACGT") for _ in range(1200))
    sec = {p: (next(b for b in "ACGT" if b != ref[p - 1]), 0.30 / 0.70)
           for p in range(1, 701)}
    blob = make_trace_ab1(ref[:700], q=50, secondary=sec)
    res = analyze([("u30.ab1", blob)], ref[:700], [], circular=False)
    prof = res["reads"][0]["mixed_profile"]
    assert prof["class"] == "widespread", prof
    assert res["verdict"]["status"] == "mixed"
    assert "疑似混合样品" in res["conclusion"]


def test_t11_local_cluster_not_mixed_sample():
    """T11：同样 5 处双峰但集中单一区段 → local（局部信号异常），不升级
    mixed_sample；位点仍逐个进待核（2026-11-06 定稿：个别双峰不升级）"""
    import random as _r
    rng = _r.Random("t11loc")
    ref = "".join(rng.choice("ACGT") for _ in range(1200))
    sec = {p: (next(b for b in "ACGT" if b != ref[p - 1]), 0.45 / 0.55)
           for p in (100, 108, 116, 124, 132)}
    blob = make_trace_ab1(ref[:800], q=50, secondary=sec)
    res = analyze([("loc.ab1", blob)], ref[:800], [], circular=False)
    prof = res["reads"][0]["mixed_profile"]
    assert prof["class"] == "local" and prof["class_reason"] == "concentrated_segment"
    assert res["verdict"]["status"] != "mixed"
    assert "疑似混合样品" not in res["conclusion"]


def test_f3_no_pcon_still_analyzed():
    """F3：310.ab1 无 PCON → 可分析并注明 quality_source"""
    spec, res = _run("F3")
    assert res["reads"], "无质量值文件不应被整体拒绝"
    assert not res["errors"]
    assert res["reads"][0].get("quality_source")


# ---------- M：其他问题 ----------

def test_m1_same_codon_subs_annotated_as_mnv():
    """M1：同密码子 AAA→TGA → 注释 K2*（终止），与 CDS 结论一致"""
    spec, res = _run("M1")
    ann = json.dumps(res["variants"], ensure_ascii=False)
    assert "K2*" in ann, f"变体注释未合并 MNV：{ann[:300]}"
    cr = next(c for c in res["cds_reports"] if c["name"] == "GeneX")
    assert "不一致" in cr["verdict"]


def test_m2_upload_order_independent_evidence():
    """M2：两条 read 支持同一变体 → status/p_real/峰证据不随顺序变化"""
    spec = S["M2"].build()
    a, b = spec["ab1_files"]
    res_ab = analyze([a, b], spec["reference"], spec["features"], circular=False)
    res_ba = analyze([b, a], spec["reference"], spec["features"], circular=False)
    v_ab, v_ba = res_ab["variants"][0], res_ba["variants"][0]
    assert v_ab["status"] == v_ba["status"]
    assert v_ab.get("p_real") == v_ba.get("p_real")
    ev_ab = sorted(v_ab.get("evidence_by_read") or [], key=lambda e: e["read"])
    ev_ba = sorted(v_ba.get("evidence_by_read") or [], key=lambda e: e["read"])
    assert ev_ab == ev_ba
    assert v_ab.get("read") == v_ba.get("read")


def test_m3_overlap_discordance_reported():
    """M3：重叠区高质量分歧 → overlap_discordance + 共识 IUPAC"""
    spec, res = _run("M3")
    assert "overlap_discordance" in _kinds(res)
    assert res["consensus"]["sequence"][499] in "RAG", "共识不得静默取单边"


def test_m4_orf_correction_bounded():
    """M4：标注 1100-1551 不得被 ORF 校正改写为 1228 起 → suspect"""
    spec, res = _run("M4")
    assert "orf_annotation_suspect" in _kinds(res)
    cr = next(c for c in res["cds_reports"] if c["name"] == "MisAnn")
    assert cr.get("start") == 1100 or cr.get("feature_start") == 1100


def test_m5_primer_mismatch_detected():
    """M5：read 起点偏离引物 3' 下游 >100bp → primer_mismatch"""
    spec, res = _run("M5")
    assert "primer_mismatch" in _kinds(res)
    assert _status(res) == "fail"


def test_m6_soft_clip_poly_windows_consistent():
    """M6：首端软剪切 ≥50bp + polyA20 → 三处窗口一致（差分断言）"""
    spec, res = _run("M6")
    reads = {r["filename"]: r for r in res["reads"]}
    for name in ("clipped.ab1", "plain.ab1"):
        r = reads[name]
        aln = r["alignment"]
        maps = _read_ref_maps(r)
        ref2call = maps[1]
        pos = [ref2call[p]["read_pos"] for p in range(200, 220) if p in ref2call]
        map_win = (min(pos) - 1, max(pos) - 1)
        w = _aligned_run_window(aln, 200, 219, "A")
        assert w is not None
        assert (w[0], w[1]) == map_win, f"{name}: 比对窗口 {w[:2]} != 映射窗口 {map_win}"


def test_m7_secondary_base_when_call_not_top():
    """M7：调用碱基为第二高通道（位点 300 次级峰 1.2 倍）→ secondary_base 非
    None 或分歧标注。T12 起 3100 的 609 位落入信号衰减段（可信区外双峰不
    记录），场景改确定性合成（见 _m7）"""
    spec, res = _run("M7")
    detail = res["reads"][0]["mixed_detail"]
    e = next((d for d in detail if d["pos"] == 300), None)
    assert e is not None, "300 位双峰应被记录"
    assert e.get("secondary_base") is not None or e.get("call_disagrees_with_trace")


def test_m9_core_analyze_requires_topology():
    """M9：直接调核心 analyze() 不传拓扑 → 报错或从参考读取，不得静默线性"""
    spec = S["M9"].build()
    with pytest.raises((TypeError, ValueError)):
        analyze(spec["ab1_files"], spec["reference"], spec["features"])


def test_m10_resolved_primer_coord_with_clip():
    """M10：反向 read 软剪切时裁决行引物内坐标 == 变体表 read_pos 口径"""
    spec, res = _run("M10")
    reads = {r["filename"]: r for r in res["reads"]}
    rev = reads["rev.ab1"]
    ref2call = _read_ref_maps(rev)[1]
    region = next(x for x in res["resolved_regions"]
                  if x["source_read"] == "rev.ab1")
    expected = sorted(ref2call[p]["read_pos"] for p in region["positions"])
    lo, hi = min(expected), max(expected)
    assert region["primer_range"] == f"{lo}-{hi}"


def test_m11_precheck_rejects_broken_reference():
    """M11：_precheck 收到 b\"\\0\" 参考 → 预检阶段即报不可解析"""
    from app.routes.sequencing_session_routes import _precheck
    spec = S["M11"].build()
    with pytest.raises(Exception):
        _precheck(spec["precheck_manifest"], spec["excel_bytes"],
                  spec["read_limits"])


def test_m12_ploc_shorter_than_pbas_warns():
    """M12：PLOC 短于 PBAS → 明确告警，不静默截短"""
    spec, res = _run("M12")
    warnings = res.get("warnings") or []
    assert any("PLOC" in str(w) for w in warnings), \
        f"PLOC 截短未告警：{warnings}"


def test_m13_structured_verdict_in_payload():
    """M13（后端半边）：payload 携带 verdict 与 resolved_regions；
    前端结构化渲染的 vitest 断言随 T2 落地"""
    spec, res = _run("M13")
    assert "verdict" in res and "resolved_regions" in res


def test_m14_end_insertion_single_low_vote_not_applied():
    """M14：参考末端插入仅一票且 low → 不静默定插：要么不写入共识（ref 等长），
    要么照实写入但标 unresolved（T1 三态；不得冒充已验证序列）"""
    spec, res = _run("M14")
    cons = res["consensus"]["sequence"]
    if len(cons) != len(S["M14"].build()["reference"]):
        ins_diffs = [d for d in res["consensus"].get("diffs", [])
                     if d.get("ref_base") == "-"]
        assert ins_diffs, "共识变长必须由插入差异解释"
        assert all(d.get("unresolved") for d in ins_diffs), \
            "单 low 票插入必须带 unresolved 标记"


def test_m15_enzyme_site_pending_hint():
    """M15：计划酶位 ±10bp 有未决 indel → 提示「位点归属待定」"""
    spec, res = _run("M15")
    blob = res["conclusion"] + json.dumps(res["variants"], ensure_ascii=False)
    assert "位点归属待定" in blob


# ---------- xfail 注册表（T0 现状红测） ----------
#
# goal §5/T0.4：当前行为不达「最终期望」的场景挂 xfail(strict=True)，reason =
# 使其转绿的任务号。对应任务完成后**删除该行**——测试随即转绿、且若行为回退
# strict 标记会立刻红。M14 是唯一例外：end_insert_votes 的 idx==L 路径经局部
# 比对不可达（尾端插入恒被软剪切，B P2-16 为静态确认），「不定插」行为当前已
# 满足，故不挂 xfail；T25.3 仍按规格补 low/rejected 票排除（防御深度）。
XFAIL_BY_TASK = {
    # 全部场景已转绿（P2 闸门口径）：M1 随 T16/T25.1（MNV 联合注释）、
    # S1 随 T12、F1/F2 随 T11、M7 随 T12/T24、F3/M12 随 T24（峰型估 Q /
    # PLOC 截短告警）、M9 随 T25.2（circular 必填关键字）、M15 随 T25.4
    # （酶位 ±10bp 未决 indel 提示）。
}

for _name, _task in XFAIL_BY_TASK.items():
    globals()[_name] = pytest.mark.xfail(strict=True, reason=_task)(globals()[_name])
if XFAIL_BY_TASK:   # 全部转绿后注册表为空，循环体未执行时无名字可删
    del _name, _task


def test_s18_inframe_cds_deletion_confirmed():
    """S18（复审 R7 独立正例）：CDS 内框内缺失 3bp（planted）→ ✗ 确证"""
    _spec, res = _run("S18")
    v = _v(res, 500)
    assert v and v["type"] == "deletion" and v["confidence"] == "high"
    assert _status(res) == "fail"


def test_s19_backbone_substitution_confirmed():
    """S19（复审 R7 独立正例）：骨架确证替换 + read 全覆盖 → ✗ 确证"""
    _spec, res = _run("S19")
    v = _v(res, 650)
    assert v and v["confidence"] == "high"
    assert res["conclusion"].startswith("✗")
    assert _status(res) == "fail"


# ==================== goal v3 §4 场景真值（W0 定稿，不得随输出修改） ====================
# 期望 = goal_sanger_scope_v3 §4 表格右列 + §8 已采用的默认决策；实现未落地
# 的任务挂 xfail(strict=True, reason=<工作项>)，落地后移除标记即转绿。
# 真值来源：goal 文本与场景构造（planted），不是「跑出来是什么就是什么」。

XF = pytest.mark.xfail


# ---------- §4.1 SC：范围 ----------

def test_sc1_scope_geneX_pass_headline_elements():
    """SC1：scope=GeneX 正反全覆盖 → pass；headline 含 GeneX（301–900）；
    AmpR/ori 元件结论 not_assessed"""
    spec, res = _run("SC1")
    vd = res["verdict"]
    assert _status(res) == "pass"
    assert vd["blocking"] == []
    assert "GeneX（301–900）" in (vd.get("headline") or "")
    els = {e["name"]: e for e in vd.get("element_verdicts") or []}
    assert els["AmpR"]["status"] == "not_assessed"
    assert els["ori"]["status"] == "not_assessed"


def test_sc2_filename_match_scope_pass():
    """SC2：不声明 scope，参考名 GeneX 命中 → source=filename_match，pass"""
    from core.sanger.scope import infer_scope  # noqa: W10 落地
    spec = S["SC2"].build()
    scope = infer_scope(spec["ref_name"], [n for n, _ in spec["ab1_files"]],
                        spec["features"])
    assert scope.source == "filename_match" and scope.declared
    kwargs = dict(spec["kwargs"])
    kwargs["scope"] = scope
    res = analyze(spec["ab1_files"], spec["reference"], spec["features"], **kwargs)
    assert _status(res) == "pass"
    assert res["verdict"]["scope"]["source"] == "filename_match"


def test_sc3_undeclared_scope_measured_wording():
    """SC3：未声明范围 → declared=false、status=pass；headline 写「已测 x–y…
    …未评估」；任何出口不得出现「构建与设计一致」"""
    spec, res = _run("SC3")
    vd = res["verdict"]
    assert vd["scope"]["declared"] is False
    assert _status(res) == "pass"
    head = vd.get("headline") or ""
    assert "已测" in head and "未评估" in head
    assert "构建与设计一致" not in head
    assert "构建与设计一致" not in res["conclusion"]
    assert not excel_conclusion("P", res, 1, True).startswith("合格：与设计一致")


def test_sc4_whole_partial_never_blanket_pass():
    """SC4：3000bp 只测 1-900 不声明范围 → 同 SC3：pass + 已测区段口径"""
    spec, res = _run("SC4")
    vd = res["verdict"]
    assert vd["scope"]["declared"] is False
    assert _status(res) == "pass"
    head = vd.get("headline") or ""
    assert "已测" in head and "构建与设计一致" not in head


def test_sc5_scope_tail_beyond_primer():
    """SC5：scope=301-1500 只有正向 read（至 1250）→ partial；
    缺口 cause=beyond_primer（加引物动作）"""
    spec, res = _run("SC5")
    assert _status(res) == "partial"
    blk = [b for b in res["verdict"]["blocking"]
           if b.get("kind") == "scope_beyond_primer"]
    assert blk and blk[0]["ref_end"] >= 1400
    assert "引物" in (blk[0].get("action") or "")


def test_sc6_read_shortfall_enters_review_queue():
    """SC6：read 400bp 处衰减 → partial；cause=read_shortfall；进复核队列"""
    from core.sanger.review import build_queue  # noqa: W14 落地
    spec, res = _run("SC6")
    assert _status(res) == "partial"
    blk = [b for b in res["verdict"]["blocking"]
           if b.get("kind") == "scope_read_shortfall"]
    assert blk, "测序未达缺口必须单列"
    queue = build_queue(res)
    assert queue, "read_shortfall 缺口必须进入复核队列"
    assert any(q.get("evidence") for q in queue), "队列项自带峰图证据"


def test_sc7_out_of_scope_variant_info_only():
    """SC7：R 外骨架 1200 确证替换 → pass；out_of_scope_variant 单列不进 blocking"""
    spec, res = _run("SC7")
    assert _status(res) == "pass"
    assert res["verdict"]["blocking"] == []
    info = [b for b in res["verdict"]["info"]
            if b.get("kind") == "out_of_scope_variant"]
    assert info and info[0].get("ref_start") == 1200


def test_sc8_out_of_scope_critical_element_pending():
    """SC8：R 外 ori 内确证替换 → pending（critical_element_variant），不判 fail"""
    spec, res = _run("SC8")
    assert _status(res) == "pending"
    blk = [b for b in res["verdict"]["blocking"]
           if b.get("kind") == "critical_element_variant"]
    assert blk and blk[0].get("element") == "ori"


def test_sc9_verdict_reproducible_across_scopes(monkeypatch):
    """SC9：同一份 result 依次 GeneX / 全质粒 / 不声明 重算 verdict，
    与本快照一致、不调用比对、重算可复现"""
    import core.sanger.aligner as _aligner
    from core.sanger.verdict import build_verdict

    def _boom(*_a, **_k):
        raise AssertionError("build_verdict 不得调用比对")
    monkeypatch.setattr(_aligner, "align_read", _boom)
    monkeypatch.setattr(_aligner, "align_read_circular", _boom)
    spec = S["SC9"].build()
    kwargs = {k: v for k, v in (spec.get("kwargs") or {}).items()
              if not k.startswith("_")}
    res = analyze(spec["ab1_files"], spec["reference"], spec["features"], **kwargs)
    L = len(spec["reference"])
    v_gene = build_verdict(res, L, scope="GeneX")
    v_whole = build_verdict(res, L, scope="全质粒")
    v_none = build_verdict(res, L, scope=None)
    # 快照（goal §4 SC9 期望，W0 定稿）：scope=GeneX 全覆盖 → pass；
    # 全质粒声明范围 → 范围内未测元件 pending；未声明 → 已测区段 pass
    assert v_gene["status"] == "pass" and v_gene["blocking"] == []
    assert "GeneX（301–900）" in (v_gene.get("headline") or "")
    assert v_whole["status"] == "pending"
    kinds = {b["kind"] for b in v_whole["blocking"]}
    assert {"scope_beyond_primer", "critical_element_unverified"} <= kinds
    assert v_none["status"] == "pass" and v_none["scope"]["declared"] is False
    assert build_verdict(res, L, scope="GeneX") == v_gene


def test_sc10_verified_gap_never_pass_with_blocking():
    """SC10（W3 落地，RV P0-2）：verified ≈99%、measured 100% → 不得出现
    pass 同时带 blocking；verified 口径按 ROI 分数断言（goal W3「统一
    verified 口径」——整参考 coverage_percent 混入范围外语义，不再使用）"""
    spec, res = _run("SC10")
    vd = res["verdict"]
    assert not (vd["status"] == "pass" and vd["blocking"]), "pass 与 blocking 并存"
    assert vd["status"] == "pending"
    assert any(b["kind"] == "roi_unverified" for b in vd["blocking"])
    roi = vd["roi"]
    assert roi["measured_fraction"] == 1.0
    assert 0.98 <= roi["verified_fraction"] < 1.0


def test_sc11_double_strand_required_partial():
    """SC11：scope 要求双链、GeneX 后半段单链 → partial；阻断指向单链区"""
    spec, res = _run("SC11")
    assert _status(res) == "partial"
    blk = [b for b in res["verdict"]["blocking"]
           if (b.get("ref_start") or 10**9) <= 700 <= (b.get("ref_end") or -1)]
    assert blk, "单链区（651-900）必须有归因阻断项"


# ---------- §4.2 CM：目的基因模式 ----------

def _run_cds_mode(sid):
    """W8 目标路径：prepare_scope_mode（全质粒 + scope，不裁参考）→ analyze"""
    from core.sanger.cds_extract import prepare_scope_mode  # noqa: W8 落地
    spec = S[sid].build()
    ref2, feats2, scope, circular = prepare_scope_mode(
        spec["reference"], spec["features"], spec["ref_name"],
        [n for n, _ in spec["ab1_files"]], None,
        circular=bool((spec.get("kwargs") or {}).get("circular", True)))
    res = analyze(spec["ab1_files"], ref2, feats2, scope=scope, circular=circular)
    return spec, res


def test_cm1_long_reads_beyond_flank_pass():
    """CM1：正反 read 覆盖 700-1701/1000-1900，序列全对 → pass，
    无 unaligned_hq_segment / partial_alignment"""
    spec, res = _run_cds_mode("CM1")
    assert _status(res) == "pass"
    kinds = _kinds(res)
    assert "unaligned_hq_segment" not in kinds
    assert "partial_alignment" not in kinds
    assert "GeneX（1001–1600）" in (res["verdict"].get("headline") or "")


def test_cm2_vector_primer_read_pass():
    """CM2：载体通用引物 read 起点距 ATG 300bp → pass"""
    spec, res = _run_cds_mode("CM2")
    assert _status(res) == "pass"
    assert "unaligned_hq_segment" not in _kinds(res)


@XF(strict=True, reason="X4")
def test_cm3_cross_origin_cds_junction_insertions():
    """CM3：跨原点 CDS 的 ATG 前 / 终止后 1bp 插入 → 检出并判 junction_variant

    v3.1 X0：断言恢复 W0 原样（goal §4：两处 `type == "insertion"`）。
    微同源边界上的 1bp 插入必须仍报为 insertion（RB P1-4），由 X4
    indel 规范化实现；不得再放宽成「接合区窗口内有确证差异即可」。"""
    spec, res = _run_cds_mode("CM3")
    ins = [v for v in res["variants"] if v["type"] == "insertion"]
    assert len(ins) >= 2, f"两处接合区插入都必须检出：{res['variants']}"
    assert "junction_variant" in _kinds(res)
    assert _status(res) == "fail"


def test_cm4_near_origin_cds_matches_scope_mode():
    """CM4：近原点 CDS ATG 前 1bp 插入 → 检出；与「全质粒 + scope=该 CDS」一致"""
    spec, res = _run_cds_mode("CM4")
    assert any(v["type"] == "insertion" for v in res["variants"]), "插入必须检出"
    assert "junction_variant" in _kinds(res)
    # 对照：同一份 read 直接走全质粒 + scope=该 CDS
    res2 = analyze(spec["ab1_files"], spec["reference"], spec["features"],
                   scope="NearCDS", circular=True)
    ins2 = [v for v in res2["variants"] if v["type"] == "insertion"]
    assert len(ins2) == len([v for v in res["variants"] if v["type"] == "insertion"])
    assert _status(res2) == _status(res)


def test_cm5_cds_mode_equals_scope_mode():
    """CM5：同一组 read，cds_mode 与「全质粒 + scope=该 CDS」的 status、
    headline、blocking kind 集合逐项一致"""
    spec, res_cds = _run_cds_mode("CM5")
    res_scope = analyze(spec["ab1_files"], spec["reference"], spec["features"],
                        scope="GeneX", circular=True)
    assert _status(res_cds) == _status(res_scope)
    assert (res_cds["verdict"].get("headline") or "") == \
           (res_scope["verdict"].get("headline") or "")
    assert {b["kind"] for b in res_cds["verdict"]["blocking"]} == \
           {b["kind"] for b in res_scope["verdict"]["blocking"]}


def test_cm6_ref_read_name_conflict_ambiguous():
    """CM6：参考名命中 GFP、read 名命中 mCherry → 判歧义返回候选清单"""
    from core.sanger.cds_extract import cds_candidates, resolve_auto
    spec = S["CM6"].build()
    cand, note = resolve_auto(cds_candidates(spec["features"]),
                              "GFP_backbone", ["mCherry_F.ab1"])
    assert cand is None and "歧义" in note


def test_cm7_explicit_nested_and_duplicate():
    """CM7：显式选择嵌套 6xHis 可选中；重名时报错"""
    from core.sanger.cds_extract import CdsResolveError, resolve_explicit
    spec = S["CM7"].build()
    cand = resolve_explicit(spec["features"], "6xHis")
    assert cand["name"] == "6xHis" and cand["start"] == 401 and cand["end"] == 418
    dup_feats = [dict(name="Dup", type="CDS", start=1501, end=1600, strand="+"),
                 dict(name="Dup", type="CDS", start=1701, end=1800, strand="+")]
    with pytest.raises(CdsResolveError):
        resolve_explicit(dup_feats, "Dup")


def test_cm8_negative_strand_dual_coords():
    """CM8：负链基因质粒 1100 位替换 → coords.plasmid=1100，c./p. 正确
    （CDS 1001-1600 负链：编码偏移 = 1600-1100 = 500 → c.501）"""
    spec, res = _run("CM8")
    v = _v(res, 1100)
    assert v and v.get("coords"), "变异必须携带双坐标"
    assert v["coords"]["plasmid"] == 1100
    cds = v["coords"].get("cds") or {}
    assert cds.get("name") == "GeneX" and cds.get("c") == "c.501"


# ---------- §4.3 MX-AF：混合 ----------

@pytest.mark.parametrize("af", [0.12, 0.15, 0.18, 0.20])
def test_af1_low_af_in_cds_at_least_pending(af):
    """AF1：CDS 内单 read AF 0.12/0.15/0.18/0.20 → 至少 pending，不得 pass，
    位点必须留痕（不允许静默）"""
    spec = _af1_make(af)
    kwargs = dict(spec["kwargs"])
    res = analyze(spec["ab1_files"], spec["reference"], spec["features"], **kwargs)
    assert _status(res) in ("pending", "mixed"), \
        f"AF {af} 不得静默 pass（实际 {_status(res)}）"
    assert _status(res) != "pass"
    recorded = ([u.get("ref_pos") for u in res.get("unresolved_sites") or []]
                + [m.get("ref_pos") for m in res.get("mixed_variants") or []])
    assert 450 in recorded, f"AF {af} 位点必须留痕（unresolved 或 mixed）"


def test_af2_backbone_scattered_low_af_unresolved():
    """AF2：骨架散在双峰 AF 0.15 → 经 unresolved_sites 判 △，不升级 mixed。
    真值锁定（W0 即满足）：planted 比值 0.1765 经峰幅噪声后实际高于地板
    0.18，现行口径即走 unresolved_site——本测试锁的是「不升级 mixed、
    经 unresolved_sites 判 △」这一口径不变量（2026-11-06 定稿）"""
    spec, res = _run("AF2")
    us = [u for u in res.get("unresolved_sites") or []
          if u.get("ref_pos") in (300, 700)]
    assert us and any(u.get("kind") == "mixed_uncorroborated" for u in us)
    assert _status(res) == "pending"
    assert _status(res) != "mixed"


# ---------- §4.4 PR：引物 ----------

def test_pr1_per_read_primer_mapping_ok():
    """PR1：正反两条引物各自对应自己的 read → 两条校验都 ok，不报方向不一致"""
    spec = S["PR1"].build()
    kwargs = dict(spec["kwargs"])
    res = analyze(spec["ab1_files"], spec["reference"], spec["features"], **kwargs)
    pcs = {pc["read"]: pc for pc in res["primer_checks"]}
    assert pcs["F.ab1"]["status"] == "ok", pcs["F.ab1"]
    assert pcs["R.ab1"]["status"] == "ok", pcs["R.ab1"]


def test_pr2_primer_without_read_no_signal():
    """PR2：提供了引物但没有对应的 read → 引物无信号，归 read_shortfall"""
    spec = S["PR2"].build()
    kwargs = dict(spec["kwargs"])
    res = analyze(spec["ab1_files"], spec["reference"], spec["features"], **kwargs)
    blk = [b for b in res["verdict"]["blocking"]
           if b.get("kind") == "scope_read_shortfall"]
    assert blk and "引物无信号" in blk[0]["detail"]
    assert _status(res) == "partial"


def test_pr3_primer_window_mismatch():
    """PR3：read 起点与引物 3′ 端距离出窗 → primer_mismatch（T17 口径保持，
    W0 即应满足——真值锁定防回潮）"""
    spec, res = _run("PR3")
    assert "primer_mismatch" in _kinds(res)
    assert _status(res) == "fail"


# ---------- §4.5 OC：出口一致 ----------

# W12 落地（2026-11-10）：网页头行/Excel 前缀由 verdict.status 经
# status_symbol/status_excel_word 导出——xfail 摘除，OC1 全绿
@pytest.mark.parametrize("sid", ["SC3", "SC5", "SC6", "S1"])
def test_oc1_three_exits_status_derived(sid):
    """OC1：网页头行符号、Excel 结论前缀、（前端卡片 vitest 于 W13 断言）
    全部由 verdict.status 导出"""
    from core.sanger.verdict import status_excel_word, status_symbol
    spec, res = _run(sid)
    st = _status(res)
    assert (res["conclusion"] or "").startswith(status_symbol(st))
    excel = excel_conclusion("P", res, 1, True)
    assert excel.startswith(status_excel_word(st)), \
        f"{sid}: Excel 前缀与 status {st} 不一致：{excel[:30]}"


def test_oc2_cross_origin_exits_consistent():
    """OC2：跨原点探针不再出现 fail / △ / 不合格 三种说法并存"""
    from core.sanger.verdict import status_excel_word, status_symbol
    spec, res = _run_cds_mode("CM3")
    st = _status(res)
    sym = status_symbol(st)
    assert (res["conclusion"] or "").startswith(sym)
    excel = excel_conclusion("P", res, 1, True)
    assert excel.startswith(status_excel_word(st))
    # 网页 △ 而 Excel 不合格 的并存不再出现：符号与 Excel 词同源于 status
    assert (sym == "✗") == excel.startswith("不合格")


# ---------- W12：三出口映射单元行为（符号/Excel 词由 status 导出） ----------


def test_w12_status_maps_cover_all_statuses():
    """STATUS_SYMBOLS/STATUS_EXCEL_WORDS 覆盖 status 全集；符号二值化
    （✓/✗ 之外一律 △），Excel 词与各出口既有分支词一致"""
    from core.sanger.verdict import STATUS_EXCEL_WORDS, STATUS_SYMBOLS, build_verdict
    statuses = {"pass", "fail", "pending", "partial", "mixed",
                "insufficient", "failed_reads"}
    assert set(STATUS_SYMBOLS) == statuses and set(STATUS_EXCEL_WORDS) == statuses
    assert all(s in ("✓", "✗", "△") for s in STATUS_SYMBOLS.values())
    assert STATUS_SYMBOLS["pass"] == "✓" and STATUS_SYMBOLS["fail"] == "✗"
    assert STATUS_EXCEL_WORDS["pass"] == "合格"
    assert STATUS_EXCEL_WORDS["fail"] == "不合格"
    assert STATUS_EXCEL_WORDS["pending"] == "待定"
    assert STATUS_EXCEL_WORDS["insufficient"] == "无法判定"


def test_w12_excel_conclusion_word_follows_verdict():
    """有 verdict 的记录：excel_conclusion 前缀词由 verdict.status 导出
    （不再由各分支硬编码）；无 verdict 的旧记录走降级词（历史口径）"""
    from core.sanger.verdict import status_excel_word
    _spec, res = _run("S1")                   # 确证差异 → fail
    st = _status(res)
    assert st == "fail"
    excel = excel_conclusion("P", res, 1, True)
    assert excel.startswith(status_excel_word("fail"))
    # 旧记录形态（无 verdict）：降级词分支保持原口径
    _spec2, res2 = _run("S1")
    res2.pop("verdict", None)
    excel2 = excel_conclusion("P", res2, 1, True)
    assert excel2.startswith("不合格")


# ==================== goal v3.1 §4 场景（X0 真值先行）====================
# 真值口径见 docs/audit/20261110/goal_sanger_scope_v3.1.md §4。
# 未实现的工作项以 XF(strict) 标注（reason=工作项 ID），实现后摘除即转绿；
# X0 提交之后不得修改本段构造或断言（goal §2 真值先行且不可改）。


def _queue(res):
    from core.sanger.review import build_queue
    return build_queue(res)


# ---------- X1：峰形门控（RB P0-1） ----------


def test_sc6_degraded_tail_is_read_shortfall_not_mixed():
    """SC6（恢复原构造）：峰宽 ×3.0 的尾端 → partial、缺口 cause=read_shortfall、
    不判 mixed、进复核队列、read 带 degraded_ranges"""
    _spec, res = _run("SC6")
    assert _status(res) == "partial"
    blk = [b for b in res["verdict"]["blocking"]
           if b.get("kind") == "scope_read_shortfall"]
    assert blk, "峰形退化必须归因为覆盖缺口"
    assert "mixed_variant" not in _kinds(res), "峰形退化不得判混合"
    assert _queue(res), "read_shortfall 必须进复核队列"
    assert any(r.get("degraded_ranges") for r in res["reads"]), \
        "read 必须带 degraded_ranges（前端灰显依据）"


def test_sc6b_peak_broadening_never_mixed():
    """SC6b：×2.0/2.5/3.0 三档声明范围 + 一组未声明——都不判 mixed；
    blocking 无 mixed_variant/minor_af_site；未声明组 pass 且 headline 无「混合」"""
    from tests.sanger_scenarios import _sc6_tail
    for tw in (2.0, 2.5, 3.0):
        spec = _sc6_tail(tw)
        res = analyze(spec["ab1_files"], spec["reference"], spec["features"],
                      **spec["kwargs"])
        kinds = _kinds(res)
        assert _status(res) != "mixed", f"×{tw} 误判 mixed"
        assert "mixed_variant" not in kinds, f"×{tw}: {kinds}"
        assert "minor_af_site" not in kinds, f"×{tw}: {kinds}"
    spec = _sc6_tail(3.0, scope=None)
    res = analyze(spec["ab1_files"], spec["reference"], spec["features"],
                  **spec["kwargs"])
    assert _status(res) == "pass", f"未声明组应为 pass，实得 {_status(res)}"
    assert "混合" not in (res["verdict"].get("headline") or "")


def test_sc6c_real_mixed_before_degraded_zone_still_mixed():
    """SC6c：退化区段之前（read 坐标 150）叠加 AF 0.35 真实次峰 → 判 mixed
    （门控不得误杀区段外的真实混合）"""
    _spec, res = _run("SC6c")
    kinds = _kinds(res)
    assert _status(res) == "mixed" or "mixed_variant" in kinds, \
        f"区段外真实次峰必须判混合：{_status(res)} / {kinds}"


def test_sc10_degraded_tail_pending_not_mixed():
    """SC10（恢复原构造）：峰宽 ×3.0 尾端 → pending + roi_unverified，
    不判 mixed，pass 与 blocking 不并存"""
    _spec, res = _run("SC10")
    assert _status(res) == "pending"
    assert "roi_unverified" in _kinds(res)
    assert "mixed_variant" not in _kinds(res)
    assert _status(res) != "pass"


# ---------- X4：indel 规范化（RB P1-4） ----------


@XF(strict=True, reason="X4")
def test_cm9_microhomology_insert_stays_insertion():
    """CM9：CDS 内微同源边界 1bp 插入（插入碱基与左邻相同）→ type=insertion，
    c. 符合 3′ 规则，后果移码"""
    _spec, res = _run_cds_mode("CM9")
    ins = [v for v in res["variants"] if v["type"] == "insertion"]
    assert ins, f"微同源 1bp 插入必须是 insertion：{res['variants']}"
    v = ins[0]
    coords = (v.get("coords") or {}).get("cds") or {}
    assert coords.get("c"), "必须给 c. 坐标"
    # 3′ 右移：c. 位置应是微同源串的最后一个（同聚物右端）
    conseq = (coords.get("consequence") or v.get("consequence") or "")
    assert "移码" in conseq or "frameshift" in str(conseq).lower(), \
        f"1bp 插入后果必须是移码：{conseq}"


# ---------- X2：复核队列聚合（RB P0-2） ----------


def test_rq1_real_subset_queue_bounded():
    """RQ1：真实子集未声明范围 → 队列 ≤15 项、结论 ≤6 行、3 处确证
    变异全在队列中、status=fail"""
    _spec, res = _run("RQ1")
    q = _queue(res)
    assert len(q) <= 15, f"复核队列 {len(q)} 项超上限：{[(x['kind'], x['ref_start']) for x in q]}"
    assert len(res["conclusion"].split("\n")) <= 6, \
        f"结论 {len(res['conclusion'].split(chr(10)))} 行超上限"
    assert _status(res) == "fail"
    positions = {x.get("ref_start") for x in q}
    for pos in (335, 440, 4081):
        assert pos in positions, f"真实变异 {pos} 必须进队列：{sorted(positions)}"


def test_rq2_real_subset_full_plasmid_queue_bounded():
    """RQ2：真实子集声明全质粒 → 队列 ≤25 项、3 处确证变异全在队列"""
    _spec, res = _run("RQ2")
    q = _queue(res)
    assert len(q) <= 25, f"队列 {len(q)} 项超上限"
    positions = {x.get("ref_start") for x in q}
    for pos in (335, 440, 4081):
        assert pos in positions, f"真实变异 {pos} 必须进队列：{sorted(positions)}"


def test_rq3_mixed_read_folds_into_one_item():
    """RQ3：单 read 判 mixed 且 40 个双峰位点 → 队列只有 1 项 read 问题"""
    _spec, res = _run("RQ3")
    q = _queue(res)
    read_items = [x for x in q if x.get("level") == "read"]
    assert len(read_items) == 1, f"必须折叠为 1 项 read 问题：{q}"
    assert len(q) <= 2, f"位点应全部折叠，实得 {len(q)} 项：{[(x['kind'], x.get('level')) for x in q]}"


# ---------- X3：出口全文同源（RB P0-3 / P1-1 / P1-2） ----------


_OC3_SIDS = ("SC3", "SC5", "SC6")


def test_oc3_three_exits_verbatim():
    """OC3：网页首行 = headline；Excel 结论 = 去掉符号的 headline；
    全文逐字比较（不只比符号）"""
    from core.sanger.evidence import _conclusion_summary  # noqa: F401
    for sid in _OC3_SIDS:
        _spec, res = _run(sid)
        vd = res["verdict"]
        head = vd.get("headline") or ""
        assert head, f"{sid}: verdict 必须带 headline"
        first = (res["conclusion"] or "").split("\n")[0]
        assert first == head, f"{sid}: 网页首行与 headline 不一致\n  head={head}\n  web ={first}"
        excel = excel_conclusion("P", res, len(res["reads"]), True)
        body = head.lstrip("✓✗△ ").strip()
        assert excel.startswith(body) or body in excel, \
            f"{sid}: Excel 结论未逐字采用 headline\n  head={head}\n  xls ={excel[:80]}"


def test_oc4_cross_origin_excel_not_self_contradictory():
    """OC4：跨原点 ATG 前插入 → Excel 不得同时出现「不合格」与「与设计一致」，
    不得出现「范围外 1 处差异」"""
    _spec, res = _run_cds_mode("CM3")
    excel = excel_conclusion("P", res, len(res["reads"]), True)
    assert not ("不合格" in excel and "与设计一致" in excel), excel
    assert "范围外 1 处差异" not in excel, excel


def test_oc5_declared_pass_never_claims_whole_construct():
    """OC5：声明范围 pass（CM1 干净样品）→ 任何出口不得出现「构建与设计一致」
    或不带范围的「与设计一致」"""
    _spec, res = _run_cds_mode("CM1")
    assert _status(res) == "pass"
    vd = res["verdict"]
    assert vd["scope"]["declared"]
    outputs = [res["conclusion"], excel_conclusion("P", res, len(res["reads"]), True),
               vd.get("headline") or ""]
    for out in outputs:
        assert "构建与设计一致" not in out, out
        assert "与设计一致" not in out.replace("与设计一致（未检出差异）", "") or \
               "范围" in out, f"声明范围下出现无范围的整体主张：{out}"


def test_hc1_junction_insert_counts_once():
    """HC1：一处接合区插入（同时触发 confirmed 与 junction）→ headline
    证据写「1 处确证差异」（按位点去重）"""
    _spec, res = _run_cds_mode("CM4")
    head = res["verdict"].get("headline") or ""
    assert "1 处确证差异" in head, f"headline 未按位点去重：{head}"


def test_hc2_cross_origin_range_has_name_and_single_span():
    """HC2：跨原点 CDS Cross → headline 范围写「Cross（3701–300，跨原点）」"""
    _spec, res = _run_cds_mode("CM3")
    head = res["verdict"].get("headline") or ""
    assert "Cross" in head, head
    assert "跨原点" in head, f"跨原点范围必须标注：{head}"
    assert "3701" in head and "300" in head, head


# ---------- X5：范围记忆（RB P1-3 / P2-3） ----------
# 记忆键改为「用户 + 参考」；只记显式声明；套用带 remembered 标记与
# headline 注记；批量不套用；提供 DELETE 清除端点。


@XF(strict=True, reason="X5")
def test_sm1_memory_is_not_shared_between_users():
    """SM1：用户 A 以 scope=GeneX 分析 P 后，用户 B 不声明范围分析 P
    → B 不得套用 A 的范围（记忆按用户隔离）"""
    from app import sequencing_store as st
    from core.sanger.scope import Scope

    mem = {}
    st_key = st._scope_memory_key if hasattr(st, "_scope_memory_key") else None
    assert st_key is not None, "需要按「用户 + 参考」的记忆键函数"
    st.save_scope_memory("ACGT" * 40, {"ranges": [[301, 900]], "source": "user",
                                       "declared": True, "elements": ["GeneX"]},
                         owner_id="user-A")
    got_a = st.load_scope_memory("ACGT" * 40, owner_id="user-A")
    got_b = st.load_scope_memory("ACGT" * 40, owner_id="user-B")
    assert got_a and got_a.get("declared")
    assert got_b is None, f"用户 B 不得继承用户 A 的范围：{got_b}"


@XF(strict=True, reason="X5")
def test_sm2_cds_target_never_remembered():
    """SM2：cds_mode/cds_target（自动来源）不被记忆——第二次用 cds_mode
    选 AmpR 时不得套用上次 GeneX 的记忆"""
    from app import sequencing_store as st
    ref = "TGCA" * 100
    st.save_scope_memory(ref, {"ranges": [[301, 900]], "source": "cds_target",
                               "declared": True, "elements": ["GeneX"]},
                         owner_id="u1")
    assert st.load_scope_memory(ref, owner_id="u1") is None, \
        "自动来源（cds_target）不得写入记忆"


@XF(strict=True, reason="X5")
def test_sm3_memory_marks_remembered_and_clearable():
    """SM3：记忆生效时 scope.remembered=true、reason=沿用上次范围、
    headline 带注记；清除后回到未声明口径"""
    from app import sequencing_store as st
    from core.sanger.scope import Scope
    from core.sanger.verdict import build_verdict

    ref = "TGCA" * 250
    scope = {"ranges": [[301, 900]], "source": "user", "declared": True,
             "elements": ["GeneX"]}
    st.save_scope_memory(ref, scope, owner_id=None)
    got = st.load_scope_memory(ref, owner_id=None)
    assert got is not None
    remembered = dict(got, remembered=True, reason="沿用上次范围")
    res = {"variants": [], "unresolved_sites": [], "cds_reports": [],
           "errors": [], "reads": [], "mixed_profiles": {}, "features": [],
           "coverage": {"verified_ranges": [[1, len(ref)]]},
           "consensus": {"covered_ranges": [[1, len(ref)]]},
           "reference": ref}
    vd = build_verdict(res, len(ref), Scope.from_dict(remembered))
    assert vd["scope"].get("remembered") is True
    assert "沿用上次范围" in (vd.get("headline") or ""), vd.get("headline")
    # 清除：DELETE 端点实现层（store.clear_scope_memory）
    assert hasattr(st, "clear_scope_memory"), "需要 clear_scope_memory（DELETE 端点）"
    st.clear_scope_memory(ref, owner_id=None)
    assert st.load_scope_memory(ref, owner_id=None) is None
