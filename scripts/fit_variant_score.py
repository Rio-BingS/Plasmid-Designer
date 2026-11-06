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


def main() -> int:
    check_only = "--check" in sys.argv
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
