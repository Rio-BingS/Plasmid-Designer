# -*- coding: utf-8 -*-
"""T11 双峰模型参数拟合（goal_sanger_full_fix_v2 T23；A 5.6）

对混合判定真值（truth.yaml mixed 标注）网格扫描 MixedParams 的两个核心
参数（ratio_floor 双峰比下限、adaptive_k 自适应背景倍数），以
status 级 TPR/FPR（判「疑似混合」）与位点级检出率择优：

- FPR=0 是硬约束（F1/F2 染料肩峰误判「疑似混合」是本链路的历史病根）；
- TPR 与真值混合位点检出率最大化；平分保持现参数（不过拟合小语料）。

用法：.venv/Scripts/python.exe scripts/fit_mixed_params.py [--json]
结果与校准曲线写入 docs/SANGER_CALIBRATION.md（T23 报告）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

from scripts.sanger_benchmark import load_truth, run_corpus  # noqa: E402

# 混合判定语料：真值混合（S2/S3/S4/S14）+ 伪影非混合（F1/F2/S13/S17/M7/M12）
MIXED_IDS = ["S2", "S3", "S4", "S14", "F1", "F2", "S13", "S17", "M7", "M12"]


def evaluate(truth, corpus, want_sites):
    """(status_TPR, status_FPR, site_TPR)：status=mixed 为「判混合」"""
    mixed_true = [s for s in MIXED_IDS if truth["scenarios"][s].get("mixed")]
    mixed_false = [s for s in MIXED_IDS if not truth["scenarios"][s].get("mixed")]
    tpr = sum(1 for s in mixed_true
              if corpus[s]["verdict"]["status"] == "mixed") / len(mixed_true)
    fpr = sum(1 for s in mixed_false
              if corpus[s]["verdict"]["status"] == "mixed") / len(mixed_false)
    hits = total = 0
    for s in mixed_true:
        got = {v["ref_pos"] for v in corpus[s].get("mixed_variants", [])}
        for ms in truth["scenarios"][s].get("mixed_sites", []):
            total += 1
            hits += 1 if ms["pos"] in got else 0
    return round(tpr, 4), round(fpr, 4), round(hits / total, 4) if total else None


def main() -> int:
    import core.sanger.mixed_model as MM
    truth = load_truth()
    cur = MM._DEFAULT_PARAMS
    results = []
    # 2026-11-08 复审 R3：补低地板 0.15/0.18——AF= r/(1+r)，旧网格下限 0.20
    # （AF 0.167）漏掉 MINOR_AF_MIN=0.15（AF 0.15↔r 0.176）对应的比值带，
    # 网格从未覆盖过它；0.18（AF 0.153）是让 MINOR_AF_MIN 恰好生效的最整值
    for ratio_floor in (0.15, 0.18, 0.2, 0.25, 0.3, 0.35, 0.4):
        for adaptive_k in (3.0, 4.0, 5.0, 6.0):
            cand = MM.MixedParams(ratio_floor=ratio_floor, adaptive_k=adaptive_k)
            MM._DEFAULT_PARAMS = cand
            MM._PARAMS_CACHE.clear()
            try:
                corpus = run_corpus(truth)
                # 只保留混合语料的结果（省内存，跑全套保持判定链一致）
                corpus = {s: corpus[s] for s in MIXED_IDS}
                tpr, fpr, site = evaluate(truth, corpus, True)
            finally:
                MM._DEFAULT_PARAMS = cur
                MM._PARAMS_CACHE.clear()
            results.append({"ratio_floor": ratio_floor, "adaptive_k": adaptive_k,
                            "status_tpr": tpr, "status_fpr": fpr, "site_tpr": site})
    feasible = [r for r in results if r["status_fpr"] == 0.0]
    feasible.sort(key=lambda r: (-r["status_tpr"], -(r["site_tpr"] or 0)))
    cur_row = next(r for r in results
                   if r["ratio_floor"] == cur.ratio_floor
                   and r["adaptive_k"] == cur.adaptive_k)
    out = {"current": cur_row, "best_feasible": feasible[0] if feasible else None,
           "grid": results}
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print(f"现参数 ratio_floor={cur.ratio_floor} adaptive_k={cur.adaptive_k}: "
          f"TPR={cur_row['status_tpr']} FPR={cur_row['status_fpr']} "
          f"site={cur_row['site_tpr']}")
    if feasible:
        b = feasible[0]
        print(f"FPR=0 约束下最优: ratio_floor={b['ratio_floor']} "
              f"adaptive_k={b['adaptive_k']} TPR={b['status_tpr']} "
              f"site={b['site_tpr']}")
        better = (b["status_tpr"], b["site_tpr"] or 0) > \
                 (cur_row["status_tpr"], cur_row["site_tpr"] or 0)
        print("结论：" + ("存在更优参数——请人工复核后写 params/mixed_default.json"
                          if better else "现参数已是约束下最优（或并列），保持不变"))
    else:
        print("警告：网格内无 FPR=0 的参数组合——现参数已是最不坏选择")
    return 0


if __name__ == "__main__":
    sys.exit(main())
