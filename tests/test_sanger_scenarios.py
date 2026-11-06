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
from tests.sanger_scenarios import SCENARIOS

S = {sc.id: sc for sc in SCENARIOS}


def _run(sid):
    spec = S[sid].build()
    kwargs = {k: v for k, v in (spec.get("kwargs") or {}).items()
              if not k.startswith("_")}
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
    """S8：唯一 CDS 覆盖 34.5% → 部分验证，不得合格"""
    spec, res = _run("S8")
    excel = excel_conclusion("P", res, 1, True)
    assert not excel.startswith("合格")
    assert _status(res) in ("partial", "insufficient")


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
    """S13：不可信碱基凑满的覆盖 → verified 口径下 partial/roi_unverified"""
    spec, res = _run("S13")
    excel = excel_conclusion("P", res, 1, True)
    assert not excel.startswith("合格")
    assert _status(res) != "pass"
    assert ("roi_unverified" in _kinds(res) or _status(res) == "partial")
    cov = res.get("coverage") or {}
    if cov:
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
    """S16：poly 后 20+bp 的真实混合双峰不被滑移序列吞掉"""
    spec, res = _run("S16")
    r = res["reads"][0]
    assert 140 in r["mixed_positions"], "真实混合位点被 slip 序列剔除"


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
    res_ab = analyze([a, b], spec["reference"], spec["features"])
    res_ba = analyze([b, a], spec["reference"], spec["features"])
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
    """M7：3100 第 609 位调用碱基为第二高通道 → secondary_base 非 None 或分歧标注"""
    spec, res = _run("M7")
    detail = res["reads"][0]["mixed_detail"]
    e = next((d for d in detail if d["pos"] == 609), None)
    assert e is not None, "609 位双峰应被记录"
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
    "test_s1_tail_zone_q50_missense_not_silent": "T12",
    "test_s2_cds_mixed_35_detected": "T7",
    "test_s3_cds_mixed_5050_detected": "T7",
    "test_s14_true_mixed_not_flipped_by_weak_corroboration": "T8",
    "test_s16_mixed_site_after_poly_survives": "T7",
    "test_f1_3730_dye_shoulders_not_mixed": "T11",
    "test_f2_3100_decay_segment_not_mixed": "T11",
    "test_f3_no_pcon_still_analyzed": "T24",
    "test_m1_same_codon_subs_annotated_as_mnv": "T25",
    "test_m2_upload_order_independent_evidence": "T14",
    "test_m3_overlap_discordance_reported": "T21",
    "test_m4_orf_correction_bounded": "T16",
    "test_m5_primer_mismatch_detected": "T17",
    "test_m7_secondary_base_when_call_not_top": "T24",
    "test_m9_core_analyze_requires_topology": "T25",
    "test_m11_precheck_rejects_broken_reference": "T18",
    "test_m12_ploc_shorter_than_pbas_warns": "T24",
    "test_m15_enzyme_site_pending_hint": "T25",
}

for _name, _task in XFAIL_BY_TASK.items():
    globals()[_name] = pytest.mark.xfail(strict=True, reason=_task)(globals()[_name])
del _name, _task
