"""T15 校准 P(真实) 与四档行动（goal_sanger_full_fix_v2 T15；A 5.3）

T23 基准集就绪前：hard tier（confidence_tier，自 pipeline 迁入）+ 手设
锚点 logistic——status/confidence 由 p_real 分档回填，与既有规则逐位
等价（行为不倒退）；p_real/action 为新增字段。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))

from core.sanger.scoring import (  # noqa: E402
    attach_score, confidence_tier, p_real_for,
)


def _v(**kw):
    base = {"type": "substitution", "ref_pos": 100, "read_pos": 100,
            "ref_base": "G", "alt_base": "A", "support_reads": 1}
    base.update(kw)
    return base


EV_CLEAN = {"mutant_pct": 98.0, "snr": 50.0}


def test_tier_tree_unchanged_from_pipeline_rules():
    """迁移后的 hard tier 与既有规则逐位一致（边界用例取自原测试）"""
    z31 = {"start": 31, "end": 900, "front_hard": 30}
    assert confidence_tier(_v(read_q=45), set(), EV_CLEAN) == "high"
    assert confidence_tier(_v(read_q=30), set(), EV_CLEAN) == "medium"
    assert confidence_tier(_v(read_q=45), set(), {"mutant_pct": 10.0, "snr": 50.0}) == "low"
    assert confidence_tier(_v(read_q=45), set(), {"mutant_pct": 98.0, "snr": 2.0}) == "low"
    # N/模糊调用恒低
    assert confidence_tier(_v(read_q=50, alt_base="N"), set(), None) == "low"
    # 首端硬区单 read 强制低（T12 口径），多 read 升级
    assert confidence_tier(_v(read_q=45, read_pos=10), set(), EV_CLEAN,
                           zone=z31) == "low"
    # 混合背景（次级峰位点，无插入峰反证）恒低
    assert confidence_tier(_v(read_q=45), {100}, EV_CLEAN) == "low"
    # indel：单 read Q30 门槛
    assert confidence_tier(_v(type="insertion", read_q=25), set(), None) == "low"
    assert confidence_tier(_v(type="insertion", read_q=30), set(), None) == "medium"
    assert confidence_tier(_v(type="insertion", read_q=30, support_reads=2),
                           set(), None) == "high"


def test_p_real_within_tier_bounds():
    """层内修正不跨层边界：high>0.99、medium∈[0.5,0.99)、low<0.01"""
    for q in (0, 25, 40, 60):
        for mp in (None, 80, 100):
            for snr in (None, 10, 60):
                ev = None if mp is None and snr is None else \
                    {"mutant_pct": mp, "snr": snr}
                ph = p_real_for(_v(read_q=q), "high", ev)
                pm = p_real_for(_v(read_q=q), "medium", ev)
                pl = p_real_for(_v(read_q=q), "low", ev)
                assert 0.99 < ph < 0.9999, (q, mp, snr, ph)
                assert 0.5 <= pm < 0.99, (q, mp, snr, pm)
                assert 0.001 <= pl < 0.01, (q, mp, snr, pl)


def test_attach_score_status_confidence_equivalent_to_rules():
    """attach_score 回填的 status/confidence 与既有三态/置信逐位等价"""
    v = _v(read_q=45)
    v["confidence"] = confidence_tier(v, set(), EV_CLEAN)
    attach_score(v, EV_CLEAN)
    assert v["confidence"] == "high"
    assert v["status"] == "confirmed"
    assert v["action"] == "accept"
    assert v["p_real"] > 0.99

    v2 = _v(read_q=30)
    v2["confidence"] = confidence_tier(v2, set(), EV_CLEAN)
    attach_score(v2, EV_CLEAN)
    assert v2["confidence"] == "medium"
    assert v2["status"] == "confirmed"
    assert v2["action"] == "review_trace"

    # 反证 + 低置信 → exclude + rejected（互检定案）
    v3 = _v(read_q=20)
    v3["confidence"] = "low"
    v3["cross_read_resolved"] = True
    attach_score(v3, None)
    assert v3["status"] == "rejected"
    assert v3["action"] == "exclude"
    assert v3["p_real"] < 0.01

    # 低置信无反证、无峰可看 → unresolved + resequence
    v4 = _v(read_q=20)
    v4["confidence"] = "low"
    attach_score(v4, None)
    assert v4["status"] == "unresolved"
    assert v4["action"] == "resequence"
    assert 0.001 <= v4["p_real"] < 0.01


def test_analyze_result_carries_p_real_and_action():
    """管线结果逐变体携带 p_real/action；确证替换 → accept"""
    import random
    from sanger_scenarios import _rand_seq, _read_from_ref
    rng = random.Random("t15")
    ref = _rand_seq(rng, 800)
    sub = (300, "G" if ref[299] != "G" else "T")
    r1 = _read_from_ref(ref, 1, 600, subs=[sub], q=50)
    r2 = _read_from_ref(ref, 1, 600, subs=[sub], q=50)
    from core.sanger.pipeline import analyze
    res = analyze([("a.ab1", r1), ("b.ab1", r2)], ref, [])
    v = next(x for x in res["variants"] if x["ref_pos"] == 300)
    assert v["p_real"] > 0.99
    assert v["action"] == "accept"
    assert v["status"] == "confirmed"
    assert len(v["evidence_by_read"]) == 2
