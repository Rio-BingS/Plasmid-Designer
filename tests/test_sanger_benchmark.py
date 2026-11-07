"""Sanger 基准集 CI 断言（goal_sanger_full_fix_v2 T23；A 5.6）

跑 tests/data/sanger_bench/truth.yaml 标注的全部场景（33 项经 analyze），
断言：S/M 场景 verdict 全对（混淆矩阵对角）；F1/F2 染料肩峰混合误报为 0；
位点级敏感度 ≥0.95、特异度 ≥0.99（阈值以 2026-11-07 基准集实测标定——
当时指标为 1.0/1.0，见 docs/SANGER_CALIBRATION.md 与 KNOWN_ISSUES）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from scripts.sanger_benchmark import load_truth, metrics, run_corpus  # noqa: E402


def test_benchmark_verdict_and_sites():
    truth = load_truth()
    corpus = run_corpus(truth)
    m = metrics(truth, corpus)
    # 1. S/M 场景 verdict 全对（混淆矩阵对角，无任何不一致）
    assert m["verdict_mismatches"] == [], \
        f"verdict 与真值不一致：{m['verdict_mismatches']}"
    # 2. 混合判定：TPR=1（S2/S3/S4/S14 判混合）、FPR=0（染料肩峰/poly 滑移
    #    等伪影场景一律不升级「疑似混合」，F1/F2 是本项的历史病根）
    assert m["mixed_tpr"] == 1.0, m["mixed_true_ids"]
    assert m["mixed_fpr"] == 0.0, m["mixed_false_positive_ids"]
    # 3. 位点级阈值（truth.yaml thresholds；标定值与依据见校准文档）
    th = truth["thresholds"]
    assert m["site_sensitivity"] >= th["site_sensitivity_min"], m["site_tp_fp_fn_tn"]
    assert m["site_specificity"] >= th["site_specificity_min"], m["site_tp_fp_fn_tn"]
    # 4. 真值位点上的判定与 p_real 分档自洽（T15 契约：confirmed ≥0.5、
    #    unresolved <0.5），真值未决不被确证
    for row in m["site_rows"]:
        if row["at_site"] and row["truth"] == "confirmed" and \
                row["actual"] == "confirmed":
            assert (row["p_real"] or 0) >= 0.5, row
        if row["at_site"] and row["truth"] == "unresolved":
            assert row["actual"] != "confirmed", row
            assert (row["p_real"] or 1) < 0.5, row
