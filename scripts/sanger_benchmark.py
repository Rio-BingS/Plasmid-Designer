# -*- coding: utf-8 -*-
"""Sanger 基准评测（goal T23；A 5.6）

对 tests/data/sanger_bench/truth.yaml 标注的 35 项场景（35 项中 33 项经
analyze）+ 3 个真实 AB1（经 F1/F2/F3 场景包壳）计算：

- verdict 混淆矩阵：真值状态 × 实际状态（S/M 场景必须全对）；
- 位点级敏感度/特异度：真值位点的检出率（同位同型且非 rejected）与
  覆盖区内的确证假阳性率；
- read 级混合 TPR/FPR：真值 mixed 场景判「疑似混合」（status=mixed）的
  比例 vs 非混合场景（染料肩峰/衰减段/poly 滑移）误判比例；
- p_real 阈值扫描 ROC：真值位点 vs 非位点上的变体 P(真实) 分布。

用法：
  .venv/Scripts/python.exe scripts/sanger_benchmark.py            # 报告
  .venv/Scripts/python.exe scripts/sanger_benchmark.py --json     # 机器可读
阈值断言在 tests/test_sanger_benchmark.py（CI 运行，不 skip）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # tests 包（tests.sanger_scenarios）
sys.path.insert(0, str(ROOT / "src" / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

try:
    import yaml  # PyYAML 为既有依赖
except ImportError:  # pragma: no cover
    yaml = None

TRUTH_PATH = ROOT / "tests" / "data" / "sanger_bench" / "truth.yaml"


def load_truth() -> dict:
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML 缺失：truth.yaml 无法解析")
    return yaml.safe_load(TRUTH_PATH.read_text(encoding="utf-8"))


def run_corpus(truth: dict) -> dict:
    """跑全部场景，返回 {sid: result}（raw 场景跳过）"""
    from test_sanger_scenarios import _run
    out = {}
    for sid in truth["scenarios"]:
        if sid in truth.get("raw_scenarios", []):
            continue
        _spec, res = _run(sid)
        out[sid] = res
    return out


def _covered_bases(res: dict) -> int:
    return sum(e - s + 1 for s, e in (res["consensus"].get("covered_ranges") or []))


def metrics(truth: dict, corpus: dict) -> dict:
    """四类指标（定义见模块 docstring）"""
    tp = fn = fp = tn = 0
    conf: dict = {}
    site_rows = []   # (sid, pos, truth_status, actual_status, p_real) 供 ROC
    mismatches = []
    for sid, t in truth["scenarios"].items():
        if sid in truth.get("raw_scenarios", []) or sid not in corpus:
            continue
        res = corpus[sid]
        vd = res["verdict"]
        conf.setdefault(t["verdict"], {}).setdefault(vd["status"], 0)
        conf[t["verdict"]][vd["status"]] += 1
        if t["verdict"] != vd["status"]:
            mismatches.append({"id": sid, "truth": t["verdict"],
                               "actual": vd["status"]})
        truth_sites = {s["pos"]: s for s in t.get("sites", [])}
        unresolved_pos = {int(u.get("ref_pos") or 0)
                          for u in res.get("unresolved_sites", [])}
        variant_pos = set()
        for v in res.get("variants", []):
            variant_pos.add(v["ref_pos"])
            actual_status = v.get("status") or "confirmed"
            tr = truth_sites.get(v["ref_pos"])
            if tr is not None and v.get("type") == tr["type"]:
                site_rows.append({"sid": sid, "pos": v["ref_pos"],
                                  "truth": tr["status"],
                                  "actual": actual_status,
                                  "p_real": v.get("p_real"),
                                  "at_site": True})
            elif actual_status == "confirmed":
                site_rows.append({"sid": sid, "pos": v["ref_pos"],
                                  "truth": None, "actual": actual_status,
                                  "p_real": v.get("p_real"),
                                  "at_site": False})
                fp += 1   # 真值外确证变异 = 假阳性
            else:
                # 非真值位点的未决/排除变异：ROC 负样本（确证才算 FP）
                site_rows.append({"sid": sid, "pos": v["ref_pos"],
                                  "truth": None, "actual": actual_status,
                                  "p_real": v.get("p_real"),
                                  "at_site": False})
        for pos, tr in truth_sites.items():
            hit = any(v["ref_pos"] == pos and v.get("type") == tr["type"]
                      for v in res.get("variants", []))
            if hit:
                tp += 1
            else:
                fn += 1
        # TN：覆盖区内既非真值位点、也无任何变体记录/未决位点的位置
        tn += (_covered_bases(res) - len(truth_sites)
               - len(variant_pos - set(truth_sites)) - len(unresolved_pos - set(truth_sites)))
    # 混合 TPR/FPR（verdict.status=mixed 为「判混合」）
    mixed_true = [sid for sid, t in truth["scenarios"].items()
                  if sid in corpus and t.get("mixed")]
    mixed_false = [sid for sid, t in truth["scenarios"].items()
                   if sid in corpus and not t.get("mixed")]
    tpr_hits = [sid for sid in mixed_true
                if corpus[sid]["verdict"]["status"] == "mixed"]
    fpr_hits = [sid for sid in mixed_false
                if corpus[sid]["verdict"]["status"] == "mixed"]
    # ROC：p_real 阈值扫描（正例=真值位点上的变体）
    roc = []
    for tau in [round(0.01 * i, 2) for i in range(1, 100)]:
        pos = [r for r in site_rows if r["at_site"] and r["p_real"] is not None]
        neg = [r for r in site_rows if not r["at_site"] and r["p_real"] is not None]
        if not pos or not neg:
            break
        tpr = sum(1 for r in pos if r["p_real"] >= tau) / len(pos)
        fpr = sum(1 for r in neg if r["p_real"] >= tau) / len(neg)
        roc.append({"tau": tau, "tpr": round(tpr, 4), "fpr": round(fpr, 4)})
    return {
        "confusion": conf,
        "verdict_mismatches": mismatches,
        "site_sensitivity": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "site_specificity": round(tn / (tn + fp), 4) if (tn + fp) else None,
        "site_tp_fp_fn_tn": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "mixed_tpr": round(len(tpr_hits) / len(mixed_true), 4) if mixed_true else None,
        "mixed_fpr": round(len(fpr_hits) / len(mixed_false), 4) if mixed_false else None,
        "mixed_true_ids": mixed_true,
        "mixed_false_positive_ids": fpr_hits,
        "site_rows": site_rows,
        "roc": roc,
    }


def main() -> None:
    truth = load_truth()
    corpus = run_corpus(truth)
    m = metrics(truth, corpus)
    if "--json" in sys.argv:
        print(json.dumps(m, ensure_ascii=False, indent=1))
        return
    print("=== verdict 混淆矩阵（行=真值，列=实际）===")
    for tv, cols in sorted(m["confusion"].items()):
        for av, n in sorted(cols.items()):
            mark = "✓" if tv == av else "✗"
            print(f"  {mark} {tv:>12} → {av:<12} {n}")
    if m["verdict_mismatches"]:
        print("  不一致：", m["verdict_mismatches"])
    print(f"=== 位点级：敏感度 {m['site_sensitivity']}，特异度 {m['site_specificity']}"
          f"（{m['site_tp_fp_fn_tn']}）===")
    print(f"=== 混合：TPR {m['mixed_tpr']}（真值 {len(m['mixed_true_ids'])} 项），"
          f"FPR {m['mixed_fpr']}（误判 {m['mixed_false_positive_ids']}）===")
    print("=== ROC 关键点（tau / TPR / FPR）===")
    for r in m["roc"][::10]:
        print(f"  {r['tau']:.2f}  {r['tpr']:.4f}  {r['fpr']:.4f}")


if __name__ == "__main__":
    main()
