"""拟合变体评分系数（goal_sanger_full_fix_v2 T15/T23；A 5.3）

用法：
    python scripts/fit_variant_score.py [--check]

T23 基准集（tests/data/sanger_bench + truth.yaml）就绪前，本脚本对
「现规则标签」拟合：枚举 confidence_tier 决策树的输入空间（Q × 支持数 ×
峰强比 × 信噪比 × 插入峰比 × 混合背景 × 反证），以规则 tier 为标签拟合
logistic 锚点+权重，验证手设系数（params/variant_score.json）与规则逐位
等价（行为不倒退），并输出校准曲线数据（p_real 分桶 vs 规则标签频率）。

T23 后：输入换 truth.yaml 的真值标签（real/artifact），同一拟合流程
输出连续系数写回 params/variant_score.json，校准曲线写入
docs/SANGER_CALIBRATION.md。

--check 模式：只校验当前 params 与规则标签的一致率（CI/门禁可调）。
"""

import itertools
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src/backend"))

from core.sanger.scoring import (  # noqa: E402
    PARAMS_PATH, ScoreParams, confidence_tier, load_params, p_real_for,
)

EPS = 1e-6


def enumerate_space():
    """规则决策树的输入网格（覆盖全部 tier 边界邻近区）"""
    for q, support, mp, snr, ins, mixed, counter in itertools.product(
            (0, 10, 15, 24, 25, 29, 30, 39, 40, 50),
            (1, 2),
            (None, 40, 69.9, 70, 79.9, 80, 95),
            (None, 2, 4.9, 5, 9.9, 10, 30),
            (None, 0.1, 0.29, 0.3, 0.59, 0.6, 0.9),
            (False, True),
            (False, True)):
        v = {"type": "substitution", "read_pos": 100, "read_q": q,
             "support_reads": support,
             "alt_base": "A" if not counter else "N"}
        ev = None
        if ins is not None:
            v["type"] = "insertion"
            ev = {"insertion_peak_ratio": ins}
        elif mp is not None or snr is not None:
            ev = {"mutant_pct": mp, "snr": snr}
        yield v, ev, mixed, counter


def rule_label(v, ev, mixed):
    """现规则标签（hard tier）；混合背景经由 mixed_positions 传入"""
    return confidence_tier(v, {100} if mixed else set(), ev,
                           zone={"start": 31, "end": 900, "front_hard": 30})


def fit():
    """按规则标签拟合锚点+权重（网格步进搜索；T23 换真值标签后换 logistic
    梯度下降，此处保证脚本端到端可跑）"""
    data = [(v, ev, mixed, rule_label(v, ev, mixed))
            for v, ev, mixed, _ in enumerate_space()]
    best, best_acc = ScoreParams(), -1.0
    for high_z in (4.8, 5.0, 5.2):
        for medium_z in (2.0, 2.2, 2.4):
            for low_z in (-5.5, -5.3, -5.1):
                cand = ScoreParams(high_z=high_z, medium_z=medium_z,
                                   low_z=low_z)
                acc = accuracy(cand, data)
                if acc > best_acc:
                    best, best_acc = cand, acc
    return best, best_acc, data


def accuracy(p: ScoreParams, data):
    """p_real 分档回填 tier 与规则标签的一致率（必须 1.0 才算等价）"""
    ok = 0
    for v, ev, _mixed, label in data:
        # counter 进 v（与管线一致：定案必伴随 low tier）
        v = {**v, "cross_read_resolved": label == "low" and
             v.get("alt_base") == "N"}
        p_real = _p_real_with(p, v, label, ev)
        ok += _tier(p, p_real) == label
    return ok / len(data)


def _p_real_with(p: ScoreParams, v, tier, ev) -> float:
    """用指定参数算 p_real（绕过 load_params 的缓存）"""
    import core.sanger.scoring as S
    old = S.load_params
    S.load_params = lambda: p
    try:
        return S.p_real_for(v, tier, ev)
    finally:
        S.load_params = old


def _tier(p: ScoreParams, p_real: float) -> str:
    if p_real > p.accept_p:
        return "high"
    if p_real >= p.confirmed_min:
        return "medium"
    return "low"


def calibration(data, p: ScoreParams):
    """校准曲线数据：p_real 分桶内各规则标签的频率"""
    buckets: dict = {}
    for v, ev, _mixed, label in data:
        p_real = _p_real_with(p, v, label, ev)
        # 下取整两位分桶：0.9526 属 [0.95,0.96)、0.9933 属 [0.99,1.0)
        b = buckets.setdefault(math.floor(min(max(p_real, 0.0), 0.9999) * 100) / 100, {})
        b[label] = b.get(label, 0) + 1
    return {str(k): buckets[k] for k in sorted(buckets)}


def benchmark_rows():
    """基准集真值行（T23；A 5.6）：真值位点上的替换变异 + 构造伪影位点。

    label 1 = 真值 confirmed（设计注入的真实变异）；label 0 = 构造的伪影
    位点（首端硬区/低 Q，truth status=unresolved）。上下文裁决不计入：
    M3 重叠分歧降级与 M10 互检排除的证据特征本就强，其排除是位置级上下文
    （互检/分歧），不是证据不足——把它们塞进证据拟合会系统性压低权重。"""
    from scripts.sanger_benchmark import load_truth, run_corpus
    truth = load_truth()
    corpus = run_corpus(truth)
    rows = []
    for sid, t in truth["scenarios"].items():
        if sid not in corpus:
            continue
        for v in corpus[sid].get("variants", []):
            tr = next((s for s in t.get("sites", [])
                       if s["pos"] == v["ref_pos"]), None)
            if tr is None or v.get("type") != tr["type"] \
                    or v.get("type") != "substitution":
                continue
            ev = next((e for e in (v.get("evidence_by_read") or [])
                       if e.get("mutant_pct") is not None), None) or {}
            if ev.get("mutant_pct") is None or ev.get("snr") is None:
                continue
            rows.append({"sid": sid, "pos": v["ref_pos"],
                         "label": 1 if tr["status"] == "confirmed" else 0,
                         "q": v.get("read_q") or 0,
                         "mp": ev["mutant_pct"], "snr": ev["snr"],
                         "p_real": v.get("p_real")})
    return rows


def _auc(scores, labels):
    """AUC（秩统计；同分并序）"""
    pairs = sorted(zip(scores, labels))
    rank = {}
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            rank[k] = r
        i = j + 1
    pos = [rank[k] for k, (_, lb) in enumerate(pairs) if lb == 1]
    neg = [rank[k] for k, (_, lb) in enumerate(pairs) if lb == 0]
    if not pos or not neg:
        return None
    return (sum(pos) - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def benchmark_fit():
    """真值拟合：logistic（纯 Python 梯度下降）对真值行拟合
    [截距, (q-40)/10, (mp-80)/20, (snr-10)/40]；与现参数 p_real 的 AUC 对比。
    小语料（10 行）防过拟合：结果只入校准文档，不自动写 params。"""
    rows = benchmark_rows()
    if not rows:
        print("基准集无可用真值行")
        return
    X = [[1.0, (r["q"] - 40) / 10, (r["mp"] - 80) / 20, (r["snr"] - 10) / 40]
         for r in rows]
    y = [r["label"] for r in rows]
    w = [0.0] * 4
    lr, n = 0.1, len(X)
    for _ in range(4000):
        g = [0.0] * 4
        for xi, yi in zip(X, y):
            z = sum(a * b for a, b in zip(w, xi))
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))
            err = p - yi
            for k in range(4):
                g[k] += err * xi[k]
        w = [wi - lr * gi / n for wi, gi in zip(w, g)]
    fitted = [sum(a * b for a, b in zip(w, xi)) for xi in X]
    cur_auc = _auc([r["p_real"] or 0.0 for r in rows], y)
    fit_auc = _auc(fitted, y)
    print(f"真值行数：{len(rows)}（正 {sum(y)} / 负 {len(y) - sum(y)}）")
    for r in rows:
        print(f"  {r['sid']} @{r['pos']}: label={r['label']} "
              f"Q={r['q']} mp={r['mp']:.0f} snr={r['snr']:.1f} "
              f"p_real={r['p_real']}")
    print(f"现参数 p_real AUC={cur_auc:.4f}；真值拟合 AUC={fit_auc:.4f} "
          f"(w={[round(x, 3) for x in w]})")
    print("结论：小语料防过拟合——系数仅入校准文档，不自动写回 params；"
          "仅当拟合 AUC 严格更高且全量/对照/MX 回归复验通过时人工替换")


def main() -> int:
    check_only = "--check" in sys.argv
    if "--benchmark" in sys.argv:
        benchmark_fit()
        return 0
    cur = load_params()
    data = [(v, ev, mixed, rule_label(v, ev, mixed))
            for v, ev, mixed, _ in enumerate_space()]
    cur_acc = accuracy(cur, data)
    print(f"当前参数与规则标签一致率：{cur_acc:.4f}（{len(data)} 个网格点）")
    cal = calibration(data, cur)
    print("校准曲线（p_real 分桶 → 规则标签分布）：")
    for b, dist in cal.items():
        print(f"  P≈{b}: {dist}")
    if check_only:
        return 0 if cur_acc >= 0.999 else 1
    best, best_acc, _ = fit()
    print(f"网格搜索最优一致率：{best_acc:.4f}")
    if best_acc > cur_acc:
        raw = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
        raw.update({k: getattr(best, k) for k in
                    ("high_z", "medium_z", "low_z")})
        PARAMS_PATH.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"已写回 {PARAMS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
